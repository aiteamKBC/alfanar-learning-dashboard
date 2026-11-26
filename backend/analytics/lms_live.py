"""Live, read-only LMS PostgreSQL reads for Advanced Research Methods.

Every call opens one short-lived connection, runs SELECT statements inside a
READ ONLY transaction with a statement timeout, rolls back and closes. Nothing is
written to the LMS, and nothing read here is stored in any dashboard database,
file or cache: callers receive plain Python values for the current request only.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

from django.conf import settings

logger = logging.getLogger(__name__)

ARM_MODULE_ID = 'MOD-202611181252373876512A22A1DDA1C1'
ARM_TITLE = 'Advanced Research Methods'
# Dashboard-local integer key for the module filter and URLs (LMS module IDs are text).
ARM_COURSE_ID = 1_000_001
# Learners who are not already dashboard learners get an ID in their own namespace.
ARM_LEARNER_ID_OFFSET = 2_000_000
# LMS learner IDs assigned to ARM in the LMS but not part of the intended Al Fanar roster
# (owner decision). Matched by exact LMS learner ID only; LMS records are never changed.
EXCLUDED_LMS_LEARNER_IDS = frozenset({272, 593, 1213})


class LmsUnavailable(Exception):
    """The LMS could not be read; callers decide how to degrade."""


def configured() -> bool:
    return bool(getattr(settings, 'LMS_DATABASE_URL', ''))


# Each statement depends only on the module ID, so all can be sent together.
_ROSTER = """
    select pm.learner_id from "Learner".learner_training_plan_modules pm
    where (pm.module_ref = %(module)s or pm.curriculum_module_id = %(module)s)
      and pm.learner_id <> all(%(excluded)s)"""
_COMPONENT_IDS = """
    select c.id from curriculum.components c where c.module_catalogue_id = %(module)s"""
_QUIZ_IDS = f"""
    select q.quiz_id::text from curriculum.quiz_component_links q where q.component_id in ({_COMPONENT_IDS})"""
_COMPLETED_OCCURRENCES = """
    select o.id from curriculum.live_session_occurrences o
    join curriculum.live_sessions ls on ls.id = o.live_session_id
    where ls.module_catalogue_id = %(module)s and o.status = 'completed'"""

QUERIES = {
    'module': """
        select module_catalogue_id, title, coalesce(is_programme_deleted, false) as is_deleted, deleted_at
        from curriculum.modules where module_catalogue_id = %(module)s""",
    # The LMS expected module roster: learners whose training plan assigns this module.
    'roster': f"""
        select l.id, l.full_name, l.email, l.email_normalized
        from "Learner".learners l where l.id in ({_ROSTER})
        order by l.id""",
    'components': """
        select c.id, c.type, c.title, w.week_number, w.display_order as week_order, c.display_order
        from curriculum.components c
        join curriculum.weeks w on w.id = c.week_id
        where c.module_catalogue_id = %(module)s and w.module_catalogue_id = %(module)s
          and not coalesce(c.is_programme_deleted, false) and c.deleted_at is null
          and not coalesce(w.is_programme_deleted, false) and w.deleted_at is null
        order by w.week_number, w.display_order, c.display_order, c.id""",
    'quiz_links': f"""
        select q.component_id, q.quiz_id::text as quiz_id
        from curriculum.quiz_component_links q where q.component_id in ({_COMPONENT_IDS})""",
    'entries': f"""
        select e.id, e.learner_id, e.component_ref, e.curriculum_component_ref, e.quiz_ref, e.kind, e.accepted,
               e.attempt, e.grade, e.passed,
               coalesce(e.submitted_at, e.feed_occurred_at, e.declared_completed_at, e.ssot_created_at) as occurred_at
        from "Learner".learner_progress_entries e
        where e.learner_id in ({_ROSTER}) and e.deleted_at is null
          and (e.component_ref in ({_COMPONENT_IDS}) or e.curriculum_component_ref in ({_COMPONENT_IDS})
               or e.quiz_ref in ({_QUIZ_IDS}))
        order by e.learner_id, e.id""",
    # Commercial-learner submissions are keyed by the learner's enrolment ID.
    'submissions': f"""
        select s.id::text as id, l.id as learner_id, s.activity_id, s.status, s.submitted_at
        from "Learner".learning_reflection_submissions s
        join "Learner".learners l on s.learner_id = l.enrolment_id::text
        where s.activity_id in ({_COMPONENT_IDS}) and l.id in ({_ROSTER})
        order by s.submitted_at nulls first, s.id""",
    'occurrences': f"""
        select o.id, o.session_number, o.scheduled_start, o.scheduled_end, o.actual_end, o.status, ls.timezone,
               (select case when count(*) > 0 and count(a.calculated_at) = count(*)
                            then min(a.calculated_at) end
                from curriculum.live_session_learner_attendance a
                where a.occurrence_id = o.id and a.module_catalogue_id = %(module)s
                  and a.learner_profile_id in ({_ROSTER})) as attendance_finalized_at
        from curriculum.live_session_occurrences o
        join curriculum.live_sessions ls on ls.id = o.live_session_id
        where ls.module_catalogue_id = %(module)s
        order by o.scheduled_start, o.session_number, o.id""",
    # Exactly one persisted row per learner, lecture and module (LMS unique key); latest row if ever repeated.
    'attendance': f"""
        select distinct on (a.occurrence_id, a.learner_profile_id)
               a.occurrence_id, a.learner_profile_id, a.attendance_status, a.attended_seconds,
               a.first_join_at, a.last_leave_at
        from curriculum.live_session_learner_attendance a
        where a.module_catalogue_id = %(module)s and a.occurrence_id in ({_COMPLETED_OCCURRENCES})
          and a.learner_profile_id in ({_ROSTER})
        order by a.occurrence_id, a.learner_profile_id, a.updated_at desc nulls last, a.id desc""",
    # Use the artifact's exact meeting/organizer, not the parent recurring-series ID.
    # No transcripts, playback URLs, storage credentials or media are copied/cached.
    'artifacts': f"""
        select a.id, a.occurrence_id, a.artifact_type, a.graph_artifact_id, a.created_datetime, a.end_datetime,
               a.metadata->>'meetingId' as meeting_id,
               a.metadata->'meetingOrganizer'->'user'->>'id' as organizer_id
        from curriculum.live_session_artifacts a
        where a.occurrence_id in ({_COMPLETED_OCCURRENCES})
          and a.artifact_type in ('recording', 'transcript')
          and coalesce(a.metadata->>'lmsHiddenFromLearners', 'false') <> 'true'
        order by a.created_datetime nulls last, a.id""",
}
assert all(sql.lstrip().lower().startswith('select') for sql in QUERIES.values())


@dataclass
class ArmSnapshot:
    """Raw LMS rows for one request. Never persisted."""
    module: dict
    roster: list[dict] = field(default_factory=list)
    components: list[dict] = field(default_factory=list)
    quiz_links: list[dict] = field(default_factory=list)
    entries: list[dict] = field(default_factory=list)
    submissions: list[dict] = field(default_factory=list)
    occurrences: list[dict] = field(default_factory=list)
    attendance: list[dict] = field(default_factory=list)
    artifacts: list[dict] = field(default_factory=list)


def _read(conn) -> dict[str, list[dict]]:
    import psycopg

    params = {'module': ARM_MODULE_ID, 'excluded': sorted(EXCLUDED_LMS_LEARNER_IDS)}
    guard = ("select set_config('statement_timeout', %s, true) as timeout, "
             "current_setting('transaction_read_only') as ro")
    timeout = f'{int(settings.LMS_STATEMENT_TIMEOUT_MS)}ms'
    cursors = {}
    if psycopg.Pipeline.is_supported():
        with conn.pipeline():  # one network round trip for every statement
            cursors['guard'] = conn.cursor()
            cursors['guard'].execute(guard, (timeout,))
            for name, sql in QUERIES.items():
                cursors[name] = conn.cursor()
                cursors[name].execute(sql, params)
    else:
        cursors['guard'] = conn.cursor()
        cursors['guard'].execute(guard, (timeout,))
        for name, sql in QUERIES.items():
            cursors[name] = conn.cursor()
            cursors[name].execute(sql, params)
    if cursors['guard'].fetchone()['ro'] != 'on':
        raise LmsUnavailable('The LMS session is not read-only.')
    return {name: cursors[name].fetchall() for name in QUERIES}


def load_snapshot() -> ArmSnapshot:
    """Read the module's current LMS records in one read-only transaction."""
    if not configured():
        raise LmsUnavailable('The LMS connection is not configured.')
    import psycopg
    from psycopg.rows import dict_row

    try:
        conn = psycopg.connect(settings.LMS_DATABASE_URL, connect_timeout=settings.LMS_CONNECT_TIMEOUT_SECONDS,
                               row_factory=dict_row, application_name='alfanar-dashboard-readonly')
    except psycopg.Error as exc:
        logger.warning('LMS connection failed (%s)', type(exc).__name__)
        raise LmsUnavailable('The LMS is currently unavailable.') from None
    try:
        conn.read_only = True  # every transaction starts as BEGIN READ ONLY
        rows = _read(conn)
    except psycopg.Error as exc:
        logger.warning('LMS read failed (%s)', type(exc).__name__)
        raise LmsUnavailable('The LMS is currently unavailable.') from None
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()
    module = rows['module'][0] if rows['module'] else None
    if module is None or module['is_deleted'] or module['deleted_at'] is not None:
        raise LmsUnavailable('Advanced Research Methods is not an active LMS module.')
    return ArmSnapshot(module=module, **{name: rows[name] for name in QUERIES if name != 'module'})


def as_utc(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)

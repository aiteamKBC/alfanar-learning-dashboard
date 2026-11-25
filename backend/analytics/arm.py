"""Advanced Research Methods: dashboard payloads built only from live LMS records.

Response shapes mirror `metrics` so the existing screens render this module without
new components. Values are taken from the LMS as stored: attendance statuses and
durations are the LMS learner attendance records, completions are LMS progress
entries and submissions are LMS submission records. Nothing is corrected, reconciled,
recalculated from raw Teams data, written back or persisted.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import Counter, defaultdict
from zoneinfo import ZoneInfo

from . import lms_live
from .course_labels import course_order_key
from .lms_live import ARM_COURSE_ID, ARM_LEARNER_ID_OFFSET, ARM_TITLE, as_utc
from .metrics import KIND_LABELS, LESSON_KIND_VALUES, _avg, _pct, defs, learner_rollup
from .lectures.lms_media import resource_id, valid_reference

# LMS component types with a direct dashboard equivalent. Lecture components are reported
# through the LMS live-session records instead; other types have no dashboard column.
COMPONENT_KINDS = {'video': 'video', 'pdf': 'pdf', 'audio': 'audio', 'podcast': 'audio',
                   'reading': 'reading', 'quiz': 'quiz', 'assignment': 'assignment'}
# Windows time-zone names used by LMS live sessions.
SESSION_ZONES = {'GMT Standard Time': 'Europe/London', 'UTC': 'UTC', 'Coordinated Universal Time': 'UTC',
                 'Egypt Standard Time': 'Africa/Cairo', 'Arabian Standard Time': 'Asia/Dubai',
                 'Arab Standard Time': 'Asia/Riyadh'}
KNOWN_STATUSES = {'present', 'absent'}
# The LMS stores no module progress percentage for this module. Approved display fallback for
# enrolled learners in ARM views only; it is not an LMS value and is never written anywhere.
# Cross-module averages exclude it (see `rollup`).
PROGRESS_FALLBACK = 0.0

DEFINITIONS = {
    'attendance': 'Present / recorded LMS attendance observations for completed lectures, using the LMS '
                  'learner attendance statuses as stored.',
    'delivered_lectures': 'Lectures with status completed in the LMS live-session schedule.',
    'delivered_sessions': 'Lectures with status completed in the LMS live-session schedule.',
    'materials_completed': 'Accepted LMS progress entries for the module\'s videos, PDFs, audio and readings.',
}


def definitions() -> dict:
    return {**defs(), **DEFINITIONS}


def _latest(*values):
    found = [v for v in values if v is not None]
    return max(found) if found else None


def _dashboard_registry():
    from .lectures.library import registry
    learners, emails, _ = registry()
    return learners, emails


class Arm:
    """One request's view of the module. Built from a snapshot; holds nothing beyond the request."""

    course_id = ARM_COURSE_ID
    title = ARM_TITLE

    def __init__(self, snapshot: lms_live.ArmSnapshot, registry=None):
        dashboard_learners, emails = registry if registry is not None else _dashboard_registry()
        self.components = []
        for c in snapshot.components:
            kind = COMPONENT_KINDS.get((c['type'] or '').strip().lower())
            if kind:
                self.components.append({'id': c['id'], 'kind': kind, 'title': c['title'] or '',
                                        'week': c['week_number']})
        self.component_by_id = {c['id']: c for c in self.components}
        self.catalogue = Counter(c['kind'] for c in self.components)
        quiz_component = {q['quiz_id']: q['component_id'] for q in snapshot.quiz_links}

        self.learners: list[dict] = []
        used = set()
        excluded = lms_live.EXCLUDED_LMS_LEARNER_IDS
        for r in snapshot.roster:
            if r['id'] in excluded:
                continue  # also filtered in SQL; kept here so no path can reintroduce them
            email = (r['email_normalized'] or r['email'] or '').strip().casefold()
            sid = emails.get(email) if email else None
            dashboard = dashboard_learners.get(sid) if sid is not None and sid not in used else None
            learner_id = dashboard.source_id if dashboard else ARM_LEARNER_ID_OFFSET + r['id']
            used.add(learner_id)
            self.learners.append({
                'lms_id': r['id'], 'learner_id': learner_id, 'dashboard': dashboard is not None,
                'name': dashboard.name if dashboard else ((r['full_name'] or '').strip() or f'Learner {learner_id}'),
                'email': dashboard.email if dashboard else (r['email'] or ''),
            })
        self.by_lms = {x['lms_id']: x for x in self.learners}
        self.by_learner_id = {x['learner_id']: x for x in self.learners}

        self.sessions = []
        for o in snapshot.occurrences:
            if o['status'] != 'completed':
                continue  # scheduled, cancelled or unprocessed lectures are never treated as delivered
            zone = ZoneInfo(SESSION_ZONES.get(o['timezone'] or '', 'UTC'))
            start, end = o['scheduled_start'], o['scheduled_end']
            local_start = start.replace(tzinfo=zone) if start and start.tzinfo is None else start
            local_end = end.replace(tzinfo=zone) if end and end.tzinfo is None else end
            self.sessions.append({
                'id': o['id'], 'number': o['session_number'],
                'date': local_start.date() if local_start else None,
                'starts_at': local_start.astimezone(dt.timezone.utc) if local_start else None,
                'ends_at': local_end.astimezone(dt.timezone.utc) if local_end else None,
            })
        for n, s in enumerate(self.sessions, start=1):
            s['number'] = s['number'] or n
        session_ids = {s['id'] for s in self.sessions}
        self.artifacts = defaultdict(list)
        for row in snapshot.artifacts:
            if row['occurrence_id'] in session_ids and valid_reference(row):
                self.artifacts[row['occurrence_id']].append(row)
        self.attendance = {(a['learner_profile_id'], a['occurrence_id']): a for a in snapshot.attendance
                           if a['occurrence_id'] in session_ids and a['learner_profile_id'] in self.by_lms}
        self.present_by_session = Counter(occ for (_, occ), a in self.attendance.items()
                                          if a['attendance_status'] == 'present')

        self.stats = {x['lms_id']: {'done': {}, 'quiz': defaultdict(list), 'subs': defaultdict(list),
                                    'has_activity': False, 'last_activity': None} for x in self.learners}
        for e in snapshot.entries:
            s = self.stats.get(e['learner_id'])
            component = next((ref for ref in (e['component_ref'], e['curriculum_component_ref'])
                              if ref in self.component_by_id), None) or quiz_component.get(e['quiz_ref'])
            if s is None or component not in self.component_by_id:
                continue
            kind = self.component_by_id[component]['kind']
            when = as_utc(e['occurred_at'])
            s['has_activity'] = True
            s['last_activity'] = _latest(s['last_activity'], when)
            if kind == 'quiz':
                s['quiz'][component].append(e)
            elif kind in LESSON_KIND_VALUES and e['accepted'] is True:
                s['done'][component] = _latest(s['done'].get(component), when)
        for sub in snapshot.submissions:
            s = self.stats.get(sub['learner_id'])
            component = self.component_by_id.get(sub['activity_id'])
            if s is None or component is None or component['kind'] != 'assignment':
                continue
            s['subs'][component['id']].append(sub)
            s['has_activity'] = True
            s['last_activity'] = _latest(s['last_activity'], as_utc(sub['submitted_at']))

    # --- learner-level figures --------------------------------------------------------

    def _quiz(self, entries):
        ordered = sorted(entries, key=lambda e: (e['attempt'] or 0, as_utc(e['occurred_at']) or dt.datetime.min.replace(tzinfo=dt.timezone.utc), e['id']))
        latest = ordered[-1]
        grades = [float(e['grade']) for e in ordered if e['grade'] is not None]
        return {'attempts': len(ordered), 'latest_attempt': latest['attempt'],
                'latest_score': float(latest['grade']) if latest['grade'] is not None else None,
                'latest_status': 'passed' if latest['passed'] is True else ('failed' if latest['passed'] is False else ''),
                'best_score': max(grades) if grades else None,
                'ever_passed': any(e['passed'] is True for e in ordered) if any(e['passed'] is not None for e in ordered) else None,
                'history': [{'attempt': e['attempt'], 'score': float(e['grade']) if e['grade'] is not None else None,
                             'status': 'passed' if e['passed'] is True else ('failed' if e['passed'] is False else ''),
                             'created_at': as_utc(e['occurred_at'])} for e in ordered]}

    def _figures(self, lms_id):
        s = self.stats[lms_id]
        lessons = Counter(self.component_by_id[c]['kind'] for c in s['done'])
        quizzes = [self._quiz(entries) for entries in s['quiz'].values()]
        scores = [q['latest_score'] for q in quizzes if q['latest_score'] is not None]
        statuses = Counter((sub['status'] or 'unknown') for subs in s['subs'].values() for sub in subs)
        return {
            'lessons': lessons, 'lesson_total': sum(lessons.values()),
            'quizzes_attempted': len(quizzes), 'quiz_attempts': sum(q['attempts'] for q in quizzes),
            'quiz_scores': scores, 'quizzes_graded': len(scores),
            'quizzes_passed_latest': sum(1 for q in quizzes if q['latest_score'] is not None and q['latest_status'] == 'passed'),
            'submissions': sum(statuses.values()), 'submitted': sum(v for k, v in statuses.items() if k != 'draft'),
            'submission_status': dict(statuses),
            'assignments_submitted': sum(1 for subs in s['subs'].values() if any(x['status'] != 'draft' for x in subs)),
            'has_activity': s['has_activity'], 'last_activity': s['last_activity'],
        }

    def _observations(self, lms_id):
        rows = [self.attendance.get((lms_id, s['id'])) for s in self.sessions]
        known = [r for r in rows if r and r['attendance_status'] in KNOWN_STATUSES]
        seconds = [r['attended_seconds'] for r in rows if r and r['attended_seconds'] is not None]
        return {'known': len(known), 'attended': sum(1 for r in known if r['attendance_status'] == 'present'),
                'seconds': sum(seconds) if seconds else None}

    def enrollment_attendance(self, lms_id) -> dict:
        delivered = len(self.sessions)
        if not delivered:
            return {'available': False, 'delivered': None, 'attended': None, 'pct': None, 'live_seconds': None,
                    'reason': 'No completed lectures are recorded in the LMS for this module.'}
        o = self._observations(lms_id)
        return {'available': o['known'] > 0, 'delivered': delivered, 'measured': o['known'],
                'missing': delivered - o['known'], 'attended': o['attended'] if o['known'] else None,
                'pct': _pct(o['attended'], o['known']), 'live_seconds': o['seconds'], 'evidence_source': 'lms',
                'reason': '' if o['known'] else 'No LMS attendance record for this learner.'}

    def directory_attendance(self, lms_id, original: dict) -> dict:
        delivered = len(self.sessions)
        o = self._observations(lms_id)
        return {**original, 'basis': 'lms', 'available': delivered > 0,
                'attended': o['attended'] if delivered else None, 'expected': delivered if delivered else None,
                'pct': _pct(o['attended'], delivered), 'known': o['known'], 'absent': o['known'] - o['attended'],
                'unknown': delivered - o['known'] if delivered else None,
                'modules_covered': 1 if o['known'] else 0, 'modules_total': 1, 'validation_issues': []}

    def verified_seconds(self, lms_id):
        return self._observations(lms_id)['seconds']

    def enrollment_payload(self, lms_id) -> dict:
        f = self._figures(lms_id)
        lessons_known = sum(self.catalogue[k] for k in LESSON_KIND_VALUES)
        return {
            'course_id': ARM_COURSE_ID, 'course': ARM_TITLE, 'progress_pct': PROGRESS_FALLBACK,
            'has_activity': f['has_activity'], 'lessons_completed': f['lesson_total'], 'lessons_known': lessons_known,
            'lessons_by_type': {k: {'completed': f['lessons'].get(k, 0), 'known': self.catalogue[k]}
                                for k in LESSON_KIND_VALUES},
            'quizzes_attempted': f['quizzes_attempted'], 'quizzes_known': self.catalogue['quiz'],
            'quiz_attempts': f['quiz_attempts'], 'mean_latest_score': _avg(f['quiz_scores']),
            'latest_score_sum': sum(f['quiz_scores']), 'quizzes_passed_latest': f['quizzes_passed_latest'],
            'quizzes_graded': f['quizzes_graded'], 'assignments_submitted': f['assignments_submitted'],
            'assignments_known': self.catalogue['assignment'], 'submission_status': f['submission_status'],
            'recorded_seconds': 0, 'last_activity': f['last_activity'], 'last_login': None, 'registered_at': None,
            'attendance': self.enrollment_attendance(lms_id),
        }

    def module_metrics(self, lms_id) -> dict:
        p = self.enrollment_payload(lms_id)
        return {**{k: p[k] for k in ('quizzes_attempted', 'quizzes_known', 'mean_latest_score', 'lessons_by_type',
                                     'assignments_submitted', 'assignments_known')},
                'verified_attendance_seconds': self.verified_seconds(lms_id)}

    def directory_row(self, lms_id) -> dict:
        learner = self.by_lms[lms_id]
        payload = self.enrollment_payload(lms_id)
        roll = rollup([payload])
        roll['attendance'] = self.directory_attendance(lms_id, roll['attendance'])
        roll.update(lessons_known=payload['lessons_known'],
                    lessons_completion_pct=_pct(roll['lessons_completed'], payload['lessons_known']),
                    quiz_completion_pct=_pct(roll['quizzes_attempted'], roll['quizzes_known']),
                    verified_attendance_seconds=self.verified_seconds(lms_id))
        return {'learner_id': learner['learner_id'], 'name': learner['name'],
                'enrollments': [{'course_id': ARM_COURSE_ID, 'course': ARM_TITLE, 'progress_pct': PROGRESS_FALLBACK}], **roll}

    # --- selections --------------------------------------------------------------------

    def select(self, engagement=None, also_active=frozenset()):
        """Roster learners for a learner-status filter; `also_active` adds activity in other modules."""
        rows = self.learners
        active = lambda x: self.stats[x['lms_id']]['has_activity'] or x['learner_id'] in also_active  # noqa: E731
        if engagement == 'active':
            rows = [x for x in rows if active(x)]
        elif engagement == 'inactive':
            rows = [x for x in rows if not active(x)]
        return rows

    def attendance_aggregate(self, learners) -> dict:
        delivered = len(self.sessions)
        obs = [self._observations(x['lms_id']) for x in learners] if delivered else []
        known = sum(o['known'] for o in obs)
        attended = sum(o['attended'] for o in obs)
        seconds = [o['seconds'] for o in obs if o['seconds'] is not None]
        possible = len(learners) * delivered
        return {
            'available': known > 0, 'attended': attended, 'expected': known, 'pct': _pct(attended, known),
            'live_seconds': sum(seconds) if seconds else None,
            'possible_learner_sessions': possible, 'missing_learner_sessions': possible - known,
            'coverage_pct': _pct(known, possible),
            'enrollments_without_cells': sum(1 for o in obs if not o['known']),
            'evidence_source': 'lms', 'delivered_sessions': delivered,
            'enrollments_covered': len(learners) if delivered else 0,
            'enrollments_measured': sum(1 for o in obs if o['known']), 'enrollments_total': len(learners),
            'enrollments_without_teams_identity': 0, 'learners_without_teams_identity': 0,
            'modules_without_data': [] if delivered else [ARM_COURSE_ID],
        }

    def totals(self, learners) -> dict:
        figures = [self._figures(x['lms_id']) for x in learners]
        n = len(learners)
        lessons_known = sum(self.catalogue[k] for k in LESSON_KIND_VALUES)
        statuses = Counter()
        for f in figures:
            statuses.update(f['submission_status'])
        return {
            'learner_ids': {x['learner_id'] for x in learners},
            'active_ids': {x['learner_id'] for x, f in zip(learners, figures) if f['has_activity']},
            'enrollments': n,
            'quizzes_attempted': sum(f['quizzes_attempted'] for f in figures), 'quiz_den': self.catalogue['quiz'] * n,
            'scores': [s for f in figures for s in f['quiz_scores']],
            'passed': sum(f['quizzes_passed_latest'] for f in figures), 'graded': sum(f['quizzes_graded'] for f in figures),
            'submitted': sum(f['submitted'] for f in figures), 'submissions': sum(f['submissions'] for f in figures),
            'by_status': dict(statuses),
            'assignments_submitted': sum(f['assignments_submitted'] for f in figures),
            'assign_den': self.catalogue['assignment'] * n,
            'lessons': sum(f['lesson_total'] for f in figures), 'lesson_den': lessons_known * n,
            'with_activity': sum(1 for f in figures if f['has_activity']),
            'lessons_by_kind': {k: sum(f['lessons'].get(k, 0) for f in figures) for k in LESSON_KIND_VALUES},
            'attendance': self.attendance_aggregate(learners),
        }

    def summary(self, engagement=None) -> dict:
        t = self.totals(self.select(engagement))
        learners, active = len(t['learner_ids']), len(t['active_ids'])
        return {
            'filters': {'course': ARM_COURSE_ID, 'engagement': engagement},
            'kpis': {
                'unique_learners': {'value': learners},
                'modules': {'value': 1 if t['enrollments'] else 0},
                'enrollments': {'value': t['enrollments']},
                'active_learners': {'value': active, 'denominator': learners, 'pct': _pct(active, learners)},
                'mean_progress': {'value': PROGRESS_FALLBACK if t['enrollments'] else None,
                                  'median': PROGRESS_FALLBACK if t['enrollments'] else None,
                                  'n': t['enrollments'], 'unit': '%'},
                'quiz_completion': {'value': t['quizzes_attempted'], 'denominator': t['quiz_den'],
                                    'pct': _pct(t['quizzes_attempted'], t['quiz_den'])},
                'mean_latest_score': {'value': _avg(t['scores']), 'n': len(t['scores']), 'unit': '%'},
                'pass_rate': {'value': t['passed'], 'denominator': t['graded'], 'pct': _pct(t['passed'], t['graded'])},
                'submissions': {'value': t['submitted'], 'total_records': t['submissions'], 'by_status': t['by_status']},
                'assignment_coverage': {'value': t['assignments_submitted'], 'denominator': t['assign_den'],
                                        'pct': _pct(t['assignments_submitted'], t['assign_den'])},
                'materials_completed': {'value': t['lessons'], 'denominator': t['lesson_den'],
                                        'pct': _pct(t['lessons'], t['lesson_den'])},
                'activity_coverage': {'value': t['with_activity'], 'denominator': t['enrollments'],
                                      'pct': _pct(t['with_activity'], t['enrollments'])},
                'recorded_time': {'value': 0, 'timed_records': 0, 'untimed_records': t['lessons'], 'unit': 'seconds',
                                  'verified': False, 'warning': 'LMS timers may include idle/open sessions; not verified study hours'},
                'attendance': t['attendance'],
            },
            'definitions': definitions(),
        }

    # --- charts ------------------------------------------------------------------------

    def chart_parts(self, learners, activity_type=None) -> dict:
        t = self.totals(learners)
        n = len(learners)
        kinds = [activity_type] if activity_type else list(KIND_LABELS)
        figures = {x['lms_id']: self._figures(x['lms_id']) for x in learners}
        recordings = sum(1 for c in self.components if c['kind'] == 'video'
                         and any(c['id'] in self.stats[x['lms_id']]['done'] for x in self.learners))
        by_type = {}
        for k in kinds:
            known = self.catalogue[k]
            if k == 'quiz':
                done = t['quizzes_attempted']
            elif k == 'assignment':
                done = t['assignments_submitted']
            else:
                done = t['lessons_by_kind'].get(k, 0)
            by_type[k] = {'completed': done, 'possible': known * n, 'known_items': known, 'pct': _pct(done, known * n)}
        counts = []
        for k in kinds:
            if k == 'quiz':
                counts.append((k, 'Quizzes attempted', t['quizzes_attempted']))
            elif k == 'assignment':
                counts.append((k, 'Assignments submitted', t['submitted']))
            else:
                counts.append((k, f'{KIND_LABELS[k]} completions', t['lessons_by_kind'].get(k, 0)))
        delivered = len(self.sessions)
        return {
            'module_progress': {
                'course_id': ARM_COURSE_ID, 'course': ARM_TITLE, 'enrollments': n, 'active': t['with_activity'],
                'mean_progress': PROGRESS_FALLBACK if n else None, 'median_progress': PROGRESS_FALLBACK if n else None,
                'delivered_sessions': delivered if delivered else None,
                'delivered_lectures': {'count': delivered, 'source': 'lms', 'teams_sessions': None,
                                       'differs_from_teams': False},
                'lecture_recordings': recordings, 'attendance': t['attendance'],
            },
            'quiz_by_module': {
                'course_id': ARM_COURSE_ID, 'course': ARM_TITLE, 'known_quizzes': self.catalogue['quiz'],
                'attempted_pairs': t['quizzes_attempted'], 'possible_pairs': t['quiz_den'],
                'completion_pct': _pct(t['quizzes_attempted'], t['quiz_den']),
                'mean_latest_score': _avg(t['scores']), 'pass_rate_pct': _pct(t['passed'], t['graded']),
            },
            'activity_completion': {'course_id': ARM_COURSE_ID, 'course': ARM_TITLE, 'by_type': by_type},
            'activity_by_type': counts,
            'rows': [{'learner_id': x['learner_id'], 'name': x['name'], 'progress': {ARM_COURSE_ID: PROGRESS_FALLBACK}}
                     for x in learners],
            'figures': figures,
        }

    def charts(self, engagement=None, activity_type=None) -> dict:
        learners = self.select(engagement)
        parts = self.chart_parts(learners, activity_type)
        rows = [{**row, 'module_metrics': {ARM_COURSE_ID: self.module_metrics(x['lms_id'])}}
                for row, x in zip(parts['rows'], learners)]
        n = len(learners)
        return {
            'filters': {'course': ARM_COURSE_ID, 'engagement': engagement, 'activity_type': activity_type},
            'module_progress': [parts['module_progress']] if n else [],
            'quiz_by_module': [parts['quiz_by_module']] if n else [],
            'activity_completion': [parts['activity_completion']] if n else [],
            'activity_by_type': [{'type': k, 'label': label, 'count': c} for k, label, c in parts['activity_by_type']],
            'progress_distribution': {'buckets': [{'bucket': b, 'count': n if b == '0%' else 0} for b in
                                                  ('0%', '1–24%', '25–49%', '50–74%', '75–99%', '100%')],
                                      'missing': 0, 'total': n},
            'learner_module_matrix': {'modules': [{'course_id': ARM_COURSE_ID, 'course': ARM_TITLE}] if n else [],
                                      'rows': sorted(rows, key=lambda r: r['name'].lower())},
            'definitions': definitions(),
        }

    # --- learner pages -----------------------------------------------------------------

    def matrix(self, learner_id) -> dict:
        learner = self.by_learner_id[learner_id]
        s = self.stats[learner['lms_id']]
        rows = []
        for c in self.components:
            row = {'activity_id': c['id'], 'kind': c['kind'], 'kind_label': KIND_LABELS[c['kind']],
                   'title': c['title'], 'recorded': False}
            if c['kind'] in LESSON_KIND_VALUES:
                done = c['id'] in s['done']
                row['lesson'] = {'recorded_seconds': None, 'started_at': s['done'][c['id']], 'ended_at': None,
                                 'time_source': 'lms'} if done else None
                row['recorded'] = done
            elif c['kind'] == 'quiz':
                entries = s['quiz'].get(c['id'])
                row['quiz'] = {'questions': None, **self._quiz(entries)} if entries else None
                if not entries:
                    row['quiz_questions'] = None
                row['recorded'] = bool(entries)
            else:
                subs = s['subs'].get(c['id'], [])
                latest = subs[-1] if subs else None
                row['assignment'] = None if latest is None else {
                    'status': latest['status'], 'grade': None, 'updated_at': as_utc(latest['submitted_at']),
                    'has_submitted': any(x['status'] != 'draft' for x in subs),
                    'history': [{'source_id': x['id'], 'number': i, 'status': x['status'], 'grade': None,
                                 'updated_at': as_utc(x['submitted_at'])} for i, x in enumerate(subs, start=1)],
                    'submissions': len(subs)}
                row['recorded'] = latest is not None
            rows.append(row)

        def col(kind):
            items = [r for r in rows if r['kind'] == kind]
            return {'known': len(items), 'recorded': sum(1 for r in items if r['recorded'])}

        quiz_rows = [r for r in rows if r['kind'] == 'quiz' and r.get('quiz')]
        scores = [r['quiz']['latest_score'] for r in quiz_rows if r['quiz']['latest_score'] is not None]
        assignment_rows = [r for r in rows if r['kind'] == 'assignment']
        footer = {
            'activities': {'known': len(rows), 'recorded': sum(1 for r in rows if r['recorded'])},
            'quiz': {**col('quiz'), 'passed_latest': sum(1 for r in quiz_rows if r['quiz']['latest_status'] == 'passed'
                                                         and r['quiz']['latest_score'] is not None),
                     'mean_latest_score': _avg(scores), 'graded': len(scores),
                     'attempts': sum(len(r['quiz']['history']) for r in quiz_rows)},
            'video': col('video'), 'audio': col('audio'), 'pdf': col('pdf'), 'reading': col('reading'),
            'assignment': {**col('assignment'), 'submitted': sum(1 for r in assignment_rows if r.get('assignment')
                                                                 and r['assignment']['has_submitted'])},
            'recorded_seconds': 0,
        }
        sessions = []
        for session in self.sessions:
            a = self.attendance.get((learner['lms_id'], session['id']))
            status = a['attendance_status'] if a else None
            seconds = a['attended_seconds'] if a else None
            sessions.append({
                'number': session['number'], 'occurrence_key': session['id'], 'date': session['date'],
                'starts_at': session['starts_at'], 'ends_at': session['ends_at'], 'subject': ARM_TITLE,
                'learners_attended': self.present_by_session[session['id']],
                'status': 'attended' if status == 'present' else ('absent' if status == 'absent' else 'unavailable'),
                'presence_seconds': None, 'first_join': None, 'last_leave': None, 'joins': None,
                'reconciliation': None,
                'timing': {'available': seconds is not None, 'timezone': 'UTC',
                           'first_join': as_utc(a['first_join_at']) if a else None,
                           'last_leave': as_utc(a['last_leave_at']) if a else None,
                           'duration_seconds': seconds, 'visits': None},
            })
        attendance = {**self.enrollment_attendance(learner['lms_id']), 'sessions': sessions,
                      'timing_available': any(x['timing']['available'] for x in sessions),
                      'verified_attendance_seconds': self.verified_seconds(learner['lms_id'])}
        present_kinds = [k for k in KIND_LABELS if any(r['kind'] == k for r in rows)]
        return {
            'learner': {'learner_id': learner['learner_id'], 'name': learner['name']},
            'course': {'course_id': ARM_COURSE_ID, 'title': ARM_TITLE, 'progress_pct': PROGRESS_FALLBACK},
            'matrix_basis': 'activity',
            'matrix_note': 'Rows are the module\'s learning activities in the LMS curriculum, in week order.',
            'attendance': attendance, 'kinds': present_kinds, 'rows': rows, 'footer': footer,
            'definitions': definitions(),
        }

    def learner_detail(self, learner_id) -> dict:
        learner = self.by_learner_id[learner_id]
        payload = self.enrollment_payload(learner['lms_id'])
        return {'learner': {'learner_id': learner['learner_id'], 'name': learner['name'], 'email': learner['email']},
                'summary': rollup([payload]), 'courses': [payload], 'definitions': definitions()}

    # --- lectures (completed LMS lectures only) -------------------------------------------

    @staticmethod
    def lecture_id(occurrence_id: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f'lms-live-session-occurrence:{occurrence_id}'))

    def lectures(self, start=None, end=None) -> list[dict]:
        rows = []
        for s in self.sessions:
            if (start and (s['date'] is None or s['date'] < start)) or (end and (s['date'] is None or s['date'] > end)):
                continue
            records = [self.attendance.get((x['lms_id'], s['id'])) for x in self.learners]
            assets = self.artifacts[s['id']]
            recordings = sum(a['artifact_type'] == 'recording' for a in assets)
            transcripts = sum(a['artifact_type'] == 'transcript' for a in assets)
            rows.append({
                'id': self.lecture_id(s['id']), 'course_id': ARM_COURSE_ID, 'course': ARM_TITLE, 'date': s['date'],
                'starts_at': s['starts_at'], 'ends_at': s['ends_at'], 'source': 'LMS live session',
                'recording_session': False, 'learners': len(self.learners),
                'present': self.present_by_session[s['id']],
                'recorded': sum(1 for r in records if r and r['attendance_status'] in KNOWN_STATUSES),
                'recordings': recordings, 'playable_recordings': recordings,
                'transcripts': transcripts, 'readable_transcripts': transcripts,
                'verified_speakers': 0, 'speaking_covered': 0, 'updated_at': None,
            })
        return rows

    def lecture_detail(self, lecture_id: str) -> dict | None:
        session = next((s for s in self.sessions if self.lecture_id(s['id']) == str(lecture_id)), None)
        if session is None:
            return None
        summary = next(r for r in self.lectures() if r['id'] == str(lecture_id))
        participants = []
        for x in sorted(self.learners, key=lambda x: (x['name'].casefold(), x['learner_id'])):
            a = self.attendance.get((x['lms_id'], session['id']))
            if a is None or a['attendance_status'] not in KNOWN_STATUSES:
                continue  # only learners with a stored LMS attendance status for this lecture
            participants.append({
                'learner_id': x['learner_id'], 'name': x['name'], 'email': x['email'],
                'attendance': a['attendance_status'], 'first_join': as_utc(a['first_join_at']),
                'last_leave': as_utc(a['last_leave_at']), 'duration_seconds': a['attended_seconds'],
                'speaking_seconds': None, 'speaking_match': None, 'speaking_partial': False,
                'camera_on': None, 'microphone_on': None, 'needs_review': False,
            })
        assets = [{'id': resource_id(a['id']), 'kind': a['artifact_type'], 'available': True,
                   'created_at': as_utc(a['created_datetime']),
                   'url': f"/api/lectures/{lecture_id}/resources/{resource_id(a['id'])}"}
                  for a in self.artifacts[session['id']]]
        return {**summary, 'participants': participants, 'resources': assets, 'recording_folder_url': None,
                'timezone': 'Africa/Cairo'}

    def lecture_resource(self, lecture_id, asset_id):
        session = next((s for s in self.sessions if self.lecture_id(s['id']) == str(lecture_id)), None)
        if session is None:
            return None
        found = [row for row in self.artifacts[session['id']] if resource_id(row['id']) == asset_id]
        return found[0] if len(found) == 1 else None

    def standalone(self, learner_id) -> bool:
        """An ARM learner who is not a dashboard learner (served from the LMS alone)."""
        learner = self.by_learner_id.get(learner_id)
        return learner is not None and not learner['dashboard']


# ---------------------------------------------------------------------------------------
# "All modules" views: existing results stay as calculated; the LMS module is added beside them.
# ---------------------------------------------------------------------------------------

def _add_ratio(kpi: dict, value, denominator):
    merged = {**kpi, 'value': (kpi.get('value') or 0) + value, 'denominator': (kpi.get('denominator') or 0) + denominator}
    merged['pct'] = _pct(merged['value'], merged['denominator'])
    return merged


def merge_attendance(old: dict, new: dict) -> dict:
    out = dict(old)
    for key in ('attended', 'expected', 'possible_learner_sessions', 'missing_learner_sessions',
                'enrollments_without_cells', 'delivered_sessions', 'enrollments_covered', 'enrollments_measured',
                'enrollments_total', 'enrollments_without_teams_identity', 'learners_without_teams_identity'):
        out[key] = (old.get(key) or 0) + (new.get(key) or 0)
    out['available'] = bool(old.get('available') or new.get('available'))
    out['pct'] = _pct(out['attended'], out['expected'])
    out['coverage_pct'] = _pct(out['expected'], out['possible_learner_sessions'])
    seconds = [v for v in (old.get('live_seconds'), new.get('live_seconds')) if v is not None]
    out['live_seconds'] = sum(seconds) if seconds else None
    out['modules_without_data'] = sorted(set(old.get('modules_without_data') or []) | set(new.get('modules_without_data') or []))
    return out


def merge_summary(old: dict, arm: Arm, engagement, existing) -> dict:
    """`existing` = (learner IDs in the current selection, learner IDs active in existing modules)."""
    old_ids, old_active_ids = existing
    learners = arm.select(engagement, also_active=old_active_ids)
    if not learners:
        return old
    t = arm.totals(learners)
    k = {key: dict(value) if isinstance(value, dict) else value for key, value in old['kpis'].items()}
    all_ids = old_ids | t['learner_ids']
    active_ids = (old_active_ids & old_ids) | t['active_ids']
    k['unique_learners'] = {'value': len(all_ids)}
    k['modules'] = {'value': (old['kpis']['modules']['value'] or 0) + 1}
    k['enrollments'] = {'value': (old['kpis']['enrollments']['value'] or 0) + t['enrollments']}
    k['active_learners'] = {'value': len(active_ids), 'denominator': len(all_ids), 'pct': _pct(len(active_ids), len(all_ids))}
    k['quiz_completion'] = _add_ratio(k['quiz_completion'], t['quizzes_attempted'], t['quiz_den'])
    if t['scores']:
        old_n = k['mean_latest_score'].get('n') or 0
        old_sum = (k['mean_latest_score'].get('value') or 0) * old_n
        n = old_n + len(t['scores'])
        k['mean_latest_score'] = {**k['mean_latest_score'], 'value': round((old_sum + sum(t['scores'])) / n, 1), 'n': n}
    k['pass_rate'] = _add_ratio(k['pass_rate'], t['passed'], t['graded'])
    by_status = Counter(k['submissions'].get('by_status') or {})
    by_status.update(t['by_status'])
    k['submissions'] = {**k['submissions'], 'value': (k['submissions'].get('value') or 0) + t['submitted'],
                        'total_records': (k['submissions'].get('total_records') or 0) + t['submissions'],
                        'by_status': dict(by_status)}
    k['assignment_coverage'] = _add_ratio(k['assignment_coverage'], t['assignments_submitted'], t['assign_den'])
    k['materials_completed'] = _add_ratio(k['materials_completed'], t['lessons'], t['lesson_den'])
    k['activity_coverage'] = _add_ratio(k['activity_coverage'], t['with_activity'], t['enrollments'])
    k['attendance'] = merge_attendance(old['kpis']['attendance'], t['attendance'])
    return {**old, 'kpis': k}


def merge_charts(old: dict, arm: Arm, engagement, activity_type, existing) -> dict:
    _, old_active_ids = existing
    learners = arm.select(engagement, also_active=old_active_ids)
    if not learners:
        return old
    parts = arm.chart_parts(learners, activity_type)
    counts = {row['type']: row['count'] for row in old['activity_by_type']}
    for kind, _, count in parts['activity_by_type']:
        counts[kind] = counts.get(kind, 0) + count
    rows = {row['learner_id']: {**row, 'progress': dict(row['progress'])} for row in old['learner_module_matrix']['rows']}
    for row in parts['rows']:
        if row['learner_id'] in rows:
            rows[row['learner_id']]['progress'][ARM_COURSE_ID] = PROGRESS_FALLBACK
        else:
            rows[row['learner_id']] = {**row, 'module_metrics': {}}
    distribution = old['progress_distribution']
    return {
        **old,
        'module_progress': [*old['module_progress'], parts['module_progress']],
        'quiz_by_module': [*old['quiz_by_module'], parts['quiz_by_module']],
        'activity_completion': [*old['activity_completion'], parts['activity_completion']],
        'activity_by_type': [{**row, 'count': counts[row['type']]} for row in old['activity_by_type']],
        'progress_distribution': {**distribution, 'missing': distribution['missing'] + len(learners),
                                  'total': distribution['total'] + len(learners)},
        'learner_module_matrix': {
            'modules': [*old['learner_module_matrix']['modules'], {'course_id': ARM_COURSE_ID, 'course': ARM_TITLE}],
            'rows': sorted(rows.values(), key=lambda r: r['name'].lower()),
        },
    }


def _merge_directory_row(row: dict, arm: Arm, lms_id) -> dict:
    p = arm.enrollment_payload(lms_id)
    out = dict(row)
    out['enrollments'] = sorted([*row['enrollments'], {'course_id': ARM_COURSE_ID, 'course': ARM_TITLE,
                                                       'progress_pct': PROGRESS_FALLBACK}],
                                key=lambda e: course_order_key(e['course_id'], e['course']))
    out['modules'] = row['modules'] + 1
    if row['mean_progress'] is None:  # no real progress in other modules: ARM's fallback is the only value
        out['mean_progress'] = PROGRESS_FALLBACK
    for key in ('lessons_completed', 'lessons_known', 'quizzes_attempted', 'quizzes_known', 'quiz_attempts',
                'quizzes_passed_latest', 'assignments_submitted', 'assignments_known'):
        out[key] = row[key] + p[key]
    if p['quizzes_graded']:
        graded = row['quizzes_graded'] + p['quizzes_graded']
        out['mean_latest_score'] = round(((row['mean_latest_score'] or 0) * row['quizzes_graded'] + p['latest_score_sum']) / graded, 1)
    out['quizzes_graded'] = row['quizzes_graded'] + p['quizzes_graded']
    out['lessons_completion_pct'] = _pct(out['lessons_completed'], out['lessons_known'])
    out['quiz_completion_pct'] = _pct(out['quizzes_attempted'], out['quizzes_known'])
    out['has_activity'] = row['has_activity'] or p['has_activity']
    out['last_activity'] = _latest(row['last_activity'], p['last_activity'])
    durations = [v for v in (row['verified_attendance_seconds'], arm.verified_seconds(lms_id)) if v is not None]
    out['verified_attendance_seconds'] = sum(durations) if durations else None
    old_att, new_att = row['attendance'], arm.directory_attendance(lms_id, {})
    att = {**old_att, 'modules_total': (old_att.get('modules_total') or 0) + 1}
    if old_att.get('available') and new_att['available']:
        for key in ('attended', 'expected', 'known', 'absent', 'unknown'):
            att[key] = (old_att.get(key) or 0) + (new_att.get(key) or 0)
        att['pct'] = _pct(att['attended'], att['expected'])
        att['modules_covered'] = (old_att.get('modules_covered') or 0) + new_att['modules_covered']
    out['attendance'] = att
    return out


def _matches(learner: dict, search: str | None) -> bool:
    if not search:
        return True
    return search.casefold() in learner['name'].casefold() or (search.isdigit() and int(search) == learner['learner_id'])


def directory(arm: Arm, search=None, engagement=None, ordering='name') -> list[dict]:
    from .metrics import sort_directory_rows
    rows = [arm.directory_row(x['lms_id']) for x in arm.select(engagement) if _matches(x, search)]
    return sort_directory_rows(rows, ordering)


def merge_directory(old_rows: list[dict], arm: Arm, search=None, engagement=None, ordering='name') -> list[dict]:
    """`old_rows` are the unfiltered-by-status existing rows; the learner-status filter applies after merging."""
    from .metrics import sort_directory_rows
    rows = {row['learner_id']: row for row in old_rows}
    for x in arm.learners:
        if x['learner_id'] in rows:
            rows[x['learner_id']] = _merge_directory_row(rows[x['learner_id']], arm, x['lms_id'])
        elif _matches(x, search):
            rows[x['learner_id']] = arm.directory_row(x['lms_id'])
    merged = list(rows.values())
    if engagement == 'active':
        merged = [r for r in merged if r['has_activity']]
    elif engagement == 'inactive':
        merged = [r for r in merged if not r['has_activity']]
    return sort_directory_rows(merged, ordering)


def merge_learner_detail(old: dict, arm: Arm, learner_id) -> dict:
    learner = arm.by_learner_id.get(learner_id)
    if learner is None:
        return old
    courses = sorted([*old['courses'], arm.enrollment_payload(learner['lms_id'])],
                     key=lambda p: course_order_key(p['course_id'], p['course']))
    return {**old, 'summary': rollup(courses), 'courses': courses}


def rollup(payloads: list[dict]) -> dict:
    """learner_rollup, with ARM's display fallback excluded from cross-module progress averages."""
    real = [{**p, 'progress_pct': None} if p['course_id'] == ARM_COURSE_ID else p for p in payloads]
    out = learner_rollup(real)
    if out['mean_progress'] is None and any(p['course_id'] == ARM_COURSE_ID for p in payloads):
        out['mean_progress'] = PROGRESS_FALLBACK
    return out

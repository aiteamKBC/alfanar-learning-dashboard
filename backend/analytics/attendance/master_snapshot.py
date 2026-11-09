"""Private, deterministic attendance exports. Reads evidence; never imports or changes it."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction

from ..course_labels import COURSE_DISPLAY_NAMES, COURSE_DISPLAY_ORDER
from ..models import (Enrollment, Learner, LearnerIdentity, LectureSession, LectureTiming,
                      SessionAttendance, TeamsOccurrence, TeamsOccurrenceAttendance,
                      TeamsDiscoveryState, TeamsReportEvidence, TimingImport)
from .confirmed import CONFIRMED_DELIVERED_LECTURES
from .importer import load_aliases, registry_lookup
from .matrices import SOURCES, header_date
from .metrics import AttendanceIndex
from .reconciliation import RULE_VERSION, minimum_minutes

SCHEMA_VERSION = 1
HEADERS = {
    'AT_Modules': 'CourseID DisplayName DisplayOrder OfficialHistoricalLectures BaselineDatedLectures PolicyVersion'.split(),
    'AT_Learners': 'LearnerID LearnerName RegisteredEmail IdentityRegistryVersion Active'.split(),
    'AT_Enrollments': 'EnrollmentKey LearnerID CourseID Eligible RegistryVersion'.split(),
    'AT_Lectures': 'LectureID CourseID LectureDate ScheduledStartUTC ScheduledEndUTC DisplayTimezone Status CalendarVerified IncludeInAttendance OrganizerRef MeetingRef OccurrenceRef EvidenceRef SourceHash GenerationID'.split(),
    'AT_Attendance': 'AttendanceKey LearnerID LectureID CourseID LectureDate Eligible MatrixOriginalStatus MatrixEvidenceRef TimingEvidenceStatus EffectiveStatus FirstJoinUTC LastLeaveUTC FirstJoinLocal LastLeaveLocal DisplayTimezone DurationSeconds DurationDisplay VisitCount PreferredTimingSource IdentityMatchStatus NeedsReview ReasonCode EvidenceRef PolicyVersion UpdatedAtUTC RowHash GenerationID'.split(),
    'AT_Summary': 'SummaryKey LearnerID CourseID Enrolled OfficialLectures DatedLectures TimedLectures PossibleObservations KnownObservations Present Absent Unknown AttendanceRate EvidenceCoverage TimingPairs ReviewCount AsOfUTC'.split(),
    'AT_Review': 'ReviewID Kind LearnerID CandidateLearnerID CourseID LectureID ObservedDate OriginalEvidenceRef ReasonCode State OwnerDecision ApprovedBy ApprovedAtUTC DecisionEvidenceRef'.split(),
    'AT_SyncLog': 'RunID SourceAsOfUTC GenerationID SnapshotHash PolicyVersion Status ModulesChecked Lectures Rows ReviewOpen'.split(),
}
REVIEW_MACHINE_COLUMNS = HEADERS['AT_Review'].index('OwnerDecision')
BASELINE = dict(learners=27, enrollments=103, official_lectures=58, dated_lectures=53,
                historical_decisions=1372, historical_timing=1048, historical_corrections=160,
                historical_reviews=16, historical_timed_dates=51, pending_historical_pairs=347)


class ExportError(Exception):
    """Messages must be sanitized; no source cells, credentials or remote error text."""


def require(condition, message):
    if not condition:
        raise ExportError(message)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def private_json(path, value):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'wb') as stream:
        stream.write(encoded(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def iso(value):
    return value.astimezone(dt.timezone.utc).isoformat().replace('+00:00', 'Z') if value else ''


def status(value):
    return 'Not Available' if value is None else ('Present' if value else 'Absent')


def matrix_evidence(directory):
    """Capture exact original cells and immutable workbook hashes using approved date rules."""
    import openpyxl
    learners, by_email, _ = registry_lookup()
    aliases, errors = load_aliases(settings.TEAMS_ALIASES_PATH, {r[0] for r in learners})
    require(not errors, 'Existing matrix identity aliases are invalid')
    cells, sources = defaultdict(list), {}
    for cid, (filename, main) in SOURCES.items():
        path = Path(directory) / filename
        require(path.is_file(), 'An authoritative matrix workbook is missing')
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        sources[str(cid)] = {'filename': filename, 'sha256': sha}
        with path.open('rb') as stream:
            wb = openpyxl.load_workbook(stream, read_only=True, data_only=True)
            try:
                sheets = {s.title: list(s.values) for s in wb}
            finally:
                wb.close()

        def observe(email, day, flag, sheet, row, col):
            if flag in (None, ''):
                return
            require(flag in (0, 1), 'Nonbinary matrix evidence cannot be exported')
            email = str(email or '').strip().lower()
            uid = by_email.get(email, aliases.get(email))
            if uid is not None:
                cells[(uid, cid, str(day))].append({'file_sha256': sha, 'sheet': sheet,
                    'row': row, 'column': col, 'value': int(flag), 'email': email})

        name = next((n for n in sheets if n.strip() == main), None)
        require(name is not None, 'Authoritative matrix worksheet missing')
        rows = sheets[name]
        for col, value in enumerate(rows[0]):
            if str(rows[1][col] or '').strip().lower() != 'attendance':
                continue
            day = header_date(value, rows[0][col + 1] if col + 1 < len(rows[0]) else None)
            if day:
                for number, row in enumerate(rows[2:], 3):
                    if row[1]: observe(row[1], day, row[col], name, number, col + 1)
        for name, rows in sheets.items():
            if 'unregistered' not in name.lower() or not rows:
                continue
            columns = [str(v or '').strip() for v in rows[0]]
            for number, row in enumerate(rows[1:], 2):
                if not any(v is not None for v in row): continue
                day = row[columns.index('Date')]
                if isinstance(day, dt.datetime): day = day.date()
                elif isinstance(day, str): day = dt.datetime.strptime(day, '%d/%m/%Y').date()
                observe(row[columns.index('AttendanceEmail')], day, row[columns.index('present')],
                        name, number, columns.index('present') + 1)
        require(hashlib.sha256(path.read_bytes()).hexdigest() == sha, 'Matrix changed during export')
    return cells, sources


@transaction.atomic
def build_snapshot(*, strict=True, matrix_directory=None):
    """One consistent DB read transaction. No HTTP and no model writes."""
    require(minimum_minutes() == 30, 'Export requires the approved 30-minute policy')
    from ..identity import state as identity_state, policy as identity_policy, projection
    from ..identity_evidence import projected_timing, projected_matrix
    from ..models import IdentityOperation, IdentityEvidenceArchive, OriginalLearner
    identity = identity_state()
    restored = bool(identity and identity.restored)
    index = AttendanceIndex()
    learners = list(Learner.objects.order_by('source_id').values('source_id', 'name', 'email'))
    identities = list(LearnerIdentity.objects.order_by('source_id').values('source_id', 'canonical_source_id', 'email', 'name'))
    pairs = sorted(Enrollment.objects.values_list('learner__source_id', 'course__source_id'))
    from ..importer.exclusions import NON_LEARNER_SOURCE_IDS
    from ..importer.identities import MERGED_LEARNER_IDS
    require(not ({r['source_id'] for r in learners} & (set(NON_LEARNER_SOURCE_IDS) | set(MERGED_LEARNER_IDS))), 'Registry contains excluded or merged source accounts')
    require(all(c in COURSE_DISPLAY_NAMES for u, c in pairs), 'Unapproved module in export registry')
    registry_hash = digest([learners, identities, pairs])
    runs = list(TimingImport.objects.filter(active=True))
    require(len(runs) == 1, 'Exactly one active verified timing import is required')
    run = runs[0]
    historical = list(LectureSession.objects.filter(delivered=True).select_related('meeting').order_by('meeting__course_source_id', 'session_date'))
    require(all(s.meeting.evidence_source == 'attendance_matrix' for s in historical), 'Export requires verified matrix history')
    require(len({(s.meeting.course_source_id, s.session_date) for s in historical}) == len(historical), 'Duplicate historical lecture date')
    observed = {(r.learner_source_id, r.session_id): r for r in SessionAttendance.objects.filter(session__in=historical)}
    raw_timings = list(LectureTiming.objects.filter(import_run=run).order_by('course_source_id', 'session_date', 'learner_source_id'))
    timings = {(key[0], key[1], str(key[2])): r for key, r in projected_timing([t for t in raw_timings if t.status == 'verified']).items()}
    cells, matrices = matrix_evidence(matrix_directory or settings.ATTENDANCE_DIRECTORY)
    for (uid, sid), row in observed.items():
        s = next(s for s in historical if s.pk == sid)
        raw = cells.get((uid, s.meeting.course_source_id, str(s.session_date)), [])
        require(raw and any(c['value'] for c in raw) == row.attended, 'Matrix file and stored observation disagree')
    database_keys = {(u, s.meeting.course_source_id, str(s.session_date)) for (u, sid) in observed for s in historical if s.pk == sid}
    require(set(cells) == database_keys, 'Matrix source cells differ from imported evidence')
    from types import SimpleNamespace
    observed = {key: SimpleNamespace(**{**r, 'pk': r['id']}) for key, r in projected_matrix([
        {'id': r.pk, 'session_id': r.session_id, 'learner_source_id': r.learner_source_id,
         'attended': r.attended, 'presence_seconds': r.presence_seconds, 'first_join': r.first_join,
         'last_leave': r.last_leave, 'interval_count': r.interval_count} for r in observed.values()]).items()}
    mapping, archived = identity_policy()
    projected_cells = defaultdict(list)
    for (uid, cid, day), values in cells.items():
        if uid not in archived: projected_cells[(mapping.get(uid, uid), cid, day)].extend(values)
    cells = projected_cells
    occurrences = list(TeamsOccurrence.objects.select_related('series').order_by('key'))
    future_timing = projected_timing(TeamsOccurrenceAttendance.objects.order_by('occurrence_id', 'learner_source_id'), occurrence=True)
    report_evidence = list(TeamsReportEvidence.objects.order_by('series_id', 'report_id', 'sha256').values())
    source_times = [run.created_at] + [r['retrieved_at'] for r in report_evidence]
    asof = iso(max(source_times))
    evidence = {'registry': {'learners': learners, 'identities': identities, 'enrollments': pairs},
                'matrices': matrices, 'timing_import': {'fingerprint': run.fingerprint, 'source_name': run.source_name, 'source_sha256': run.source_sha256,
                'policy_sha256': run.policy_sha256, 'registry_sha256': run.registry_sha256},
                'records': {}, 'occurrences': {}, 'report_revisions': [],
                'pending_discovery': list(TeamsDiscoveryState.objects.values('key', 'pending_events'))}
    identity_version = None
    if restored:
        expected = projection(identity.snapshot)
        require(set(expected.learners) == {r['source_id'] for r in learners} and set(expected.enrollments) == set(pairs),
                'Active identity projection disagrees with approved source policy')
        identity_version = {'revision': identity.revision, 'source_sha256': identity.snapshot.sha256,
            'audit_sha256': digest(list(IdentityOperation.objects.filter(applied_at__isnull=False).order_by('created_at')
                .values('kind', 'revision', 'snapshot_sha256', 'request', 'result')))}
        evidence['identity_version'] = identity_version
        evidence['original_registry'] = list(OriginalLearner.objects.order_by('source_id').values())
        for original in evidence['original_registry']:
            if original['group_id']: original['group_id'] = str(original['group_id'])
        evidence['original_timing_records'] = [{'id': t.pk, 'learner_source_id': t.learner_source_id,
            'course_source_id': t.course_source_id, 'date': str(t.session_date), 'status': t.status,
            'provenance': t.provenance, 'duration_microseconds': t.duration_microseconds} for t in raw_timings]
        evidence['identity_archives'] = list(IdentityEvidenceArchive.objects.order_by('key').values('key', 'reason', 'payload', 'needs_review'))
    # Datetimes in the private raw report archive are serialized explicitly.
    for r in report_evidence:
        r['retrieved_at'] = iso(r['retrieved_at']); evidence['report_revisions'].append(r)
    tables = {n: [] for n in HEADERS}

    def add(tab, **row):
        tables[tab].append({h: row.get(h, '') for h in HEADERS[tab]})

    def review(key, kind, uid='', cid='', lecture='', day='', reason=''):
        add('AT_Review', ReviewID=key, Kind=kind, LearnerID=str(uid), CourseID=str(cid),
            LectureID=lecture, ObservedDate=str(day), OriginalEvidenceRef=key,
            ReasonCode=reason, State='Open')

    if restored:
        for hold in evidence['identity_archives']:
            if hold['needs_review']:
                review('identity-hold:'+hold['key'], 'IdentitySeparationReview', reason=hold['reason'])
                evidence['records']['identity-hold:'+hold['key']] = hold['payload']
    for r in learners:
        add('AT_Learners', LearnerID=str(r['source_id']), LearnerName=r['name'], RegisteredEmail=r['email'], IdentityRegistryVersion=registry_hash, Active=True)
    for uid, cid in pairs:
        add('AT_Enrollments', EnrollmentKey=f'{uid}:{cid}', LearnerID=str(uid), CourseID=str(cid), Eligible=True, RegistryVersion=registry_hash)
    history_keys, lecture_to_session = {}, {}
    for order, cid in enumerate(COURSE_DISPLAY_ORDER, 1):
        group = [s for s in historical if s.meeting.course_source_id == cid]
        official = CONFIRMED_DELIVERED_LECTURES[cid]['count']
        require(len(group) <= official, 'Historical calendar exceeds approved lecture count')
        add('AT_Modules', CourseID=str(cid), DisplayName=COURSE_DISPLAY_NAMES[cid], DisplayOrder=order,
            OfficialHistoricalLectures=official, BaselineDatedLectures=len(group), PolicyVersion=RULE_VERSION)
        for s in group:
            key = f'hist:{cid}:{s.session_date}'
            history_keys[s.pk] = key; lecture_to_session[key] = s.pk
            add('AT_Lectures', LectureID=key, CourseID=str(cid), LectureDate=str(s.session_date),
                DisplayTimezone='Africa/Cairo', Status='HistoricalConfirmed', CalendarVerified=True,
                IncludeInAttendance=True, MeetingRef=f'matrix:{cid}', EvidenceRef=f'matrix:{cid}', SourceHash=matrices[str(cid)]['sha256'])
        for number in range(official - len(group)):
            key = f'undated:{cid}:{number+1}'
            add('AT_Lectures', LectureID=key, CourseID=str(cid), DisplayTimezone='Africa/Cairo',
                Status='UndatedOfficial', CalendarVerified=False, IncludeInAttendance=False, EvidenceRef='owner_confirmed_counts')
            review(key, 'UndatedOfficial', cid=cid, lecture=key, reason='official_slot_without_verified_date')
    for o in occurrences:
        key = f'auto:{o.key}'
        evidence['occurrences'][key] = {'meeting_id': o.series.meeting_id, 'organizer_id': o.series.organizer_id,
            'calendar_uid': o.calendar_uid, 'provenance': o.provenance, 'reason': o.reason, 'needs_review': o.needs_review}
        add('AT_Lectures', LectureID=key, CourseID=str(o.course_source_id), LectureDate=str((o.starts_at or o.scheduled_start).date()),
            ScheduledStartUTC=iso(o.scheduled_start), ScheduledEndUTC=iso(o.scheduled_end), DisplayTimezone='Africa/Cairo',
            Status={'verified':'VerifiedDelivered', 'cancelled':'Cancelled'}.get(o.status, 'PendingReview'),
            CalendarVerified=True, IncludeInAttendance=o.status == 'verified', OrganizerRef=digest(o.series.organizer_id),
            MeetingRef=digest(o.series.meeting_id), OccurrenceRef=o.key, EvidenceRef=key, SourceHash=digest(evidence['occurrences'][key]))
        if o.status == 'verified': lecture_to_session[key] = f'auto:{o.pk}'
        if o.status != 'verified' or o.needs_review:
            review(key, 'OccurrenceReview', cid=o.course_source_id, lecture=key, day=str(o.scheduled_start.date()), reason=o.reason)
    rows_by_course = defaultdict(list)
    for uid, cid in pairs:
        for s in index.sessions_by_course[cid]:
            sid = s['id']; historical_row = isinstance(sid, int)
            if historical_row:
                lecture = history_keys[sid]
                decision = index.decisions[(uid, sid)]
                t = timings.get((uid, cid, str(s['session_date']))) if index.decision_evidence[(uid, sid)]['timing_record_id'] else None
                raw_cells = cells.get((uid, cid, str(s['session_date'])), [])
                raw = observed.get((uid, sid))
                effective = decision['effective_present']; flagged = decision['needs_review']; reason = decision['reason_code']
                source = list(t.provenance) if t else []
            else:
                o = next(o for o in occurrences if f'auto:{o.pk}' == sid)
                lecture = f'auto:{o.key}'; t = future_timing.get((uid, o.pk)); raw_cells = []; raw = None
                effective = True if (uid, sid) in index.records else None
                flagged = bool(t and (t.needs_review or effective is None))
                reason = 'graph_evidence_reduction' if t and t.needs_review else ('verified_timing_present' if effective else ('short_timing_unknown' if t else 'no_evidence'))
                source = t.intervals if t else []
            key = f'{uid}|{lecture}'
            evidence['records'][key] = {'matrix_cells': raw_cells, 'matrix_record_id': raw.pk if raw else None,
                'matrix_effective_original': raw.attended if raw else None, 'timing_record_id': t.pk if t else None,
                'timing': source, 'duration_microseconds': t.duration_microseconds if t else None}
            kinds = {p.get('source_kind') for p in source if isinstance(p, dict)}
            match = {p.get('match_method') or '' for p in source if isinstance(p, dict)}
            seconds = t.duration_microseconds // 1_000_000 if t else None
            add('AT_Attendance', AttendanceKey=key, LearnerID=str(uid), LectureID=lecture, CourseID=str(cid), LectureDate=str(s['session_date']), Eligible=True,
                MatrixOriginalStatus='Conflicting' if len({c['value'] for c in raw_cells}) > 1 else status(raw.attended if raw else None),
                MatrixEvidenceRef=key if raw_cells else '', TimingEvidenceStatus='Verified' if t else 'Not Available', EffectiveStatus=status(effective),
                FirstJoinUTC=iso(t.first_join) if t else '', LastLeaveUTC=iso(t.last_leave) if t else '',
                FirstJoinLocal=t.first_join.astimezone(ZoneInfo('Africa/Cairo')).isoformat() if t else '',
                LastLeaveLocal=t.last_leave.astimezone(ZoneInfo('Africa/Cairo')).isoformat() if t else '', DisplayTimezone='Africa/Cairo',
                DurationSeconds=seconds if t else '', DurationDisplay=f'{seconds//3600}:{seconds//60%60:02}:{seconds%60:02}' if t else 'Not Available',
                VisitCount=t.visit_count if t else '', PreferredTimingSource=('Microsoft Graph' if not historical_row or 'microsoft_graph' in kinds else 'Teams Excel') if t else '',
                IdentityMatchStatus=('VERIFIED_ALIAS' if any('alias' in m for m in match) else 'CONFIRMED_MATCH') if t or raw else 'Not Available',
                NeedsReview=flagged, ReasonCode=reason, EvidenceRef=key, PolicyVersion=RULE_VERSION, UpdatedAtUTC=asof)
            rows_by_course[cid].append(tables['AT_Attendance'][-1])
            if flagged: review(key, 'AttendanceReview', uid, cid, lecture, s['session_date'], reason)
            elif seconds is not None and seconds < 1800: review(key, 'ShortVisit', uid, cid, lecture, s['session_date'], reason)
    for t in raw_timings:
        uid, cid, day = t.learner_source_id, t.course_source_id, str(t.session_date)
        if t.status != 'verified':
            key = f'pending:{uid}:{cid}:{day}'
            review(key, 'HistoricalTimingPending', uid, cid, day=day, reason=t.status)
            evidence['records'][key] = {'status': t.status, 'timing': t.provenance, 'duration_microseconds': t.duration_microseconds,
                'first_join': iso(t.first_join), 'last_leave': iso(t.last_leave), 'visit_count': t.visit_count}
    for state in evidence['pending_discovery']:
        for key, pending in state['pending_events'].items():
            review('discovery:'+key, 'DiscoveryPending', cid=pending.get('course_source_id', ''), reason='unresolved_calendar_series')
    review_file = getattr(settings, 'MASTER_ATTENDANCE_IDENTITY_REVIEW_FILE', '')
    if review_file:
        candidate_rows = json.loads(Path(review_file).read_text(encoding='utf-8'))
        require(isinstance(candidate_rows, list), 'Invalid private identity review archive')
        evidence['identity_review'] = candidate_rows
        for candidate in candidate_rows:
            if candidate.get('classification') not in ('AMBIGUOUS_REVIEW', 'HIGH_CONFIDENCE_CANDIDATE'): continue
            key = 'identity:'+digest([candidate.get('identity'), candidate.get('observed_email'), candidate.get('display_name')])
            evidence['records'][key] = candidate
            review(key, 'IdentityCandidate', reason=candidate['classification'])
            tables['AT_Review'][-1]['CandidateLearnerID'] = ','.join(str(c['canonical_id']) for c in candidate.get('candidates', []))
    timed_lectures = {r['LectureID'] for r in tables['AT_Attendance'] if r['TimingEvidenceStatus'] == 'Verified'}
    for row in tables['AT_Lectures']:
        if row['IncludeInAttendance'] and row['LectureID'] not in timed_lectures:
            review('missing:'+row['LectureID'], 'MissingLearnerTiming', cid=row['CourseID'],
                   lecture=row['LectureID'], day=row['LectureDate'], reason='no_verified_learner_timing')
    for cid, rows in rows_by_course.items():
        for uid in ['', *[str(u) for u, c in pairs if c == cid]]:
            selected = [r for r in rows if not uid or r['LearnerID'] == uid]
            present = sum(r['EffectiveStatus'] == 'Present' for r in selected)
            absent = sum(r['EffectiveStatus'] == 'Absent' for r in selected); known = present + absent
            timed = [r for r in selected if r['TimingEvidenceStatus'] == 'Verified']
            add('AT_Summary', SummaryKey=f'{uid or "module"}:{cid}', LearnerID=uid, CourseID=str(cid),
                Enrolled=1 if uid else sum(c == cid for u, c in pairs), OfficialLectures=CONFIRMED_DELIVERED_LECTURES[cid]['count'] + index.new_occurrences[cid],
                DatedLectures=len(index.sessions_by_course[cid]), TimedLectures=len({r['LectureID'] for r in timed}),
                PossibleObservations=len(selected), KnownObservations=known, Present=present, Absent=absent, Unknown=len(selected)-known,
                AttendanceRate=round(100*present/known, 1) if known else '', EvidenceCoverage=round(100*known/len(selected), 1) if selected else '',
                TimingPairs=len(timed), ReviewCount=sum(r['NeedsReview'] for r in selected), AsOfUTC=asof)
    historical_rows = [r for r in tables['AT_Attendance'] if r['LectureID'].startswith('hist:')]
    totals = {'learners': len(learners), 'enrollments': len(pairs), 'official_lectures': sum(v['count'] for v in CONFIRMED_DELIVERED_LECTURES.values()),
        'dated_lectures': len(historical), 'historical_decisions': len(historical_rows), 'historical_timing': sum(r['TimingEvidenceStatus']=='Verified' for r in historical_rows),
        'historical_corrections': sum(r['corrected'] for r in index.decisions.values()), 'historical_reviews': sum(r['needs_review'] for r in index.decisions.values()),
        'historical_timed_dates': len({r['LectureID'] for r in historical_rows if r['TimingEvidenceStatus']=='Verified'}),
        'pending_historical_pairs': sum(t.status!='verified' for t in raw_timings), 'new_verified_lectures': sum(index.new_occurrences.values()),
        'attendance_rows': len(tables['AT_Attendance']), 'review_rows': len(tables['AT_Review'])}
    if strict and not restored:
        require(all(totals[k] == v for k, v in BASELINE.items()), 'Approved historical export baseline changed; review before publishing')
    for tab, rows in tables.items():
        key = HEADERS[tab][0]
        require(len({r[key] for r in rows}) == len(rows), 'Duplicate export key')
    for row in tables['AT_Attendance']: row['RowHash'] = digest(row)
    payload = {'schema_version': SCHEMA_VERSION, 'policy_version': RULE_VERSION, 'source_as_of': asof,
               'totals': totals, 'tables': tables, 'evidence': evidence, 'identity_version': identity_version}
    generation = digest(payload)
    for rows in tables.values():
        for row in rows:
            if 'GenerationID' in row: row['GenerationID'] = generation
    return json.loads(encoded({**payload, 'generation': generation}))


def save_snapshot(snapshot, directory, matrix_directory=None):
    """Content-addressed private snapshot plus originals; safe to retry after interruption."""
    target = Path(directory) / 'snapshots' / snapshot['generation']
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    for source in snapshot['evidence']['matrices'].values():
        path = Path(matrix_directory or settings.ATTENDANCE_DIRECTORY) / source['filename']
        blob = path.read_bytes()
        require(hashlib.sha256(blob).hexdigest() == source['sha256'], 'Matrix changed before archival')
        dest = target / source['filename']
        if dest.exists(): require(dest.read_bytes() == blob, 'Private archived source changed')
        else:
            with os.fdopen(os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream: stream.write(blob)
    path = target / 'snapshot.json'
    if path.exists(): require(path.read_bytes() == encoded(snapshot), 'Immutable export snapshot changed')
    else: private_json(path, snapshot)
    manifest = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in target.iterdir() if p.is_file() and p.name != 'SHA256SUMS'}
    (target / 'SHA256SUMS').write_text(''.join(f'{sha}  {name}\n' for name, sha in sorted(manifest.items())), encoding='utf-8')
    return path

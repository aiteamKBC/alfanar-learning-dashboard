"""Calendar-led Teams delivery discovery. No Excel dependency and no historical writes.

All identifiers and report revisions stay in private database tables. Only aggregate
diagnostics leave this module. Invitations alone never establish lecture delivery.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import urllib.parse
import uuid
from collections import Counter, defaultdict
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ..course_labels import COURSE_DISPLAY_NAMES
from ..importer.exclusions import NON_LEARNER_SOURCE_IDS
from ..importer.identities import MERGED_LEARNER_IDS
from ..models import (Course, Enrollment, LectureSession, TeamsDiscoveredSeries, TeamsDiscoveryRun,
                      TeamsDiscoveryState, TeamsOccurrence, TeamsOccurrenceAttendance, TeamsReportEvidence)
from ..management.commands.run_sync_scheduler import ProcessLock
from .graph_client import GraphClient, GraphError, meeting_path
from .graph_timing import covers, validate_report
from .importer import merge_intervals, norm_email, registry_lookup
from .mapping import map_subject, normalize_title
from .reconciliation import minimum_minutes
from .timing import APPROVED_TIMING_SUBJECTS, TimingValidationError, digest, microseconds, require, timestamp

COURSES = (83334, 121642, 135945, 154268)
CALENDAR_FIELDS = 'id,iCalUId,subject,isCancelled,isDraft,isOnlineMeeting,onlineMeeting,start,end,organizer'


class DiscoveryInProgress(Exception):
    pass


def load_config(path):
    try:
        config = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        automation = config['automation']
        cutoff = timestamp(automation['cutover_at'])
        readers = automation['calendar_readers']
        require(config['version'] == 1 and isinstance(readers, list) and readers, 'Invalid discovery configuration')
        for oid in readers:
            uuid.UUID(oid)
        require(len(readers) == len(set(readers)), 'Duplicate calendar readers')
        bindings = config['meetings']
        require(isinstance(bindings, list) and bindings, 'Approved series are required')
        seen = set()
        courses = {**dict(Course.objects.values_list('source_id', 'title')), **COURSE_DISPLAY_NAMES}
        for m in bindings:
            uuid.UUID(m['organizer_id'])
            key = (m['organizer_id'], m['meeting_id'])
            require(key not in seen and isinstance(m['meeting_id'], str) and 0 < len(m['meeting_id']) <= 1024, 'Invalid series identifier')
            seen.add(key)
            mapped = map_subject(m['approved_subject'], courses)[0] or APPROVED_TIMING_SUBJECTS.get(normalize_title(m['approved_subject']))
            require(m['course_source_id'] in COURSES and mapped == m['course_source_id'], 'Unapproved course/subject mapping')
        require({m['course_source_id'] for m in bindings} == set(COURSES), 'All four courses must be configured')
        require(type(automation.get('lookback_days', 30)) is int and 7 <= automation.get('lookback_days', 30) <= 180, 'Invalid lookback')
        require(type(automation.get('grace_minutes', 120)) is int and 0 <= automation.get('grace_minutes', 120) <= 1440, 'Invalid report grace')
        require(isinstance(config.get('aliases', []), list), 'Invalid aliases')
        return config, cutoff
    except (KeyError, ValueError, TypeError, OSError) as exc:
        if isinstance(exc, TimingValidationError):
            raise
        raise TimingValidationError('Private discovery configuration is missing or invalid') from None


def registry(config):
    learners, emails, ambiguous = registry_lookup()
    included = {r[0] for r in learners} - set(NON_LEARNER_SOURCE_IDS) - set(MERGED_LEARNER_IDS)
    emails = {email: uid for email, uid in emails.items() if uid in included}
    from ..identity import source_enrollments
    pairs = source_enrollments()
    aliases = {}
    for a in config.get('aliases', []):
        email, uid = norm_email(a.get('email')), a.get('learner_source_id')
        require(email and email not in ambiguous and email not in aliases and uid in included, 'Invalid canonical alias')
        require(email not in emails or emails[email] == uid, 'Alias conflicts with registry')
        require(a.get('identity_id') and a.get('tenant_id'), 'Verified alias identity is required')
        aliases[email] = (uid, a['identity_id'], a['tenant_id'])
    return emails, aliases, pairs


def calendar_time(value):
    # calendarView defaults to UTC; reject unexpected naive local-zone responses.
    text = value.get('dateTime')
    parsed = dt.datetime.fromisoformat(str(text).replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        require(value.get('timeZone') in ('UTC', 'Etc/UTC'), 'Unexpected calendar timezone')
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return timestamp(parsed)


def subject_course(subject, config):
    key = normalize_title(subject)
    values = {m['course_source_id'] for m in config['meetings'] if normalize_title(m['approved_subject']) == key}
    return next(iter(values)) if len(values) == 1 else None


def identity(record, emails, aliases):
    email = norm_email(record.get('emailAddress'))
    alias = aliases.get(email)
    if alias:
        actual = record.get('identity') or {}
        return alias[0] if (actual.get('id'), actual.get('tenantId')) == alias[1:] else None
    return emails.get(email)


def run_discovery(config_path, *, dry_run=False, client=None, now=None, lock_path=None):
    lock = ProcessLock(Path(lock_path or os.environ.get('TEAMS_SYNC_LOCK_FILE') or settings.BASE_DIR / '.teams-discovery.lock'))
    if not lock.acquire():
        raise DiscoveryInProgress('Another Teams discovery run is active')
    try:
        return _run(config_path, dry_run=dry_run, client=client or GraphClient.from_environment(), now=now or timezone.now())
    finally:
        lock.release()


def _run(config_path, *, dry_run, client, now):
    config, cutoff = load_config(config_path)
    require(cutoff <= now, 'Discovery cutover cannot be in the future')
    require(minimum_minutes() == 30, 'Approved discovery policy requires 30 minutes')
    require(type(settings.TEAMS_MIN_LEARNERS_FOR_DELIVERED) is int and settings.TEAMS_MIN_LEARNERS_FOR_DELIVERED >= 1, 'Invalid delivery quorum')
    emails, aliases, enrolled = registry(config)
    registry_hash = digest([sorted(emails.items()), sorted(aliases.items()), sorted(enrolled)])
    history = list(LectureSession.objects.filter(delivered=True).values_list('meeting__course_source_id', 'session_date'))
    require(not history or max(day for _, day in history) <= cutoff.date(), 'Cutover predates the verified historical calendar')
    baseline_hash = digest(sorted(history))
    previous = TeamsDiscoveryState.objects.filter(pk='daily').first()
    if previous:
        require(previous.cutover_at == cutoff, 'Historical cutover is immutable; do not silently rebase discovery')
        require(previous.baseline_sha256 == baseline_hash, 'Historical calendar changed; review before discovery continues')
    checkpoints = dict(previous.checkpoints) if previous else {}
    pending = dict(previous.pending_events) if previous else {}
    counts = Counter({key: 0 for key in ('lectures_added', 'timing_pairs_added', 'timing_pairs_updated',
        'attendance_decisions_changed', 'report_revisions_added', 'series_added', 'calendar_candidates_added',
        'complete_reports', 'historical_reports_skipped', 'unmatched_or_non_enrolled_participants')})
    diagnostics = []
    def issue(code, course=None, error=None):
        value = {'code': code}
        if course is not None: value['course_id'] = course
        if error: value.update(http_status=error.status, graph_code=error.code)
        diagnostics.append(value)
    # Only the audit row is written before network I/O. Domain updates are atomic.
    run = None
    if not dry_run:
        TeamsDiscoveryRun.objects.filter(status='running').update(status='interrupted', finished_at=now)
        run = TeamsDiscoveryRun.objects.create()
    try:
        client.authenticate()
        prior_decisions = {(r.occurrence_id, r.learner_source_id): r.duration_microseconds >= 1_800_000_000
            for r in TeamsOccurrenceAttendance.objects.filter(occurrence__status='verified')}
        series = {(m['organizer_id'], m['meeting_id']): dict(m) for m in config['meetings']}
        approved = defaultdict(set)
        for m in config['meetings']: approved[m['course_source_id']].add(m['organizer_id'])
        for s in TeamsDiscoveredSeries.objects.all():
            if s.organizer_id in approved[s.course_source_id]:
                series[(s.organizer_id, s.meeting_id)] = {'organizer_id': s.organizer_id, 'meeting_id': s.meeting_id,
                    'course_source_id': s.course_source_id, 'approved_subject': s.subject, 'join_url': s.join_url}
        # Metadata is optional for known series. Attendance requests are never gated on it.
        blocked_series = set()
        for key, binding in series.items():
            try:
                meta = client.get(meeting_path(*key))
                organizer = (((meta.get('participants') or {}).get('organizer') or {}).get('identity') or {}).get('user', {}).get('id')
                if (meta.get('id') == key[1] and organizer == key[0]
                        and subject_course(meta.get('subject'), config) == binding['course_source_id']):
                    binding['join_url'] = meta.get('joinWebUrl', '')
                else:
                    blocked_series.add(key); issue('series_metadata_conflicts_with_approved_mapping', binding['course_source_id'])
            except GraphError:
                counts['unavailable_series_metadata'] += 1
        events = dict(pending)
        lookback = dt.timedelta(days=config['automation'].get('lookback_days', 30))
        for reader in config['automation']['calendar_readers']:
            start = max(cutoff, min(timestamp(checkpoints.get(reader, cutoff)), now) - lookback)
            batches = 0
            while start < now and batches < 60:
                end = min(start + dt.timedelta(days=7), now); batches += 1
                query = urllib.parse.urlencode({'startDateTime': start.isoformat(), 'endDateTime': end.isoformat(), '$select': CALENDAR_FIELDS, '$top': '100'})
                try:
                    rows, pages = client.collection('/users/' + urllib.parse.quote(reader, safe='') + '/calendarView?' + query)
                    counts['calendar_pages'] += pages
                    for e in rows:
                        cid = subject_course(e.get('subject'), config)
                        if cid is None or e.get('isDraft') is True or e.get('isOnlineMeeting') is not True or not (e.get('onlineMeeting') or {}).get('joinUrl'):
                            counts['unrelated_events_skipped'] += 1; continue
                        try:
                            require(isinstance(e.get('iCalUId'), str) and e['iCalUId'], 'Calendar occurrence lacks stable iCalUId')
                            require(type(e.get('isCancelled')) is bool, 'Calendar cancellation status is missing')
                            a, b = calendar_time(e['start']), calendar_time(e['end'])
                        except (TimingValidationError, ValueError, TypeError, KeyError):
                            issue('invalid_calendar_occurrence', cid); continue
                        if a < cutoff or b <= a: counts['historical_events_skipped'] += 1; continue
                        # iCalUId is stable across attendee calendars and unique per recurrence.
                        event_key = digest([cid, e['iCalUId']])
                        candidate = {'course_source_id': cid, 'event': e, 'calendar_reader': reader}
                        existing = events.get(event_key)
                        if existing and existing['event'].get('isCancelled') != e.get('isCancelled'):
                            candidate['event'] = {**e, 'isCancelled': True}  # conservative until organizer review
                        events[event_key] = candidate
                    checkpoints[reader] = end.isoformat(); start = end
                except GraphError as exc:
                    issue('calendar_unavailable', error=exc); break
            if start < now: issue('calendar_backlog_pending')
        resolved = []
        for event_key, candidate in events.items():
            e, cid = candidate['event'], candidate['course_source_id']; url = e['onlineMeeting']['joinUrl']
            candidates = [key for key, m in series.items() if key not in blocked_series and m.get('join_url') == url and m['course_source_id'] == cid]
            if not candidates:
                # A new Meeting ID must be resolved from the exact calendar join URL
                # under an already approved organizer and course, never from its title alone.
                for organizer in sorted(approved[cid]):
                    query = urllib.parse.urlencode({'$filter': "JoinWebUrl eq '" + url.replace("'", "''") + "'"})
                    try:
                        metas, _ = client.collection('/users/' + urllib.parse.quote(organizer, safe='') + '/onlineMeetings?' + query)
                        for meta in metas:
                            actual = (((meta.get('participants') or {}).get('organizer') or {}).get('identity') or {}).get('user', {}).get('id')
                            if actual != organizer or meta.get('joinWebUrl') != url or subject_course(meta.get('subject'), config) != cid: continue
                            key = (organizer, meta['id'])
                            if key in blocked_series: continue
                            if key in series and series[key]['course_source_id'] != cid:
                                issue('conflicting_series_mapping', cid); continue
                            series[key] = {'organizer_id': organizer, 'meeting_id': meta['id'], 'course_source_id': cid,
                                           'approved_subject': meta['subject'], 'join_url': url,
                                           'mapping_evidence': {'meeting_id': meta['id'], 'organizer_id': actual,
                                               'subject': meta['subject'], 'join_url': url, 'calendar_uid': e['iCalUId']}}
                            candidates.append(key)
                    except GraphError as exc: issue('meeting_resolution_unavailable', cid, exc)
            if len(set(candidates)) != 1:
                pending[event_key] = candidate; issue('unresolved_calendar_series', cid); continue
            resolved.append((event_key, candidates[0], candidate)); pending.pop(event_key, None)
        # Existing pending/verified occurrences remain candidates beyond the lookback.
        report_batches = []
        for key, binding in series.items():
            cid = binding['course_source_id']; counts['series_checked'] += 1
            base = meeting_path(*key) + '/attendanceReports'
            listed = {}
            try:
                reports, pages = client.collection(base); counts['report_pages'] += pages
                listed = {r['id']: r for r in reports}
                if len(listed) >= 50: counts['latest_50_cap_observed'] += 1
            except GraphError as exc: issue('report_listing_unavailable', cid, exc)
            stored = list(TeamsReportEvidence.objects.filter(series__organizer_id=key[0], series__meeting_id=key[1])
                          .values('report_id', 'complete', 'payload__report__meetingStartDateTime', 'payload__report__meetingEndDateTime'))
            complete_ids = {r['report_id'] for r in stored if r['complete']}
            waiting = list(TeamsOccurrence.objects.filter(series__organizer_id=key[0], series__meeting_id=key[1], status='pending')
                           .values_list('scheduled_start', 'scheduled_end'))
            known = set()
            for row in stored:
                if row['report_id'] not in complete_ids:
                    known.add(row['report_id']); continue
                try:
                    a, b = timestamp(row['payload__report__meetingStartDateTime']), timestamp(row['payload__report__meetingEndDateTime'])
                    if b >= now - lookback or any(x < b and y > a for x, y in waiting): known.add(row['report_id'])
                except TimingValidationError:
                    continue  # a previous invalid revision does not erase a complete one
            for rid in sorted(set(listed) | known):
                path = base + '/' + urllib.parse.quote(rid, safe=''); report = None; records = []
                try:
                    report = listed[rid] if rid in listed else client.get(path)
                    require(report.get('id') == rid, 'Report identifier mismatch')
                    if timestamp(report.get('meetingStartDateTime')) < cutoff:
                        counts['historical_reports_skipped'] += 1; continue
                    if rid in complete_ids and rid not in known:
                        counts['archived_complete_reports_preserved'] += 1; continue
                    records, pages = client.collection(path + '/attendanceRecords'); counts['participant_pages'] += pages
                    validate_report(report, records)
                    payload = {'report': report, 'records': sorted(records, key=lambda r: r['id'])}
                    report_batches.append((key, rid, payload, True)); counts['complete_reports'] += 1
                except (GraphError, TimingValidationError, TypeError, KeyError, ValueError) as exc:
                    issue('report_unavailable_or_partial', cid, exc if isinstance(exc, GraphError) else None)
                    if report is not None: report_batches.append((key, rid, {'report': report, 'records': records}, False))
        after = registry(config)
        require(registry_hash == digest([sorted(after[0].items()), sorted(after[1].items()), sorted(after[2])]), 'Registry changed during retrieval; retry safely')
        with transaction.atomic():
            locked_registry = registry(config)
            require(registry_hash == digest([sorted(locked_registry[0].items()), sorted(locked_registry[1].items()), sorted(locked_registry[2])]), 'Registry changed before activation; retry safely')
            state, _ = TeamsDiscoveryState.objects.get_or_create(key='daily', defaults={'cutover_at': cutoff, 'baseline_sha256': baseline_hash})
            objects = {}
            for key, m in series.items():
                s, created = TeamsDiscoveredSeries.objects.get_or_create(organizer_id=key[0], meeting_id=key[1], defaults={
                    'course_source_id': m['course_source_id'], 'subject': m['approved_subject'], 'join_url': m.get('join_url', ''),
                    'provenance': {'policy_sha256': digest(config), 'source': 'approved_policy_or_verified_calendar_join_url',
                                   'mapping_evidence': m.get('mapping_evidence')}})
                require(s.course_source_id == m['course_source_id'], 'Stored series mapping conflicts with policy')
                if not s.join_url and m.get('join_url'): s.join_url = m['join_url']; s.save(update_fields=['join_url'])
                objects[key] = s; counts['series_added'] += created
            for event_key, key, candidate in resolved:
                e, cid = candidate['event'], candidate['course_source_id']
                defaults = {'series': objects[key], 'course_source_id': cid, 'calendar_uid': e['iCalUId'],
                            'scheduled_start': calendar_time(e['start']), 'scheduled_end': calendar_time(e['end']),
                            'provenance': candidate}
                occurrence, created = TeamsOccurrence.objects.get_or_create(key=event_key, defaults=defaults)
                require(occurrence.series_id == objects[key].pk and occurrence.course_source_id == cid, 'Calendar occurrence mapping changed; review required')
                if e.get('isCancelled'):
                    if occurrence.status == 'verified':
                        occurrence.needs_review = True; issue('delivered_calendar_now_cancelled', cid)
                    else: occurrence.status = 'cancelled'; occurrence.reason = 'calendar_cancelled'
                elif occurrence.status != 'verified':
                    occurrence.scheduled_start = defaults['scheduled_start']; occurrence.scheduled_end = defaults['scheduled_end']
                    if occurrence.status == 'cancelled':
                        occurrence.status = 'pending'; occurrence.reason = 'awaiting_complete_report'
                elif (occurrence.scheduled_start, occurrence.scheduled_end) != (defaults['scheduled_start'], defaults['scheduled_end']):
                    occurrence.needs_review = True; issue('delivered_occurrence_rescheduled', cid)
                prior = dict(occurrence.provenance)
                revisions = list(prior.pop('calendar_revisions', []))
                if prior and prior != candidate and prior not in revisions: revisions.append(prior)
                occurrence.provenance = {**candidate, **({'calendar_revisions': revisions} if revisions else {})}
                occurrence.save()
                counts['calendar_candidates_added'] += created
            for key, rid, payload, complete in report_batches:
                _, added = TeamsReportEvidence.objects.get_or_create(series=objects[key], report_id=rid, sha256=digest(payload),
                    defaults={'payload': payload, 'complete': complete})
                counts['report_revisions_added'] += added
            reconcile_occurrences(now, cutoff, config, emails, aliases, enrolled, counts, issue)
            final_decisions = {(r.occurrence_id, r.learner_source_id): r.duration_microseconds >= 1_800_000_000
                for r in TeamsOccurrenceAttendance.objects.filter(occurrence__status='verified')}
            counts['attendance_decisions_changed'] = sum(prior_decisions.get(k) != v for k, v in final_decisions.items())
            state.checkpoints = checkpoints; state.pending_events = pending; state.save()
            counts['modules_processed'] = 4
            counts['verified_new_lectures'] = TeamsOccurrence.objects.filter(status='verified').count()
            counts['pending_calendar_occurrences'] = TeamsOccurrence.objects.filter(status='pending').count()
            counts['unresolved_calendar_series'] = len(pending)
            result = {'status': 'partial' if diagnostics else 'success', 'dry_run': dry_run, 'counts': dict(counts), 'diagnostics': diagnostics}
            if run:
                run.status = result['status']; run.counts = dict(counts); run.diagnostics = diagnostics; run.finished_at = now; run.save()
            if dry_run: transaction.set_rollback(True)
        return result
    except Exception as exc:
        if run:
            run.status = 'failed'; run.finished_at = now
            run.diagnostics = [exc.diagnostic() if isinstance(exc, GraphError) else {'code': type(exc).__name__}]
            run.save(update_fields=['status', 'finished_at', 'diagnostics'])
        raise


def reconcile_occurrences(now, cutoff, config, emails, aliases, enrolled, counts, issue):
    grace = dt.timedelta(minutes=config['automation'].get('grace_minutes', 120))
    by_occurrence = defaultdict(list)
    for series in TeamsDiscoveredSeries.objects.all():
        occurrences = list(series.occurrences.all())
        revisions = defaultdict(list)
        for evidence in series.reports.filter(complete=True): revisions[evidence.report_id].append(evidence)
        for versions in revisions.values():
            choices = set()
            for evidence in versions:
                report = evidence.payload['report']; start, end = timestamp(report['meetingStartDateTime']), timestamp(report['meetingEndDateTime'])
                matches = [o for o in occurrences if o.scheduled_start < end and o.scheduled_end > start]
                choices.update(o.pk for o in matches)
            if len(choices) != 1:
                issue('report_without_unique_calendar_occurrence', series.course_source_id); continue
            by_occurrence[next(iter(choices))].extend(versions)
    for occurrence in TeamsOccurrence.objects.all():
        if occurrence.status == 'cancelled': continue
        versions = by_occurrence[occurrence.pk]
        if not versions: continue
        windows = [(timestamp(v.payload['report']['meetingStartDateTime']), timestamp(v.payload['report']['meetingEndDateTime'])) for v in versions]
        start, end = min(a for a, b in windows), max(b for a, b in windows)
        if start < cutoff or end > now - grace:
            counts['reports_waiting_grace_or_historical'] += 1; continue
        # Detect identity reuse across the complete evidence before accepting any intervals.
        identity_users = defaultdict(set)
        for version in versions:
            for record in version.payload['records']:
                uid = identity(record, emails, aliases); ident = record.get('identity') or {}
                if uid is not None and ident.get('id') and ident.get('tenantId'):
                    identity_users[(ident['id'], ident['tenantId'])].add(uid)
        groups = defaultdict(list); fingerprints = defaultdict(set)
        shrinking = set()
        by_report = defaultdict(list)
        for version in versions: by_report[version.report_id].append(version)
        for report_versions in by_report.values():
            report_versions.sort(key=lambda v: (v.retrieved_at, v.pk))
            latest = defaultdict(list); older = defaultdict(list)
            for version in report_versions:
                target = latest if version is report_versions[-1] else older
                for record in version.payload['records']:
                    uid = identity(record, emails, aliases)
                    if uid is not None:
                        target[uid].extend((timestamp(i['joinDateTime']), timestamp(i['leaveDateTime'])) for i in record['attendanceIntervals'])
            for uid, prior in older.items():
                if not covers(latest[uid], prior): shrinking.add(uid)
        if shrinking: issue('shorter_graph_revision_preserved_for_review', occurrence.course_source_id)
        for version in versions:
            for record in version.payload['records']:
                uid = identity(record, emails, aliases); ident = record.get('identity') or {}
                if uid is None or (uid, occurrence.course_source_id) not in enrolled:
                    counts['unmatched_or_non_enrolled_participants'] += 1; continue
                if len(identity_users[(ident.get('id'), ident.get('tenantId'))]) > 1:
                    issue('ambiguous_participant_identity', occurrence.course_source_id); continue
                groups[uid].extend((timestamp(i['joinDateTime']), timestamp(i['leaveDateTime'])) for i in record['attendanceIntervals'])
                fingerprints[uid].add(version.sha256)
        for uid, values in groups.items():
            merged = merge_intervals(values)
            if not merged: continue
            previous = occurrence.attendance.filter(learner_source_id=uid).first()
            if previous:
                merged = merge_intervals(merged + [(timestamp(a), timestamp(b)) for a, b in previous.intervals])
            data = {'first_join': merged[0][0], 'last_leave': merged[-1][1],
                    'duration_microseconds': sum(microseconds(b-a) for a,b in merged), 'visit_count': len(merged),
                    'intervals': [[a.isoformat(), b.isoformat()] for a,b in merged], 'evidence_sha256': digest(sorted(fingerprints[uid])),
                    'needs_review': uid in shrinking}
            if previous is None:
                TeamsOccurrenceAttendance.objects.create(occurrence=occurrence, learner_source_id=uid, **data)
                counts['timing_pairs_added'] += 1
            elif any(getattr(previous,k) != v for k,v in data.items()):
                before_present = previous.duration_microseconds >= 1_800_000_000
                for k,v in data.items(): setattr(previous,k,v)
                previous.save(); counts['timing_pairs_updated'] += 1
                counts['attendance_decisions_changed'] += before_present != (data['duration_microseconds'] >= 1_800_000_000)
        from ..identity_evidence import projected_timing
        qualified = sum(r.duration_microseconds >= 1_800_000_000 for r in
            projected_timing(occurrence.attendance.all(), occurrence=True).values())
        if qualified >= settings.TEAMS_MIN_LEARNERS_FOR_DELIVERED:
            overlap = TeamsOccurrence.objects.filter(course_source_id=occurrence.course_source_id,
                status='verified', starts_at__lt=end, ends_at__gt=start).exclude(pk=occurrence.pk).exists()
            if overlap and occurrence.status != 'verified':
                occurrence.reason = 'overlapping_delivery_needs_review'; occurrence.needs_review = True
                occurrence.save(update_fields=['reason', 'needs_review'])
                issue('overlapping_delivery_needs_review', occurrence.course_source_id); continue
            if occurrence.status != 'verified': counts['lectures_added'] += 1
            occurrence.status = 'verified'; occurrence.reason = 'calendar_and_complete_reports_and_delivery_quorum'
            occurrence.starts_at = start; occurrence.ends_at = end; occurrence.save()
        elif occurrence.status != 'verified':
            occurrence.reason = 'insufficient_verified_delivery_evidence'; occurrence.save(update_fields=['reason'])

"""Read-only visibility check for an existing canonical Teams lecture; no Graph calls."""
import json

from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from analytics import metrics
from analytics.attendance.importer import merge_intervals
from analytics.attendance.metrics import AttendanceIndex
from analytics.attendance.reconciliation import minimum_minutes
from analytics.attendance.timing import microseconds, timestamp
from analytics.directory_attendance import DirectoryAttendance
from analytics.models import Enrollment, TeamsOccurrence


class Command(BaseCommand):
    help = 'Read existing SQLite occurrence evidence and verify dashboard visibility without importing or changing data.'

    def add_arguments(self, parser):
        parser.add_argument('--occurrence-id', type=int, required=True)

    def handle(self, *args, **options):
        if connections['default'].vendor != 'sqlite':
            raise CommandError('Dashboard default database must be SQLite; no changes made.')
        occurrence = TeamsOccurrence.objects.select_related('series').filter(pk=options['occurrence_id']).first()
        if occurrence is None:
            raise CommandError('Occurrence not found in the configured dashboard SQLite database; no changes made.')
        cid, sid = occurrence.course_source_id, f'auto:{occurrence.pk}'
        issues = []
        if occurrence.status != 'verified':
            raise CommandError('Occurrence is not verified; no delivery or attendance inferred.')
        if not occurrence.starts_at or not occurrence.ends_at or occurrence.ends_at <= occurrence.starts_at:
            raise CommandError('Verified occurrence lacks a valid actual meeting window; no changes made.')
        if cid != occurrence.series.course_source_id or not occurrence.calendar_uid:
            raise CommandError('Occurrence course/calendar mapping is invalid; no changes made.')

        raw = list(occurrence.attendance.all())
        invalid = 0
        for row in raw:
            try:
                values = [(timestamp(a), timestamp(b)) for a, b in row.intervals]
                if not values or any(b <= a for a, b in values):
                    raise ValueError
                merged = merge_intervals(values)
                if (sum(microseconds(b-a) for a, b in merged) != row.duration_microseconds or
                        merged[0][0] != row.first_join or merged[-1][1] != row.last_leave):
                    raise ValueError
            except (ValueError, TypeError, KeyError):
                invalid += 1
        if invalid:
            issues.append('stored_timing_disagrees_with_intervals')

        index = AttendanceIndex()
        official = DirectoryAttendance(index)
        issues.extend(sorted(official.issues[cid]))
        members = official.groups[cid].get(('calendar', occurrence.calendar_uid), set())
        if sid not in members:
            issues.append('occurrence_not_in_official_reporting_calendar')
        if members != {sid}:
            issues.append('duplicate_or_missing_canonical_occurrence')
        sessions = [s for s in index.sessions_by_course[cid] if s['id'] == sid]
        if len(sessions) != 1:
            issues.append('occurrence_not_visible_exactly_once')

        learners = set(Enrollment.objects.filter(course__source_id=cid).values_list('learner__source_id', flat=True))
        timing = {uid: row for (uid, key), row in index.occurrence_timing.items() if key == sid}
        present = {uid for (uid, key), row in index.records.items() if key == sid and row['attended']}
        qualified = {uid for uid, row in timing.items()
                     if row.duration_microseconds >= minimum_minutes()*60*1_000_000}
        if present != qualified:
            issues.append('presence_disagrees_with_approved_duration_threshold')

        directory = metrics.learner_directory(course_id=cid)
        expected = official.totals.get(cid)
        directory_errors = sum(not row['attendance']['available'] or row['attendance']['expected'] != expected
                               for row in directory)
        if directory_errors:
            issues.append('directory_official_denominator_mismatch')
        result = {
            'status': 'failed' if issues else 'passed',
            'database': {'alias': 'default', 'engine': 'sqlite', 'path': str(connections['default'].settings_dict['NAME'])},
            'occurrence_id': occurrence.pk, 'course_id': cid,
            'starts_at_utc': occurrence.starts_at.isoformat(), 'ends_at_utc': occurrence.ends_at.isoformat(),
            'canonical_source': 'TeamsOccurrence / TeamsOccurrenceAttendance',
            'historical_backfill_required': False, 'official_delivered_lectures': expected,
            'raw_timing_records': len(raw), 'invalid_interval_records': invalid,
            'eligible_projected_timing_pairs': len(timing), 'confirmed_present': len(present),
            'short_duration_review': len(set(timing)-qualified), 'no_timing_unknown': len(learners-set(timing)),
            'enrolled_learners': len(learners), 'minimum_minutes': minimum_minutes(),
            'directory_rows_checked': len(directory), 'directory_denominator_mismatches': directory_errors,
            'issues': sorted(set(issues)),
        }
        self.stdout.write(json.dumps(result, sort_keys=True))
        if issues:
            raise CommandError('Occurrence verification failed; investigate the reported issues without backfilling historical tables.')

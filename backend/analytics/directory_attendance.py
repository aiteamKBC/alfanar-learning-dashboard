"""Directory-only confirmed attendance / official delivery; never mutate evidence.

The existing owner-confirmed historical totals include undated official lectures.
Their missing observations remain unknown. Only subsequently verified calendar
occurrences extend those totals; raw reports and off-calendar timing do not.
"""
import datetime as dt
from collections import defaultdict
from zoneinfo import ZoneInfo

from django.utils import timezone

from .attendance import confirmed
from .models import LectureSession, TeamsDiscoveryState, TeamsOccurrence

DEFINITION = 'Confirmed attendance / officially delivered lectures in the enrolled modules.'


class DirectoryAttendance:
    def __init__(self, index):
        self.index = index
        self.groups = defaultdict(lambda: defaultdict(set))
        self.issues = defaultdict(set)
        self.totals = {}
        now = timezone.now()
        today = now.astimezone(ZoneInfo('Africa/Cairo')).date()
        historical = LectureSession.objects.filter(
            delivered=True, exclusion_reason='', session_date__lte=today,
            meeting__evidence_source='attendance_matrix',
            meeting__mapping_status__in=['approved_rule', 'exact_title'],
        ).values_list('id', 'meeting__course_source_id', 'session_date')
        history_dates = defaultdict(set)
        for sid, cid, day in historical:
            history_dates[cid].add(day)
            self.groups[cid][('historical', day)].add(sid)

        cutoff = TeamsDiscoveryState.objects.filter(pk='daily').values_list('cutover_at', flat=True).first()
        windows = defaultdict(dict)
        for occurrence in TeamsOccurrence.objects.filter(status='verified').select_related('series'):
            cid = occurrence.course_source_id
            start, end = occurrence.starts_at, occurrence.ends_at
            if not start or not end or end <= start or not occurrence.calendar_uid or cid != occurrence.series.course_source_id:
                self.issues[cid].add('invalid_verified_occurrence')
                continue
            if end > now:
                continue  # a future occurrence cannot establish delivered attendance
            if cutoff is None:
                self.issues[cid].add('missing_historical_cutover')
                continue
            if start < cutoff or start.astimezone(ZoneInfo('Africa/Cairo')).date() in history_dates[cid]:
                continue  # historical evidence cannot create an additional delivered lecture
            key = ('calendar', occurrence.calendar_uid)
            window = (start, end)
            if key in windows[cid] and windows[cid][key] != window:
                self.issues[cid].add('conflicting_occurrence_windows')
            windows[cid][key] = window
            self.groups[cid][key].add(f'auto:{occurrence.pk}')

        for cid, group in windows.items():
            ordered = sorted(group.values())
            if any(a[1] > b[0] for a, b in zip(ordered, ordered[1:])):
                # Different calendar IDs with overlapping delivery need review,
                # not a second count or a silent cap.
                self.issues[cid].add('overlapping_verified_occurrences')
        for cid, policy in confirmed.CONFIRMED_DELIVERED_LECTURES.items():
            count = policy.get('count')
            try:
                verified_on = dt.date.fromisoformat(policy['confirmed_on'])
            except (KeyError, TypeError, ValueError):
                verified_on = None
            if type(count) is not int or count < 0 or not policy.get('confirmed_by') or not verified_on or verified_on > today:
                self.issues[cid].add('unverified_official_count')
                continue
            if len(history_dates[cid]) > count:
                self.issues[cid].add('historical_calendar_exceeds_official_total')
            self.totals[cid] = count + len(windows[cid])

    def summary(self, learner_id, course_ids, original):
        course_ids = set(course_ids)
        issues = set().union(*(self.issues[cid] for cid in course_ids))
        if any(cid not in self.totals for cid in course_ids):
            issues.add('official_count_unavailable')
        expected = sum(self.totals[cid] for cid in course_ids) if not issues else None
        attended = known = modules_covered = 0
        for cid in course_ids:
            module_known = 0
            for session_ids in self.groups[cid].values():
                records = [self.index.records[(learner_id, sid)] for sid in session_ids
                           if (learner_id, sid) in self.index.records]
                if records:
                    # Duplicate evidence for one occurrence contributes once.
                    module_known += 1
                    attended += any(row['attended'] for row in records)
            known += module_known
            modules_covered += bool(module_known)
        if expected is not None and (attended > expected or known > expected):
            issues.add('attendance_exceeds_official_total')
        valid = not issues and expected is not None and expected > 0
        return {
            **original, 'basis': 'official_delivered', 'definition': DEFINITION,
            'available': valid, 'attended': attended if not issues else None,
            'expected': expected, 'pct': round(100 * attended / expected, 1) if valid else None,
            'known': known, 'absent': known - attended,
            'unknown': expected - known if expected is not None and not issues else None,
            'modules_covered': modules_covered, 'modules_total': len(course_ids),
            'validation_issues': sorted(issues),
        }

"""Synthetic directory-only official denominators; source evidence stays immutable."""
import datetime as dt
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from analytics import metrics
from analytics.models import (Course, Learner, Enrollment, TeamsMeeting, LectureSession, SessionAttendance,
    TimingImport, LectureTiming, TeamsDiscoveryState, TeamsDiscoveredSeries, TeamsOccurrence, TeamsOccurrenceAttendance)

COURSES = (83334, 121642, 135945, 154268)
UTC = dt.timezone.utc


class OfficialDirectoryAttendanceTests(TestCase):
    def setUp(self):
        self.policy = {cid: {'count': 2, 'confirmed_by': 'Synthetic owner', 'confirmed_on': '2026-11-05', 'note': 'Synthetic confirmation'} for cid in COURSES}
        policy = patch('analytics.attendance.confirmed.CONFIRMED_DELIVERED_LECTURES', self.policy)
        policy.start(); self.addCleanup(policy.stop)
        self.client.force_login(get_user_model().objects.create_user('owner_review'))
        self.courses = {cid: Course.objects.create(source_id=cid, title=f'Synthetic module {cid}') for cid in COURSES}
        self.learners = {uid: Learner.objects.create(source_id=uid, name=f'Synthetic {uid}') for uid in (101, 102, 103)}
        for uid, courses in [(101, COURSES), (102, COURSES), (103, COURSES[:2])]:
            for cid in courses:
                Enrollment.objects.create(learner=self.learners[uid], course=self.courses[cid],
                    registered_at=timezone.now() if uid == 102 else None)
        self.sessions = {cid: self.session(cid, '2026-11-01') for cid in COURSES}

    def session(self, cid, day, *, delivered=True, source='attendance_matrix', mapping='approved_rule'):
        meeting = TeamsMeeting.objects.create(meeting_id=f'synthetic-{TeamsMeeting.objects.count()}', subject='Synthetic',
            course_source_id=cid, evidence_source=source, mapping_status=mapping)
        return LectureSession.objects.create(meeting=meeting, session_date=day, delivered=delivered,
            report_count=100, verified_learners=1)

    def present(self, uid, cid, flag=True, session=None):
        return SessionAttendance.objects.create(session=session or self.sessions[cid], learner_source_id=uid, attended=flag)

    def rows(self, **params):
        response = self.client.get('/api/learners', {'page_size': 100, **params})
        self.assertEqual(response.status_code, 200)
        return {r['learner_id']: r for r in response.json()['results']}

    def occurrence(self, uid='one', *, status='verified', start=None, end=None, key=None):
        TeamsDiscoveryState.objects.get_or_create(key='daily', defaults={
            'cutover_at': dt.datetime(2026, 11, 6, tzinfo=UTC), 'baseline_sha256': 'test'})
        series, _ = TeamsDiscoveredSeries.objects.get_or_create(organizer_id='synthetic-owner', meeting_id='synthetic-series',
            defaults={'course_source_id': COURSES[0], 'subject': 'Synthetic'})
        start = start or dt.datetime(2026, 11, 7, 9, tzinfo=UTC)
        end = end or start + dt.timedelta(hours=2)
        return TeamsOccurrence.objects.create(key=key or uid, series=series, course_source_id=COURSES[0], calendar_uid=uid,
            scheduled_start=start, scheduled_end=end, starts_at=start, ends_at=end, status=status)

    def test_same_enrollments_same_denominator_despite_late_enrollment_or_unknown_observations(self):
        for cid in COURSES[:3]: self.present(101, cid)
        self.present(102, COURSES[0]); self.present(102, COURSES[1], False)
        rows = self.rows()
        self.assertEqual((rows[101]['attendance']['attended'], rows[101]['attendance']['expected'], rows[101]['attendance']['pct']), (3, 8, 37.5))
        self.assertEqual((rows[102]['attendance']['attended'], rows[102]['attendance']['expected'], rows[102]['attendance']['pct']), (1, 8, 12.5))
        self.assertEqual(rows[103]['attendance']['expected'], 4)
        self.assertEqual(self.rows(course=COURSES[0])[101]['attendance']['expected'], 2)
        self.assertEqual(self.rows(search='102')[102]['attendance']['expected'], 8)

    def test_unknown_evidence_is_not_absent_even_when_no_presence_is_confirmed(self):
        row = self.rows()[101]['attendance']
        self.assertEqual((row['attended'], row['known'], row['absent'], row['unknown'], row['pct']), (0, 0, 0, 8, 0))
        self.assertEqual(SessionAttendance.objects.count(), 0)
        self.assertEqual(row['basis'], 'official_delivered')

    def test_duplicate_calendar_rows_and_reports_count_once(self):
        self.present(101, COURSES[0])
        duplicate = self.session(COURSES[0], '2026-11-01')
        self.present(101, COURSES[0], session=duplicate)
        row = self.rows(course=COURSES[0])[101]['attendance']
        self.assertEqual((row['attended'], row['known'], row['expected']), (1, 1, 2))

    def test_undelivered_unmapped_and_off_calendar_interval_records_do_not_count(self):
        for kwargs in ({'delivered': False}, {'mapping': 'unmapped'}, {'source': 'teams_intervals'}):
            self.present(101, COURSES[0], session=self.session(COURSES[0], '2026-11-02', **kwargs))
        row = self.rows()[101]['attendance']
        self.assertEqual((row['attended'], row['known'], row['expected']), (0, 0, 8))

    def test_existing_positive_timing_correction_is_reused_without_changing_matrix(self):
        original = self.present(101, COURSES[0], False)
        run = TimingImport.objects.create(fingerprint='a'*64, active=True)
        timing = LectureTiming.objects.create(import_run=run, learner_source_id=101, course_source_id=COURSES[0],
            session_date='2026-11-01', status='verified', duration_microseconds=1_800_000_000,
            first_join=dt.datetime(2026,11,1,9,tzinfo=UTC), last_leave=dt.datetime(2026,11,1,9,30,tzinfo=UTC),visit_count=1)
        self.assertEqual(self.rows()[101]['attendance']['attended'], 1)
        original.refresh_from_db(); self.assertFalse(original.attended)
        timing.status = 'off_calendar'; timing.save()
        self.assertEqual(self.rows()[101]['attendance']['attended'], 0)

    def test_new_verified_delivery_adds_dynamically_and_repeated_reports_do_not_duplicate(self):
        first = self.occurrence()
        duplicate = self.occurrence(key='duplicate')  # same canonical calendar UID
        for occurrence in (first, duplicate):
            TeamsOccurrenceAttendance.objects.create(occurrence=occurrence, learner_source_id=101,
                first_join=occurrence.starts_at, last_leave=occurrence.ends_at,
                duration_microseconds=1_800_000_000, visit_count=1, evidence_sha256='test')
        rows = self.rows()
        self.assertEqual((rows[101]['attendance']['attended'], rows[101]['attendance']['expected']), (1, 9))
        self.assertEqual(rows[102]['attendance']['expected'], 9)
        self.policy[COURSES[1]]['count'] = 3  # use the approved source at read time, never a fixed total
        self.assertEqual(self.rows()[101]['attendance']['expected'], 10)

    def test_pending_cancelled_future_and_historical_occurrences_do_not_extend_delivery(self):
        for status in ('pending', 'cancelled', 'rejected'): self.occurrence(uid=status, status=status)
        self.occurrence(uid='future', start=timezone.now()+dt.timedelta(days=5))
        self.occurrence(uid='historical', start=dt.datetime(2026,11,1,9,tzinfo=UTC))
        self.assertEqual(self.rows()[101]['attendance']['expected'], 8)

    def test_unreliable_counts_and_overlapping_delivery_return_na_not_a_capped_percentage(self):
        self.occurrence(uid='first'); self.occurrence(uid='other-calendar')
        row = self.rows()[101]['attendance']
        self.assertFalse(row['available']); self.assertIsNone(row['pct'])
        self.assertIn('overlapping_verified_occurrences', row['validation_issues'])

    def test_historical_calendar_exceeding_owner_total_is_reported_not_silently_capped(self):
        for day in ('2026-11-02', '2026-11-03'):
            self.present(101, COURSES[0], session=self.session(COURSES[0], day))
        row = self.rows()[101]['attendance']
        self.assertFalse(row['available']); self.assertIsNone(row['attended'])
        self.assertIn('historical_calendar_exceeds_official_total', row['validation_issues'])

    def test_missing_or_zero_official_count_never_invents_a_percentage(self):
        del self.policy[COURSES[0]]
        row = self.rows(course=COURSES[0])[101]['attendance']
        self.assertFalse(row['available']); self.assertIsNone(row['expected']); self.assertIsNone(row['pct'])
        self.policy[COURSES[0]] = {'count': 0, 'confirmed_by':'Synthetic owner', 'confirmed_on':'2026-11-05'}
        self.sessions[COURSES[0]].delete()
        row = self.rows(course=COURSES[0])[101]['attendance']
        self.assertFalse(row['available']); self.assertEqual(row['expected'], 0); self.assertIsNone(row['pct'])

    def test_directory_reads_preserve_overview_profiles_source_records_and_other_metrics(self):
        self.present(101, COURSES[0]); self.present(101, COURSES[1], False)
        before = (metrics.summary(), metrics.charts(), metrics.learner_detail(self.learners[101]),
                  list(SessionAttendance.objects.values()), list(Enrollment.objects.values()))
        row = self.rows()[101]
        self.assertEqual(row['attendance']['pct'], 12.5)
        self.assertEqual(before, (metrics.summary(), metrics.charts(), metrics.learner_detail(self.learners[101]),
                  list(SessionAttendance.objects.values()), list(Enrollment.objects.values())))
        self.assertEqual(before[2]['summary']['attendance']['pct'], 50)

"""Selected-module presentation must agree with existing learner evidence."""
import datetime as dt
import json
from unittest.mock import patch

from analytics import metrics
from analytics.attendance.timing import import_timing, verified_duration_total
from analytics.models import Enrollment, Learner, LectureSession, LectureTiming, SessionAttendance, TeamsMeeting
from .test_metrics_api import ApiTestBase
from .teams_fixtures import REPORTS, write_teams_workbook


class ModulePerformanceTests(ApiTestBase):
    def rows(self, course=9001, **filters):
        response = self.client.get('/api/dashboard/charts', {'course': course, **filters})
        self.assertEqual(response.status_code, 200)
        return {r['learner_id']: r for r in response.json()['learner_module_matrix']['rows']}

    def performance(self, uid=101, course=9001):
        return self.rows(course)[uid]['module_metrics'][str(course)]

    def test_module_metrics_equal_activity_matrix_and_profile(self):
        for cid in (9001, 9002):
            for uid, row in self.rows(cid).items():
                m = row['module_metrics'][str(cid)]
                footer = self.client.get(f'/api/learners/{uid}/courses/{cid}/matrix').json()['footer']
                profile = self.client.get(f'/api/learners/{uid}').json()
                course = next(c for c in profile['courses'] if c['course_id'] == cid)
                self.assertEqual((m['quizzes_attempted'], m['quizzes_known'], m['mean_latest_score']),
                    (footer['quiz']['recorded'], footer['quiz']['known'], footer['quiz']['mean_latest_score']))
                for kind in ('video', 'pdf', 'reading'):
                    self.assertEqual(m['lessons_by_type'][kind],
                        {'completed': footer[kind]['recorded'], 'known': footer[kind]['known']})
                self.assertEqual((m['assignments_submitted'], m['assignments_known']),
                    (footer['assignment']['submitted'], footer['assignment']['known']))
                for key in m:
                    if key != 'verified_attendance_seconds': self.assertEqual(m[key], course[key])
        # Completion uses attempted quizzes, latest rather than best score; drafts excluded.
        first = self.performance()
        self.assertEqual((first['quizzes_attempted'], first['quizzes_known'], first['mean_latest_score']), (1, 2, 60))
        self.assertEqual((first['assignments_submitted'], first['assignments_known']), (0, 1))
        self.assertEqual(self.performance(102)['mean_latest_score'], 85)

    def test_module_and_learner_filters_do_not_leak_other_modules(self):
        self.assertEqual(set(self.rows(9002)), {101, 103})
        self.assertEqual(set(self.rows(9002, engagement='inactive')), {101, 103})
        self.assertEqual(self.rows(9001, engagement='inactive'), {})
        for row in self.rows(9002).values():
            self.assertEqual(set(row['module_metrics']), {'9002'})
            m = row['module_metrics']['9002']
            self.assertEqual(m['quizzes_known'], 0)
            self.assertIsNone(m['mean_latest_score'])
            self.assertIsNone(m['verified_attendance_seconds'])
        self.assertEqual(self.rows(9001, activity_type='video'), self.rows(9001))
        self.assertEqual(self.rows(9999), {})
        all_rows = self.client.get('/api/dashboard/charts').json()['learner_module_matrix']['rows']
        self.assertTrue(all(not r['module_metrics'] for r in all_rows))

    def test_canonical_ids_not_display_names_control_matching(self):
        Learner.objects.filter(source_id__in=(101, 102)).update(name='Fictional Shared Name')
        self.assertEqual(self.performance(101)['quizzes_attempted'], 1)
        self.assertEqual(self.performance(102)['quizzes_attempted'], 2)

    def add_timing(self):
        meeting = TeamsMeeting.objects.create(meeting_id='matrix:9001', subject='Synthetic Module A',
            course_source_id=9001, evidence_source='attendance_matrix')
        for day in ('2026-01-05', '2026-01-07', '2026-01-09'):
            LectureSession.objects.create(meeting=meeting, session_date=day, delivered=True,
                report_count=1, verified_learners=1)
        session = LectureSession.objects.get(meeting=meeting, session_date='2026-01-05')
        SessionAttendance.objects.create(session=session, learner_source_id=101, attended=False)
        # The synthetic organizer arrives before the learner; retain a valid report window.
        reports = [(subject, '2026-01-05T08:55:00Z' if rid == 'R1' else start, end, rid, mid)
                   for subject, start, end, rid, mid in REPORTS]
        source = write_teams_workbook(self.tmp/'timing.xlsx', reports=reports)
        policy = self.tmp/'policy.json'
        policy.write_text(json.dumps({'version': 1, 'meetings': [{'meeting_id': 'MEET-A',
            'course_source_id': 9001, 'approved_subject': 'Synthetic Module A'}], 'aliases': [],
            'verified_lecture_dates': {'9001': ['2026-01-05', '2026-01-07']}}))
        import_timing(source, policy)

    def test_merged_duration_total_includes_short_visits_but_not_gaps_or_missing_evidence(self):
        self.add_timing()
        before = metrics.summary()
        original = list(SessionAttendance.objects.values())
        data = self.client.get('/api/learners/101/courses/9001/matrix').json()['attendance']
        # 45 minutes union across overlaps/reports/rejoins + 1-minute visit; not the 2h35m join/leave span.
        self.assertEqual(data['verified_attendance_seconds'], 46*60)
        self.assertEqual(self.performance()['verified_attendance_seconds'], 46*60)
        self.assertEqual([s['timing']['duration_seconds'] for s in data['sessions']], [45*60, 60, None])
        self.assertEqual(self.performance(102)['verified_attendance_seconds'], 30*60)
        self.assertIsNone(self.performance(101, 9002)['verified_attendance_seconds'])
        self.assertEqual(metrics.summary(), before)
        self.assertEqual(list(SessionAttendance.objects.values()), original)

    def test_pending_ambiguous_and_inactive_evidence_not_totalled(self):
        self.add_timing()
        row = LectureTiming.objects.get(learner_source_id=101, session_date='2026-01-07')
        row.status = 'off_calendar'; row.save(update_fields=['status'])
        self.assertEqual(self.performance()['verified_attendance_seconds'], 45*60)
        with patch('analytics.attendance.timing.AMBIGUOUS_DATES', {(9001, dt.date(2026, 1, 5))}):
            self.assertIsNone(self.performance()['verified_attendance_seconds'])
            detail = metrics.module_matrix(Enrollment.objects.get(learner__source_id=101, course__source_id=9001))
            self.assertIsNone(detail['attendance']['verified_attendance_seconds'])
        row.import_run.active = False; row.import_run.save(update_fields=['active'])
        self.assertIsNone(self.performance()['verified_attendance_seconds'])

    def test_bulk_timing_query_count_does_not_grow_per_learner(self):
        self.add_timing()
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as queries:
            metrics.charts(course_id=9001)
        initial = len(queries)
        course = Enrollment.objects.get(learner__source_id=101, course__source_id=9001).course
        for uid in range(110, 116):
            learner = Learner.objects.create(source_id=uid, name=f'Fictional {uid}', email=f'{uid}@example.invalid')
            Enrollment.objects.create(learner=learner, course=course)
        with CaptureQueriesContext(connection) as queries:
            metrics.charts(course_id=9001)
        self.assertEqual(len(queries), initial)

    def test_duration_total_distinguishes_no_evidence_and_verified_zero(self):
        self.assertIsNone(verified_duration_total([]))
        self.assertIsNone(verified_duration_total([{'timing': {'available': False, 'duration_seconds': None}}]))
        self.assertEqual(verified_duration_total([{'timing': {'available': True, 'duration_seconds': 0}}]), 0)

from copy import deepcopy

from django.contrib.auth.models import User
from django.test import TestCase

from analytics.attendance.mapping import map_subject
from analytics.importer.service import run_sync
from analytics.importer.sources import SourcePayload
from analytics.models import AcademicEvidence, Course, DashboardAccess, Enrollment, Learner, LearnerIdentity

from .fixtures import build_tables


NAMES = {
    83334: 'Planning, Controlling and Leading a Project',
    121642: 'Procurement Risk and Contract Management',
    135945: 'Operations and Information Management for Project Managers',
    154268: 'Advanced Project and Logistics Management',
}


class CourseDisplayTests(TestCase):
    def setUp(self):
        self.learner = Learner.objects.create(source_id=101, name='Synthetic learner')
        for sid in NAMES:
            course = Course.objects.create(source_id=sid, title=f'Upstream title {sid}')
            Enrollment.objects.create(learner=self.learner, course=course, progress_pct=0)
        self.user = User.objects.create_user('viewer', password='not-a-real-pass-123')
        DashboardAccess.objects.create(user=self.user)
        self.client.force_login(self.user)

    def test_names_are_consistent_across_all_learner_api_views(self):
        filters = self.client.get('/api/filters').json()
        self.assertEqual({c['course_id']:c['title'] for c in filters['courses']}, NAMES)
        charts = self.client.get('/api/dashboard/charts').json()
        for key in ('module_progress', 'quiz_by_module', 'activity_completion'):
            self.assertEqual({c['course_id']:c['course'] for c in charts[key]}, NAMES)
        self.assertEqual({c['course_id']:c['course'] for c in charts['learner_module_matrix']['modules']}, NAMES)
        directory = self.client.get('/api/learners').json()['results'][0]
        self.assertEqual({c['course_id']:c['course'] for c in directory['enrollments']}, NAMES)
        for url in ('/api/learners/101', '/api/learners/101/courses'):
            self.assertEqual({c['course_id']:c['course'] for c in self.client.get(url).json()['courses']}, NAMES)
        for sid, name in NAMES.items():
            matrix = self.client.get(f'/api/learners/101/courses/{sid}/matrix').json()
            self.assertEqual(matrix['course']['title'], name)
            course = Course.objects.get(source_id=sid)
            self.assertEqual(course.title, f'Upstream title {sid}')
            self.assertEqual(map_subject(course.title, {sid:course.title})[0], sid)

    def test_module_order_is_consistent_in_filters_charts_directory_profiles_and_matrix(self):
        expected = [83334, 121642, 135945, 154268]
        self.assertEqual([c['course_id'] for c in self.client.get('/api/filters').json()['courses']], expected)
        charts = self.client.get('/api/dashboard/charts').json()
        for key in ('module_progress', 'quiz_by_module', 'activity_completion'):
            self.assertEqual([c['course_id'] for c in charts[key]], expected)
        self.assertEqual([c['course_id'] for c in charts['learner_module_matrix']['modules']], expected)
        directory = self.client.get('/api/learners').json()['results'][0]
        self.assertEqual([c['course_id'] for c in directory['enrollments']], expected)
        for url in ('/api/learners/101', '/api/learners/101/courses'):
            self.assertEqual([c['course_id'] for c in self.client.get(url).json()['courses']], expected)
        filtered = self.client.get('/api/dashboard/charts?course=121642').json()
        self.assertEqual([c['course_id'] for c in filtered['module_progress']], [121642])

    def test_other_courses_follow_known_modules_in_alphabetical_order(self):
        modules = [Course(source_id=9002, title='Zulu'), Course(source_id=154268, title='Original'),
                   Course(source_id=9001, title='Alpha'), Course(source_id=83334, title='Original')]
        self.assertEqual([c.source_id for c in sorted(modules, key=lambda c:c.display_order_key)],
                         [83334,154268,9001,9002])

    def test_owner_lecture_counts_are_separate_from_attendance_sessions_and_rates(self):
        import datetime as dt
        from unittest.mock import patch
        from analytics.attendance import confirmed
        from analytics.models import LectureSession, SessionAttendance, TeamsMeeting

        official = {83334:16,121642:14,135945:16,154268:12}
        evidence = {83334:16,121642:12,135945:14,154268:9}
        for cid, count in evidence.items():
            meeting = TeamsMeeting.objects.create(meeting_id=f'synthetic:{cid}', subject=f'Upstream title {cid}',
                                                  course_source_id=cid, evidence_source='attendance_matrix')
            for n in range(count):
                session = LectureSession.objects.create(meeting=meeting, session_date=dt.date(2026,1,n+1),
                                                         report_count=1, verified_learners=1, delivered=True)
                SessionAttendance.objects.create(session=session, learner_source_id=101,
                                                  attended=n % 2 == 0, match_method='email')
        records = list(SessionAttendance.objects.order_by('id').values())
        with patch.dict(confirmed.CONFIRMED_DELIVERED_LECTURES, {}, clear=True):
            before = self.client.get('/api/dashboard/summary').json()
        after = self.client.get('/api/dashboard/summary').json()
        self.assertEqual(before,after)
        rows = self.client.get('/api/dashboard/charts').json()['module_progress']
        self.assertEqual([r['course_id'] for r in rows], list(official))
        self.assertEqual({r['course_id']:r['delivered_lectures']['count'] for r in rows}, official)
        self.assertEqual({r['course_id']:r['delivered_sessions'] for r in rows}, evidence)
        for row in rows:
            cid = row['course_id']
            self.assertEqual(row['delivered_lectures']['source'], 'owner_confirmed')
            self.assertEqual(row['delivered_lectures']['teams_sessions'], evidence[cid])
            self.assertEqual(row['attendance']['expected'], evidence[cid])
            matrix = self.client.get(f'/api/learners/101/courses/{cid}/matrix').json()
            self.assertEqual(matrix['attendance']['delivered'], evidence[cid])
        self.assertEqual(records,list(SessionAttendance.objects.order_by('id').values()))

    def test_display_names_survive_source_title_changes_and_repeat_google_sync(self):
        tables = deepcopy(build_tables())
        remap = {9001:83334, 9002:121642}
        for rows in tables.values():
            if 'Course ID' not in rows[0]:
                continue
            index = rows[0].index('Course ID')
            title_index = rows[0].index('Course') if 'Course' in rows[0] else None
            for row in rows[1:]:
                if row[index] in remap:
                    row[index] = remap[row[index]]
                    if title_index is not None:
                        row[title_index] = f'Unmodified upstream title {row[index]}'
        original = deepcopy(tables)
        run = run_sync('google', fetcher=lambda:SourcePayload('google', 'synthetic', tables))
        self.assertEqual(run.status, 'success', run.errors)
        for course in Course.objects.all():
            self.assertEqual(course.display_title, NAMES[course.source_id])
            self.assertEqual(course.title, f'Unmodified upstream title {course.source_id}')
        repeat = run_sync('google', fetcher=lambda:SourcePayload('google', 'synthetic', tables))
        self.assertFalse(repeat.changed)
        self.assertEqual(tables, original)

    def test_unknown_course_uses_its_source_title(self):
        self.assertEqual(Course(source_id=9001, title='Synthetic module').display_title, 'Synthetic module')

    def test_new_exclusion_removes_all_academic_facts_and_preserves_nearby_account(self):
        tables = build_tables()
        for rows in tables.values():
            if 'User ID' not in rows[0]:
                continue
            index = rows[0].index('User ID')
            for row in rows[1:]:
                if row[index] == 102:
                    row[index] = 1057
                elif row[index] == 103:
                    row[index] = 744
        run = run_sync('google', fetcher=lambda:SourcePayload('google', 'synthetic', tables))
        self.assertEqual(run.status, 'success', run.errors)
        self.assertEqual(set(Learner.objects.values_list('source_id', flat=True)), {101,744})
        self.assertFalse(LearnerIdentity.objects.filter(source_id=1057).exists())
        self.assertFalse(AcademicEvidence.objects.filter(source_learner_id=1057).exists())
        self.assertEqual(self.client.get('/api/learners/1057').status_code, 404)
        self.assertEqual(self.client.get('/api/learners/744').status_code, 200)
        kpis = self.client.get('/api/dashboard/summary').json()['kpis']
        self.assertEqual(kpis['unique_learners']['value'], 2)
        self.assertEqual(kpis['materials_completed']['value'], 3)
        self.assertEqual(kpis['quiz_completion']['value'], 1)

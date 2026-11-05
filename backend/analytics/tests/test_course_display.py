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

"""Directory/export consistency using only fictional source identities and intervals."""
import copy
import csv
import datetime as dt
import io
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from openpyxl import load_workbook

from analytics import identity, metrics
from analytics.course_labels import COURSE_DISPLAY_NAMES
from analytics.importer.service import run_sync
from analytics.importer.sources import SourcePayload
from analytics.learner_export import HEADERS, MODULE_SHEETS, attendance_label
from analytics.models import (Course, DashboardAccess, Enrollment, Learner, LectureSession, LectureTiming,
    QuizResult, SessionAttendance, TeamsMeeting, TimingImport)
from .fixtures import build_tables


class LearnerDirectoryExportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tables = copy.deepcopy(build_tables())
        for table in cls.tables.values():
            if 'Course ID' not in table[0]:
                continue
            column = table[0].index('Course ID')
            for row in table[1:]:
                row[column] = {9001: 83334, 9002: 121642}.get(row[column], row[column])
        run = run_sync('excel', fetcher=lambda: SourcePayload('excel', 'synthetic', cls.tables))
        assert run.status == 'success'
        for cid in (135945, 154268):
            Course.objects.create(source_id=cid, title=COURSE_DISPLAY_NAMES[cid])
        cls.owner = get_user_model().objects.create_user('owner_review')

    def setUp(self):
        self.client.force_login(self.owner)

    def directory(self, cid=None):
        response = self.client.get('/api/learners', {'course': cid, 'page_size': 100} if cid else {'page_size': 100})
        self.assertEqual(response.status_code, 200)
        return {r['learner_id']: r for r in response.json()['results']}

    def export(self, ids='83334', kind='xlsx', **extra):
        return self.client.get('/api/learners/export', {'courses': ids, 'file_type': kind, **extra})

    def workbook(self, ids='83334', **extra):
        response = self.export(ids, **extra)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertIn('attachment;', response['Content-Disposition'])
        return load_workbook(io.BytesIO(response.content))

    def timing(self):
        run = TimingImport.objects.create(fingerprint='a'*64, source_name='synthetic', source_sha256='b'*64,
            policy_sha256='c'*64, registry_sha256='d'*64, calendar_sha256='e'*64, active=True)
        for cid, visits in ((83334, ((101, 2700), (102, 1800))), (121642, ((101, 600),))):
            meeting = TeamsMeeting.objects.create(meeting_id=f'synthetic:{cid}', course_source_id=cid,
                subject='Synthetic module', evidence_source='attendance_matrix')
            session = LectureSession.objects.create(meeting=meeting, session_date='2026-01-05', delivered=True,
                report_count=1, verified_learners=len(visits))
            for uid, seconds in visits:
                start = dt.datetime(2026, 1, 5, 9, tzinfo=dt.timezone.utc)
                # Store actual merged intervals separated by a gap, not the overall elapsed span.
                intervals = [(start, start + dt.timedelta(seconds=seconds//2)),
                    (start + dt.timedelta(hours=2), start + dt.timedelta(hours=2, seconds=seconds//2))]
                LectureTiming.objects.create(import_run=run, course_source_id=cid, learner_source_id=uid,
                    session_date=session.session_date, status='verified', first_join=intervals[0][0],
                    last_leave=intervals[-1][1], duration_microseconds=seconds*1_000_000, visit_count=2,
                    provenance=[{'join': a.isoformat(), 'leave': b.isoformat()} for a,b in intervals])
                SessionAttendance.objects.create(session=session, learner_source_id=uid, attended=False)

    def test_directory_counts_percentages_and_mean_equal_authoritative_matrix(self):
        for cid in (83334, 121642):
            for uid, row in self.directory(cid).items():
                footer = self.client.get(f'/api/learners/{uid}/courses/{cid}/matrix').json()['footer']
                self.assertEqual(row['quizzes_attempted'], footer['quiz']['recorded'])
                self.assertEqual(row['quizzes_known'], footer['quiz']['known'])
                self.assertEqual(row['mean_latest_score'], footer['quiz']['mean_latest_score'])
                self.assertEqual(row['lessons_completed'], sum(footer[k]['recorded'] for k in ('video','pdf','audio','reading')))
                self.assertEqual(row['lessons_known'], sum(footer[k]['known'] for k in ('video','pdf','audio','reading')))
        row = self.directory(83334)[101]
        self.assertEqual((row['quizzes_attempted'], row['quizzes_known'], row['quiz_completion_pct']), (1, 2, 50))
        self.assertEqual((row['lessons_completed'], row['lessons_known'], row['lessons_completion_pct']), (3, 4, 75))
        self.assertEqual(row['mean_latest_score'], 60)  # latest 60, not best 90 or mean retry 75
        empty = self.directory(121642)[101]
        self.assertIsNone(empty['quiz_completion_pct'])
        self.assertIsNone(empty['lessons_completion_pct'])
        self.assertIsNone(empty['mean_latest_score'])

    def test_scoped_durations_equal_overview_footer_and_all_module_sum(self):
        self.timing()
        for cid in (83334, 121642):
            chart = self.client.get('/api/dashboard/charts', {'course': cid}).json()['learner_module_matrix']
            overview = {r['learner_id']: r['module_metrics'][str(cid)] for r in chart['rows']}
            for uid, row in self.directory(cid).items():
                detail = self.client.get(f'/api/learners/{uid}/courses/{cid}/matrix').json()['attendance']
                self.assertEqual(row['verified_attendance_seconds'], detail['verified_attendance_seconds'])
                self.assertEqual(row['verified_attendance_seconds'], overview[uid]['verified_attendance_seconds'])
        self.assertEqual(self.directory(83334)[101]['verified_attendance_seconds'], 2700)
        self.assertEqual(self.directory()[101]['verified_attendance_seconds'], 3300)
        self.assertIsNone(self.directory()[103]['verified_attendance_seconds'])
        self.assertEqual(self.directory()[101]['mean_latest_score'], 60)

    def test_pending_ambiguous_and_inactive_timing_never_totalled(self):
        self.timing()
        LectureTiming.objects.filter(course_source_id=121642).update(status='off_calendar')
        self.assertIsNone(self.directory(121642)[101]['verified_attendance_seconds'])
        with patch('analytics.attendance.timing.AMBIGUOUS_DATES', {(83334, dt.date(2026,1,5))}):
            self.assertIsNone(self.directory(83334)[101]['verified_attendance_seconds'])
        TimingImport.objects.update(active=False)
        self.assertTrue(all(r['verified_attendance_seconds'] is None for r in self.directory().values()))

    def test_excel_numeric_values_sheets_order_and_csv_match_current_directory(self):
        self.timing()
        workbook = self.workbook('154268,121642,83334,135945,83334', search='no match', page=999)
        self.assertEqual(workbook.sheetnames, list(MODULE_SHEETS.values()))
        for cid, title in MODULE_SHEETS.items():
            sheet = workbook[title]
            rows = self.directory(cid)
            self.assertEqual(sheet.max_row, len(rows)+1)
            self.assertEqual(tuple(c.value for c in sheet[1]), HEADERS)
            self.assertEqual(len({sheet.cell(i,1).value for i in range(2,sheet.max_row+1)}), len(rows))
            for cells in sheet.iter_rows(min_row=2):
                r = rows[cells[0].value]
                expected = [r['learner_id'], r['name'], COURSE_DISPLAY_NAMES[cid], r['mean_progress'], r['attendance']['pct'],
                    r['quizzes_attempted'], r['quizzes_known'] or None, r['quiz_completion_pct'], r['mean_latest_score'],
                    r['lessons_completed'], r['lessons_known'] or None, r['lessons_completion_pct'],
                    r['assignments_submitted'], r['assignments_known'] or None]
                self.assertEqual([c.value for c in cells[:-1]], ['N/A' if x is None else x for x in expected])
                duration = cells[-1].value
                self.assertEqual(duration.total_seconds() if isinstance(duration, dt.timedelta) else None, r['verified_attendance_seconds'])
                self.assertNotIn('f', [c.data_type for c in cells])
        response = self.export(kind='csv')
        parsed = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))
        self.assertEqual(parsed[0], list(HEADERS))
        self.assertEqual(len(parsed), 3)
        self.assertEqual(parsed[1][8], '60.0')
        self.assertEqual(parsed[1][-1], '0h 45m')
        # No stale browser snapshot or stored export cache.
        QuizResult.objects.filter(enrollment__learner__source_id=101).update(latest_score=42)
        self.assertEqual(self.workbook().active['I2'].value, 42)

    def test_formula_injection_is_literal_in_excel_and_neutralized_in_csv(self):
        for name in ('=1+1', '+1+1', '-1+1', '@SUM(1)', '\t=1+1', '\r=1+1', '  =1+1'):
            with self.subTest(name=name):
                Learner.objects.filter(source_id=101).update(name=name)
                sheet = self.workbook().active
                row = next(r for r in sheet.iter_rows(min_row=2) if r[0].value == 101)
                self.assertEqual(row[1].data_type, 's')
                self.assertEqual(row[1].value, name.replace('\r', '\n'))  # XML newline normalization
                parsed = list(csv.reader(io.StringIO(self.export(kind='csv').content.decode('utf-8-sig'))))
                self.assertEqual(next(r for r in parsed[1:] if r[0]=='101')[1], "'" + name)

    def test_export_requires_dashboard_auth_and_valid_module_format_selection(self):
        for params in ({'courses':''}, {'courses':'bad'}, {'courses':'999'},
            {'courses':'83334,121642','file_type':'csv'}, {'file_type':'pdf'}):
            response = self.client.get('/api/learners/export', {'courses':'83334','file_type':'xlsx', **params})
            self.assertEqual(response.status_code, 400)
        self.client.logout()
        self.assertIn(self.export().status_code, (401,403))
        other = get_user_model().objects.create_user('synthetic-without-access')
        self.client.force_login(other)
        self.assertEqual(self.export().status_code, 403)
        DashboardAccess.objects.create(user=other)
        self.assertEqual(self.export().status_code, 200)  # existing dashboard authorization, not owner-only

    def test_short_and_zero_verified_durations_stay_numeric_and_unknown_sorts_last(self):
        self.timing()
        LectureTiming.objects.filter(learner_source_id=101, course_source_id=83334).update(duration_microseconds=41_000_000)
        LectureTiming.objects.filter(learner_source_id=102, course_source_id=83334).update(duration_microseconds=0)
        sheet = self.workbook().active
        short = next(r[-1] for r in sheet.iter_rows(min_row=2) if r[0].value == 101)
        zero = next(r[-1] for r in sheet.iter_rows(min_row=2) if r[0].value == 102)
        self.assertAlmostEqual(short.value * 86400, 41)
        self.assertEqual(short.number_format, '"<1m"')
        self.assertEqual(zero.value, dt.timedelta())
        for ordering, expected in (('attendance_hours', [102,101,103]), ('-attendance_hours', [101,102,103])):
            rows = self.client.get('/api/learners', {'ordering':ordering}).json()['results']
            self.assertEqual([r['learner_id'] for r in rows], expected)

    def test_merge_unmerge_archive_restore_are_reflected_without_duplicate_rows(self):
        self.timing()
        def action(kind, ids, primary=None):
            preview = identity.preview(self.owner, {'kind':kind,'learner_ids':ids,'primary_id':primary})
            identity.confirm(self.owner, preview['token'])
        action('merge', [101,102], 101)
        self.assertEqual(set(self.directory(83334)), {101})
        self.assertEqual(self.workbook().active.max_row, 2)
        row = self.directory(83334)[101]
        self.assertEqual((row['lessons_completed'],row['lessons_known']), (4,4))
        self.assertEqual((row['quizzes_attempted'],row['quizzes_known']), (2,2))
        self.assertEqual(row['verified_attendance_seconds'],2700)  # overlapping original identities unioned
        action('archive',[101])
        self.assertEqual(self.workbook().active.max_row, 1)
        action('restore',[101])
        self.assertEqual(self.workbook().active.max_row, 2)
        action('unmerge',[101])
        self.assertEqual(set(self.directory(83334)), {101,102})
        self.assertEqual(self.workbook().active.max_row, 3)

    def test_bulk_queries_and_export_do_not_mutate_academic_or_timing_data(self):
        self.timing()
        before = (list(LectureTiming.objects.values()), list(SessionAttendance.objects.values()), metrics.summary())
        with CaptureQueriesContext(connection) as queries:
            metrics.learner_directory(course_id=83334)
        initial = len(queries)
        course = Course.objects.get(source_id=83334)
        for uid in range(110,115):
            learner = Learner.objects.create(source_id=uid, name=f'Synthetic {uid}')
            Enrollment.objects.create(course=course, learner=learner)
        with CaptureQueriesContext(connection) as queries:
            metrics.learner_directory(course_id=83334)
        self.assertEqual(len(queries),initial)
        summary = metrics.summary()
        self.workbook('83334,121642')
        self.assertEqual(list(LectureTiming.objects.values()), before[0])
        self.assertEqual(list(SessionAttendance.objects.values()), before[1])
        self.assertEqual(metrics.summary(), summary)

    def test_short_zero_and_unknown_duration_labels(self):
        self.assertEqual([attendance_label(s) for s in (None,0,41,90061)], ['N/A','0h 0m','<1m','25h 1m'])

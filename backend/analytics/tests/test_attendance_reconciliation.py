"""Synthetic boundary, evidence-conflict and whole-dashboard reconciliation checks."""
import io
import json

from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command, CommandError
from django.test import SimpleTestCase, TestCase, override_settings

from analytics import metrics
from analytics.attendance.metrics import AttendanceIndex, aggregate
from analytics.attendance.reconciliation import decide, minimum_minutes
from analytics.management.commands.audit_attendance_reconciliation import reconciliation_report
from analytics.models import Course, Enrollment, Learner, LectureSession, LectureTiming, SessionAttendance, TeamsMeeting
from . import test_lecture_timing as fixture


class ReconciliationRuleTests(SimpleTestCase):
    def test_exact_threshold_short_visits_and_missing_evidence(self):
        for matrix in (True, False, None):
            for micros in (None, 0, 1_799_999_999, 1_800_000_000, 3_600_000_000):
                with self.subTest(matrix=matrix, micros=micros):
                    r = decide(matrix, micros)
                    qualifies = micros is not None and micros >= 1_800_000_000
                    self.assertEqual(r['effective_present'], True if qualifies else matrix)
                    self.assertEqual(r['corrected'], qualifies and matrix is not True)
                    self.assertEqual(r['needs_review'], matrix is True and micros is not None and not qualifies)
                    self.assertEqual(r['conflict'], (matrix is False and qualifies) or r['needs_review'])
                    self.assertTrue(r['reason'])

    def test_configuration_is_explicit_and_invalid_threshold_cannot_qualify_everyone(self):
        with override_settings(ATTENDANCE_RECONCILIATION_MIN_MINUTES=60):
            self.assertIsNone(decide(None, 1_800_000_000)['effective_present'])
        for value in (0, -1, 1441, True, 30.5):
            with self.subTest(value=value), override_settings(ATTENDANCE_RECONCILIATION_MIN_MINUTES=value):
                with self.assertRaises(ImproperlyConfigured):minimum_minutes()


class AttendanceReconciliationTests(TestCase):
    def setUp(self):
        self.f = fixture.LectureTimingTests(methodName='test_command_outputs_aggregate_counts_only')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def matrix(self, uid=101):
        return metrics.module_matrix(Enrollment.objects.get(learner=self.f.learners[uid], course=self.f.course))['attendance']

    def test_raw_matrix_and_timing_stay_intact_all_views_use_effective_presence(self):
        raw = list(SessionAttendance.objects.order_by('id').values())
        self.f.run_import()
        timing = list(LectureTiming.objects.order_by('id').values())
        index = AttendanceIndex()
        # Learner 102 has exactly 30 merged minutes despite an original matrix absence.
        row = self.matrix(102)['sessions'][0]
        self.assertEqual(row['status'], 'attended')
        self.assertEqual(row['reconciliation']['reason_code'], 'timing_over_matrix_absence')
        self.assertEqual(row['reconciliation']['matrix_status'], 'absent')
        self.assertEqual(row['learners_attended'], 2)
        summary = metrics.summary()['kpis']['attendance']
        chart = metrics.charts()['module_progress'][0]['attendance']
        self.assertEqual(summary, chart)
        self.assertEqual((summary['attended'], summary['expected'], summary['pct']), (2, 2, 100.0))
        for uid in (101, 102, 103):
            m = self.matrix(uid)
            present = sum(s['status'] == 'attended' for s in m['sessions'])
            known = sum(s['status'] in ('attended', 'absent') for s in m['sessions'])
            self.assertEqual(m['pct'], round(100*present/known, 1) if known else None)
            detail = metrics.learner_detail(self.f.learners[uid])
            directory = next(r for r in metrics.learner_directory() if r['learner_id'] == uid)
            self.assertEqual(detail['summary']['attendance'], directory['attendance'])
        self.assertEqual(raw, list(SessionAttendance.objects.order_by('id').values()))
        self.assertEqual(timing, list(LectureTiming.objects.order_by('id').values()))

    def test_overlap_duplicate_intervals_and_gaps_cannot_inflate_threshold(self):
        intervals = [('2026-01-05T09:00:00Z','2026-01-05T09:12:00Z')]*3 + [('2026-01-05T09:08:00Z','2026-01-05T09:20:00Z'), ('2026-01-05T09:40:00Z','2026-01-05T09:49:00Z')]
        extra = ('R1', 'short-new', 'Fictional', 'learner103@example.invalid', 'Presenter', intervals)
        self.f.write_source(participants=[*fixture.PARTICIPANTS, extra])
        self.f.run_import()
        row = self.matrix(103)['sessions'][0]
        self.assertEqual(row['timing']['duration_seconds'], 29*60)
        self.assertEqual(row['status'], 'unavailable')
        self.assertEqual(row['reconciliation']['reason_code'], 'short_timing_unknown')
        intervals[-1] = ('2026-01-05T09:40:00Z','2026-01-05T09:50:00Z')
        self.f.write_source(participants=[*fixture.PARTICIPANTS, extra])
        self.f.run_import()
        row = self.matrix(103)['sessions'][0]
        self.assertEqual(row['timing']['duration_seconds'], 30*60)
        self.assertEqual(row['status'], 'attended')
        self.assertEqual(row['reconciliation']['reason_code'], 'timing_fills_missing')
        self.assertFalse(SessionAttendance.objects.filter(learner_source_id=103).exists())

    def test_short_matrix_presence_flagged_and_missing_timing_never_creates_absence(self):
        SessionAttendance.objects.create(session=self.f.sessions['2026-01-07'], learner_source_id=101, attended=True)
        self.f.run_import()
        row = self.matrix()['sessions'][1]
        self.assertTrue(row['timing']['available'])
        self.assertEqual(row['status'], 'attended')
        self.assertTrue(row['reconciliation']['needs_review'])
        self.assertEqual(self.matrix(103)['sessions'][1]['status'], 'unavailable')
        # Missing report data does not discard explicit matrix presence.
        LectureTiming.objects.filter(session_date='2026-01-07').update(status='date_review')
        row = self.matrix()['sessions'][1]
        self.assertEqual(row['status'], 'attended')
        self.assertEqual(row['reconciliation']['reason_code'], 'matrix_present_no_timing')

    def test_off_calendar_unapproved_dates_inactive_runs_and_non_enrollments_never_correct(self):
        self.f.run_import()
        rec = self.f.record(102)
        for state in ('off_calendar','ambiguous_date','date_review','boundary_review'):
            LectureTiming.objects.filter(pk=rec.pk).update(status=state)
            self.assertEqual(self.matrix(102)['sessions'][0]['status'], 'absent')
        LectureTiming.objects.filter(pk=rec.pk).update(status='verified')
        Enrollment.objects.filter(learner=self.f.learners[102]).delete()
        self.assertNotIn((102,self.f.sessions['2026-01-05'].pk), AttendanceIndex().records)

    def test_correct_module_and_unique_exact_date_are_required(self):
        self.f.run_import()
        course = Course.objects.create(source_id=9002, title='Other synthetic course')
        Enrollment.objects.create(course=course, learner=self.f.learners[102])
        meeting = TeamsMeeting.objects.create(meeting_id='other-matrix', course_source_id=9002, evidence_source='attendance_matrix')
        session = LectureSession.objects.create(meeting=meeting, session_date='2026-01-05', delivered=True, report_count=1, verified_learners=1)
        self.assertIsNone(AttendanceIndex().decisions[(102,session.id)]['effective_present'])
        duplicate = TeamsMeeting.objects.create(meeting_id='ambiguous-matrix', course_source_id=9001, evidence_source='attendance_matrix')
        LectureSession.objects.create(meeting=duplicate, session_date='2026-01-05', delivered=True, report_count=1, verified_learners=1)
        self.assertEqual(self.matrix(102)['sessions'][0]['status'], 'absent')

    def test_repeat_import_and_private_ledger_are_idempotent_and_keep_reason_history(self):
        self.f.run_import()
        first = reconciliation_report()
        self.assertFalse(self.f.run_import()['changed'])
        self.assertEqual(reconciliation_report(), first)
        target = self.f.root/'ledger.json'
        out = io.StringIO()
        call_command('audit_attendance_reconciliation', output=str(target), stdout=out)
        self.assertTrue(json.loads(out.getvalue())['changed'])
        out = io.StringIO()
        call_command('audit_attendance_reconciliation', output=str(target), stdout=out)
        self.assertFalse(json.loads(out.getvalue())['changed'])
        self.assertEqual(len(json.loads(target.read_text())['observations']), 6)
        with override_settings(ATTENDANCE_RECONCILIATION_MIN_MINUTES=60):
            with self.assertRaises(CommandError):call_command('audit_attendance_reconciliation', output=str(target), stdout=io.StringIO())

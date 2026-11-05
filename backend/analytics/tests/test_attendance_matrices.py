import datetime as dt
import hashlib
import tempfile
from pathlib import Path

import openpyxl
from django.test import TestCase, override_settings

from analytics.attendance.importer import run_attendance_import
from analytics.attendance.matrices import SOURCES, header_date
from analytics.attendance.metrics import AttendanceIndex, aggregate
from analytics.models import Course, Enrollment, Learner, LearnerIdentity, LectureSession, SessionAttendance


class AuthoritativeMatrixTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings_override = override_settings(ATTENDANCE_DIRECTORY=self.root, TEAMS_ALIASES_PATH=self.root/'absent.csv')
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        for uid in (101, 102, 103):
            learner = Learner.objects.create(source_id=uid, name=f'Fictional {uid}', email=f'{uid}@example.invalid')
            for cid in SOURCES:
                course, _ = Course.objects.get_or_create(source_id=cid, defaults={'title': f'Module {cid}'})
                Enrollment.objects.create(learner=learner, course=course)
        LearnerIdentity.objects.create(source_id=104, canonical_source_id=101, name='Alias', email='alias@example.invalid')
        for cid, (filename, sheet) in SOURCES.items():
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = sheet
            ws.append(['Name', 'Email', dt.datetime(2026, 5, 10), '05/10/2026 (act)'])
            ws.append(['Name', 'Email', 'Attendance', 'Activity'])
            ws.append(['Fictional 101', '101@example.invalid', 1, 0.8])
            ws.append(['Alias', 'alias@example.invalid', 0, 0.9])
            ws.append(['Fictional 102', '102@example.invalid', 0, 1])
            ws.append(['Fictional 103', '103@example.invalid', None, 1])
            wb.save(self.root / filename)
            wb.close()

    def test_dates_use_companion_and_template_convention(self):
        self.assertEqual(header_date(dt.datetime(2026, 5, 10)), dt.date(2026, 10, 5))
        self.assertEqual(header_date('16/9/2026'), dt.date(2026, 9, 16))
        self.assertEqual(header_date(dt.datetime(2026, 12, 10), '21/10/2026'), dt.date(2026, 10, 12))

    def test_binary_evidence_unknowns_alias_union_and_idempotency(self):
        hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.glob('*.xlsx')}
        run = run_attendance_import()
        self.assertEqual(run.status, 'success', run.errors)
        index = AttendanceIndex()
        a = aggregate(index, [(u,c) for u in (101,102,103) for c in SOURCES])
        self.assertEqual((a['attended'], a['expected'], a['pct']), (4, 8, 50))
        self.assertEqual(a['possible_learner_sessions'], 12)
        self.assertEqual(a['missing_learner_sessions'], 4)
        self.assertIsNone(a['live_seconds'])
        self.assertEqual(SessionAttendance.objects.count(), 8)
        self.assertTrue(index.enrollment(101,154268)['available'])
        self.assertFalse(index.enrollment(103,154268)['available'])
        self.assertEqual(index.sessions_for(102,154268)[0]['status'], 'absent')
        self.assertEqual(index.sessions_for(103,154268)[0]['status'], 'unavailable')
        again = run_attendance_import()
        self.assertFalse(again.changed)
        self.assertEqual(hashes, {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.glob('*.xlsx')})

    def test_missing_authoritative_file_preserves_all_previous_data(self):
        self.assertEqual(run_attendance_import().status, 'success')
        snapshot = list(SessionAttendance.objects.values_list('pk', 'attended'))
        (self.root / SOURCES[83334][0]).unlink()
        self.assertEqual(run_attendance_import().status, 'failed')
        self.assertEqual(snapshot, list(SessionAttendance.objects.values_list('pk', 'attended')))
        self.assertEqual(LectureSession.objects.count(), 4)

    def test_unregistered_exact_email_only_supplements_and_deduplicates(self):
        path = self.root / SOURCES[135945][0]
        wb = openpyxl.load_workbook(path)
        ws = wb.create_sheet('Unregistered')
        ws.append(['Name', 'present', 'Date', 'guessedMatch', 'AttendanceEmail'])
        ws.append(['Unknown display name', 1, dt.datetime(2026,10,5), None, '102@example.invalid'])
        ws.append(['Fictional 103', 1, dt.datetime(2026,10,5), 'Fictional 103', 'unverified@example.invalid'])
        wb.save(path)
        wb.close()
        run = run_attendance_import()
        self.assertEqual(run.status, 'success', run.errors)
        self.assertEqual(AttendanceIndex().enrollment(102,135945)['pct'], 100)
        self.assertFalse(AttendanceIndex().enrollment(103,135945)['available'])

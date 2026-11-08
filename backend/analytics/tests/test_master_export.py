"""Synthetic workbook transport and export tests; no cloud calls or private fixtures."""
import copy
import datetime as dt
import io
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from unittest.mock import Mock

import openpyxl
from django.core.management import call_command, CommandError
from django.test import SimpleTestCase, TestCase, override_settings

from analytics.attendance.master_snapshot import (HEADERS, REVIEW_MACHINE_COLUMNS, ExportError,
    build_snapshot, digest, encoded, save_snapshot)
from analytics.attendance.master_writer import (GoogleSheets, Publisher, SheetsFailure, normalized,
    OWNER_KEY, MAX_BATCH_BYTES)
from analytics.attendance.matrices import SOURCES
from analytics.course_labels import COURSE_DISPLAY_NAMES
from analytics.management.commands.export_master_attendance import validate_destination
from analytics.models import (Course, Enrollment, Learner, LectureSession, LectureTiming,
    SessionAttendance, TeamsMeeting, TimingImport, TeamsDiscoveredSeries, TeamsOccurrence,
    TeamsOccurrenceAttendance)


class FakeSheets:
    def __init__(self):
        self.spreadsheet_id = 'approved-synthetic-copy'
        self.identity = 'writer@example.invalid'
        self.remote = {'sheets': [], 'values': {}}
        self.cells = {}; self.batches = []; self.failures = []; self.reads = 0
        for i in range(15):
            title = f'LMS existing {i}'
            self.remote['sheets'].append({'properties': {'sheetId': i+1, 'title': title, 'index': i,
                'gridProperties': {'rowCount': 50, 'columnCount': 30}}})
            self.cells[title] = [['Existing formula', '=1+1'], ['=must-stay-literal', i]]

    def read(self):
        self.reads += 1
        self.remote['values'] = {n: copy.deepcopy(self.cells[n]) for n in self.cells if n in HEADERS}
        return copy.deepcopy(self.remote)

    def batch(self, body):
        failure = self.failures.pop(0) if self.failures else None
        if failure == 'before': raise SheetsFailure(429, True)
        if failure == 'denied': raise SheetsFailure(403)
        self.batches.append(copy.deepcopy(body))
        # Simulate Sheets' atomic request: reject before applying malformed requests.
        for req in body['requests']:
            if 'addSheet' in req:
                p = req['addSheet']['properties']
                if any(s['properties']['title'] == p['title'] for s in self.remote['sheets']): raise SheetsFailure(400)
        for req in body['requests']:
            kind, value = next(iter(req.items()))
            if kind == 'addSheet':
                p = copy.deepcopy(value['properties']); p['index'] = len(self.remote['sheets'])
                self.remote['sheets'].append({'properties': p, 'developerMetadata': [], 'protectedRanges': []})
                self.cells[p['title']] = []
                continue
            sid = value.get('sheetId') or value.get('start', {}).get('sheetId') or value.get('range', {}).get('sheetId') or value.get('developerMetadata', {}).get('location', {}).get('sheetId') or value.get('protectedRange', {}).get('range', {}).get('sheetId')
            sheet = next(s for s in self.remote['sheets'] if s['properties']['sheetId'] == sid)
            title = sheet['properties']['title']
            assert title in HEADERS, 'attempted mutation of an existing LMS tab'
            if kind == 'createDeveloperMetadata': sheet['developerMetadata'].append(value['developerMetadata'])
            elif kind == 'addProtectedRange': sheet['protectedRanges'].append(value['protectedRange'])
            elif kind == 'appendDimension': sheet['properties']['gridProperties']['rowCount'] += value['length']
            elif kind == 'updateCells':
                start = value['start'].get('rowIndex', 0); column = value['start'].get('columnIndex', 0)
                while len(self.cells[title]) < start+len(value['rows']): self.cells[title].append([])
                for i, row in enumerate(value['rows'], start):
                    for j, c in enumerate(row['values'], column):
                        while len(self.cells[title][i]) <= j: self.cells[title][i].append('')
                        v = c.get('userEnteredValue', {})
                        self.cells[title][i][j] = next(iter(v.values()), '')
            elif kind != 'repeatCell': raise AssertionError(kind)
        if failure == 'after': raise SheetsFailure(0, True)


def synthetic_snapshot(number=1):
    tables = {n: [] for n in HEADERS}
    for tab, columns in HEADERS.items():
        if tab == 'AT_SyncLog': continue
        row = {c: '' for c in columns}; row[columns[0]] = 'stable-'+tab
        if tab == 'AT_Review': row['State'] = 'Open'; row['ReasonCode'] = 'short_visit'
        elif tab == 'AT_Attendance': row['EffectiveStatus'] = 'Present'; row['DurationSeconds'] = 1800+number
        elif tab == 'AT_Learners': row['LearnerName'] = '=HYPERLINK("https://example.invalid")'
        tables[tab] = [row]
    return {'schema_version': 1, 'policy_version': 'synthetic', 'source_as_of': '2026-01-01T00:00:00Z',
            'generation': digest([tables, number]), 'tables': tables, 'totals': {'attendance_rows': 1, 'review_rows': 1}}


class SheetsPublisherTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.client = FakeSheets(); self.root = Path(self.tmp.name)
        self.publisher = Publisher(self.client, self.root, sleep=lambda _: None)
        self.snapshot = synthetic_snapshot()

    def publish(self, snapshot=None):
        return self.publisher.publish(snapshot or self.snapshot, initialize=True)

    def test_atomic_initialization_preserves_all_existing_tabs_and_typed_strings(self):
        before = copy.deepcopy(self.client.cells); structure = copy.deepcopy(self.client.remote['sheets'])
        self.assertEqual(self.publish()['status'], 'published')
        self.assertEqual(len(self.client.remote['sheets']), 23)
        self.assertEqual(self.client.remote['sheets'][:15], structure)
        self.assertEqual({k: self.client.cells[k] for k in before}, before)
        self.assertEqual(len(self.client.batches), 1)
        values = [c for r in self.client.batches[0]['requests'] if 'updateCells' in r for row in r['updateCells']['rows'] for c in row['values']]
        self.assertFalse(any('formulaValue' in c.get('userEnteredValue', {}) for c in values))
        self.assertTrue(any(c.get('userEnteredValue', {}).get('stringValue', '').startswith('=HYPERLINK') for c in values))

    def test_repeat_export_no_request_no_duplicate_rows_or_log(self):
        self.publish(); original = copy.deepcopy(self.client.cells)
        self.assertEqual(self.publish()['status'], 'unchanged')
        self.assertEqual(self.client.cells, original); self.assertEqual(len(self.client.batches), 1)

    def test_manual_review_decisions_preserved_across_update_and_resolved_issue(self):
        self.publish(); raw = self.client.cells['AT_Review'][1]
        while len(raw) < len(HEADERS['AT_Review']): raw.append('')
        manual = ['owner note', 'reviewer', '2026-01-02', 'private-evidence']
        raw[REVIEW_MACHINE_COLUMNS:] = manual
        new = synthetic_snapshot(2); new['tables']['AT_Review'] = []
        self.publish(new)
        self.assertEqual(self.client.cells['AT_Review'][1][REVIEW_MACHINE_COLUMNS:], manual)
        self.assertEqual(self.client.cells['AT_Review'][1][9], 'NoLongerActive')

    def test_machine_cell_tamper_duplicate_key_and_header_change_stop_without_write(self):
        self.publish(); original = copy.deepcopy(self.client.cells)
        for changed in ('cell', 'duplicate', 'header'):
            self.client.cells = copy.deepcopy(original)
            if changed == 'cell': self.client.cells['AT_Attendance'][1][9] = 'Absent'
            elif changed == 'duplicate': self.client.cells['AT_Attendance'].append(copy.deepcopy(self.client.cells['AT_Attendance'][1]))
            else: self.client.cells['AT_Attendance'][0][0] = 'Changed'
            with self.assertRaises(ExportError): self.publish(synthetic_snapshot(2))
            self.assertEqual(len(self.client.batches), 1)

    def test_missing_marker_protection_and_lms_reorder_stop(self):
        self.publish(); initial = copy.deepcopy(self.client.remote)
        for changed in ('marker', 'protection', 'lms'):
            self.client.remote = copy.deepcopy(initial)
            if changed == 'marker': self.client.remote['sheets'][15]['developerMetadata'] = []
            elif changed == 'protection': self.client.remote['sheets'][15]['protectedRanges'] = []
            else: self.client.remote['sheets'][0]['properties']['index'] = 99
            with self.assertRaises(ExportError): self.publish(synthetic_snapshot(2))
            self.assertEqual(len(self.client.batches), 1)

    def test_uncertain_initial_commit_reconciles_without_duplicate_creation(self):
        self.client.failures = ['after']
        self.publish(); self.assertEqual(len(self.client.batches), 1)
        self.assertFalse(self.publisher.pending_path.exists())

    def test_retry_before_commit_and_after_commit_on_existing_generation(self):
        self.publish(); self.client.failures = ['before', 'after']
        self.publish(synthetic_snapshot(2))
        self.assertEqual(len(self.client.batches), 2)
        self.assertEqual(len(self.client.cells['AT_SyncLog']), 3)

    def test_permission_failure_keeps_previous_generation_and_private_pending(self):
        self.publish(); before = copy.deepcopy(self.client.cells); self.client.failures = ['denied']
        with self.assertRaises(SheetsFailure): self.publish(synthetic_snapshot(2))
        self.assertEqual(self.client.cells, before); self.assertTrue(self.publisher.pending_path.exists())
        self.publish(synthetic_snapshot(2)); self.assertFalse(self.publisher.pending_path.exists())

    def test_exhausted_retries_retain_previous_generation(self):
        self.publish(); before = copy.deepcopy(self.client.cells); self.client.failures = ['before'] * 4
        with self.assertRaises(SheetsFailure): self.publish(synthetic_snapshot(2))
        self.assertEqual(self.client.cells, before)

    def test_restart_after_commit_before_state_acknowledgement(self):
        remote = self.client.read(); plan = self.publisher.plan(self.snapshot, remote, None)
        from analytics.attendance.master_snapshot import private_json
        private_json(self.publisher.pending_path, plan); self.client.batch(plan['body'])
        result = Publisher(self.client, self.root).publish(self.snapshot, initialize=True)
        self.assertEqual(result['status'], 'unchanged'); self.assertEqual(len(self.client.batches), 1)

    def test_source_row_deletion_and_unowned_existing_attendance_tab_refused(self):
        self.publish(); new = synthetic_snapshot(2); new['tables']['AT_Attendance'] = []
        with self.assertRaises(ExportError): self.publish(new)
        other = Publisher(self.client, self.root/'new-state')
        with self.assertRaises(ExportError): other.publish(self.snapshot, initialize=True)

    def test_initialization_requires_explicit_flag(self):
        with self.assertRaises(ExportError): self.publisher.publish(self.snapshot)
        self.assertFalse(self.client.batches)

    def test_partial_or_unexpected_write_fails_closed(self):
        def partial(body):
            self.client.remote['sheets'].append({'properties': {'sheetId': 999, 'title': 'AT_Attendance', 'index': 15}})
            raise SheetsFailure(500, True)
        self.client.batch = partial
        with self.assertRaises(ExportError): self.publish()
        self.assertTrue(self.publisher.pending_path.exists())

    def test_large_batch_rejected_before_mutation(self):
        self.snapshot['tables']['AT_Learners'][0]['LearnerName'] = 'x' * MAX_BATCH_BYTES
        with self.assertRaises(ExportError): self.publish()
        self.assertFalse(self.client.batches)

    def test_growth_appends_only_owned_rows_and_keeps_review_columns(self):
        self.publish(); new = synthetic_snapshot(2)
        base = new['tables']['AT_Attendance'][0]
        new['tables']['AT_Attendance'] += [{**base, 'AttendanceKey': f'new-{i}'} for i in range(110)]
        self.publish(new)
        self.assertEqual(len(self.client.cells['AT_Attendance']), 112)
        self.assertEqual(len(self.client.remote['sheets']), 23)


class ExportConfigurationTests(SimpleTestCase):
    @override_settings(MASTER_ATTENDANCE_ENABLED=False)
    def test_disabled_daily_stage_does_not_read_db_or_cloud(self):
        with patch('analytics.management.commands.export_master_attendance.build_snapshot') as build:
            output = io.StringIO(); call_command('export_master_attendance', write=True, if_enabled=True, stdout=output)
            self.assertEqual(json.loads(output.getvalue())['status'], 'disabled'); build.assert_not_called()

    @override_settings(MASTER_ATTENDANCE_ENABLED=True, MASTER_ATTENDANCE_SPREADSHEET_ID='live-master', GOOGLE_SHEETS_SPREADSHEET_ID='live-master')
    def test_live_destination_never_uses_test_bypass_and_requires_publisher_confirmation(self):
        with override_settings(MASTER_ATTENDANCE_TARGET_KIND='test'):
            with self.assertRaises(ExportError): validate_destination()
        with override_settings(MASTER_ATTENDANCE_TARGET_KIND='live', MASTER_ATTENDANCE_LIVE_WRITE_APPROVED=True, MASTER_ATTENDANCE_PUBLISHER_VERIFIED=False):
            with self.assertRaisesMessage(ExportError, 'publisher'): validate_destination()

    def test_existing_reader_scope_is_unchanged(self):
        from analytics.importer.sources import GOOGLE_READONLY_SCOPE
        from analytics.attendance.master_writer import WRITER_SCOPE
        self.assertEqual(GOOGLE_READONLY_SCOPE, 'https://www.googleapis.com/auth/spreadsheets.readonly')
        self.assertNotEqual(WRITER_SCOPE, GOOGLE_READONLY_SCOPE)

    def test_service_order_and_timer(self):
        from django.conf import settings
        service=(settings.PROJECT_ROOT/'deploy/alfanar-teams-discovery.service').read_text()
        timer=(settings.PROJECT_ROOT/'deploy/alfanar-teams-discovery.timer').read_text()
        self.assertLess(service.index('manage.py sync_teams_lectures'), service.index('manage.py export_master_attendance'))
        self.assertIn('--write --if-enabled', service)
        self.assertIn('23:00:00 Africa/Cairo', timer)

    def test_google_transport_retries_reads_and_reconciles_writes_at_publisher_layer(self):
        import httplib2
        from googleapiclient.errors import HttpError
        client=GoogleSheets.__new__(GoogleSheets); client.sleep=Mock()
        request=Mock()
        request.execute.side_effect=[HttpError(httplib2.Response({'status':'429','retry-after':'2'}),b'{}'), {'ok':True}]
        self.assertEqual(client.execute(lambda:request),{'ok':True}); client.sleep.assert_called_with(2)
        request.execute.side_effect=HttpError(httplib2.Response({'status':'503'}),b'private-response-must-not-leak')
        before=request.execute.call_count
        with self.assertRaises(SheetsFailure) as raised:client.execute(lambda:request,write=True)
        self.assertNotIn('private-response',str(raised.exception)); self.assertEqual(request.execute.call_count,before+1)
        request.execute.side_effect=TimeoutError('secret transport value')
        with self.assertRaises(SheetsFailure) as raised:client.execute(lambda:request,write=True)
        self.assertNotIn('secret',str(raised.exception))

    def test_writer_credential_scope_is_explicit_without_changing_reader(self):
        fake=Mock(service_account_email='writer@example.invalid')
        with patch('google.oauth2.service_account.Credentials.from_service_account_file',return_value=fake) as credentials, \
             patch('google_auth_httplib2.AuthorizedHttp'), patch('googleapiclient.discovery.build'):
            client=GoogleSheets('synthetic-credential.json','synthetic-spreadsheet')
        from analytics.attendance.master_writer import WRITER_SCOPE
        credentials.assert_called_once_with('synthetic-credential.json',scopes=[WRITER_SCOPE])
        self.assertEqual(client.identity,'writer@example.invalid')

    def test_lms_capture_keeps_original_cells_and_rejects_missing_tab(self):
        from analytics.attendance.master_writer import LMS_TITLES
        client=GoogleSheets.__new__(GoogleSheets);client.spreadsheet_id='synthetic'
        sheets=[{'properties':{'sheetId':i,'title':name,'index':i},'data':[{'rowData':[{'values':[{'userEnteredValue':{'formulaValue':'=1+1'},'effectiveValue':{'numberValue':2}}]}]}]} for i,name in enumerate(sorted(LMS_TITLES))]
        class API:
            def get(self,**kw):
                if 'ranges' not in kw:return {'sheets':sheets}
                return {'sheets':[next(s for s in sheets if "'"+s['properties']['title']+"'"==kw['ranges'][0])]}
        client.api=API();client.execute=lambda factory:factory()
        with tempfile.TemporaryDirectory() as folder:
            before=client.capture_lms(folder)
            self.assertEqual(len(before),15)
            self.assertEqual(client.capture_lms(None),before)
            sheets[0]['data'][0]['rowData'][0]['values'][0]['userEnteredValue']['formulaValue']='=2+2'
            self.assertNotEqual(client.capture_lms(None),before)
            sheets.pop()
            with self.assertRaises(ExportError):client.capture_lms(None)


class SnapshotTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.root = Path(self.tmp.name)
        self.settings = override_settings(TEAMS_ALIASES_PATH=self.root/'none.csv'); self.settings.enable(); self.addCleanup(self.settings.disable)
        self.learners = [Learner.objects.create(source_id=101+i, name=f'Fictional {i}', email=f'learner{i}@example.invalid') for i in range(3)]
        self.day = dt.date(2026, 1, 5); self.a = dt.datetime(2026, 1, 5, 9, tzinfo=dt.timezone.utc)
        self.run = TimingImport.objects.create(fingerprint='a'*64, source_name='synthetic.xlsx', source_sha256='b'*64,
            policy_sha256='c'*64, registry_sha256='d'*64, calendar_sha256='e'*64, active=True)
        self.sessions = {}
        for cid, (filename, sheet) in SOURCES.items():
            course = Course.objects.create(source_id=cid, title=COURSE_DISPLAY_NAMES[cid])
            for learner in self.learners: Enrollment.objects.create(learner=learner, course=course)
            meeting = TeamsMeeting.objects.create(meeting_id=f'matrix:{cid}', course_source_id=cid, evidence_source='attendance_matrix')
            session = LectureSession.objects.create(meeting=meeting, session_date=self.day, report_count=1, verified_learners=1, delivered=True)
            self.sessions[cid] = session
            wb=openpyxl.Workbook(); ws=wb.active; ws.title=sheet
            ws.append(['Name','Email','05/01/2026']); ws.append(['Name','Email','Attendance'])
            for i, learner in enumerate(self.learners):
                flag = [1,0,None][i]; ws.append([learner.name, learner.email, flag])
                if flag is not None: SessionAttendance.objects.create(session=session, learner_source_id=learner.source_id, attended=bool(flag))
            wb.save(self.root/filename); wb.close()
        self.timing(101, 83334, 600); self.timing(102, 83334, 1800); self.timing(103, 83334, 120)

    def timing(self, uid, cid, seconds, status='verified', day=None):
        return LectureTiming.objects.create(import_run=self.run, learner_source_id=uid, course_source_id=cid,
            session_date=day or self.day, status=status, first_join=self.a, last_leave=self.a+dt.timedelta(seconds=seconds),
            duration_microseconds=seconds*1000000, visit_count=1,
            provenance=[{'source_kind':'microsoft_graph','match_method':'email','join':self.a.isoformat(),'leave':(self.a+dt.timedelta(seconds=seconds)).isoformat()}])

    def build(self): return build_snapshot(strict=False, matrix_directory=self.root)

    def test_preserves_source_status_and_threshold_review_without_inventing_absence(self):
        before=list(SessionAttendance.objects.values()); snap=self.build()
        rows=[r for r in snap['tables']['AT_Attendance'] if r['CourseID']=='83334']
        self.assertEqual([r['EffectiveStatus'] for r in rows], ['Present','Present','Not Available'])
        self.assertEqual([r['MatrixOriginalStatus'] for r in rows], ['Present','Absent','Not Available'])
        self.assertEqual([r['NeedsReview'] for r in rows], [True,False,False])
        self.assertEqual(before,list(SessionAttendance.objects.values()))
        summary=next(r for r in snap['tables']['AT_Summary'] if r['SummaryKey']=='module:83334')
        self.assertEqual((summary['Present'],summary['KnownObservations'],summary['PossibleObservations']), (2,2,3))

    def test_repeat_snapshot_and_private_archive_are_identical(self):
        a=self.build(); b=self.build(); self.assertEqual(a,b)
        p=save_snapshot(a,self.root/'out',self.root)
        self.assertEqual(save_snapshot(b,self.root/'out',self.root),p)
        self.assertEqual(json.loads(p.read_text(encoding='utf-8')),a)

    def test_off_calendar_pending_preserved_but_never_makes_attendance(self):
        self.timing(103,83334,3600,'off_calendar',dt.date(2026,1,7)); snap=self.build()
        self.assertEqual(len(snap['tables']['AT_Attendance']),12)
        pending=[r for r in snap['tables']['AT_Review'] if r['Kind']=='HistoricalTimingPending']
        self.assertEqual(len(pending),1); self.assertEqual(pending[0]['ObservedDate'],'2026-01-07')

    def test_mismatching_matrix_and_baseline_fail_before_publication(self):
        with self.assertRaises(ExportError): build_snapshot(matrix_directory=self.root)
        SessionAttendance.objects.filter(learner_source_id=101).update(attended=False)
        with self.assertRaises(ExportError): self.build()

    def test_future_same_day_occurrences_remain_distinct_and_pending_does_not_count(self):
        series=TeamsDiscoveredSeries.objects.create(organizer_id='synthetic-organizer',meeting_id='synthetic-meeting',course_source_id=83334,subject='Synthetic')
        for i in range(3):
            o=TeamsOccurrence.objects.create(key=str(i)*64,series=series,course_source_id=83334,calendar_uid=f'synthetic-{i}',
                scheduled_start=self.a,scheduled_end=self.a+dt.timedelta(hours=1),starts_at=self.a,ends_at=self.a+dt.timedelta(hours=1),status='verified' if i<2 else 'pending')
            if i<2:TeamsOccurrenceAttendance.objects.create(occurrence=o,learner_source_id=101,first_join=self.a,
                last_leave=self.a+dt.timedelta(minutes=45),duration_microseconds=2700_000_000,visit_count=1,intervals=[])
        snap=self.build(); rows=[r for r in snap['tables']['AT_Attendance'] if r['LectureID'].startswith('auto:')]
        self.assertEqual(len(rows),6); self.assertEqual(len({r['LectureID'] for r in rows}),2)
        self.assertEqual(sum(r['EffectiveStatus']=='Not Available' for r in rows),4)
        self.assertEqual(snap['totals']['historical_decisions'],12)

    def test_undated_slots_are_not_attendance_rows(self):
        snap=self.build(); undated=[r for r in snap['tables']['AT_Lectures'] if r['Status']=='UndatedOfficial']
        self.assertEqual(len(undated),54)
        self.assertTrue(all(r['LectureDate']=='' and not r['IncludeInAttendance'] for r in undated))
        self.assertFalse(any(r['LectureID'].startswith('undated:') for r in snap['tables']['AT_Attendance']))

    def test_utc_and_cairo_duration_keep_real_gap_not_span(self):
        LectureTiming.objects.filter(learner_source_id=102).update(last_leave=self.a+dt.timedelta(hours=2))
        r=next(r for r in self.build()['tables']['AT_Attendance'] if r['LearnerID']=='102' and r['CourseID']=='83334')
        self.assertEqual(r['DurationSeconds'],1800); self.assertEqual(r['DurationDisplay'],'0:30:00')
        self.assertIn('+02:00',r['FirstJoinLocal']); self.assertTrue(r['FirstJoinUTC'].endswith('Z'))

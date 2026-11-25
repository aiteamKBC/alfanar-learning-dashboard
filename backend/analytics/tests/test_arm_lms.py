"""Advanced Research Methods: live LMS read path, using a synthetic snapshot (never the real LMS)."""

import datetime as dt
import io
import json
from unittest import mock

from django.test import override_settings

from analytics import lms_live
from analytics.lms_live import ARM_COURSE_ID, ARM_LEARNER_ID_OFFSET, EXCLUDED_LMS_LEARNER_IDS, ArmSnapshot, LmsUnavailable
from analytics.models import Enrollment, Learner, LessonRecord, SessionAttendance

from .test_metrics_api import ApiTestBase

UTC = dt.timezone.utc
COMPLETED, SCHEDULED = 'OCC-COMPLETED', 'OCC-SCHEDULED'


def snapshot():
    """35-style roster in miniature: one dashboard learner (by email), two LMS-only learners."""
    return ArmSnapshot(
        module={'module_catalogue_id': lms_live.ARM_MODULE_ID, 'title': 'Advanced Research Methods',
                'is_deleted': False, 'deleted_at': None},
        roster=[{'id': 501, 'full_name': 'Lms Learner Present', 'email': 'present@example.invalid', 'email_normalized': 'present@example.invalid'},
                {'id': 502, 'full_name': 'Lms Learner Absent', 'email': 'absent@example.invalid', 'email_normalized': 'absent@example.invalid'},
                {'id': 503, 'full_name': 'Different LMS Name', 'email': 'Learner101@example.invalid', 'email_normalized': 'learner101@example.invalid'}],
        components=[
            {'id': 'C-V1', 'type': 'video', 'title': 'P1:', 'week_number': 1, 'week_order': 1, 'display_order': 1},
            {'id': 'C-V2', 'type': 'video', 'title': 'P2:', 'week_number': 1, 'week_order': 1, 'display_order': 2},
            {'id': 'C-A1', 'type': 'assignment', 'title': 'Assignment', 'week_number': 1, 'week_order': 1, 'display_order': 3},
            {'id': 'C-L1', 'type': 'live_session', 'title': 'Live session 1', 'week_number': 1, 'week_order': 1, 'display_order': 4},
        ],
        quiz_links=[],
        entries=[
            {'id': 1, 'learner_id': 503, 'component_ref': 'C-V1', 'curriculum_component_ref': None, 'quiz_ref': None,
             'kind': 'video', 'accepted': True, 'attempt': None, 'grade': None, 'passed': None,
             'occurred_at': dt.datetime(2026, 11, 24, 12, 56, tzinfo=UTC)},
            {'id': 2, 'learner_id': 503, 'component_ref': 'C-V2', 'curriculum_component_ref': None, 'quiz_ref': None,
             'kind': 'video', 'accepted': False, 'attempt': None, 'grade': None, 'passed': None,
             'occurred_at': dt.datetime(2026, 11, 24, 13, 0, tzinfo=UTC)},
        ],
        submissions=[{'id': 'S1', 'learner_id': 501, 'activity_id': 'C-A1', 'status': 'draft',
                      'submitted_at': dt.datetime(2026, 11, 24, 9, 0, tzinfo=UTC)}],
        occurrences=[
            {'id': COMPLETED, 'session_number': 1, 'scheduled_start': dt.datetime(2026, 11, 23, 10, 0),
             'scheduled_end': dt.datetime(2026, 11, 23, 12, 0), 'status': 'completed', 'timezone': 'GMT Standard Time',
             'actual_end': dt.datetime(2026, 11, 23, 12, 1),
             'attendance_finalized_at': dt.datetime(2026, 11, 23, 14, 5)},
            {'id': SCHEDULED, 'session_number': 2, 'scheduled_start': dt.datetime(2026, 11, 25, 10, 0),
             'scheduled_end': dt.datetime(2026, 11, 25, 12, 0), 'status': 'scheduled', 'timezone': 'GMT Standard Time'},
        ],
        attendance=[
            {'occurrence_id': COMPLETED, 'learner_profile_id': 501, 'attendance_status': 'present', 'attended_seconds': 6700,
             'first_join_at': dt.datetime(2026, 11, 23, 9, 59, tzinfo=UTC), 'last_leave_at': dt.datetime(2026, 11, 23, 12, 1, tzinfo=UTC)},
            {'occurrence_id': COMPLETED, 'learner_profile_id': 502, 'attendance_status': 'absent', 'attended_seconds': 0,
             'first_join_at': None, 'last_leave_at': None},
            # A row on a lecture that is not completed is never counted as delivered attendance.
            {'occurrence_id': SCHEDULED, 'learner_profile_id': 501, 'attendance_status': 'present', 'attended_seconds': 60,
             'first_join_at': None, 'last_leave_at': None},
        ],
    )


class ArmTests(ApiTestBase):
    databases = {'default', 'lecture_library'}

    def setUp(self):
        super().setUp()
        self.live = True
        patches = [mock.patch('analytics.api.views.configured', side_effect=lambda: self.live),
                   mock.patch('analytics.api.views.load_snapshot', side_effect=self.fake_load)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.calls = 0
        self.fail_lms = False

    def fake_load(self):
        self.calls += 1
        if self.fail_lms:
            raise LmsUnavailable('The LMS is currently unavailable.')
        return snapshot()

    def get(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.content[:300])
        return response.json()

    def without_lms(self, url):
        self.live = False
        try:
            return self.get(url)
        finally:
            self.live = True

    # --- module identity and filters ----------------------------------------------------

    def test_module_listed_after_existing_modules(self):
        courses = self.get('/api/filters')['courses']
        self.assertEqual(courses[-1], {'course_id': ARM_COURSE_ID, 'title': 'Advanced Research Methods'})
        self.assertEqual(self.without_lms('/api/filters')['courses'], courses[:-1])

    # --- roster and attendance exactly as stored ------------------------------------------

    def test_summary_uses_lms_roster_and_attendance_statuses(self):
        k = self.get(f'/api/dashboard/summary?course={ARM_COURSE_ID}')['kpis']
        self.assertEqual(k['unique_learners']['value'], 3)
        self.assertEqual(k['enrollments']['value'], 3)
        # The LMS stores no module progress: approved ARM display fallback.
        self.assertEqual((k['mean_progress']['value'], k['mean_progress']['median'], k['mean_progress']['n']), (0.0, 0.0, 3))
        att = k['attendance']
        self.assertEqual((att['attended'], att['expected'], att['pct']), (1, 2, 50.0))
        self.assertEqual((att['delivered_sessions'], att['possible_learner_sessions']), (1, 3))
        self.assertEqual(att['live_seconds'], 6700)
        self.assertEqual(k['materials_completed'], {'value': 1, 'denominator': 6, 'pct': 16.7})  # accepted entries only
        self.assertEqual(k['submissions']['value'], 0)  # drafts are not submissions
        self.assertEqual(k['submissions']['by_status'], {'draft': 1})
        self.assertEqual(k['quiz_completion']['denominator'], 0)

    def test_scheduled_lectures_are_not_delivered(self):
        module = self.get(f'/api/dashboard/charts?course={ARM_COURSE_ID}')['module_progress'][0]
        self.assertEqual(module['delivered_lectures']['count'], 1)
        self.assertEqual(module['delivered_sessions'], 1)

    def provisional_snapshot(self):
        value = self.media_snapshot()
        today = value.occurrences[1]
        today.update(status='completed', actual_end=dt.datetime(2026, 11, 25, 10, 5),
                     attendance_finalized_at=dt.datetime(2026, 11, 25, 10, 11))
        # A recorded short fragment must not make the full scheduled lecture final.
        value.artifacts.append({**value.artifacts[0], 'id': 'ART-LIVE-FRAGMENT', 'occurrence_id': SCHEDULED})
        return value

    def test_ongoing_lecture_marked_completed_is_excluded_everywhere(self):
        from analytics.arm import Arm
        from analytics.lectures.lms_media import resource_id
        now = dt.datetime(2026, 11, 25, 10, 25, tzinfo=UTC)
        with mock.patch('analytics.api.views.load_snapshot', side_effect=self.provisional_snapshot), \
                mock.patch('analytics.arm.timezone.now', return_value=now):
            today_id = Arm.lecture_id(SCHEDULED)
            for path in ('/api/lectures', f'/api/lectures?course={ARM_COURSE_ID}'):
                self.assertNotIn(today_id, [r['id'] for r in self.get(path)['results']])
            self.assertEqual(self.client.get('/api/lectures/' + today_id).status_code, 404)
            self.assertEqual(self.client.get(f'/api/lectures/{today_id}/resources/{resource_id("ART-LIVE-FRAGMENT")}').status_code, 404)
            summary = self.get(f'/api/dashboard/summary?course={ARM_COURSE_ID}')
            self.assertEqual(summary['kpis']['attendance']['delivered_sessions'], 1)
            matrix = self.get(f'/api/learners/{ARM_LEARNER_ID_OFFSET + 501}/courses/{ARM_COURSE_ID}/matrix')
            self.assertEqual(len(matrix['attendance']['sessions']), 1)

    def test_finished_lecture_waits_for_grace_and_fresh_attendance(self):
        from analytics.arm import Arm
        value = self.provisional_snapshot()
        today = value.occurrences[1]
        after_grace = dt.datetime(2026, 11, 25, 14, 1, tzinfo=UTC)
        # The fragment's pre-end calculation remains provisional even much later.
        self.assertNotIn(SCHEDULED, [s['id'] for s in Arm(value, now=after_grace).sessions])
        today['attendance_finalized_at'] = dt.datetime(2026, 11, 25, 12, 10)
        self.assertNotIn(SCHEDULED, [s['id'] for s in Arm(value, now=dt.datetime(2026, 11, 25, 13, 59, 59, tzinfo=UTC)).sessions])
        ready = Arm(value, now=dt.datetime(2026, 11, 25, 14, 0, tzinfo=UTC))
        self.assertIn(SCHEDULED, [s['id'] for s in ready.sessions])
        # Publication never changes the source attendance classification/duration.
        detail = ready.lecture_detail(Arm.lecture_id(SCHEDULED))
        self.assertEqual(detail['participants'][0]['attendance'], 'present')
        self.assertEqual(detail['participants'][0]['duration_seconds'], 60)

    def test_actual_overrun_extends_publication_wait_and_requires_freshness(self):
        from analytics.arm import Arm
        value = self.provisional_snapshot()
        today = value.occurrences[1]
        today.update(actual_end=dt.datetime(2026, 11, 25, 12, 39),
                     attendance_finalized_at=dt.datetime(2026, 11, 25, 12, 40))
        self.assertNotIn(SCHEDULED, [s['id'] for s in Arm(value, now=dt.datetime(2026, 11, 25, 14, 38, 59, tzinfo=UTC)).sessions])
        self.assertIn(SCHEDULED, [s['id'] for s in Arm(value, now=dt.datetime(2026, 11, 25, 14, 39, tzinfo=UTC)).sessions])
        today['attendance_finalized_at'] = dt.datetime(2026, 11, 25, 12, 20)
        self.assertNotIn(SCHEDULED, [s['id'] for s in Arm(value, now=dt.datetime(2026, 11, 25, 16, 0, tzinfo=UTC)).sessions])

    def test_missing_finish_or_refresh_cannot_publish_absence(self):
        from analytics.arm import Arm
        for field in ('scheduled_start', 'scheduled_end', 'attendance_finalized_at'):
            with self.subTest(field=field):
                value = self.provisional_snapshot()
                value.occurrences[1][field] = None
                self.assertNotIn(SCHEDULED, [s['id'] for s in Arm(value, now=dt.datetime(2026, 11, 26, tzinfo=UTC)).sessions])

    def test_lms_publication_window_uses_session_timezone_and_dst(self):
        from analytics.arm import Arm
        value = self.provisional_snapshot()
        today = value.occurrences[1]
        today.update(scheduled_start=dt.datetime(2026, 7, 1, 10), scheduled_end=dt.datetime(2026, 7, 1, 12),
                     actual_end=None, attendance_finalized_at=dt.datetime(2026, 7, 1, 11, 10))
        # London is UTC+1 in July: noon ends at 11 UTC, publication at 13 UTC.
        self.assertNotIn(SCHEDULED, [s['id'] for s in Arm(value, now=dt.datetime(2026, 7, 1, 12, 59, 59, tzinfo=UTC)).sessions])
        self.assertIn(SCHEDULED, [s['id'] for s in Arm(value, now=dt.datetime(2026, 7, 1, 13, 0, tzinfo=UTC)).sessions])

    def test_directory_rows_keep_missing_records_unknown(self):
        rows = {r['name']: r for r in self.get(f'/api/learners?course={ARM_COURSE_ID}&page_size=100')['results']}
        self.assertEqual(len(rows), 3)
        present, absent = rows['Lms Learner Present'], rows['Lms Learner Absent']
        dashboard = rows['Test Learner One']  # matched dashboard learner keeps the dashboard identity
        self.assertEqual(dashboard['learner_id'], 101)
        self.assertEqual(present['learner_id'], ARM_LEARNER_ID_OFFSET + 501)
        self.assertEqual((present['attendance']['attended'], present['attendance']['expected']), (1, 1))
        self.assertEqual(present['verified_attendance_seconds'], 6700)
        self.assertEqual((absent['attendance']['attended'], absent['attendance']['absent']), (0, 1))
        self.assertEqual((dashboard['attendance']['known'], dashboard['attendance']['unknown']), (0, 1))
        # Approved: no record for a completed lecture is reported as 0 / 1 (0%).
        self.assertEqual((dashboard['attendance']['attended'], dashboard['attendance']['expected'],
                          dashboard['attendance']['pct']), (0, 1, 0.0))
        self.assertIsNone(dashboard['verified_attendance_seconds'])
        self.assertEqual(dashboard['lessons_completed'], 1)

    def test_progress_display_fallback_is_zero_only_for_arm(self):
        arm_charts = self.get(f'/api/dashboard/charts?course={ARM_COURSE_ID}')
        self.assertEqual({str(r['progress'][str(ARM_COURSE_ID)]) for r in arm_charts['learner_module_matrix']['rows']}, {'0.0'})
        self.assertEqual(arm_charts['module_progress'][0]['mean_progress'], 0.0)
        all_charts = self.get('/api/dashboard/charts')
        enrolled = [r for r in all_charts['learner_module_matrix']['rows'] if str(ARM_COURSE_ID) in r['progress']]
        self.assertEqual(len(enrolled), 3)
        self.assertEqual({r['progress'][str(ARM_COURSE_ID)] for r in enrolled}, {0.0})
        not_enrolled = [r for r in all_charts['learner_module_matrix']['rows'] if str(ARM_COURSE_ID) not in r['progress']]
        self.assertTrue(not_enrolled)  # blank cells stay blank
        old = {r['learner_id']: r for r in self.without_lms('/api/dashboard/charts')['learner_module_matrix']['rows']}
        for row in enrolled:
            if row['learner_id'] in old:  # existing modules' cells unchanged
                self.assertEqual({k: v for k, v in row['progress'].items() if k != str(ARM_COURSE_ID)}, old[row['learner_id']]['progress'])
        # Cross-module averages exclude the fallback; ARM views and ARM-only learners show 0%.
        self.assertEqual(self.get('/api/learners/101')['summary']['mean_progress'],
                         self.without_lms('/api/learners/101')['summary']['mean_progress'])
        detail = self.get('/api/learners/101')
        self.assertEqual(next(c for c in detail['courses'] if c['course_id'] == ARM_COURSE_ID)['progress_pct'], 0.0)
        self.assertEqual(self.get(f'/api/learners/{ARM_LEARNER_ID_OFFSET + 501}')['summary']['mean_progress'], 0.0)
        arm_rows = self.get(f'/api/learners?course={ARM_COURSE_ID}&page_size=100')['results']
        self.assertEqual({r['mean_progress'] for r in arm_rows}, {0.0})
        matrix = self.get(f'/api/learners/{ARM_LEARNER_ID_OFFSET + 501}/courses/{ARM_COURSE_ID}/matrix')
        self.assertEqual(matrix['course']['progress_pct'], 0.0)

    def test_excluded_lms_learners_never_appear(self):
        def with_excluded():
            snap = snapshot()
            for n, lms_id in enumerate((272, 593, 1213)):
                email = f'excluded{lms_id}@example.invalid'
                snap.roster.append({'id': lms_id, 'full_name': 'Excluded Learner', 'email': email, 'email_normalized': email})
                snap.attendance.append({'occurrence_id': COMPLETED, 'learner_profile_id': lms_id, 'attendance_status': 'present',
                                        'attended_seconds': 7000, 'first_join_at': None, 'last_leave_at': None})
                snap.entries.append({'id': 90 + n, 'learner_id': lms_id, 'component_ref': 'C-V1', 'curriculum_component_ref': None,
                                     'quiz_ref': None, 'kind': 'video', 'accepted': True, 'attempt': None, 'grade': None,
                                     'passed': None, 'occurred_at': dt.datetime(2026, 11, 24, tzinfo=UTC)})
                snap.submissions.append({'id': f'S9{n}', 'learner_id': lms_id, 'activity_id': 'C-A1', 'status': 'submitted',
                                         'submitted_at': dt.datetime(2026, 11, 24, tzinfo=UTC)})
            return snap
        self.assertEqual(EXCLUDED_LMS_LEARNER_IDS, {272, 593, 1213})
        with mock.patch('analytics.api.views.load_snapshot', side_effect=with_excluded):
            k = self.get(f'/api/dashboard/summary?course={ARM_COURSE_ID}')['kpis']
            self.assertEqual(k['unique_learners']['value'], 3)
            self.assertEqual((k['attendance']['attended'], k['attendance']['expected'], k['attendance']['live_seconds']), (1, 2, 6700))
            self.assertEqual(k['materials_completed']['value'], 1)
            self.assertEqual(k['submissions']['value'], 0)
            lecture = self.get(f'/api/lectures?course={ARM_COURSE_ID}')['results'][0]
            self.assertEqual((lecture['learners'], lecture['present']), (3, 1))
            participants = [p['learner_id'] for p in self.get(f"/api/lectures/{lecture['id']}")['participants']]
            directories = {url: [r['learner_id'] for r in self.get(url)['results']]
                           for url in (f'/api/learners?course={ARM_COURSE_ID}&page_size=100', '/api/learners?page_size=100')}
            grids = {url: [r['learner_id'] for r in self.get(url)['learner_module_matrix']['rows']]
                     for url in (f'/api/dashboard/charts?course={ARM_COURSE_ID}', '/api/dashboard/charts')}
            for lms_id in EXCLUDED_LMS_LEARNER_IDS:
                excluded_id = ARM_LEARNER_ID_OFFSET + lms_id
                for url, ids in {**directories, **grids}.items():
                    self.assertNotIn(excluded_id, ids, url)
                self.assertNotIn(excluded_id, participants)
                self.assertEqual(self.client.get(f'/api/learners/{excluded_id}').status_code, 404)

    # --- existing modules unchanged ------------------------------------------------------

    def test_existing_module_responses_are_identical(self):
        for url in ('/api/dashboard/summary?course=9001', '/api/dashboard/charts?course=9002',
                    '/api/learners?course=9001', '/api/learners/101/courses/9001/matrix'):
            self.calls = 0
            self.assertEqual(self.get(url), self.without_lms(url), url)
            self.assertEqual(self.calls, 0, f'{url} must not read the LMS')

    def test_all_modules_add_the_module_beside_existing_figures(self):
        before = self.without_lms('/api/dashboard/summary')['kpis']
        after = self.get('/api/dashboard/summary')['kpis']
        self.assertEqual(after['modules']['value'], before['modules']['value'] + 1)
        self.assertEqual(after['enrollments']['value'], before['enrollments']['value'] + 3)
        self.assertEqual(after['unique_learners']['value'], before['unique_learners']['value'] + 2)
        self.assertEqual(after['mean_progress'], before['mean_progress'])
        charts = self.get('/api/dashboard/charts')
        self.assertEqual(charts['module_progress'][-1]['course_id'], ARM_COURSE_ID)
        old_charts = self.without_lms('/api/dashboard/charts')
        self.assertEqual(charts['module_progress'][:-1], old_charts['module_progress'])

    def test_all_modules_directory_merges_matched_learner(self):
        before = {r['learner_id']: r for r in self.without_lms('/api/learners?page_size=100')['results']}
        after = {r['learner_id']: r for r in self.get('/api/learners?page_size=100')['results']}
        self.assertEqual(set(after) - set(before), {ARM_LEARNER_ID_OFFSET + 501, ARM_LEARNER_ID_OFFSET + 502})
        self.assertEqual(after[101]['modules'], before[101]['modules'] + 1)
        self.assertIn(ARM_COURSE_ID, [e['course_id'] for e in after[101]['enrollments']])
        self.assertEqual(after[102], before[102])

    def test_lms_failure_keeps_existing_views_and_reports_module_error(self):
        self.fail_lms = True
        self.assertEqual(self.client.get(f'/api/dashboard/summary?course={ARM_COURSE_ID}').status_code, 503)
        self.assertEqual(self.get('/api/dashboard/summary'), self.without_lms('/api/dashboard/summary'))
        self.assertEqual(self.get('/api/learners'), self.without_lms('/api/learners'))

    # --- learner pages and lectures -------------------------------------------------------

    def test_learner_pages(self):
        detail = self.get('/api/learners/101')
        self.assertIn(ARM_COURSE_ID, [c['course_id'] for c in detail['courses']])
        self.assertEqual(detail['summary']['modules'], len(self.without_lms('/api/learners/101')['courses']) + 1)
        lms_only = self.get(f'/api/learners/{ARM_LEARNER_ID_OFFSET + 501}')
        self.assertEqual([c['course_id'] for c in lms_only['courses']], [ARM_COURSE_ID])
        self.assertEqual(self.client.get(f'/api/learners/{ARM_LEARNER_ID_OFFSET + 503}').status_code, 404)
        matrix = self.get(f'/api/learners/{ARM_LEARNER_ID_OFFSET + 501}/courses/{ARM_COURSE_ID}/matrix')
        self.assertEqual([r['kind'] for r in matrix['rows']], ['video', 'video', 'assignment'])
        self.assertEqual(matrix['rows'][2]['assignment']['status'], 'draft')
        sessions = matrix['attendance']['sessions']
        self.assertEqual(len(sessions), 1)
        self.assertEqual((sessions[0]['status'], sessions[0]['timing']['duration_seconds']), ('attended', 6700))
        self.assertEqual(sessions[0]['date'], '2026-11-23')

    def test_lectures_list_completed_lectures_with_stored_statuses(self):
        listing = self.get(f'/api/lectures?course={ARM_COURSE_ID}')
        self.assertEqual(listing['count'], 1)
        lecture = listing['results'][0]
        self.assertEqual((lecture['present'], lecture['recorded'], lecture['learners']), (1, 2, 3))
        detail = self.get(f"/api/lectures/{lecture['id']}")
        self.assertEqual(sorted(p['attendance'] for p in detail['participants']), ['absent', 'present'])
        self.assertIn(lecture['id'], [r['id'] for r in self.get('/api/lectures')['results']])

    # --- read-only guarantees -----------------------------------------------------------

    def media_snapshot(self):
        value = snapshot()
        value.artifacts = [
            {'id': 'ART-VIDEO', 'occurrence_id': COMPLETED, 'artifact_type': 'recording',
             'graph_artifact_id': 'graph-recording', 'meeting_id': 'artifact-meeting-not-series',
             'organizer_id': '11111111-1111-4111-8111-111111111111',
             'created_datetime': dt.datetime(2026, 11, 23, 10)},
            {'id': 'ART-TEXT', 'occurrence_id': COMPLETED, 'artifact_type': 'transcript',
             'graph_artifact_id': 'graph-transcript', 'meeting_id': 'artifact-meeting-not-series',
             'organizer_id': '11111111-1111-4111-8111-111111111111',
             'created_datetime': dt.datetime(2026, 11, 23, 10)},
        ]
        return value

    def test_lms_media_references_appear_in_detail_and_all_module_counts(self):
        from analytics.arm import Arm
        lecture_id = Arm.lecture_id(COMPLETED)
        before = self.get(f'/api/lectures/{lecture_id}')['participants']
        with mock.patch('analytics.api.views.load_snapshot', side_effect=self.media_snapshot):
            detail = self.get(f'/api/lectures/{lecture_id}')
            self.assertEqual(detail['participants'], before)
            self.assertEqual((detail['recordings'], detail['transcripts']), (1, 1))
            self.assertEqual({r['kind'] for r in detail['resources']}, {'recording', 'transcript'})
            self.assertEqual(self.get(f'/api/lectures?course={ARM_COURSE_ID}')['total_recordings'], 1)
            self.assertEqual(self.get('/api/lectures')['total_recordings'],
                             self.without_lms('/api/lectures')['total_recordings'] + 1)
            public = json.dumps(detail)
            for private in ('artifact-meeting-not-series', 'graph-recording', 'graph-transcript', '11111111-1111'):
                self.assertNotIn(private, public)
            for asset in detail['resources']:
                self.assertLess(asset['id'], 2**53)
                self.assertTrue(asset['url'].startswith(f'/api/lectures/{lecture_id}/resources/'))

    def test_lms_recording_uses_artifact_meeting_and_existing_range_proxy(self):
        from analytics.arm import Arm
        value = self.media_snapshot()
        with mock.patch('analytics.api.views.load_snapshot', return_value=value):
            detail = self.get('/api/lectures/' + Arm.lecture_id(COMPLETED))
            asset = next(a for a in detail['resources'] if a['kind'] == 'recording')
            upstream = io.BytesIO(b'x')
            upstream.status, upstream.headers = 206, {'Content-Length': '1', 'Content-Range': 'bytes 0-0/10'}
            with mock.patch('analytics.lectures.media.open_recording', return_value=upstream) as opened:
                response = self.client.get(asset['url'], HTTP_RANGE='bytes=0-0')
                self.assertEqual(response.status_code, 206)
                self.assertEqual(b''.join(response.streaming_content), b'x')
                stub, byte_range = opened.call_args.args
                self.assertEqual(stub.meeting_id, 'artifact-meeting-not-series')
                self.assertEqual(stub.source_id, 'graph-recording')
                self.assertEqual(byte_range, 'bytes=0-0')

    def test_lms_transcript_uses_existing_metadata_parser_for_text_and_vtt(self):
        from analytics.arm import Arm
        cue = {'speakerName': 'Speaker', 'spokenText': 'Source transcript',
               'startDateTime': '2026-11-23T10:00:00Z', 'endDateTime': '2026-11-23T10:00:03Z'}
        metadata = 'WEBVTT\n\n00:00:00.000 --> 00:00:03.000\n' + json.dumps(cue)
        with mock.patch('analytics.api.views.load_snapshot', side_effect=self.media_snapshot), \
                mock.patch('analytics.lectures.lms_media.GraphClient.from_environment') as graph:
            graph.return_value.get.return_value = metadata
            detail = self.get('/api/lectures/' + Arm.lecture_id(COMPLETED))
            asset = next(a for a in detail['resources'] if a['kind'] == 'transcript')
            response = self.client.get(asset['url'])
            self.assertEqual(response.status_code, 200)
            self.assertIn('Source transcript', response.content.decode())
            self.assertEqual(response['Cache-Control'], 'private, no-store')
            response = self.client.get(asset['url'] + '?format=vtt')
            self.assertEqual(response.status_code, 200, response.content)
            self.assertTrue(response.content.decode().startswith('WEBVTT'))
            path = graph.return_value.get.call_args.args[0]
            self.assertIn('/onlineMeetings/artifact-meeting-not-series/', path)
            self.assertIn('/transcripts/graph-transcript/metadataContent', path)

    def test_lms_artifacts_do_not_cross_occurrences_or_expose_invalid_bindings(self):
        from analytics.arm import Arm
        from analytics.lectures.lms_media import resource_id
        value = self.media_snapshot()
        value.artifacts[0]['occurrence_id'] = SCHEDULED
        value.artifacts[1]['organizer_id'] = None
        with mock.patch('analytics.api.views.load_snapshot', return_value=value), \
                mock.patch('analytics.lectures.media.open_recording') as opened:
            lecture_id = Arm.lecture_id(COMPLETED)
            self.assertEqual(self.get('/api/lectures/' + lecture_id)['resources'], [])
            self.assertEqual(self.client.get(f'/api/lectures/{lecture_id}/resources/{resource_id("ART-VIDEO")}').status_code, 404)
            opened.assert_not_called()

    def test_lms_media_routes_require_authentication(self):
        from analytics.arm import Arm
        from analytics.lectures.lms_media import resource_id
        self.client.logout()
        with mock.patch('analytics.api.views.load_snapshot') as loaded:
            response = self.client.get(f'/api/lectures/{Arm.lecture_id(COMPLETED)}/resources/{resource_id("ART-VIDEO")}')
            self.assertIn(response.status_code, (401, 403))
            loaded.assert_not_called()

    def test_lms_media_reads_never_persist_data(self):
        from analytics.arm import Arm
        from lecture_library.models import LectureCapture, LectureLearner, LectureResource
        models = (Learner, Enrollment, SessionAttendance, LectureCapture, LectureLearner, LectureResource)
        before = [m.objects.count() for m in models]
        with mock.patch('analytics.api.views.load_snapshot', side_effect=self.media_snapshot):
            self.get('/api/lectures/' + Arm.lecture_id(COMPLETED))
        self.assertEqual([m.objects.count() for m in models], before)

    def test_unavailable_lms_transcript_returns_safe_error_without_affecting_attendance(self):
        from analytics.arm import Arm
        from analytics.attendance.graph_client import GraphError
        with mock.patch('analytics.api.views.load_snapshot', side_effect=self.media_snapshot), \
                mock.patch('analytics.lectures.lms_media.GraphClient.from_environment') as graph:
            graph.return_value.get.side_effect = GraphError(404, 'NotFound')
            url = '/api/lectures/' + Arm.lecture_id(COMPLETED)
            detail = self.get(url)
            asset = next(a for a in detail['resources'] if a['kind'] == 'transcript')
            response = self.client.get(asset['url'])
            self.assertEqual(response.status_code, 502)
            self.assertNotIn('graph-transcript', response.content.decode())
            self.assertEqual(self.get(url)['participants'], detail['participants'])

    def test_hidden_media_query_is_module_scoped_and_filters_lms_hidden_flag(self):
        sql = lms_live.QUERIES['artifacts']
        self.assertIn('ls.module_catalogue_id = %(module)s', sql)
        self.assertIn("o.status = 'completed'", sql)
        self.assertIn("lmsHiddenFromLearners", sql)
        self.assertIn("<> 'true'", sql)
        self.assertNotIn('a.metadata,', sql)

    def test_no_dashboard_records_are_written(self):
        counts = lambda: [m.objects.count() for m in (Learner, Enrollment, LessonRecord, SessionAttendance)]  # noqa: E731
        before = counts()
        for url in (f'/api/dashboard/summary?course={ARM_COURSE_ID}', '/api/dashboard/charts', '/api/learners',
                    f'/api/learners/{ARM_LEARNER_ID_OFFSET + 501}', f'/api/lectures?course={ARM_COURSE_ID}'):
            self.get(url)
        self.assertEqual(counts(), before)

    def test_only_select_statements_and_no_connection_without_configuration(self):
        for sql in lms_live.QUERIES.values():
            self.assertTrue(sql.lstrip().lower().startswith('select'))
            for word in ('insert ', 'update ', 'delete ', 'alter ', 'drop ', 'create ', 'truncate '):
                self.assertNotIn(word, sql.lower())
        with override_settings(LMS_DATABASE_URL=''), self.assertRaises(LmsUnavailable):
            lms_live.load_snapshot()

    def test_pooler_safe_read_only_session(self):
        """No startup options (Neon's pooler rejects them); read-only and timeout are set per transaction."""
        for read_only in ('on', 'off'):
            conn = mock.MagicMock()
            conn.cursor.return_value.fetchone.return_value = {'ro': read_only}
            with override_settings(LMS_DATABASE_URL='postgresql://example.invalid/db'), \
                    mock.patch('psycopg.Pipeline.is_supported', return_value=False), \
                    mock.patch('psycopg.connect', return_value=conn) as connect, self.assertRaises(LmsUnavailable):
                lms_live.load_snapshot()  # 'on' reaches the empty module check; 'off' fails the guard
            self.assertNotIn('options', connect.call_args.kwargs)
            self.assertIs(conn.read_only, True)
            first_sql, first_params = conn.cursor.return_value.execute.call_args_list[0].args
            self.assertIn("set_config('statement_timeout', %s, true)", first_sql)
            self.assertEqual(first_params, ('15000ms',))
            conn.rollback.assert_called_once()
            conn.close.assert_called_once()

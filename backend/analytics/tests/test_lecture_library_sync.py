"""Synthetic Graph evidence; independent in-memory stores, no real credentials."""
import copy
import datetime as dt
import json
from unittest import mock

from django.test import TestCase, SimpleTestCase

from analytics.attendance.graph_client import GraphError
from analytics.lectures.importer import import_source, timestamp
from analytics.lectures.sync import collect, parse_metadata, retains_records, run_sync, atomic_json, SyncInProgress
from lecture_library.models import LectureCapture, LectureLearner, LectureResource
from . import test_lecture_library as fixture


class IncrementalTests(TestCase):
    databases = {'default', 'lecture_library'}
    setUp = fixture.LectureLibraryTests.setUp
    record = fixture.LectureLibraryTests.record
    source = fixture.LectureLibraryTests.source
    load = fixture.LectureLibraryTests.load
    lecture = fixture.LectureLibraryTests.lecture

    def refresh(self, source=None, **kwargs):
        self.path.write_text(json.dumps(source or self.source()), encoding='utf8')
        return import_source(self.path, incremental_since=dt.date(2026, 10, 1), **kwargs)

    def test_additive_repeat_preserves_ids_and_does_not_double_count(self):
        self.refresh()
        pk = self.lecture().pk
        self.refresh()
        self.assertEqual(self.lecture().pk, pk)
        self.assertEqual(LectureLearner.objects.count(), 5)
        self.assertEqual(LectureLearner.objects.first().duration_seconds, 7200)

    def test_historical_and_hidden_days_are_not_rewritten_or_reactivated(self):
        self.load()
        old = self.lecture()
        old.active = False
        old.save()
        self.refresh()
        old.refresh_from_db()
        self.assertFalse(old.active)
        self.assertEqual(LectureCapture.objects.count(), 1)
        old.active = True
        old.save()
        import_source(self.path, incremental_since=dt.date(2026, 10, 2))
        old.refresh_from_db()
        self.assertTrue(old.active)
        self.assertEqual(LectureResource.objects.count(), 2)

    def test_partial_revision_rolls_back_without_erasing_presence_or_media(self):
        self.refresh()
        pk = self.lecture().pk
        source = self.source()
        source['series'][0]['recordings'] = []
        with self.assertRaisesMessage(ValueError, 'remove media'):
            self.refresh(source)
        self.assertEqual(self.lecture().pk, pk)
        self.assertEqual(LectureResource.objects.count(), 2)
        source = self.source()
        rows = source['attendance_records']['meeting|report']
        rows[0]['attendanceIntervals'][0]['leaveDateTime'] = '2026-10-01T11:00:00+00:00'
        # The verified alias retains learner 100's full presence. Shorten learner 101.
        rows[1]['attendanceIntervals'][0]['leaveDateTime'] = '2026-10-01T11:00:00+00:00'
        with self.assertRaisesMessage(ValueError, 'discard verified evidence'):
            self.refresh(source)
        self.assertEqual(LectureLearner.objects.get(learner_source_id=101).duration_seconds, 7200)

    def test_dry_run_rolls_back_new_day(self):
        self.refresh(dry_run=True)
        self.assertEqual(LectureCapture.objects.count(), 0)

    def test_new_media_only_day_is_held_until_attendance_arrives(self):
        source = self.source()
        source['previous']['attendance']['reports'] = []
        self.refresh(source)
        self.assertEqual(LectureCapture.objects.count(), 0)
        self.refresh()
        self.assertEqual(LectureCapture.objects.count(), 1)

    def test_late_attendance_preserves_existing_daily_url_and_speech(self):
        source = self.source()
        source['attendance_records']['meeting|report'][1]['identity']['displayName'] = self.roster[1].name
        source['timed_transcripts'][0]['segments'] = [{'name': self.roster[1].name,
            'start': self.start, 'end': '2026-10-01T10:00:30+00:00'}]
        complete = copy.deepcopy(source)
        # Create a short-visit recording entry using the normal importer.
        for row in source['attendance_records']['meeting|report']:
            row['attendanceIntervals'][0]['leaveDateTime'] = '2026-10-01T10:05:00+00:00'
        self.load(source)
        old = self.lecture()
        self.refresh(complete)
        self.assertEqual(self.lecture().pk, old.pk)
        self.assertEqual(LectureCapture.objects.count(), 1)
        self.assertEqual(LectureLearner.objects.get(learner_source_id=101).duration_seconds, 7200)
        self.assertEqual(LectureLearner.objects.get(learner_source_id=101).speaking_seconds, 30)

    def test_approved_risk_binding_uses_the_same_cohort_and_quorum_rules(self):
        self.course.source_id = 1216420  # free 83334 without changing FK enrollment
        self.course.save()
        self.other_course.source_id = 83334
        self.other_course.save()
        self.course.source_id = 121642
        self.other_course.source_id = 83334
        # The existing Risk course ID is freed by the previous save.
        self.course.save()
        source = self.source()
        subject = 'Module 2-Procurement Risk and Contracts Management'
        source['series'][0]['subject'] = subject
        source['previous']['attendance']['reports'][0]['subject'] = subject
        self.refresh(source, subject_courses={subject: 121642})
        self.assertEqual(self.lecture().course_source_id, 121642)
        self.assertEqual(LectureLearner.objects.count(), 5)


class FakeGraph:
    def __init__(self, source):
        self.source = copy.deepcopy(source)
        self.fail_transcript = False
        self.omit_assets = False
        self.incomplete = False
        self.calendar_error = False
        self.read_paths = []

    def collection(self, path):
        self.read_paths.append(path)
        if '/calendarView?' in path:
            if self.calendar_error:
                raise GraphError(403, 'AccessDenied')
            return [], 1
        if path.endswith('/attendanceReports'):
            return [{'id': 'report'}], 1
        if path.endswith('/attendanceRecords'):
            rows = self.source['attendance_records']['meeting|report']
            return (rows[:-1] if self.incomplete else rows), 1
        for kind in ('recordings', 'transcripts'):
            if path.endswith('/' + kind):
                return ([] if self.omit_assets else self.source['series'][0][kind]), 1
        raise AssertionError('Unexpected Graph read')

    def get(self, path, *, text=False):
        self.read_paths.append(path)
        if path.endswith('/attendanceReports/report'):
            return {'id': 'report', 'meetingStartDateTime': '2026-10-01T10:00:00Z',
                    'meetingEndDateTime': '2026-10-01T12:00:00Z',
                    'totalParticipantCount': len(self.source['attendance_records']['meeting|report'])}
        if path.endswith('/metadataContent'):
            if self.fail_transcript:
                raise GraphError(404, 'NotFound')
            return 'WEBVTT\n\n00:00:01.000 --> 00:00:04.000\n' + json.dumps({
                'startDateTime': '2026-10-01T10:00:01Z', 'endDateTime': '2026-10-01T10:00:04Z',
                'speakerName': 'External meeting label', 'spokenText': 'Test speech'})
        raise AssertionError('Unexpected Graph read')


class CollectorTests(IncrementalTests):
    def setUp(self):
        super().setUp()
        response = mock.Mock(status=206)
        response.read.return_value = b'x'
        patch = mock.patch('analytics.lectures.sync.open_recording', return_value=response)
        self.probe = patch.start()
        self.addCleanup(patch.stop)

    def policy(self):
        return {'meetings': [{'course_source_id': 83334, 'organizer_id': 'owner',
                             'meeting_id': 'meeting', 'approved_subject': self.subject}],
                'automation': {'calendar_readers': ['reader'], 'grace_minutes': 120}}

    def collect(self, source=None, client=None, now=None):
        source = copy.deepcopy(source if source is not None else self.source())
        return collect(source, self.policy(), timestamp('2026-10-01T00:00:00Z'),
                       client=client or FakeGraph(self.source()), now=now or timestamp('2026-10-01T15:00:00Z'))

    def test_late_transcript_retries_and_listing_omissions_preserve_resources(self):
        source = self.source()
        source['transcript_contents'] = {}
        source['timed_transcripts'] = []
        graph = FakeGraph(source)
        graph.fail_transcript = True
        first, _, _, issues = self.collect(source, graph)
        self.assertTrue(issues)
        graph.fail_transcript = False
        second, _, counts, _ = self.collect(first, graph)
        self.assertEqual(counts['transcripts_downloaded'], 1)
        self.assertTrue(second['transcript_contents']['transcript']['text'])
        graph.omit_assets = True
        third, _, _, _ = self.collect(second, graph)
        self.assertEqual(len(third['series'][0]['recordings']), 1)
        self.assertEqual(third['transcript_contents'], second['transcript_contents'])

    def test_incomplete_report_preserves_previous_full_records(self):
        graph = FakeGraph(self.source())
        graph.incomplete = True
        data, _, _, issues = self.collect(client=graph)
        self.assertEqual(data['attendance_records'], self.source()['attendance_records'])
        self.assertIn('incomplete_report', issues)

    def test_recording_unavailable_then_ready_is_retried(self):
        self.probe.side_effect = GraphError(404, 'NotFound')
        first, _, _, issues = self.collect()
        self.assertFalse(first['series'][0]['recordings'][0]['content_available'])
        self.assertIn('recording_content_unavailable', issues)
        self.probe.side_effect = None
        second, _, _, _ = self.collect(first)
        self.assertTrue(second['series'][0]['recordings'][0]['content_available'])

    def test_grace_period_defers_report(self):
        source = self.source()
        source['previous']['attendance']['reports'] = []
        source['attendance_records'] = {}
        data, _, _, _ = self.collect(source, now=timestamp('2026-10-01T13:00:00Z'))
        self.assertEqual(data['previous']['attendance']['reports'], [])

    def test_calendar_failure_is_reported_but_known_series_still_checked(self):
        graph = FakeGraph(self.source())
        graph.calendar_error = True
        _, _, counts, issues = self.collect(client=graph)
        self.assertEqual(counts['complete_reports_read'], 1)
        self.assertIn('calendar:403:AccessDenied', issues)

    def test_new_series_requires_exact_url_subject_and_approved_organizer(self):
        for actual_owner in ('owner', 'unapproved-owner'):
            with self.subTest(actual_owner=actual_owner):
                graph = FakeGraph(self.source())
                original = graph.collection
                def collection(path):
                    if '/calendarView?' in path:
                        return [{'id': 'event', 'subject': self.subject, 'isCancelled': False,
                            'isDraft': False, 'isOnlineMeeting': True, 'onlineMeeting': {'joinUrl': 'https://teams.invalid/exact'},
                            'start': {'dateTime': '2026-10-01T10:00:00Z'},
                            'end': {'dateTime': '2026-10-01T12:00:00Z'}}], 1
                    if '/onlineMeetings?' in path:
                        return [{'id': 'new-meeting', 'subject': self.subject, 'joinWebUrl': 'https://teams.invalid/exact',
                            'participants': {'organizer': {'identity': {'user': {'id': actual_owner}}}}}], 1
                    return original(path)
                graph.collection = collection
                data, _, _, issues = self.collect(client=graph)
                self.assertEqual(any(s['meeting_id'] == 'new-meeting' for s in data['series']), actual_owner == 'owner')
                if actual_owner != 'owner':
                    self.assertIn('unresolved_series', issues)

    def test_expired_media_series_uses_exact_meeting_organizer_index(self):
        graph = FakeGraph(self.source())
        original = graph.collection
        def collection(path):
            if path.endswith('/recordings') or path.endswith('/transcripts'):
                raise GraphError(404, 'NotFound')
            if '/getAllRecordings(' in path:
                return [{'id': 'new', 'meetingId': 'meeting', 'callId': 'report', 'createdDateTime': self.start},
                        {'id': 'unrelated', 'meetingId': 'different-meeting', 'createdDateTime': self.start}], 1
            if '/getAllTranscripts(' in path:
                return [], 1
            return original(path)
        graph.collection = collection
        data, _, counts, issues = self.collect(client=graph)
        ids = {x['id'] for x in data['series'][0]['recordings']}
        self.assertIn('new', ids)
        self.assertNotIn('unrelated', ids)
        self.assertEqual(counts['organizer_media_indexes_read'], 2)
        self.assertFalse(issues)

    def test_sync_dry_run_changes_no_database_or_snapshot(self):
        self.path.write_text(json.dumps(self.source()), encoding='utf8')
        before = self.path.read_bytes()
        config = self.path.with_name('config.json')
        atomic_json(config, {'since': '2026-10-01T00:00:00Z', 'policy': 'synthetic', 'source': str(self.path)})
        with mock.patch('analytics.lectures.sync.load_config', return_value=(self.policy(), None)):
            run_sync(config, client=FakeGraph(self.source()), now=timestamp('2026-10-01T15:00:00Z'), dry_run=True)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(LectureCapture.objects.count(), 0)

    def test_live_repeat_and_changed_cutover(self):
        self.path.write_text(json.dumps(self.source()), encoding='utf8')
        config = self.path.with_name('config.json')
        options = {'since': '2026-10-01T00:00:00Z', 'policy': 'synthetic', 'source': str(self.path)}
        atomic_json(config, options)
        with mock.patch('analytics.lectures.sync.load_config', return_value=(self.policy(), None)):
            for _ in range(2):
                run_sync(config, client=FakeGraph(self.source()), now=timestamp('2026-10-01T15:00:00Z'))
            self.assertEqual(LectureCapture.objects.count(), 1)
            options['since'] = '2026-09-30T00:00:00Z'
            atomic_json(config, options)
            with self.assertRaisesMessage(ValueError, 'immutable'):
                run_sync(config, client=FakeGraph(self.source()), now=timestamp('2026-10-01T15:00:00Z'))

    def test_second_job_is_rejected(self):
        config = self.path.with_name('config.json')
        atomic_json(config, {'since': '2026-10-01T00:00:00Z', 'policy': 'synthetic', 'source': str(self.path)})
        with mock.patch('analytics.lectures.sync.load_config', return_value=(self.policy(), None)), \
                mock.patch('analytics.lectures.sync.ProcessLock.acquire', return_value=False):
            with self.assertRaises(SyncInProgress):
                run_sync(config, now=timestamp('2026-10-01T15:00:00Z'))


class MetadataTests(SimpleTestCase):
    def test_empty_metadata_cannot_mark_transcript_readable(self):
        with self.assertRaises(ValueError):
            parse_metadata('WEBVTT\n')

    def test_shorter_intervals_are_rejected(self):
        old = [{'id': 'one', 'attendanceIntervals': [{'joinDateTime': '2026-10-01T10:00:00Z', 'leaveDateTime': '2026-10-01T12:00:00Z'}]}]
        new = copy.deepcopy(old)
        new[0]['attendanceIntervals'][0]['leaveDateTime'] = '2026-10-01T11:00:00Z'
        self.assertFalse(retains_records(old, new))
        self.assertTrue(retains_records(old, old))

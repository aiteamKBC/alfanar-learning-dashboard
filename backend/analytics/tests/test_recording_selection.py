import json
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings
from django.core.management import call_command, CommandError

from analytics.lectures.recordings import select_recordings, seconds_between, build_duration_index
from analytics.lectures.sync import atomic_json
from lecture_library.models import LectureResource
from . import test_lecture_library as fixture


class RecordingSelectionTests(SimpleTestCase):
    def test_short_fragments_hidden_but_genuine_long_parts_and_unknown_lengths_kept(self):
        parts = [75, 167, 29, 87, 6591, 3600, None]
        self.assertEqual(select_recordings(parts, lambda x: x), [6591, 3600, None])

    def test_short_only_recordings_are_hidden_without_a_large_recording(self):
        self.assertEqual(select_recordings([30, 90], lambda x: x), [])
        self.assertEqual(select_recordings([102.9, 49.36, 660.9], lambda x: x), [660.9])
        self.assertEqual(select_recordings([30, None], lambda x: x), [None])

    def test_exact_cutoffs(self):
        self.assertEqual(select_recordings([599, 600, 1800], lambda x: x), [600, 1800])
        self.assertEqual(select_recordings([599, 1799], lambda x: x), [1799])

    def test_duration_uses_actual_times_not_size_title_or_start_order(self):
        self.assertEqual(seconds_between('2026-11-25T10:22:45Z', '2026-11-25T12:12:37Z'), 6592)
        for a, b in [(None, None), ('invalid', 'invalid'), ('2026-11-25T12:00:00Z', '2026-11-25T10:00:00Z')]:
            self.assertIsNone(seconds_between(a, b))
        self.assertIsNone(seconds_between('2026-11-25T10:00:00', '2026-11-25T11:00:00Z'))


class StoredRecordingSelectionTests(TestCase):
    databases = {'default', 'lecture_library'}
    setUp = fixture.LectureLibraryTests.setUp
    record = fixture.LectureLibraryTests.record
    source = fixture.LectureLibraryTests.source
    load = fixture.LectureLibraryTests.load
    lecture = fixture.LectureLibraryTests.lecture
    url = fixture.LectureLibraryTests.url

    def test_existing_library_hides_fragments_consistently_without_removing_data(self):
        source = self.source()
        assets = source['series'][0]['recordings']
        assets[0]['endDateTime'] = self.end
        assets.append({'id': 'short', 'callId': 'report', 'createdDateTime': self.start,
                       'endDateTime': '2026-10-01T10:01:00+00:00'})
        assets.append({'id': 'long-part', 'callId': 'report', 'createdDateTime': self.start,
                       'endDateTime': '2026-10-01T11:00:00+00:00'})
        self.load(source)
        index_path = self.path.with_name('durations.json')
        with override_settings(LECTURE_RECORDING_DURATIONS_FILE=index_path):
            before = self.client.get(self.url()).json()
            atomic_json(index_path, build_duration_index(source))
            after = self.client.get(self.url()).json()
            self.assertEqual(after['participants'], before['participants'])
            self.assertEqual(after['recordings'], 2)
            self.assertEqual(after['transcripts'], before['transcripts'])
            self.assertEqual(len([r for r in after['resources'] if r['kind'] == 'recording']), 2)
            listing = self.client.get('/api/lectures').json()
            self.assertEqual(listing['results'][0]['recordings'], 2)
            self.assertEqual(listing['total_recordings'], 2)
            self.assertEqual(listing['playable_recordings'], 2)
            self.assertEqual(LectureResource.objects.filter(kind='recording').count(), 3)
            # Transcripts and all stored recording references remain intact.
            self.assertEqual(LectureResource.objects.count(), 4)

    def test_missing_or_bad_index_keeps_existing_recordings_visible(self):
        self.load()
        path = self.path.with_name('durations.json')
        with override_settings(LECTURE_RECORDING_DURATIONS_FILE=path):
            self.assertEqual(self.client.get(self.url()).json()['recordings'], 1)
            path.write_text('invalid', encoding='utf8')
            self.assertEqual(self.client.get(self.url()).json()['recordings'], 1)

    def test_index_refresh_applies_without_reimport_or_restart(self):
        source = self.source()
        source['series'][0]['recordings'][0]['endDateTime'] = self.end
        source['series'][0]['recordings'].append({'id': 'short','callId':'report',
            'createdDateTime':self.start,'endDateTime':'2026-10-01T10:00:30+00:00'})
        self.load(source)
        path = self.path.with_name('durations.json')
        with override_settings(LECTURE_RECORDING_DURATIONS_FILE=path):
            atomic_json(path, {'version':1,'durations':{}})
            self.assertEqual(self.client.get(self.url()).json()['recordings'], 2)
            atomic_json(path, build_duration_index(source))
            self.assertEqual(self.client.get(self.url()).json()['recordings'], 1)

    def test_oct14_style_recordings_hide_short_parts_and_keep_the_eleven_minute_part(self):
        source = self.source()
        source['series'][0]['recordings'] = [
            {'id':str(i), 'callId':'report', 'createdDateTime':self.start,
             'endDateTime':end} for i,end in enumerate([
                 '2026-10-01T10:01:43+00:00','2026-10-01T10:00:49+00:00','2026-10-01T10:11:01+00:00'])]
        self.load(source)
        with override_settings(LECTURE_RECORDING_DURATIONS_FILE=self.path.with_name('durations.json')):
            atomic_json(self.path.with_name('durations.json'),build_duration_index(source))
            data = self.client.get(self.url()).json()
            self.assertEqual(data['recordings'], 1)
            self.assertEqual(data['transcripts'], 1)
            self.assertEqual(self.client.get('/api/lectures').json()['total_recordings'], 1)
            self.assertEqual(LectureResource.objects.filter(kind='recording').count(), 3)

    def test_all_short_recordings_disappear_from_display_without_deleting_the_lecture(self):
        source = self.source()
        source['series'][0]['recordings'][0]['endDateTime']='2026-10-01T10:01:00+00:00'
        self.load(source)
        with override_settings(LECTURE_RECORDING_DURATIONS_FILE=self.path.with_name('durations.json')):
            atomic_json(self.path.with_name('durations.json'),build_duration_index(source))
            data = self.client.get(self.url()).json()
            self.assertEqual(data['recordings'],0)
            self.assertEqual(data['transcripts'],1)
            self.assertEqual(len(data['participants']),5)
            self.assertEqual(self.client.get('/api/lectures').json()['count'],1)
            self.assertEqual(LectureResource.objects.filter(kind='recording').count(),1)

    def test_backfill_only_writes_the_index_and_cannot_overwrite_its_source(self):
        source = self.source()
        source['series'][0]['recordings'][0]['endDateTime'] = self.end
        self.path.write_text(json.dumps(source),encoding='utf8')
        before = self.path.read_bytes()
        output = self.path.with_name('durations.json')
        call_command('index_lecture_recordings',source=str(self.path),output=str(output),stdout=mock.Mock())
        self.assertEqual(len(json.loads(output.read_text())['durations']), 1)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(LectureResource.objects.count(), 0)
        with self.assertRaises(CommandError):
            call_command('index_lecture_recordings',source=str(self.path),output=str(self.path))
        self.assertEqual(self.path.read_bytes(), before)

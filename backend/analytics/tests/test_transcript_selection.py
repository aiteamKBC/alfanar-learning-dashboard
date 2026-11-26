from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings

from analytics.lectures.recordings import build_duration_index, source_key
from analytics.lectures.sync import atomic_json
from analytics.lectures.transcripts import select_transcript, transcript_duration
from lecture_library.models import LectureResource
from . import test_lecture_library as fixture


class TranscriptSelectionTests(SimpleTestCase):
    def test_longest_duration_not_first_or_largest_file(self):
        rows = [{'seconds': 60, 'size': 9000}, {'seconds': 7200, 'size': 100}, {'seconds': 120, 'size': 5000}]
        self.assertEqual(select_transcript(rows, lambda r: r['seconds'], content_size=lambda r: r['size']), [rows[1]])
        self.assertEqual(select_transcript([], lambda r: r), [])

    def test_available_part_preferred_and_missing_timing_has_stable_content_fallback(self):
        rows = [{'seconds': 7200, 'readable': False}, {'seconds': 600, 'readable': True}]
        self.assertEqual(select_transcript(rows, lambda r: r['seconds'], available=lambda r: r['readable']), [rows[1]])
        self.assertEqual(select_transcript(['short', 'longer text'], lambda r: None, content_size=len), ['longer text'])
        self.assertEqual(select_transcript(['first', 'second'], lambda r: None), ['first'])

    def test_vtt_span_includes_pauses_and_ignores_invalid_cues(self):
        content = 'WEBVTT\n00:01:00.000 --> 00:01:05.000\nSpeech\n\n01:01:00.000 --> 01:01:10.000 align:start\nSpeech'
        self.assertEqual(transcript_duration(content), 3610)
        self.assertEqual(transcript_duration('WEBVTT\n01:00.000 --> 02:00.000\nSpeech'), 60)
        self.assertIsNone(transcript_duration('WEBVTT\ninvalid\n00:02:00.000 --> 00:01:00.000'))

    def test_recording_and_transcript_index_namespaces_do_not_collide(self):
        source = {'series': [{'organizer_id': 'owner', 'meeting_id': 'meeting',
                  'recordings': [{'id': 'same', 'createdDateTime': '2026-11-25T10:00:00Z', 'endDateTime': '2026-11-25T12:00:00Z'}],
                  'transcripts': [{'id': 'same', 'createdDateTime': '2026-11-25T10:00:00Z', 'endDateTime': '2026-11-25T11:00:00Z'}]}]}
        result = build_duration_index(source)
        key = source_key('teams', '', 'owner', 'meeting', 'same')
        self.assertEqual(result['durations'][key], 7200)
        self.assertEqual(result['transcript_durations'][key], 3600)


class StoredTranscriptSelectionTests(TestCase):
    databases = {'default', 'lecture_library'}
    setUp = fixture.LectureLibraryTests.setUp
    record = fixture.LectureLibraryTests.record
    source = fixture.LectureLibraryTests.source
    load = fixture.LectureLibraryTests.load
    lecture = fixture.LectureLibraryTests.lecture
    url = fixture.LectureLibraryTests.url

    def test_old_transcripts_use_saved_vtt_without_reimport_or_speech_changes(self):
        self.load()
        main = self.lecture().resources.get(kind='transcript')
        main.vtt_content = 'WEBVTT\n00:00:00.000 --> 02:00:00.000\nMain'
        main.save(update_fields=['vtt_content'])
        before = self.client.get(self.url()).json()
        for i, length in enumerate(('00:01:00.000', '00:02:00.000')):
            LectureResource.objects.create(lecture=self.lecture(), kind='transcript', source_id=f'short-{i}',
                organizer_id='owner', meeting_id='meeting', readable=True, text_content='Short ' * 100,
                vtt_content=f'WEBVTT\n00:00:00.000 --> {length}\nShort')
        with override_settings(LECTURE_RECORDING_DURATIONS_FILE=self.path.with_name('missing.json')):
            detail = self.client.get(self.url()).json()
            self.assertEqual(detail['participants'], before['participants'])
            self.assertEqual((detail['transcripts'], detail['readable_transcripts']), (1, 1))
            self.assertEqual([r['id'] for r in detail['resources'] if r['kind'] == 'transcript'], [main.pk])
            self.assertEqual(self.client.get('/api/lectures').json()['results'][0]['transcripts'], 1)
            self.assertEqual(self.client.get(f'{self.url()}/resources/{main.pk}').content.decode(), 'Private source transcript')
            self.assertEqual(LectureResource.objects.filter(kind='transcript').count(), 3)

    def test_new_index_selects_longest_readable_transcript_without_changing_recordings(self):
        source = self.source()
        source['series'][0]['transcripts'][0]['endDateTime'] = '2026-10-01T10:01:00+00:00'
        source['series'][0]['transcripts'].append({'id': 'main', 'callId': 'report',
            'createdDateTime': self.start, 'endDateTime': self.end})
        source['transcript_contents']['main'] = {'text': 'Main transcript', 'vtt': 'WEBVTT\n'}
        self.load(source)
        path = self.path.with_name('durations.json')
        with override_settings(LECTURE_RECORDING_DURATIONS_FILE=path):
            atomic_json(path, build_duration_index(source))
            detail = self.client.get(self.url()).json()
            chosen = next(r for r in detail['resources'] if r['kind'] == 'transcript')
            self.assertEqual(self.client.get(chosen['url']).content.decode(), 'Main transcript')
            self.assertEqual((detail['recordings'], detail['transcripts']), (1, 1))
            self.assertEqual(LectureResource.objects.filter(kind='transcript').count(), 2)

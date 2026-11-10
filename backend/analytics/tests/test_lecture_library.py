import copy
import datetime as dt
import io
import json
import tempfile
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase

from analytics.attendance.graph_client import GraphError
from analytics.lectures.importer import import_source
from analytics.lectures.media import open_recording
from analytics.models import (
    Course, DashboardAccess, Enrollment, Learner, LearnerIdentity,
)


from lecture_library.models import LectureCapture, LectureLearner, LectureResource


class LectureLibraryTests(TestCase):
    databases = {'default', 'lecture_library'}

    def test_failed_library_write_rolls_back_only_library_transaction(self):
        from analytics.database_transfer import manifest
        before = manifest('default', [Course, Learner, Enrollment, LearnerIdentity])
        with mock.patch.object(LectureResource.objects, 'update_or_create', side_effect=RuntimeError('Synthetic failure')):
            with self.assertRaises(RuntimeError):
                self.load()
        self.assertEqual(LectureCapture.objects.count(), 0)
        self.assertEqual(LectureLearner.objects.count(), 0)
        self.assertEqual(manifest('default', [Course, Learner, Enrollment, LearnerIdentity]), before)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "evidence.json"
        self.course = Course.objects.create(source_id=83334, title="Planning")
        self.other_course = Course.objects.create(source_id=121642, title="Risk")
        self.roster = []
        for i in range(5):
            learner = Learner.objects.create(source_id=100 + i, name=f"Current learner {i}", email=f"learner{i}@example.invalid")
            LearnerIdentity.objects.create(source_id=100 + i, canonical_source_id=100 + i, email=learner.email, name=learner.name)
            Enrollment.objects.create(course=self.course, learner=learner)
            self.roster.append(learner)
        LearnerIdentity.objects.create(source_id=999, canonical_source_id=100, email="verified-alias@example.invalid", name="Unrelated display label")
        self.other = Learner.objects.create(source_id=900, name="Different module learner", email="other@example.invalid")
        Enrollment.objects.create(course=self.other_course, learner=self.other)
        self.user = User.objects.create_user("library_viewer")
        DashboardAccess.objects.create(user=self.user)
        self.client.force_login(self.user)
        self.start = "2026-10-01T10:00:00+00:00"
        self.end = "2026-10-01T12:00:00+00:00"
        self.subject = "Level 7 Diploma in Project Management"

    def record(self, email, identity, start=None, end=None, name="External meeting label"):
        a, b = start or self.start, end or self.end
        return {"id": identity, "emailAddress": email, "identity": {"id": identity, "displayName": name},
                "totalAttendanceInSeconds": 7200, "engagements": [],
                "attendanceIntervals": [{"joinDateTime": a, "leaveDateTime": b}]}

    def source(self):
        rows = [self.record(x.email, f"identity-{i}") for i, x in enumerate(self.roster)]
        rows += [self.record("verified-alias@example.invalid", "alias-identity"),
                 self.record("outside@example.invalid", "outsider", name=self.roster[0].name),
                 self.record(self.other.email, "other-course")]
        report = {"subject": self.subject, "meeting_id": "meeting", "organizer_id": "owner", "report_id": "report", "start": self.start, "end": self.end, "expected": len(rows)}
        return {
            "previous": {"attendance": {"reports": [report]}, "historical_sources": []},
            "attendance_records": {"meeting|report": rows},
            "series": [{"subject": self.subject, "meeting_id": "meeting", "organizer_id": "owner",
                        "recordings": [{"id": "recording", "callId": "report", "createdDateTime": self.start}],
                        "transcripts": [{"id": "transcript", "callId": "report", "createdDateTime": self.start}]}],
            "transcript_contents": {"transcript": {"text": "Private source transcript", "vtt": "WEBVTT\n"}},
            "timed_transcripts": [{"meeting_id": "meeting", "call_id": "report", "readable": True, "segments": [
                {"name": self.roster[0].name, "start": self.start, "end": "2026-10-01T10:00:20+00:00"},
                {"name": self.roster[0].name, "start": "2026-10-01T10:00:10+00:00", "end": "2026-10-01T10:00:30+00:00"},
            ]}],
        }

    def load(self, source=None, bindings=None):
        self.path.write_text(json.dumps(source or self.source()), encoding="utf8")
        bp = None
        if bindings is not None:
            bp = Path(self.tmp.name) / "bindings.json"
            bp.write_text(json.dumps(bindings), encoding="utf8")
        return import_source(self.path, bp)

    def lecture(self):
        return LectureCapture.objects.get(active=True)

    def url(self):
        return f"/api/lectures/{self.lecture().pk}"

    def test_api_exposes_only_current_enrolled_canonical_learners(self):
        self.load()
        body = self.client.get(self.url()).json()
        self.assertEqual([x["learner_id"] for x in body["participants"]], [100, 101, 102, 103, 104])
        self.assertEqual([x["name"] for x in body["participants"]], [x.name for x in self.roster])
        self.assertNotIn("External meeting label", json.dumps(body))
        self.assertNotIn("outside@example.invalid", json.dumps(body))
        self.assertNotIn("Different module learner", json.dumps(body))
        self.assertNotIn("organizer_id", body)
        for row in body["participants"]:
            self.assertIsNone(row["speaking_seconds"])
            self.assertIsNone(row["camera_on"])
            self.assertIsNone(row["microphone_on"])

    def test_aliases_and_duplicate_report_ids_do_not_double_count_presence(self):
        source = self.source()
        second = {**source["previous"]["attendance"]["reports"][0], "report_id": "alias-report"}
        source["previous"]["attendance"]["reports"].append(second)
        source["attendance_records"]["meeting|alias-report"] = copy.deepcopy(source["attendance_records"]["meeting|report"])
        self.load(source)
        self.assertEqual(LectureCapture.objects.count(), 1)
        self.assertEqual(LectureLearner.objects.count(), 5)
        self.assertEqual(LectureLearner.objects.get(learner_source_id=self.roster[0].source_id).duration_seconds, 7200)
        pk = self.lecture().pk
        self.load(source)
        self.assertEqual(self.lecture().pk, pk)
        self.assertEqual(LectureLearner.objects.count(), 5)

    def test_same_day_parts_merge_and_exclude_gaps_from_presence(self):
        source = self.source()
        second = {**source["previous"]["attendance"]["reports"][0], "report_id": "second", "start": "2026-10-01T14:00:00+00:00", "end": "2026-10-01T16:00:00+00:00", "expected": 5}
        source["previous"]["attendance"]["reports"].append(second)
        source["attendance_records"]["meeting|second"] = [self.record(x.email, f"new-{i}", second["start"], second["end"]) for i, x in enumerate(self.roster)]
        self.load(source)
        self.assertEqual(LectureCapture.objects.filter(active=True).count(), 1)
        row = LectureLearner.objects.get(learner_source_id=self.roster[0].source_id)
        self.assertEqual(row.duration_seconds, 14400)
        self.assertEqual(row.first_join.hour, 10)
        self.assertEqual(row.last_leave.hour, 16)
        self.assertEqual(set(self.lecture().report_ids), {"report", "second"})

    def test_registry_or_enrollment_change_immediately_removes_evidence(self):
        self.load()
        Enrollment.objects.filter(course=self.course, learner=self.roster[0]).delete()
        body = self.client.get(self.url()).json()
        self.assertEqual(len(body["participants"]), 4)
        self.assertNotIn(100, [x["learner_id"] for x in body["participants"]])
        # A now-ambiguous email must not keep identifying the original learner.
        LearnerIdentity.objects.create(source_id=800, canonical_source_id=900, email=self.roster[1].email, name="Shared email")
        body = self.client.get(self.url()).json()
        row = next(x for x in body["participants"] if x["learner_id"] == 101)
        self.assertIsNone(row["duration_seconds"])
        self.assertEqual(row["attendance"], "not_available")

    def test_directory_name_alone_does_not_match_speech_and_verified_binding_unions_intervals(self):
        self.load()
        self.assertIsNone(LectureLearner.objects.get(learner_source_id=self.roster[0].source_id).speaking_seconds)
        binding = [{"meeting_id": "meeting", "call_id": "report", "speaker_name": self.roster[0].name,
                    "identity_id": "identity-0", "verified": True, "evidence": "Owner-verified identity evidence"}]
        self.load(bindings=binding)
        row = LectureLearner.objects.get(learner_source_id=self.roster[0].source_id)
        self.assertEqual(row.speaking_seconds, 30)
        self.assertTrue(row.speaker_verified)
        binding[0]["identity_id"] = "outsider"
        with self.assertRaises(ValueError):
            self.load(bindings=binding)
        self.assertEqual(LectureLearner.objects.get(learner_source_id=self.roster[0].source_id).speaking_seconds, 30)

    def speech_source(self):
        source = self.source()
        for record in source["attendance_records"]["meeting|report"]:
            if record["emailAddress"] in {self.roster[0].email, "verified-alias@example.invalid"}:
                record["identity"]["displayName"] = "Meeting learner zero"
        for segment in source["timed_transcripts"][0]["segments"]:
            segment["name"] = "Meeting learner zero"
        return source

    def test_unique_meeting_name_with_verified_email_yields_labelled_transcript_estimate(self):
        source = self.speech_source()
        # Duplicate transcript revisions must not double-count the same utterance.
        source["timed_transcripts"].append(copy.deepcopy(source["timed_transcripts"][0]))
        self.load(source)
        row = LectureLearner.objects.get(learner_source_id=self.roster[0].source_id)
        self.assertEqual(row.speaking_seconds, 30)
        self.assertEqual(row.speaking_match, "meeting_name")
        self.assertFalse(row.speaker_verified)
        self.assertFalse(row.speaking_partial)
        body = self.client.get(self.url()).json()
        participant = next(x for x in body["participants"] if x["learner_id"] == 100)
        self.assertEqual(participant["speaking_seconds"], 30)
        self.assertEqual(participant["speaking_match"], "meeting_name")
        self.assertEqual(body["speaking_covered"], 1)
        self.assertEqual(body["verified_speakers"], 0)
        self.assertEqual(participant["name"], self.roster[0].name)
        self.assertNotIn("Meeting learner zero", json.dumps(body))

    def test_outsider_with_same_display_name_blocks_automatic_speech_match(self):
        source = self.speech_source()
        source["attendance_records"]["meeting|report"][-2]["identity"]["displayName"] = "Meeting learner zero"
        self.load(source)
        self.assertIsNone(LectureLearner.objects.get(learner_source_id=self.roster[0].source_id).speaking_seconds)
        self.assertEqual(self.client.get(self.url()).json()["speaking_covered"], 0)

    def test_speech_outside_presence_is_excluded_and_partial_estimate_is_explicit(self):
        source = self.speech_source()
        source["timed_transcripts"][0]["segments"].append({"name": "Meeting learner zero", "start": "2026-10-01T13:00:00Z", "end": "2026-10-01T13:05:00Z"})
        self.load(source)
        row = LectureLearner.objects.get(learner_source_id=self.roster[0].source_id)
        self.assertEqual(row.speaking_seconds, 30)
        self.assertTrue(row.speaking_partial)

    def test_other_meeting_and_similar_names_do_not_attribute_speech(self):
        source = self.speech_source()
        for segment in source["timed_transcripts"][0]["segments"]:
            segment["name"] = "Meeting learner zero junior"
        self.load(source)
        self.assertIsNone(LectureLearner.objects.get(learner_source_id=self.roster[0].source_id).speaking_seconds)
        source = self.speech_source()
        source["timed_transcripts"][0]["call_id"] = "unrelated-call"
        self.load(source)
        self.assertIsNone(LectureLearner.objects.get(learner_source_id=self.roster[0].source_id).speaking_seconds)

    def test_registry_change_revalidates_speaking_emails_even_if_attendance_still_matches(self):
        self.load(self.speech_source())
        LearnerIdentity.objects.filter(source_id=999).update(canonical_source_id=900)
        body = self.client.get(self.url()).json()
        row = next(x for x in body["participants"] if x["learner_id"] == 100)
        self.assertEqual(row["duration_seconds"], 7200)
        self.assertIsNone(row["speaking_seconds"])
        self.assertIsNone(row["speaking_match"])
        self.assertEqual(body["speaking_covered"], 0)

    def test_small_staff_sessions_and_incomplete_reports_do_not_create_lectures(self):
        source = self.source()
        source["series"] = []  # Report-only sessions have no independent media to retain.
        source["attendance_records"]["meeting|report"] = [self.record("staff@example.invalid", "staff")]
        source["previous"]["attendance"]["reports"][0]["expected"] = 1
        self.load(source)
        self.assertEqual(LectureCapture.objects.count(), 0)
        source["previous"]["attendance"]["reports"][0]["expected"] = 10
        self.assertEqual(self.load(source)["incomplete_reports"], 1)
        self.assertEqual(LectureCapture.objects.count(), 0)

    def test_historical_evidence_is_not_erased_by_a_shorter_live_report(self):
        source = self.source()
        source["previous"]["historical_sources"] = [
            {"range": "AT_Lectures!A1:D2", "values": [["LectureID", "CourseID", "LectureDate"], ["historical", "83334", "2026-10-01"]]},
            {"range": "AT_Learners!A1:C2", "values": [["LearnerID", "LearnerName", "RegisteredEmail"], ["100", "Old name", self.roster[0].email]]},
            {"range": "AT_Attendance!A1:H2", "values": [["LearnerID", "LectureID", "EffectiveStatus", "FirstJoinUTC", "LastLeaveUTC", "DurationSeconds"], ["100", "historical", "Present", "2026-10-01T09:00:00Z", self.end, 10800]]},
        ]
        self.load(source)
        self.assertEqual(LectureCapture.objects.count(), 1)
        self.assertEqual(self.lecture().source_key, "historical")
        self.assertEqual(LectureLearner.objects.get(learner_source_id=self.roster[0].source_id).duration_seconds, 10800)

    def historical_only_media_source(self):
        source = self.source()
        source["previous"]["attendance"]["reports"] = []
        source["attendance_records"] = {}
        source["previous"]["historical_sources"] = [
            {"range": "AT_Lectures!A1:C2", "values": [["LectureID", "CourseID", "LectureDate"], ["historical", "83334", "2026-10-01"]]},
            {"range": "AT_Learners!A1:C2", "values": [["LearnerID", "LearnerName", "RegisteredEmail"], ["100", "Archived learner", self.roster[0].email]]},
            {"range": "AT_Attendance!A1:F2", "values": [["LearnerID", "LectureID", "EffectiveStatus", "FirstJoinUTC", "LastLeaveUTC", "DurationSeconds"], ["100", "historical", "Present", self.start, self.end, 7200]]},
        ]
        for kind in ("recordings", "transcripts"):
            source["series"][0][kind][0]["endDateTime"] = self.end
        return source

    def test_media_can_link_to_unique_historical_timing_without_a_report(self):
        result = self.load(self.historical_only_media_source())
        self.assertEqual(result["media_window_links"], 1)
        self.assertEqual(self.lecture().resources.count(), 2)
        self.assertEqual(LectureLearner.objects.count(), 1)
        self.assertIsNone(LectureLearner.objects.get().speaking_seconds)

    def test_same_day_unlinked_media_is_grouped_without_inventing_attendance(self):
        source = self.historical_only_media_source()
        for kind in ("recordings", "transcripts"):
            source["series"][0][kind][0].update(createdDateTime="2026-10-01T16:00:00Z", endDateTime="2026-10-01T18:00:00Z")
        self.load(source)
        self.assertEqual(self.lecture().source_key, "historical")
        self.assertEqual(self.lecture().resources.count(), 2)
        self.assertEqual(self.lecture().ends_at.hour, 18)
        self.assertEqual(LectureLearner.objects.count(), 1)
        self.assertEqual(LectureLearner.objects.get().duration_seconds, 7200)
        self.assertIsNone(LectureLearner.objects.get().speaking_seconds)

    def test_overlapping_historical_windows_merge_without_duplicate_presence(self):
        source = self.historical_only_media_source()
        source["previous"]["historical_sources"][0]["values"].append(["second", "83334", "2026-10-01"])
        source["previous"]["historical_sources"][2]["values"].append(["100", "second", "Present", self.start, self.end, 7200])
        self.load(source)
        self.assertEqual(LectureCapture.objects.filter(active=True).count(), 1)
        self.assertEqual(self.lecture().resources.count(), 2)
        self.assertEqual(LectureLearner.objects.get().duration_seconds, 7200)

    def test_archived_gaps_are_preserved_when_adjacent_part_is_merged(self):
        source = self.historical_only_media_source()
        source["series"] = []
        source["previous"]["historical_sources"][0]["values"].append(["later", "83334", "2026-10-01"])
        attendance = source["previous"]["historical_sources"][2]["values"]
        # The first/last envelope includes a one-hour break; only two hours count.
        attendance[1] = ["100", "historical", "Present", "2026-10-01T09:00:00Z", "2026-10-01T12:00:00Z", 7200]
        attendance.append(["100", "later", "Present", "2026-10-01T12:00:00Z", "2026-10-01T13:00:00Z", 3600])
        self.load(source)
        row = LectureLearner.objects.get()
        self.assertEqual(row.duration_seconds, 10800)
        self.assertEqual(row.first_join.hour, 9)
        self.assertEqual(row.last_leave.hour, 13)

    def test_short_recording_parts_merge_into_attended_day_and_keep_every_file(self):
        source = self.speech_source()
        source["series"][0]["recordings"].insert(0, {
            "id": "short-start", "callId": "short-call", "createdDateTime": "2026-10-01T09:59:00Z",
            "endDateTime": "2026-10-01T09:59:26Z",
        })
        result = self.load(source)
        self.assertEqual(result["merged_same_day_windows"], 1)
        self.assertEqual(self.lecture().source_label, "Teams attendance")
        body = self.client.get(self.url()).json()
        self.assertEqual(body["recordings"], 2)
        self.assertEqual(body["readable_transcripts"], 1)
        self.assertEqual(body["speaking_covered"], 1)
        self.assertEqual(body["present"], 5)
        self.assertEqual(LectureLearner.objects.get(learner_source_id=self.roster[0].source_id).duration_seconds, 7200)
        pk = self.lecture().pk
        self.load(source)
        self.assertEqual(self.lecture().pk, pk)
        self.assertEqual(self.lecture().resources.count(), 3)

    def test_overlapping_speech_in_different_calls_is_unioned_after_day_merge(self):
        source = self.speech_source()
        second = {**source["previous"]["attendance"]["reports"][0], "report_id": "second",
                  "start": "2026-10-01T10:00:10Z", "end": "2026-10-01T12:00:10Z", "expected": 5}
        source["previous"]["attendance"]["reports"].append(second)
        source["attendance_records"]["meeting|second"] = [
            self.record(x.email, f"second-{i}", second["start"], second["end"], name="Meeting learner zero" if i == 0 else f"Participant {i}")
            for i, x in enumerate(self.roster)]
        source["series"][0]["transcripts"].append({"id": "second-transcript", "callId": "second", "createdDateTime": second["start"]})
        source["timed_transcripts"].append({"meeting_id": "meeting", "call_id": "second", "readable": True,
                                             "segments": [{"name": "Meeting learner zero", "start": "2026-10-01T10:00:20Z", "end": "2026-10-01T10:00:40Z"}]})
        self.load(source)
        row = LectureLearner.objects.get(learner_source_id=self.roster[0].source_id)
        self.assertEqual(row.duration_seconds, 7210)
        self.assertEqual(row.speaking_seconds, 40)

    def test_grouping_uses_cairo_date_and_keeps_modules_and_unconfirmed_archive_distinct(self):
        source = self.source()
        source["previous"]["attendance"]["reports"] = []
        source["attendance_records"] = {}
        source["series"][0]["transcripts"] = []
        source["series"][0]["recordings"] = [
            {"id": "next-local-day", "callId": "night", "createdDateTime": "2026-10-01T23:30:00Z"},
            {"id": "same-local-day", "callId": "day", "createdDateTime": "2026-10-02T10:00:00Z"},
            {"id": "earlier-local-day", "callId": "earlier", "createdDateTime": self.start},
        ]
        other_course = Course.objects.create(source_id=135945, title="Operations")
        Enrollment.objects.create(course=other_course, learner=self.roster[0])
        source["series"].append({"subject": "Andrew Operations and Information Management for Project Managers",
                                 "meeting_id": "other", "organizer_id": "owner", "transcripts": [],
                                 "recordings": [{"id": "other-module", "createdDateTime": self.start}]})
        source["series"].append({"subject": "PCP Alfanar Group", "meeting_id": "archive", "organizer_id": "owner", "transcripts": [],
                                 "recordings": [{"id": "unconfirmed", "provider": "onedrive", "drive_id": "drive", "createdDateTime": self.start}]})
        self.load(source)
        self.assertEqual(LectureCapture.objects.filter(active=True).count(), 4)
        local_day = LectureCapture.objects.get(course_source_id=self.course.source_id, session_date="2026-10-02")
        self.assertEqual(local_day.resources.count(), 2)
        self.assertEqual(LectureCapture.objects.get(course_source_id=None).observations.count(), 0)

    def test_undated_evidence_is_not_merged_by_missing_date(self):
        source = self.source()
        source["previous"]["attendance"]["reports"] = []
        source["series"] = []
        source["previous"]["historical_sources"] = [{"range": "AT_Lectures!A1:C3", "values": [
            ["LectureID", "CourseID", "LectureDate"], ["undated-a", "83334", ""], ["undated-b", "83334", ""]]}]
        self.load(source)
        self.assertEqual(LectureCapture.objects.filter(active=True).count(), 2)

    def test_bookmarked_fragment_and_resource_resolve_to_merged_day(self):
        old = LectureCapture.objects.create(source_key="old-fragment", course_source_id=self.course.source_id, session_date="2026-10-01", source_fingerprint="old")
        asset = LectureResource.objects.create(lecture=old, kind="transcript", source_id="transcript", organizer_id="owner", meeting_id="meeting", readable=True)
        self.load()
        response = self.client.get(f"/api/lectures/{old.pk}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], str(self.lecture().pk))
        response = self.client.get(f"/api/lectures/{old.pk}/resources/{asset.pk}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), "Private source transcript")

    def test_recordings_are_not_hidden_by_staff_only_or_missing_attendance(self):
        source = self.source()
        source["previous"]["attendance"]["reports"] = []
        source["attendance_records"] = {}
        result = self.load(source)
        self.assertEqual(result["recording_sessions"], 1)
        self.assertEqual(self.lecture().source_label, "Recording session")
        self.assertEqual(self.lecture().resources.count(), 2)
        body = self.client.get(self.url()).json()
        self.assertTrue(body["recording_session"])
        self.assertEqual(len(body["participants"]), 5)
        self.assertTrue(all(x["attendance"] == "not_available" for x in body["participants"]))

    def test_recording_parts_of_one_call_share_a_stable_session(self):
        source = self.source()
        source["previous"]["attendance"]["reports"] = []
        source["attendance_records"] = {}
        source["series"][0]["recordings"].append({"id": "second-part", "callId": "report", "createdDateTime": "2026-10-01T11:00:00Z", "endDateTime": self.end})
        self.load(source)
        pk = self.lecture().pk
        self.assertEqual(self.lecture().resources.filter(kind="recording").count(), 2)
        self.load(source)
        self.assertEqual(self.lecture().pk, pk)
        self.assertEqual(LectureCapture.objects.filter(active=True).count(), 1)

    def test_unplayable_source_files_remain_listed_without_a_playback_url(self):
        source = self.source()
        source["series"][0]["recordings"][0]["content_available"] = False
        self.load(source)
        body = self.client.get(self.url()).json()
        recording = next(x for x in body["resources"] if x["kind"] == "recording")
        self.assertEqual(body["recordings"], 1)
        self.assertEqual(body["playable_recordings"], 0)
        self.assertFalse(recording["available"])
        self.assertIsNone(recording["url"])

    def test_unknown_series_cannot_attach_media_by_window(self):
        source = self.historical_only_media_source()
        source["series"][0]["subject"] = "Unapproved similar course title"
        self.load(source)
        self.assertEqual(self.lecture().resources.count(), 0)

    def test_project_archive_does_not_infer_module_or_learner_participation(self):
        source = self.source()
        source["previous"]["attendance"]["reports"] = []
        source["attendance_records"] = {}
        series = source["series"][0]
        series.update(subject="PCP Alfanar Group", transcripts=[])
        series["recordings"][0].update(provider="onedrive", drive_id="original-drive")
        self.load(source)
        pk = self.lecture().pk
        self.assertIsNone(self.lecture().course_source_id)
        body = self.client.get(self.url()).json()
        self.assertIsNone(body["course_id"])
        self.assertEqual(body["participants"], [])
        self.assertEqual(body["learners"], 0)
        self.assertEqual(body["recordings"], 1)
        self.assertEqual(self.client.get("/api/lectures").json()["total_recordings"], 1)
        self.assertEqual(self.client.get("/api/lectures?course=83334").json()["count"], 0)
        self.load(source)
        self.assertEqual(self.lecture().pk, pk)
        self.assertEqual(LectureResource.objects.get().provider, "onedrive")

    def test_onedrive_stream_uses_exact_item_reference(self):
        requests = []
        fake = SimpleNamespace(_token="synthetic-token", authenticate=lambda: None,
                               _opener=SimpleNamespace(open=lambda request, timeout: requests.append(request) or io.BytesIO(b"video")))
        asset = SimpleNamespace(provider="onedrive", drive_id="drive!id", source_id="item/id")
        open_recording(asset, "bytes=0-8", client=fake)
        self.assertEqual(requests[0].full_url, "https://graph.microsoft.com/v1.0/drives/drive%21id/items/item%2Fid/content")
        self.assertEqual(requests[0].get_header("Range"), "bytes=0-8")

    def test_unknown_provider_and_incomplete_drive_reference_are_rejected(self):
        for values in ({"provider": "unknown"}, {"provider": "onedrive"}):
            source = self.source()
            source["series"][0]["recordings"][0].update(values)
            with self.assertRaises(ValueError):
                self.load(source)

    def test_authentication_and_resource_ownership(self):
        self.load()
        self.client.logout()
        self.assertIn(self.client.get("/api/lectures").status_code, (401, 403))
        self.assertIn(self.client.get(self.url()).status_code, (401, 403))
        self.client.force_login(self.user)
        transcript = LectureResource.objects.get(kind="transcript")
        url = f"{self.url()}/resources/{transcript.pk}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), "Private source transcript")
        self.assertEqual(response["Cache-Control"], "private, no-store")
        other = LectureCapture.objects.create(source_key="other", course_source_id=self.other_course.source_id, source_fingerprint="a")
        self.assertEqual(self.client.get(f"/api/lectures/{other.pk}/resources/{transcript.pk}").status_code, 404)
        self.assertEqual(self.client.get("/api/lectures?from=bad-date").status_code, 400)

    def test_recording_range_streaming_does_not_expose_provider_headers(self):
        self.load()
        asset = LectureResource.objects.get(kind="recording")
        upstream = io.BytesIO(b"mp4-bytes")
        upstream.status = 206
        upstream.headers = {"Content-Length": "9", "Content-Range": "bytes 0-8/100", "Location": "https://private.invalid/signed", "Authorization": "private-token"}
        with mock.patch("analytics.lectures.media.open_recording", return_value=upstream) as opened:
            response = self.client.get(f"{self.url()}/resources/{asset.pk}", HTTP_RANGE="bytes=0-8")
            self.assertEqual(response.status_code, 206)
            self.assertEqual(b"".join(response.streaming_content), b"mp4-bytes")
            self.assertEqual(response["Content-Range"], "bytes 0-8/100")
            self.assertNotIn("Location", response)
            self.assertNotIn("Authorization", response)
            self.assertEqual(opened.call_args.args[1], "bytes=0-8")
        with mock.patch("analytics.lectures.media.open_recording") as opened:
            self.assertEqual(self.client.get(f"{self.url()}/resources/{asset.pk}", HTTP_RANGE="bytes=0-1,5-8").status_code, 416)
            opened.assert_not_called()

    def test_bearer_token_is_not_forwarded_to_microsoft_file_redirect(self):
        requests = []
        def open_url(request, timeout):
            requests.append(request)
            if len(requests) == 1:
                raise urllib.error.HTTPError(request.full_url, 302, "Redirect", {"Location": "https://tenant.sharepoint.com/recording"}, io.BytesIO())
            return io.BytesIO(b"video")
        fake = SimpleNamespace(_token="synthetic-token", authenticate=lambda: None, _opener=SimpleNamespace(open=open_url))
        asset = SimpleNamespace(organizer_id="owner", meeting_id="meeting", source_id="recording")
        with mock.patch("analytics.lectures.media.GraphClient.from_environment", return_value=fake):
            open_recording(asset)
        self.assertEqual(requests[0].get_header("Authorization"), "Bearer synthetic-token")
        self.assertIsNone(requests[1].get_header("Authorization"))
        fake._opener.open = lambda *a, **k: (_ for _ in ()).throw(urllib.error.HTTPError("", 302, "", {"Location": "https://untrusted.invalid"}, io.BytesIO()))
        with mock.patch("analytics.lectures.media.GraphClient.from_environment", return_value=fake), self.assertRaises(GraphError):
            open_recording(asset)

    def test_initial_media_connection_retries_transient_failure_once(self):
        upstream = io.BytesIO(b"video")
        fake = SimpleNamespace(_token="synthetic-token", authenticate=lambda: None, _sleep=mock.Mock(),
                               _opener=SimpleNamespace(open=mock.Mock(side_effect=[urllib.error.URLError("temporary"), upstream])))
        asset = SimpleNamespace(provider="onedrive", drive_id="drive", source_id="item")
        self.assertIs(open_recording(asset, "bytes=0-8", client=fake), upstream)
        self.assertEqual(fake._opener.open.call_count, 2)
        fake._sleep.assert_called_once_with(0.5)
        fake._opener.open.reset_mock(side_effect=True)
        fake._opener.open.side_effect = urllib.error.URLError("still unavailable")
        with self.assertRaises(urllib.error.URLError):
            open_recording(asset, client=fake)
        self.assertEqual(fake._opener.open.call_count, 2)

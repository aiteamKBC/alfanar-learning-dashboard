"""Fictional timing fixtures only; private workbooks/aliases must never enter Git."""
import copy
import datetime as dt
import hashlib
import io
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import openpyxl
from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import IntegrityError
from django.test import TestCase

from analytics import metrics
from analytics.attendance.importer import read_workbook
from analytics.attendance.timing import TimingValidationError, import_timing, timestamp
from analytics.importer.exclusions import NON_LEARNER_SOURCE_IDS
from analytics.importer.identities import MERGED_LEARNER_IDS
from analytics.models import (Course, DashboardAccess, Enrollment, Learner, LearnerIdentity,
                              LectureSession, LectureTiming, SessionAttendance, TeamsMeeting, TimingImport)
from .teams_fixtures import PARTICIPANTS, REPORTS as BASE_REPORTS, write_teams_workbook

# This fixture's organiser joins five minutes before its other participants.
REPORTS = [(s, "2026-01-05T08:55:00Z" if rid == "R1" else a, b, rid, mid)
           for s, a, b, rid, mid in BASE_REPORTS]


def without_attendance(value):
    """The reconciliation feature may change attendance, never the LMS payload."""
    if isinstance(value, dict):
        return {k: without_attendance(v) for k, v in value.items() if k != "attendance"}
    if isinstance(value, (list, tuple)):
        return [without_attendance(v) for v in value]
    return value


class LectureTimingTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "fictional-teams.xlsx"
        self.policy_file = self.root / "private-policy.json"
        self.course = Course.objects.create(source_id=9001, title="Synthetic Module A")
        self.learners = {}
        for uid in (101, 102, 103):
            self.learners[uid] = Learner.objects.create(source_id=uid, name=f"Fictional {uid}", email=f"learner{uid}@example.invalid")
            Enrollment.objects.create(learner=self.learners[uid], course=self.course)
        self.meeting = TeamsMeeting.objects.create(meeting_id="matrix:9001", subject=self.course.title,
                                                   course_source_id=9001, evidence_source="attendance_matrix")
        self.sessions = {}
        for day in ("2026-01-05", "2026-01-07"):
            self.sessions[day] = LectureSession.objects.create(meeting=self.meeting, session_date=day,
                                                               delivered=True, report_count=1, verified_learners=1)
        SessionAttendance.objects.create(session=self.sessions["2026-01-05"], learner_source_id=101, attended=True)
        SessionAttendance.objects.create(session=self.sessions["2026-01-05"], learner_source_id=102, attended=False)
        self.policy = {"version": 1, "meetings": [{"meeting_id": "MEET-A", "course_source_id": 9001,
                                                   "approved_subject": self.course.title}], "aliases": [],
                       "verified_lecture_dates": {"9001": ["2026-01-05", "2026-01-07"]}}
        self.write_source()
        self.write_policy()

    def write_source(self, reports=None, participants=None):
        write_teams_workbook(self.source, reports=reports or REPORTS, participants=participants or PARTICIPANTS)

    def write_policy(self):
        self.policy_file.write_text(json.dumps(self.policy), encoding="utf-8")

    def run_import(self, **kwargs):
        return import_timing(self.source, self.policy_file, **kwargs)

    def record(self, uid=101, day="2026-01-05"):
        return LectureTiming.objects.get(import_run__active=True, learner_source_id=uid, session_date=day)

    def add_verified_alias(self):
        # Same verified Teams identity, but a different approved email. No name matching.
        wb = openpyxl.load_workbook(self.source)
        ws = wb["Teams attendance"]
        for row in ws.iter_rows(min_row=4):
            if row[7].value in ("rec-102", "rec-alt"):
                row[8].value = "verified-identity-two"
        wb.save(self.source)
        wb.close()
        self.policy["aliases"] = [{"email": "l2-alt@example.invalid", "learner_source_id": 102,
                                   "identity_id": "verified-identity-two", "tenant_id": "tenant"}]
        self.write_policy()

    def test_union_across_reports_devices_rejoins_and_duplicate_rows(self):
        self.run_import()
        rec = self.record()
        self.assertEqual(rec.duration_microseconds, 45 * 60 * 1_000_000)
        self.assertEqual(rec.visit_count, 2)
        self.assertEqual(rec.first_join, timestamp("2026-01-05T09:00:00Z"))
        self.assertEqual(rec.last_leave, timestamp("2026-01-05T11:35:00Z"))
        self.assertEqual(self.record(102).duration_microseconds, 30 * 60 * 1_000_000)
        self.assertEqual({r["report_id"] for r in rec.provenance}, {"R1", "R1b"})

    def test_alias_preserves_raw_absence_and_lms_while_reconciling_effective_attendance(self):
        self.add_verified_alias()
        before = (metrics.summary(), metrics.charts(), metrics.learner_directory(),
                  [metrics.learner_detail(l) for l in self.learners.values()])
        attendance = list(SessionAttendance.objects.order_by("id").values())
        self.run_import()
        self.assertEqual(self.record(102).duration_microseconds, 50 * 60 * 1_000_000)
        self.assertEqual(attendance, list(SessionAttendance.objects.order_by("id").values()))
        self.assertEqual(without_attendance(before), without_attendance((metrics.summary(), metrics.charts(), metrics.learner_directory(),
                                 [metrics.learner_detail(l) for l in self.learners.values()])))
        matrix = metrics.module_matrix(Enrollment.objects.get(learner=self.learners[102]))
        self.assertEqual(matrix["attendance"]["sessions"][0]["status"], "attended")
        self.assertEqual(matrix["attendance"]["sessions"][0]["reconciliation"]["matrix_status"], "absent")
        self.assertTrue(matrix["attendance"]["sessions"][0]["reconciliation"]["conflict"])
        self.assertTrue(matrix["attendance"]["sessions"][0]["timing"]["available"])
        self.assertIsNone(matrix["attendance"]["live_seconds"])

    def test_alias_requires_exact_stable_identity_anchor(self):
        self.add_verified_alias()
        self.policy["aliases"][0]["identity_id"] = "unverified"
        self.write_policy()
        with self.assertRaises(TimingValidationError):
            self.run_import()
        self.assertFalse(TimingImport.objects.exists())

    def test_unknown_email_matching_display_name_is_not_accepted(self):
        self.run_import()
        self.assertEqual(self.record(102).duration_microseconds, 30 * 60 * 1_000_000)
        self.assertFalse(any(p["attendance_record_id"] == "rec-alt" for p in self.record(102).provenance))

    def test_approved_canonical_identity_aliases_are_preserved(self):
        LearnerIdentity.objects.create(source_id=111, canonical_source_id=101, name="Fictional alias", email="alias@example.invalid")
        extra = ("R1b", "canonical-alias", "Unrelated display name", "alias@example.invalid", "Presenter",
                 [("2026-01-05T11:35:00Z", "2026-01-05T11:40:00Z")])
        self.write_source(participants=[*PARTICIPANTS, extra])
        self.run_import()
        self.assertEqual(self.record().duration_microseconds, 50 * 60 * 1_000_000)
        self.assertFalse(LectureTiming.objects.filter(learner_source_id=111).exists())

    def test_excluded_and_noncanonical_accounts_never_match(self):
        for uid in (next(iter(NON_LEARNER_SOURCE_IDS)), next(iter(MERGED_LEARNER_IDS))):
            learner = Learner.objects.create(source_id=uid, name="Excluded fictional", email="l2-alt@example.invalid")
            Enrollment.objects.create(learner=learner, course=self.course)
            self.run_import()
            self.assertFalse(LectureTiming.objects.filter(learner_source_id=uid).exists())
            learner.delete()

    def test_not_enrolled_learner_is_not_counted(self):
        Enrollment.objects.filter(learner=self.learners[102]).delete()
        self.run_import()
        self.assertFalse(LectureTiming.objects.filter(learner_source_id=102).exists())

    def test_timing_visible_without_binary_observation_and_missing_is_null(self):
        participants = [*PARTICIPANTS, ("R1", "rec-103-timing", "Fictional", "learner103@example.invalid", "Presenter",
                                      [("2026-01-05T09:10:00Z", "2026-01-05T09:10:41Z")])]
        self.write_source(participants=participants)
        self.run_import()
        data = metrics.module_matrix(Enrollment.objects.get(learner=self.learners[103]))["attendance"]
        self.assertFalse(data["available"])
        self.assertTrue(data["timing_available"])
        self.assertIsNone(data["pct"])
        self.assertEqual([s["status"] for s in data["sessions"]], ["unavailable", "unavailable"])
        self.assertEqual(data["sessions"][0]["timing"]["duration_seconds"], 41)
        self.assertIsNone(data["sessions"][1]["timing"]["duration_seconds"])
        self.assertEqual(data["sessions"][0]["timing"]["timezone"], "UTC")

    def test_confirmed_advanced_dates_still_require_explicit_policy_approval(self):
        advanced = Course.objects.create(source_id=154268, title="Advanced Project and Logistics Management")
        Enrollment.objects.create(learner=self.learners[101], course=advanced)
        meeting = TeamsMeeting.objects.create(meeting_id="matrix:154268", subject=advanced.title,
                                              course_source_id=154268, evidence_source="attendance_matrix")
        for day in ("2026-10-05", "2026-10-07", "2026-10-12", "2026-11-02"):
            LectureSession.objects.create(meeting=meeting, session_date=day, delivered=True, report_count=1, verified_learners=1)
        reports = [(advanced.title, f"{d}T09:00:00Z", f"{d}T10:00:00Z", f"ADV-{d}", "MEET-ADV")
                   for d in ("2026-10-05", "2026-10-07", "2026-10-12", "2026-11-02")]
        participants = [(rid, rid + "-person", "Fictional", "learner101@example.invalid", "Presenter", [(a, b)])
                        for subject, a, b, rid, mid in reports]
        self.write_source(reports=reports, participants=participants)
        self.policy["meetings"].append({"meeting_id": "MEET-ADV", "course_source_id": 154268, "approved_subject": advanced.title})
        self.policy["verified_lecture_dates"]["154268"] = []
        self.write_policy()
        result = self.run_import()
        self.assertEqual((result["date_review"], result["verified"]), (4, 0))
        self.assertEqual(LectureTiming.objects.count(), 4)
        data = metrics.module_matrix(Enrollment.objects.get(learner=self.learners[101], course=advanced))["attendance"]
        self.assertTrue(all(s["timing"]["first_join"] is None for s in data["sessions"]))
        before = metrics.summary()
        self.policy["verified_lecture_dates"]["154268"] = [r[1][:10] for r in reports]
        self.write_policy()
        result = self.run_import()
        self.assertEqual((result["ambiguous_date"], result["verified"]), (0, 4))
        data = metrics.module_matrix(Enrollment.objects.get(learner=self.learners[101], course=advanced))["attendance"]
        self.assertTrue(all(s["timing"]["duration_seconds"] == 3600 for s in data["sessions"]))
        self.assertEqual(without_attendance(before), without_attendance(metrics.summary()))
        self.assertFalse(self.run_import()["changed"])

    def test_unresolved_date_hold_still_overrides_policy_approval(self):
        with patch("analytics.attendance.timing.AMBIGUOUS_DATES", {(9001, dt.date(2026, 1, 5))}):
            result = self.run_import()
            self.assertEqual((result["ambiguous_date"], result["verified"]), (2, 1))
            data = metrics.module_matrix(Enrollment.objects.get(learner=self.learners[101]))["attendance"]
            self.assertIsNone(data["sessions"][0]["timing"]["first_join"])

    def test_off_calendar_evidence_is_retained_without_creating_lectures(self):
        self.sessions["2026-01-07"].delete()
        result = self.run_import()
        self.assertEqual(result["off_calendar"], 1)
        self.assertEqual(self.record(day="2026-01-07").status, "off_calendar")
        self.assertEqual(LectureSession.objects.count(), 1)
        self.assertEqual(len(metrics.module_matrix(Enrollment.objects.get(learner=self.learners[101]))["attendance"]["sessions"]), 1)

    def test_current_date_not_in_verified_policy_is_pending(self):
        self.policy["verified_lecture_dates"]["9001"] = ["2026-01-05"]
        self.write_policy()
        self.assertEqual(self.run_import()["date_review"], 1)

    def test_verified_subject_alias_works_with_different_lms_title(self):
        self.course.source_id, self.course.title = 83334, "PCP source title"
        self.course.save()
        self.meeting.course_source_id = 83334
        self.meeting.save()
        reports = [("Level 7 Diploma in Project Management", a, b, rid, mid) if mid == "MEET-A" else (s, a, b, rid, mid)
                   for s, a, b, rid, mid in REPORTS]
        self.write_source(reports=reports)
        self.policy["meetings"][0].update(course_source_id=83334, approved_subject="Level 7 Diploma in Project Management")
        self.policy["verified_lecture_dates"] = {"83334": ["2026-01-05", "2026-01-07"]}
        self.write_policy()
        self.assertEqual(self.run_import()["verified"], 3)

    def test_same_subject_different_meeting_id_does_not_match(self):
        reports = [*REPORTS, (self.course.title, "2026-01-05T12:00:00Z", "2026-01-05T13:00:00Z", "OTHER", "UNAPPROVED")]
        participants = [*PARTICIPANTS, ("OTHER", "other-person", "Fictional", "learner101@example.invalid", "Presenter",
                                      [("2026-01-05T12:00:00Z", "2026-01-05T13:00:00Z")])]
        self.write_source(reports=reports, participants=participants)
        self.run_import()
        self.assertEqual(self.record().duration_microseconds, 45 * 60 * 1_000_000)

    def test_offsets_are_converted_and_cross_date_reports_stay_pending(self):
        self.assertEqual(timestamp("2026-01-06T01:00:00+03:00"), timestamp("2026-01-05T22:00:00Z"))
        reports = [(self.course.title, "2026-01-05T23:50:00Z", "2026-01-06T00:10:00Z", "BOUNDARY", "MEET-A")]
        participants = [("BOUNDARY", "person", "Fictional", "learner101@example.invalid", "Presenter",
                         [("2026-01-05T23:55:00Z", "2026-01-06T00:05:00Z")])]
        self.write_source(reports=reports, participants=participants)
        self.assertEqual(self.run_import()["boundary_review"], 1)
        self.assertEqual(self.record().duration_microseconds, 600 * 1_000_000)

    def test_timezone_offset_and_subsecond_duration_are_not_rounded_per_interval(self):
        reports = [(self.course.title, "2026-01-06T01:00:00+03:00", "2026-01-06T01:10:00+03:00", "OFFSET", "MEET-A")]
        participants = [("OFFSET", "person", "Fictional", "learner101@example.invalid", "Presenter",
                         [("2026-01-06T01:00:00.1000000+03:00", "2026-01-06T01:00:00.9000000+03:00"),
                          ("2026-01-06T01:00:01.1000000+03:00", "2026-01-06T01:00:01.9000000+03:00")])]
        self.write_source(reports=reports, participants=participants)
        self.run_import()
        rec = self.record()
        self.assertEqual(rec.first_join.date(), dt.date(2026, 1, 5))
        self.assertEqual(rec.duration_microseconds, 1_600_000)
        self.assertEqual(rec.visit_count, 2)

    def test_invalid_inputs_fail_atomically_and_keep_active_snapshot(self):
        self.run_import()
        before = list(LectureTiming.objects.values())
        fingerprint = TimingImport.objects.get(active=True).fingerprint
        original = read_workbook(self.source)
        for field, value in (("Leave time (UTC)", "invalid"), ("Duration (seconds)", -1),
                             ("Email", "wrong@example.invalid"), ("Report ID", "missing")):
            tables = copy.deepcopy(original)
            tables["intervals"][0][field] = value
            with patch("analytics.attendance.timing.read_workbook", return_value=tables):
                with self.assertRaises(TimingValidationError):
                    self.run_import()
            self.assertEqual(fingerprint, TimingImport.objects.get(active=True).fingerprint)
            self.assertEqual(before, list(LectureTiming.objects.values()))

    def test_conflicting_report_id_or_subject_is_rejected(self):
        original = read_workbook(self.source)
        for field in ("Meeting subject", "Meeting ID"):
            tables = copy.deepcopy(original)
            conflicting = dict(tables["reports"][0])
            conflicting[field] = "changed"
            tables["reports"].append(conflicting)
            with patch("analytics.attendance.timing.read_workbook", return_value=tables):
                with self.assertRaises(TimingValidationError):
                    self.run_import()

    def test_repeat_import_is_noop_dry_run_does_not_write_and_source_unchanged(self):
        source_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.assertTrue(self.run_import(dry_run=True)["changed"])
        self.assertFalse(TimingImport.objects.exists())
        self.assertTrue(self.run_import()["changed"])
        before = list(LectureTiming.objects.values())
        self.assertFalse(self.run_import()["changed"])
        self.assertEqual(before, list(LectureTiming.objects.values()))
        self.assertEqual(TimingImport.objects.count(), 1)
        self.assertEqual(source_hash, hashlib.sha256(self.source.read_bytes()).hexdigest())

    def test_new_snapshot_keeps_previous_evidence_and_atomic_failure_keeps_active(self):
        self.run_import()
        original = TimingImport.objects.get(active=True)
        self.policy["verified_lecture_dates"]["9001"] = ["2026-01-05"]
        self.write_policy()
        with patch.object(LectureTiming.objects, "bulk_create", side_effect=IntegrityError("synthetic failure")):
            with self.assertRaises(IntegrityError):
                self.run_import()
        self.assertEqual(TimingImport.objects.get(active=True).id, original.id)
        self.run_import()
        self.assertEqual(TimingImport.objects.count(), 2)
        self.assertEqual(original.records.count(), 3)
        self.assertEqual(TimingImport.objects.filter(active=True).count(), 1)

    def test_matrix_recreation_preserves_timing_but_requires_existing_lecture(self):
        self.run_import()
        rec = self.record()
        self.sessions["2026-01-05"].delete()
        self.assertTrue(LectureTiming.objects.filter(pk=rec.pk).exists())
        enrollment = Enrollment.objects.get(learner=self.learners[101])
        self.assertEqual(len(metrics.module_matrix(enrollment)["attendance"]["sessions"]), 1)
        LectureSession.objects.create(meeting=self.meeting, session_date="2026-01-05", delivered=True,
                                      report_count=1, verified_learners=1)
        data = metrics.module_matrix(enrollment)["attendance"]
        self.assertTrue(data["available"])
        self.assertEqual(data["sessions"][0]["timing"]["duration_seconds"], 2700)
        self.assertEqual(data["sessions"][0]["status"], "attended")
        self.assertEqual(data["sessions"][0]["reconciliation"]["reason_code"], "timing_fills_missing")

    def test_private_provenance_is_not_in_authenticated_api_and_auth_stays_required(self):
        self.run_import()
        endpoint = "/api/learners/101/courses/9001/matrix"
        self.assertIn(self.client.get(endpoint).status_code, (401, 403))
        user = User.objects.create_user(username="fictional-viewer")
        DashboardAccess.objects.create(user=user)
        self.client.force_login(user)
        response = self.client.get(endpoint)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        for private in ("@example.invalid", "provenance", "report_id", "attendance_record_id", "MEET-A", "source_sha256"):
            self.assertNotIn(private, body)
        self.assertEqual(response.json()["attendance"]["sessions"][0]["timing"]["duration_seconds"], 2700)

    def test_command_outputs_aggregate_counts_only(self):
        output = io.StringIO()
        call_command("import_lecture_timing", source=str(self.source), config=str(self.policy_file), dry_run=True, stdout=output)
        result = json.loads(output.getvalue())
        self.assertEqual(result["verified"], 3)
        self.assertNotIn("@", output.getvalue())
        self.assertFalse(TimingImport.objects.exists())

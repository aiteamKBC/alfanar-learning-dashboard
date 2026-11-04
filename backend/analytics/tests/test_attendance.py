import datetime as dt
import hashlib
import shutil
import tempfile
from pathlib import Path

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from analytics.attendance.importer import merge_intervals, run_attendance_import
from analytics.attendance.mapping import map_subject
from analytics.importer.service import run_sync
from analytics.models import DashboardAccess, LectureSession, SessionAttendance, TeamsMeeting

from .fixtures import write_workbook
from .teams_fixtures import write_teams_workbook

D = dt.datetime


class MergeIntervalTests(TestCase):
    def test_merge_overlapping_touching_and_duplicate(self):
        a = D(2026, 1, 1, 9, 0)
        ivs = [(a, a + dt.timedelta(minutes=30)), (a + dt.timedelta(minutes=20), a + dt.timedelta(minutes=40)),
               (a + dt.timedelta(minutes=40), a + dt.timedelta(minutes=50)),       # touching -> merged
               (a, a + dt.timedelta(minutes=30)),                                    # exact duplicate
               (a + dt.timedelta(hours=2), a + dt.timedelta(hours=2, minutes=5))]  # separate
        self.assertEqual(merge_intervals(ivs), [(a, a + dt.timedelta(minutes=50)),
                                                (a + dt.timedelta(hours=2), a + dt.timedelta(hours=2, minutes=5))])


class ModuleMappingTests(TestCase):
    COURSES = {9001: "Synthetic Module A", 83334: "PCP Al-Fanar 2025"}

    def test_exact_title(self):
        self.assertEqual(map_subject("  synthetic   module a ", self.COURSES)[:2], (9001, "exact_title"))

    def test_approved_rule(self):
        self.assertEqual(map_subject("Level 7 Diploma in Project Management", self.COURSES)[:2], (83334, "approved_rule"))

    def test_unknown_subject_stays_unmapped(self):
        self.assertEqual(map_subject("PCP planning call", self.COURSES)[:2], (None, "unmapped"))  # no filename/keyword guessing

    def test_approved_rule_requires_existing_course(self):
        self.assertEqual(map_subject("Level 7 Diploma in Project Management", {9001: "x"})[:2], (None, "unmapped"))


@override_settings(TEAMS_MIN_ATTENDANCE_MINUTES=10, TEAMS_MIN_LEARNERS_FOR_DELIVERED=2)
class AttendanceImportTests(TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        run_sync("excel", path=str(write_workbook(self.tmp / "lms.xlsx")))
        self.teams = write_teams_workbook(self.tmp / "teams.xlsx")
        self.aliases = self.tmp / "aliases.csv"  # absent unless a test writes it
        self.override = override_settings(TEAMS_ALIASES_PATH=self.aliases)
        self.override.enable()

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def presence(self, learner, day="2026-01-05", subject="Synthetic Module A"):
        rec = SessionAttendance.objects.filter(learner_source_id=learner, session__session_date=day,
                                               session__meeting__subject=subject).first()
        return rec.presence_seconds if rec else None

    def test_import_and_source_unchanged(self):
        digest = hashlib.sha256(self.teams.read_bytes()).hexdigest()
        run = run_attendance_import(self.teams)
        self.assertEqual(run.status, "success", run.errors)
        self.assertEqual(hashlib.sha256(self.teams.read_bytes()).hexdigest(), digest)

    def test_learner_matching_and_exclusions(self):
        d = run_attendance_import(self.teams).diagnostics
        cls = d["participant_rows_by_class"]
        self.assertEqual(cls["learner"], 6)                      # 101 x4 records (case-insensitive), 102, 103
        self.assertEqual(cls["organizer"], 1)
        self.assertEqual(cls["training_provider_staff"], 1)
        self.assertEqual(cls["unmatched_external"], 1)
        self.assertEqual(cls["anonymous_or_external_no_email"], 1)
        self.assertEqual(cls["unmatched_learner_domain"], 1)     # l2-alt@ -> flagged for alias review
        self.assertEqual(d["matched_learners"], 3)
        self.assertEqual(d["name_only_candidates_not_accepted"], 0)  # 102 matched by email elsewhere
        # Name-only row never attributed to learner 102:
        self.assertEqual(self.presence(102), 30 * 60)

    def test_verified_alias_is_used(self):
        self.aliases.write_text("teams_email,learner_id\nl2-alt@example.invalid,102\n", encoding="utf-8")
        run = run_attendance_import(self.teams)
        self.assertEqual(run.status, "success", run.errors)
        self.assertEqual(self.presence(102), 50 * 60)            # 30 min + 20 min alias interval
        self.assertEqual(run.diagnostics["matched_by_alias"], 1)  # learners with at least one alias-matched record

    def test_bad_alias_file_fails_and_preserves_data(self):
        run_attendance_import(self.teams)
        before = SessionAttendance.objects.count()
        self.aliases.write_text("teams_email,learner_id\nsomeone@example.invalid,999999\n", encoding="utf-8")
        run = run_attendance_import(self.teams)
        self.assertEqual(run.status, "failed")
        self.assertEqual(SessionAttendance.objects.count(), before)

    def test_duplicates_and_overlaps(self):
        d = run_attendance_import(self.teams).diagnostics
        self.assertEqual(d["duplicate_interval_rows_removed"], 1)
        # 101: device A 09:00-09:30 + device B 09:20-09:40 (overlap) + rejoin 11:30-11:35 in a 2nd report
        self.assertEqual(self.presence(101), 45 * 60)            # not 30+20+5=55
        rec = SessionAttendance.objects.get(learner_source_id=101, session__session_date="2026-01-05")
        self.assertEqual(rec.interval_count, 2)

    def test_reports_are_not_lectures_and_delivered_rule(self):
        run_attendance_import(self.teams)
        a = LectureSession.objects.filter(meeting__subject="Synthetic Module A")
        self.assertEqual(a.count(), 2)                           # 3 reports -> 2 occurrences (dates)
        day1 = a.get(session_date="2026-01-05")
        self.assertEqual(day1.report_count, 2)
        self.assertTrue(day1.delivered)
        self.assertFalse(a.get(session_date="2026-01-07").delivered)  # 1 learner, 1 minute -> link-open

    def test_module_mapping_and_unmapped_kept_separate(self):
        run_attendance_import(self.teams)
        self.assertEqual(TeamsMeeting.objects.get(subject="Synthetic Module A").mapping_status, "exact_title")
        x = TeamsMeeting.objects.get(subject="Unmapped Workshop")
        self.assertEqual((x.mapping_status, x.course_source_id), ("unmapped", None))
        self.assertTrue(SessionAttendance.objects.filter(learner_source_id=103).exists())  # stored for review

    def test_idempotent_and_survives_lms_resync(self):
        run_attendance_import(self.teams)
        snap = list(SessionAttendance.objects.order_by("pk").values_list("pk", "presence_seconds"))
        again = run_attendance_import(self.teams)
        self.assertFalse(again.changed)
        run_sync("excel", path=str(self.tmp / "lms.xlsx"))      # LMS/Google sync must not touch attendance
        self.assertEqual(snap, list(SessionAttendance.objects.order_by("pk").values_list("pk", "presence_seconds")))

    def test_missing_sheet_fails_and_preserves_data(self):
        run_attendance_import(self.teams)
        before = SessionAttendance.objects.count()
        broken = write_teams_workbook(self.tmp / "broken.xlsx", drop_sheet="Join and leave")
        run = run_attendance_import(broken)
        self.assertEqual(run.status, "failed")
        self.assertEqual(SessionAttendance.objects.count(), before)

    # ---- API ---------------------------------------------------------------------------
    def client_login(self):
        u = User.objects.create_user("viewer", password="not-a-real-pass-123")
        DashboardAccess.objects.create(user=u)
        self.client.force_login(u)

    def test_api_attendance_and_missing_denominator(self):
        run_attendance_import(self.teams)
        self.client_login()
        k = self.client.get("/api/dashboard/summary").json()["kpis"]["attendance"]
        # Module A: 1 delivered session x 2 enrolled learners, both attended. Module B: no Teams data.
        self.assertEqual((k["attended"], k["expected"], k["pct"]), (2, 2, 100.0))
        self.assertEqual(k["delivered_sessions"], 1)
        self.assertEqual(k["modules_without_data"], [9002])
        self.assertEqual(k["live_seconds"], 45 * 60 + 30 * 60)
        b = self.client.get("/api/dashboard/summary?course=9002").json()["kpis"]["attendance"]
        self.assertFalse(b["available"])
        self.assertIsNone(b["pct"])                              # no denominator -> no percentage
        mods = {m["course_id"]: m["delivered_sessions"] for m in self.client.get("/api/dashboard/charts").json()["module_progress"]}
        self.assertEqual(mods, {9001: 1, 9002: None})

    def test_owner_confirmed_lectures_do_not_change_teams_attendance(self):
        from unittest import mock
        from analytics.attendance import confirmed
        run_attendance_import(self.teams)
        self.client_login()
        before = self.client.get("/api/dashboard/summary").json()["kpis"]["attendance"]
        rows = SessionAttendance.objects.count()
        cfg = {9001: {"count": 3, "confirmed_by": "Project owner", "confirmed_on": "2026-11-04", "note": "test"}}
        with mock.patch.dict(confirmed.CONFIRMED_DELIVERED_LECTURES, cfg, clear=True):
            mods = {m["course_id"]: m for m in self.client.get("/api/dashboard/charts").json()["module_progress"]}
            after = self.client.get("/api/dashboard/summary").json()["kpis"]["attendance"]
        a = mods[9001]["delivered_lectures"]
        self.assertEqual((a["count"], a["source"], a["teams_sessions"], a["differs_from_teams"]), (3, "owner_confirmed", 1, True))
        self.assertEqual(mods[9001]["delivered_sessions"], 1)    # Teams evidence kept separately
        b = mods[9002]["delivered_lectures"]
        self.assertEqual((b["count"], b["source"]), (None, None))  # no Teams data, no confirmation -> not invented
        self.assertEqual(before, after)                          # attendance unchanged by a display-only confirmation
        self.assertEqual(SessionAttendance.objects.count(), rows)
        with mock.patch.dict(confirmed.CONFIRMED_DELIVERED_LECTURES, {}, clear=True):
            m = {m["course_id"]: m for m in self.client.get("/api/dashboard/charts").json()["module_progress"]}
        self.assertEqual((m[9001]["delivered_lectures"]["count"], m[9001]["delivered_lectures"]["source"]), (1, "teams"))

    def test_unmatched_learner_is_unknown_not_absent(self):
        run_attendance_import(self.teams)
        SessionAttendance.objects.filter(learner_source_id=102).delete()  # 102 now has no matched Teams identity
        self.client_login()
        k = self.client.get("/api/dashboard/summary").json()["kpis"]["attendance"]
        # 102 is excluded from numerator AND denominator (not 1 / 2 = 50%).
        self.assertEqual((k["attended"], k["expected"], k["pct"]), (1, 1, 100.0))
        self.assertEqual((k["enrollments_covered"], k["enrollments_measured"]), (2, 1))
        self.assertEqual((k["enrollments_without_teams_identity"], k["learners_without_teams_identity"]), (1, 1))
        m = self.client.get("/api/learners/102/courses/9001/matrix").json()["attendance"]
        self.assertFalse(m["available"])
        self.assertIs(m["teams_identity"], False)
        self.assertEqual(m["sessions"], [])                      # no per-session "not recorded" rows
        self.assertIn("unknown", m["reason"])
        row = next(r for r in self.client.get("/api/learners").json()["results"] if r["learner_id"] == 102)
        self.assertFalse(row["attendance"]["available"])
        self.assertIsNone(row["attendance"]["pct"])
        self.assertEqual(row["attendance"]["modules_without_teams_identity"], 1)

    def test_api_learner_views(self):
        run_attendance_import(self.teams)
        self.client_login()
        m = self.client.get("/api/learners/101/courses/9001/matrix").json()["attendance"]
        self.assertEqual((m["delivered"], m["attended"], len(m["sessions"])), (1, 1, 1))
        self.assertEqual(m["sessions"][0]["status"], "attended")
        nb = self.client.get("/api/learners/101/courses/9002/matrix").json()["attendance"]
        self.assertFalse(nb["available"])
        self.assertIn("reason", nb)
        row = next(r for r in self.client.get("/api/learners").json()["results"] if r["learner_id"] == 103)
        self.assertFalse(row["attendance"]["available"])         # 103 only in module B (no Teams data)
        status = self.client.get("/api/sync/status").json()["teams_attendance"]
        self.assertEqual(status["last_status"], "success")

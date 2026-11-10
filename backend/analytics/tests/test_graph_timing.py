"""Synthetic Graph responses only. No live credentials, HTTP or learner data in tests."""
import copy
import datetime as dt
import io
import json
import urllib.error
import urllib.parse
from collections import defaultdict
from unittest.mock import Mock

import openpyxl
from django.test import SimpleTestCase, TestCase

from analytics import metrics
from analytics.attendance.graph_client import GraphClient, GraphError, NoRedirect
from analytics.attendance.graph_timing import import_graph_timing
from analytics.attendance.importer import read_workbook
from analytics.attendance.timing import TimingValidationError, prepare
from analytics.attendance.mapping import map_subject
from analytics.models import Course, Enrollment, LectureSession, LectureTiming, SessionAttendance, TeamsMeeting, TimingImport
from . import test_lecture_timing as fixture

ORGANIZER = "11111111-1111-4111-8111-111111111111"


class FakeGraph:
    def __init__(self, tables):
        self.reports, self.records = {}, defaultdict(list)
        intervals = defaultdict(list)
        for row in tables["intervals"]:
            intervals[(row["Report ID"], row["Attendance record ID"])].append({
                "joinDateTime": row["Join time (UTC)"], "leaveDateTime": row["Leave time (UTC)"],
                "durationInSeconds": row["Duration (seconds)"]})
        for row in tables["attendance"]:
            rid, pid = row["Report ID"], row["Attendance record ID"]
            self.records[rid].append({"id": pid, "emailAddress": row["Email"], "role": row["Role"],
                "identity": {"id": row["Identity ID"], "tenantId": row["Tenant ID"]},
                "attendanceIntervals": intervals[(rid, pid)],
                "totalAttendanceInSeconds": sum(i["durationInSeconds"] for i in intervals[(rid, pid)])})
        for row in tables["reports"]:
            if row["Meeting ID"] != "MEET-A":
                continue
            rid = row["Report ID"]
            self.reports[rid] = {"id": rid, "meetingStartDateTime": row["Meeting start (UTC)"],
                "meetingEndDateTime": row["Meeting end (UTC)"], "totalParticipantCount": len(self.records[rid])}
        self.list_error = None
        self.record_errors = set()
        self.requests = []

    def authenticate(self):
        return None

    def collection(self, path):
        self.requests.append(path)
        if path.endswith("/attendanceReports"):
            if self.list_error:
                raise self.list_error
            return copy.deepcopy(list(self.reports.values())), 1
        rid = path.split("/")[-2]
        if rid in self.record_errors:
            raise GraphError(503, "IncompletePage")
        if rid not in self.records:
            raise GraphError(404, "NotFound")
        return copy.deepcopy(self.records[rid]), 1

    def get(self, path):
        self.requests.append(path)
        rid = path.split("/")[-1]
        if rid not in self.reports:
            raise GraphError(404, "NotFound")
        return copy.deepcopy(self.reports[rid])


class GraphTimingTests(TestCase):
    def setUp(self):
        self.f = fixture.LectureTimingTests(methodName="test_command_outputs_aggregate_counts_only")
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        workbook = openpyxl.load_workbook(self.f.source)
        for row in workbook["Teams reports"].iter_rows(min_row=4):
            row[8].value = ORGANIZER
        workbook.save(self.f.source)
        workbook.close()
        self.graph = FakeGraph(read_workbook(self.f.source))
        self.cache = self.f.root / "private-graph-cache"

    def run_graph(self, **options):
        return import_graph_timing(self.f.source, self.f.policy_file, self.cache, client=self.graph, **options)

    def test_graph_primary_preserves_kpis_and_is_idempotent(self):
        self.f.run_import()
        before = (metrics.summary(), metrics.charts(), metrics.learner_directory())
        result = self.run_graph()
        self.assertEqual(result["primary_source"], "microsoft_graph")
        self.assertEqual(result["verified_pair_sources"], {"microsoft_graph": 3})
        self.assertEqual(self.f.record().duration_microseconds, 2700 * 1_000_000)
        self.assertEqual(before, (metrics.summary(), metrics.charts(), metrics.learner_directory()))
        self.assertFalse(self.run_graph()["changed"])
        self.assertEqual(TimingImport.objects.filter(active=True).count(), 1)
        self.assertTrue(all(p["source_kind"] == "microsoft_graph" for p in self.f.record().provenance))

    def test_graph_source_noop_dry_run_has_no_database_writes(self):
        result = self.run_graph(dry_run=True)
        self.assertTrue(result["changed"])
        self.assertFalse(TimingImport.objects.exists())
        self.assertEqual(len(list(self.cache.glob("graph-*.json"))), 1)

    def test_complete_graph_missing_old_participant_is_rejected(self):
        self.graph.records["R1"] = self.graph.records["R1"][1:]
        self.graph.reports["R1"]["totalParticipantCount"] -= 1
        result = self.run_graph()
        self.assertEqual(result["incomplete_reports_preserved"], 1)
        self.assertEqual(self.f.record().duration_microseconds, 2700 * 1_000_000)
        self.assertIn("mixed", result["verified_pair_sources"])

    def test_partial_record_collection_keeps_excel_and_other_graph_reports(self):
        self.graph.record_errors.add("R1")
        result = self.run_graph()
        self.assertEqual(result["graph_errors"][0]["status"], 503)
        self.assertEqual(result["verified"], 3)
        self.assertEqual(self.f.record().duration_microseconds, 2700 * 1_000_000)

    def test_missing_participant_page_is_detected_from_report_count(self):
        self.graph.records["R1"].pop()
        result = self.run_graph()
        self.assertEqual(result["graph_errors"][0]["code"], "IncompleteOrInvalidReport")
        self.assertEqual(self.f.record().duration_microseconds, 2700 * 1_000_000)

    def test_empty_graph_preserves_the_active_excel_snapshot(self):
        self.f.run_import()
        before = TimingImport.objects.get(active=True).fingerprint
        self.graph.reports.clear()
        result = self.run_graph()
        self.assertFalse(result["changed"])
        self.assertEqual(result["graph_status"], "unavailable_existing_timing_preserved")
        self.assertEqual(TimingImport.objects.get(active=True).fingerprint, before)

    def test_authentication_failure_keeps_active_snapshot(self):
        self.f.run_import()
        before = TimingImport.objects.get(active=True).fingerprint
        self.graph.authenticate = Mock(side_effect=GraphError(401, "invalid_client", operation="authentication"))
        with self.assertRaises(GraphError):
            self.run_graph()
        self.assertEqual(TimingImport.objects.get(active=True).fingerprint, before)

    def test_expired_report_http_200_stub_and_empty_records_preserve_history(self):
        self.f.run_import()
        before = TimingImport.objects.get(active=True).fingerprint
        self.graph.reports.clear()
        self.graph.records = {rid: [] for rid in ("R1", "R1b", "R2")}
        self.graph.get = lambda path: {"id": path.split("/")[-1], "externalEventInformation": []}
        result = self.run_graph()
        self.assertFalse(result["changed"])
        self.assertEqual(result["graph_errors"][0]["code"], "IncompleteOrInvalidReport")
        self.assertEqual(TimingImport.objects.get(active=True).fingerprint, before)

    def test_empty_or_shorter_intervals_cannot_replace_verified_excel(self):
        record = self.graph.records["R1"][0]
        record["attendanceIntervals"] = []
        record["totalAttendanceInSeconds"] = 0
        result = self.run_graph()
        self.assertEqual(result["incomplete_or_conflicting_records_preserved"], 1)
        self.assertEqual(self.f.record().duration_microseconds, 2700 * 1_000_000)

    def test_source_identity_conflict_is_held_for_review(self):
        self.graph.records["R1"][0]["emailAddress"] = "different@example.invalid"
        result = self.run_graph()
        self.assertEqual(result["identity_conflicts_preserved"], 1)
        self.assertEqual(self.f.record().duration_microseconds, 2700 * 1_000_000)

    def add_report(self, day="2026-01-09"):
        self.graph.reports["NEW"] = {"id": "NEW", "meetingStartDateTime": day + "T09:00:00Z",
                                    "meetingEndDateTime": day + "T10:00:00Z", "totalParticipantCount": 1}
        self.graph.records["NEW"] = [{"id": "new-person", "emailAddress": "learner101@example.invalid", "role": "Presenter",
            "identity": {"id": "new-person", "tenantId": "tenant"}, "totalAttendanceInSeconds": 1800,
            "attendanceIntervals": [{"joinDateTime": day + "T09:00:00Z", "leaveDateTime": day + "T09:30:00Z", "durationInSeconds": 1800}]}]

    def test_additional_exact_verified_date_is_imported_without_new_lecture(self):
        self.add_report("2026-01-06")
        self.f.policy["verified_lecture_dates"]["9001"].append("2026-01-06")
        self.f.write_policy()
        LectureSession.objects.create(meeting=self.f.meeting, session_date="2026-01-06", delivered=True,
                                      report_count=1, verified_learners=1)
        result = self.run_graph()
        self.assertEqual((result["verified"], result["verified_dates"]), (4, 3))
        self.assertEqual(LectureSession.objects.count(), 3)

    def test_private_report_seed_recovers_unlisted_duplicate_intervals_once(self):
        self.add_report("2026-01-05")
        self.f.policy["meetings"][0]["known_report_ids"] = ["NEW"]
        self.f.write_policy()
        original = self.graph.collection

        def without_seed(path):
            values, pages = original(path)
            return ([r for r in values if r["id"] != "NEW"] if path.endswith("/attendanceReports") else values), pages

        self.graph.collection = without_seed
        result = self.run_graph()
        self.assertEqual((result["graph_listed_reports"], result["graph_reports"]), (3, 4))
        self.assertEqual(self.f.record().duration_microseconds, 2700 * 1_000_000)
        self.assertIn("NEW", {p["report_id"] for p in self.f.record().provenance})
        self.assertFalse(self.run_graph()["changed"])
        self.graph.record_errors.add("NEW")
        self.run_graph()
        self.assertIn("NEW", {p["report_id"] for p in self.f.record().provenance})
        self.assertEqual(self.f.record().duration_microseconds, 2700 * 1_000_000)

    def test_new_series_without_excel_rows_can_use_verified_direct_report_ids(self):
        self.add_report("2026-01-05")
        self.f.policy["meetings"].append({"meeting_id": "NEW-SERIES", "course_source_id": 9001,
            "approved_subject": self.f.course.title, "organizer_id": ORGANIZER,
            "known_report_ids": ["NEW"]})
        self.f.write_policy()
        original = self.graph.collection

        def collections(path):
            if path.endswith("/attendanceReports"):
                if "/NEW-SERIES/" in path:
                    raise GraphError(404, "NotFound")
                return [r for rid, r in self.graph.reports.items() if rid != "NEW"], 1
            return original(path)

        self.graph.collection = collections
        self.run_graph()
        self.assertIn("NEW-SERIES", {p["meeting_id"] for p in self.f.record().provenance})
        self.assertEqual(self.f.record().duration_microseconds, 2700 * 1_000_000)
        self.assertFalse(any(p.endswith("/NEW-SERIES") for p in self.graph.requests))

    def test_invalid_private_report_seeds_fail_before_graph_requests(self):
        for value in ("NEW", [""], ["NEW", "NEW"], [123], ["x" * 1025]):
            with self.subTest(value_type=type(value).__name__):
                self.f.policy["meetings"][0]["known_report_ids"] = value
                self.f.write_policy()
                with self.assertRaises(TimingValidationError):
                    self.run_graph()
                self.assertFalse(self.graph.requests)

    def test_off_calendar_new_report_is_retained_and_never_becomes_a_lecture(self):
        self.add_report()
        result = self.run_graph()
        self.assertEqual((result["verified"], result["off_calendar"]), (3, 1))
        self.assertEqual(LectureSession.objects.count(), 2)

    def test_graph_history_survives_expiry_even_when_absent_from_excel(self):
        self.add_report()
        self.run_graph()
        before = self.f.record(day="2026-01-09").duration_microseconds
        del self.graph.reports["NEW"]
        del self.graph.records["NEW"]
        result = self.run_graph()
        self.assertEqual(result["off_calendar"], 1)
        self.assertEqual(result["cached_graph_records_retained"], 1)
        self.assertEqual(self.f.record(day="2026-01-09").duration_microseconds, before)
        self.assertTrue(any(path.endswith("/attendanceReports/NEW") for path in self.graph.requests))
        self.assertFalse(self.run_graph()["changed"])

    def test_incomplete_refresh_cannot_shorten_previously_verified_graph_duration(self):
        record = self.graph.records["R1"][0]
        record["attendanceIntervals"][0]["leaveDateTime"] = "2026-01-05T10:30:00Z"
        record["attendanceIntervals"][0]["durationInSeconds"] = 5400
        record["totalAttendanceInSeconds"] = 5400
        self.run_graph()
        self.assertEqual(self.f.record().duration_microseconds, 5700 * 1_000_000)
        self.graph = FakeGraph(read_workbook(self.f.source))
        self.run_graph()
        self.assertEqual(self.f.record().duration_microseconds, 5700 * 1_000_000)

    def test_excel_command_cannot_downgrade_active_graph_history(self):
        self.run_graph()
        before = TimingImport.objects.get(active=True).fingerprint
        with self.assertRaises(TimingValidationError):
            self.f.run_import()
        self.assertEqual(TimingImport.objects.get(active=True).fingerprint, before)

    def test_organizer_id_is_required_and_never_guessed_from_name(self):
        self.f.policy["meetings"][0]["organizer_id"] = "22222222-2222-4222-8222-222222222222"
        self.f.write_policy()
        with self.assertRaises(TimingValidationError):
            self.run_graph()
        self.assertFalse(TimingImport.objects.exists())


class MultiSeriesGraph:
    """Each fictional series has its own organiser, report list and record scope."""

    def __init__(self, tables):
        self.clients, self.requests = {}, []
        for mid in {r["Meeting ID"] for r in tables["reports"]}:
            reports = [r for r in tables["reports"] if r["Meeting ID"] == mid]
            owner = reports[0]["Organizer ID"]
            ids = {r["Report ID"] for r in reports}
            scoped = {"reports": [{**r, "Meeting ID": "MEET-A"} for r in reports],
                      "attendance": [r for r in tables["attendance"] if r["Report ID"] in ids],
                      "intervals": [r for r in tables["intervals"] if r["Report ID"] in ids]}
            self.clients[(owner, mid)] = FakeGraph(scoped)

    def authenticate(self):
        return None

    def route(self, path):
        self.requests.append(path)
        parts = path.split("/")
        # Reproduce the real distinction: missing meeting metadata does not mean
        # its attendance artifacts are unavailable.
        if len(parts) < 6 or parts[5] != "attendanceReports":
            raise GraphError(404, "NotFound")
        key = (parts[2], urllib.parse.unquote(parts[4]))
        if key not in self.clients:
            raise GraphError(403, "WrongOrganizer")
        return self.clients[key]

    def get(self, path):
        return self.route(path).get(path)

    def collection(self, path):
        return self.route(path).collection(path)


class RiskMultiSeriesTests(TestCase):
    subject = "Module 2-Procurement Risk and Contracts Management"
    second_organizer = "22222222-2222-4222-8222-222222222222"

    def setUp(self):
        self.f = fixture.LectureTimingTests(methodName="test_command_outputs_aggregate_counts_only")
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        risk = Course.objects.create(source_id=121642, title="Synthetic Risk LMS title")
        for learner in self.f.learners.values():
            Enrollment.objects.create(learner=learner, course=risk)
        matrix = TeamsMeeting.objects.create(meeting_id="matrix:121642", subject=risk.title,
                                             course_source_id=121642, evidence_source="attendance_matrix")
        for day in ("2026-01-05", "2026-01-07"):
            session = LectureSession.objects.create(meeting=matrix, session_date=day, delivered=True,
                                                    report_count=1, verified_learners=1)
            SessionAttendance.objects.create(session=session, learner_source_id=102, attended=False)
        reports = [(self.subject, day + "T09:00:00Z", day + "T10:00:00Z", rid, mid)
                   for day, rid, mid in (("2026-01-05", "RA1", "RISK-A"),
                                        ("2026-01-05", "RA2", "RISK-B"),
                                        ("2026-01-07", "RA3", "RISK-B"),
                                        ("2026-01-09", "RA4", "RISK-B"))]
        people = [
            ("RA1", "risk-one", "Fictional 101", "learner101@example.invalid", "Presenter",
             [("2026-01-05T09:00:00Z", "2026-01-05T09:30:00Z"),
              ("2026-01-05T09:40:00Z", "2026-01-05T09:50:00Z")]),
            ("RA2", "risk-two", "Fictional 101", "learner101@example.invalid", "Presenter",
             [("2026-01-05T09:20:00Z", "2026-01-05T09:45:00Z")] * 2),
            ("RA3", "risk-three", "Fictional 102", "learner102@example.invalid", "Presenter",
             [("2026-01-07T09:00:00Z", "2026-01-07T09:20:00Z")]),
            ("RA4", "risk-four", "Fictional 101", "learner101@example.invalid", "Presenter",
             [("2026-01-09T09:00:00Z", "2026-01-09T09:15:00Z")]),
        ]
        self.f.write_source(reports=[*fixture.REPORTS, *reports], participants=[*fixture.PARTICIPANTS, *people])
        workbook = openpyxl.load_workbook(self.f.source)
        for row in workbook["Teams reports"].iter_rows(min_row=4):
            row[8].value = self.second_organizer if row[6].value == "RISK-B" else ORGANIZER
        workbook.save(self.f.source)
        workbook.close()
        for mid, owner in (("RISK-A", ORGANIZER), ("RISK-B", self.second_organizer)):
            self.f.policy["meetings"].append({"meeting_id": mid, "organizer_id": owner,
                                             "course_source_id": 121642, "approved_subject": self.subject})
        self.f.policy["verified_lecture_dates"]["121642"] = ["2026-01-05", "2026-01-07"]
        self.f.write_policy()
        self.graph = MultiSeriesGraph(read_workbook(self.f.source))
        self.cache = self.f.root / "private-graph-cache"

    def run_graph(self):
        return import_graph_timing(self.f.source, self.f.policy_file, self.cache, client=self.graph)

    def record(self, uid=101, day="2026-01-05"):
        return LectureTiming.objects.get(import_run__active=True, course_source_id=121642,
                                         learner_source_id=uid, session_date=day)

    def test_multiple_series_merge_overlaps_preserve_lms_and_raw_flags_and_repeat_is_noop(self):
        before = (metrics.summary(), metrics.charts(), metrics.learner_directory(),
                  list(SessionAttendance.objects.order_by("id").values()))
        result = self.run_graph()
        self.assertEqual((result["verified"], result["off_calendar"]), (5, 1))
        record = self.record()
        self.assertEqual((record.duration_microseconds, record.visit_count), (3000 * 1_000_000, 1))
        self.assertEqual({p["meeting_id"] for p in record.provenance}, {"RISK-A", "RISK-B"})
        self.assertEqual(self.record(day="2026-01-09").status, "off_calendar")
        other = LectureTiming.objects.get(import_run__active=True, course_source_id=9001,
                                           learner_source_id=101, session_date="2026-01-05")
        self.assertEqual(other.duration_microseconds, 2700 * 1_000_000)
        self.assertEqual(fixture.without_attendance(before), fixture.without_attendance((metrics.summary(), metrics.charts(), metrics.learner_directory(),
                                  list(SessionAttendance.objects.order_by("id").values()))))
        self.assertFalse(self.run_graph()["changed"])

    def test_metadata_404_and_failed_list_do_not_block_known_report_ids(self):
        second = self.graph.clients[(self.second_organizer, "RISK-B")]
        second.list_error = GraphError(404, "NotFound")
        self.run_graph()
        self.assertEqual(self.record(102, "2026-01-07").duration_microseconds, 1200 * 1_000_000)
        self.assertTrue(any(p.endswith("/attendanceReports/RA3") for p in self.graph.requests))
        self.assertTrue(all("/attendanceReports" in p for p in self.graph.requests))
        self.assertTrue(any(self.second_organizer + "/onlineMeetings/RISK-B/" in p for p in self.graph.requests))

    def test_missing_series_uses_excel_on_initial_import(self):
        second = self.graph.clients[(self.second_organizer, "RISK-B")]
        second.reports.clear()
        second.records.clear()
        result = self.run_graph()
        self.assertEqual((result["verified"], result["off_calendar"]), (5, 1))
        self.assertEqual(self.record().duration_microseconds, 3000 * 1_000_000)
        self.assertEqual({p["source_kind"] for p in self.record().provenance}, {"microsoft_graph", "teams_excel"})

    def test_missing_series_after_import_preserves_prior_graph_evidence(self):
        self.run_graph()
        evidence = self.record(102, "2026-01-07").provenance
        second = self.graph.clients[(self.second_organizer, "RISK-B")]
        second.reports.clear()
        second.records.clear()
        result = self.run_graph()
        self.assertGreaterEqual(result["cached_graph_records_retained"], 3)
        self.assertEqual(self.record(102, "2026-01-07").provenance, evidence)
        self.assertEqual(self.record().duration_microseconds, 3000 * 1_000_000)
        self.assertFalse(self.run_graph()["changed"])

    def test_empty_graph_keeps_complete_active_multi_series_snapshot(self):
        self.run_graph()
        before = TimingImport.objects.get(active=True).fingerprint
        for client in self.graph.clients.values():
            client.reports.clear()
            client.records.clear()
        self.assertFalse(self.run_graph()["changed"])
        self.assertEqual(TimingImport.objects.get(active=True).fingerprint, before)

    def test_risk_subject_approval_is_timing_only(self):
        self.assertIsNone(map_subject(self.subject, {121642: "Synthetic Risk LMS title"})[0])
        _, rows, _ = prepare(self.f.source, self.f.policy_file)
        self.assertEqual(sum(r["course_source_id"] == 121642 for r in rows), 3)

    def test_duplicate_meeting_and_wrong_course_mapping_are_rejected(self):
        original = copy.deepcopy(self.f.policy)
        for invalid in ("duplicate", "wrong_course"):
            with self.subTest(invalid=invalid):
                self.f.policy = copy.deepcopy(original)
                if invalid == "duplicate":
                    self.f.policy["meetings"].append(copy.deepcopy(original["meetings"][-1]))
                else:
                    self.f.policy["meetings"][-1]["course_source_id"] = 9001
                self.f.write_policy()
                with self.assertRaises(TimingValidationError):
                    self.run_graph()
        self.assertFalse(TimingImport.objects.exists())

    def test_wrong_organizer_for_one_series_fails_before_graph_reads(self):
        self.f.policy["meetings"][-1]["organizer_id"] = ORGANIZER
        self.f.write_policy()
        with self.assertRaises(TimingValidationError):
            self.run_graph()
        self.assertEqual(self.graph.requests, [])
        self.assertFalse(TimingImport.objects.exists())


class GraphClientTests(SimpleTestCase):
    def test_transcript_text_uses_authenticated_graph_path_and_rejects_redirect(self):
        client, opener = self.build_client([io.BytesIO(b'WEBVTT\n')])
        self.assertEqual(client.get('/users/test/onlineMeetings/test/transcripts/t/metadataContent', text=True), 'WEBVTT\n')
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_header('Accept'), 'text/vtt')
        self.assertEqual(request.get_header('Authorization'), 'Bearer synthetic-token')
        with self.assertRaises(GraphError):
            client.get('https://example.invalid/transcript', text=True)
        self.assertEqual(opener.open.call_count, 1)

    def build_client(self, responses):
        opener = Mock()
        opener.open.side_effect = [io.BytesIO(json.dumps(r).encode()) if isinstance(r, dict) else r for r in responses]
        client = GraphClient(ORGANIZER, ORGANIZER, "synthetic-not-a-secret", opener=opener, sleep=Mock())
        client._token, client._expires = "synthetic-token", float("inf")
        return client, opener

    def test_all_pages_followed_and_identical_duplicates_collapsed(self):
        path = "/users/test/onlineMeetings/test/attendanceReports"
        client, opener = self.build_client([{"value": [{"id": "1"}], "@odata.nextLink": "https://graph.microsoft.com/v1.0" + path + "?$skiptoken=next"},
                                      {"value": [{"id": "1"}, {"id": "2"}]}])
        rows, pages = client.collection(path)
        self.assertEqual((len(rows), pages), (2, 2))
        self.assertEqual(opener.open.call_count, 2)

    def test_conflicting_duplicate_id_and_pagination_loop_are_rejected(self):
        client, _ = self.build_client([{"value": [{"id": "1", "x": 1}, {"id": "1", "x": 2}]}])
        with self.assertRaises(GraphError):
            client.collection("/users/test")
        client, _ = self.build_client([{"value": [], "@odata.nextLink": "https://graph.microsoft.com/v1.0/users/test"}])
        with self.assertRaises(GraphError):
            client.collection("/users/test")

    def test_token_never_sent_to_untrusted_or_different_pagination_resource(self):
        for url in ("https://example.invalid/steal", "http://graph.microsoft.com/v1.0/users/test",
                    "https://graph.microsoft.com/beta/users/test", "https://graph.microsoft.com/v1.0/users/other"):
            client, opener = self.build_client([{"value": [], "@odata.nextLink": url}])
            with self.assertRaises(GraphError):
                client.collection("/users/test")
            self.assertEqual(opener.open.call_count, 1)

    def test_rate_limit_retries_and_long_throttle_returns_safely(self):
        error = urllib.error.HTTPError("https://graph.microsoft.com", 429, "throttled", {"Retry-After": "0"},
                                       io.BytesIO(b'{"error":{"code":"TooManyRequests"}}'))
        client, opener = self.build_client([error, {"value": []}])
        self.assertEqual(client.collection("/users/test"), ([], 1))
        self.assertEqual(opener.open.call_count, 2)
        error = urllib.error.HTTPError("https://graph.microsoft.com", 429, "throttled", {"Retry-After": "60"}, io.BytesIO(b'{}'))
        client, _ = self.build_client([error])
        with self.assertRaisesRegex(GraphError, "RetryLater"):
            client.collection("/users/test")

    def test_permission_error_redacts_server_message(self):
        error = urllib.error.HTTPError("https://graph.microsoft.com", 403, "forbidden", {},
            io.BytesIO(b'{"error":{"code":"Forbidden","message":"No application access policy found for this app private@example.invalid"}}'))
        client, _ = self.build_client([error])
        with self.assertRaises(GraphError) as caught:
            client.get("/users/test")
        self.assertIn("ApplicationAccessPolicyMissing", str(caught.exception))
        self.assertNotIn("private@example.invalid", str(caught.exception))

    def test_transient_server_failures_retry_with_bounded_backoff(self):
        for status in (500, 502, 503, 504):
            with self.subTest(status=status):
                errors=[urllib.error.HTTPError('https://graph.microsoft.com',status,'temporary',{},io.BytesIO(b'{}')) for _ in range(4)]
                client,opener=self.build_client(errors)
                with self.assertRaises(GraphError):client.get('/users/test')
                self.assertEqual(opener.open.call_count,4)
                self.assertEqual([call.args[0] for call in client._sleep.call_args_list],[1,2,4])

    def test_credentials_only_sent_to_official_token_endpoint_and_kept_in_memory(self):
        client, opener = self.build_client([{"access_token": "synthetic-token", "expires_in": 3600}])
        client._token = None
        client.authenticate()
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, f"https://login.microsoftonline.com/{ORGANIZER}/oauth2/v2.0/token")
        self.assertIn(b"grant_type=client_credentials", request.data)
        self.assertNotIn("synthetic-not-a-secret", request.full_url)
        self.assertNotIn("synthetic-not-a-secret", repr(client))

    def test_expired_token_refreshes_once_without_exposing_auth_errors(self):
        expired = urllib.error.HTTPError("https://graph.microsoft.com", 401, "expired", {},
                                         io.BytesIO(b'{"error":{"code":"InvalidAuthenticationToken"}}'))
        client, opener = self.build_client([expired, {"access_token": "replacement", "expires_in": 3600}, {"value": []}])
        self.assertEqual(client.collection("/users/test"), ([], 1))
        self.assertEqual(opener.open.call_count, 3)
        failure = urllib.error.HTTPError("https://login.microsoftonline.com", 400, "invalid", {},
            io.BytesIO(b'{"error":"invalid_client","error_description":"synthetic-not-a-secret"}'))
        client, _ = self.build_client([failure])
        client._token = None
        with self.assertRaises(GraphError) as caught:
            client.authenticate()
        self.assertNotIn("synthetic-not-a-secret", str(caught.exception))

    def test_redirects_never_forward_credentials(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://example.invalid"))

import shutil
import tempfile
from pathlib import Path

from django.contrib.auth.models import User
from django.test import TestCase

from analytics.importer.service import run_sync
from analytics.models import DashboardAccess

from .fixtures import write_workbook


class ApiTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        run_sync("excel", path=str(write_workbook(cls.tmp / "synthetic.xlsx")))
        cls.user = User.objects.create_user("viewer", password="not-a-real-pass-123")
        DashboardAccess.objects.create(user=cls.user)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.client.force_login(self.user)


class SecurityTests(ApiTestBase):
    PROTECTED = ["/api/filters", "/api/dashboard/summary", "/api/dashboard/charts", "/api/learners",
                 "/api/learners/101", "/api/learners/101/courses", "/api/learners/101/courses/9001/matrix",
                 "/api/sync/status"]

    def test_anonymous_cannot_read_learner_data(self):
        self.client.logout()
        for url in self.PROTECTED:
            r = self.client.get(url)
            self.assertIn(r.status_code, (401, 403), url)
            self.assertNotIn(b"Test Learner", r.content)

    def test_no_write_endpoints(self):
        for url in self.PROTECTED:
            self.assertEqual(self.client.post(url, {}).status_code, 405, url)

    def test_login_flow(self):
        self.client.logout()
        bad = self.client.post("/api/auth/login", {"username": "viewer", "password": "wrong"}, content_type="application/json")
        self.assertEqual(bad.status_code, 400)
        ok = self.client.post("/api/auth/login", {"username": "viewer", "password": "not-a-real-pass-123"}, content_type="application/json")
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(self.client.get("/api/learners").status_code, 200)


class SummaryMetricTests(ApiTestBase):
    def test_summary_matches_independent_totals(self):
        k = self.client.get("/api/dashboard/summary").json()["kpis"]
        self.assertEqual(k["unique_learners"]["value"], 3)
        self.assertEqual(k["modules"]["value"], 2)
        self.assertEqual(k["enrollments"]["value"], 4)
        self.assertEqual((k["active_learners"]["value"], k["active_learners"]["denominator"]), (2, 3))
        self.assertEqual(k["mean_progress"]["value"], 50.0)   # (50 + 0 + 100) / 3 ; missing excluded
        self.assertEqual(k["mean_progress"]["n"], 3)
        self.assertEqual((k["quiz_completion"]["value"], k["quiz_completion"]["denominator"]), (3, 4))
        self.assertEqual(k["mean_latest_score"]["value"], 76.7)  # (60 + 70 + 100) / 3
        self.assertEqual((k["pass_rate"]["value"], k["pass_rate"]["denominator"]), (2, 3))
        self.assertEqual(k["submissions"]["value"], 1)          # draft excluded
        self.assertEqual(k["submissions"]["total_records"], 2)
        self.assertEqual((k["materials_completed"]["value"], k["materials_completed"]["denominator"]), (5, 8))
        self.assertEqual((k["activity_coverage"]["value"], k["activity_coverage"]["denominator"]), (2, 4))
        self.assertFalse(k["attendance"]["available"])

    def test_course_filter(self):
        k = self.client.get("/api/dashboard/summary?course=9002").json()["kpis"]
        self.assertEqual(k["enrollments"]["value"], 2)
        self.assertIsNone(k["mean_latest_score"]["value"])  # no quizzes -> unavailable, not zero
        self.assertEqual(k["mean_progress"]["value"], 0.0)

    def test_engagement_filter(self):
        k = self.client.get("/api/dashboard/summary?engagement=inactive").json()["kpis"]
        self.assertEqual(k["enrollments"]["value"], 1)
        directory = self.client.get('/api/learners?engagement=inactive').json()
        self.assertEqual(k['unique_learners']['value'], directory['count'])

    def test_bad_filter_rejected(self):
        self.assertEqual(self.client.get("/api/dashboard/summary?course=abc").status_code, 400)
        self.assertEqual(self.client.get("/api/dashboard/charts?activity_type=nope").status_code, 400)

    def test_charts(self):
        c = self.client.get("/api/dashboard/charts").json()
        mods = {m["course_id"]: m for m in c["quiz_by_module"]}
        self.assertEqual(mods[9001]["attempted_pairs"], 3)
        self.assertEqual(mods[9001]["possible_pairs"], 4)
        by_type = {r["type"]: r["count"] for r in c["activity_by_type"]}
        self.assertEqual(by_type, {"quiz": 3, "video": 2, "pdf": 1, "audio": 1, "reading": 1, "assignment": 1})
        self.assertEqual(sum(b["count"] for b in c["progress_distribution"]["buckets"]), 3)
        self.assertEqual(c["progress_distribution"]["missing"], 1)
        recordings = {m["course_id"]: m["lecture_recordings"] for m in c["module_progress"]}
        self.assertEqual(recordings, {9001: 1, 9002: 0})  # one video (501) with completions; none in 9002
        # No Teams data imported in this test -> delivered sessions are not available (None, never 0)
        self.assertEqual({m["course_id"]: m["delivered_sessions"] for m in c["module_progress"]}, {9001: None, 9002: None})
        # module-level fact: unaffected by learner filters
        inactive = self.client.get("/api/dashboard/charts?engagement=inactive").json()
        self.assertEqual({m["course_id"]: m["lecture_recordings"] for m in inactive["module_progress"]}.get(9002), 0)
        matrix = {r["learner_id"]: r["progress"] for r in c["learner_module_matrix"]["rows"]}
        self.assertEqual(matrix[101], {"9001": 50.0, "9002": 0.0})


class LearnerApiTests(ApiTestBase):
    def test_latest_submission_is_included_in_last_lms_activity(self):
        from django.utils import timezone
        from analytics.models import Submission
        stamp = timezone.now().replace(microsecond=0)
        Submission.objects.filter(enrollment__learner__source_id=101).update(updated_at=stamp)
        detail = self.client.get('/api/learners/101').json()
        self.assertEqual(detail['summary']['last_activity'], stamp.isoformat().replace('+00:00','Z'))

    def test_learner_score_rounds_once_across_modules(self):
        from analytics.models import Activity, Enrollment, QuizResult
        QuizResult.objects.filter(enrollment__learner__source_id=101).update(latest_score=60.04)
        enrollment = Enrollment.objects.get(learner__source_id=101,course__source_id=9002)
        activity = Activity.objects.create(course=enrollment.course,kind='quiz',source_id=901,title='Synthetic rounding quiz')
        QuizResult.objects.create(enrollment=enrollment,activity=activity,latest_score=66.65,latest_status='passed')
        detail = self.client.get('/api/learners/101').json()
        self.assertEqual(detail['summary']['mean_latest_score'], round((60.04+66.65)/2,1))

    def test_directory_search_sort_paginate(self):
        r = self.client.get("/api/learners?ordering=-progress&page_size=2").json()
        self.assertEqual(r["count"], 3)
        self.assertEqual(r["pages"], 2)
        self.assertEqual([x["learner_id"] for x in r["results"]], [102, 101])
        last = self.client.get("/api/learners?ordering=-progress&page_size=2&page=2").json()
        self.assertEqual(last["results"][0]["learner_id"], 103)  # unknown progress sorts last
        s = self.client.get("/api/learners?search=three").json()
        self.assertEqual([x["learner_id"] for x in s["results"]], [103])

    def test_directory_module_filter(self):
        r = self.client.get("/api/learners?course=9002").json()
        self.assertEqual({x["learner_id"] for x in r["results"]}, {101, 103})

    def test_learner_detail(self):
        d = self.client.get("/api/learners/101").json()
        self.assertEqual(d["learner"]["learner_id"], 101)
        self.assertEqual(len(d["courses"]), 2)
        self.assertEqual(d["summary"]["quizzes_attempted"], 1)
        self.assertEqual(d["summary"]["mean_latest_score"], 60.0)
        self.assertEqual(self.client.get("/api/learners/99999").status_code, 404)

    def test_module_matrix_relationships(self):
        m = self.client.get("/api/learners/101/courses/9001/matrix").json()
        rows = {(r["kind"], r["activity_id"]): r for r in m["rows"]}
        self.assertEqual(len(rows), 7)  # every known activity of the module
        self.assertTrue(rows[("video", 501)]["recorded"])
        self.assertFalse(rows[("reading", 504)]["recorded"])  # completed by another learner only
        self.assertIsNone(rows[("reading", 504)]["lesson"])   # no record != failure
        quiz = rows[("quiz", 701)]["quiz"]
        self.assertEqual((quiz["latest_score"], quiz["best_score"], len(quiz["history"])), (60.0, 90.0, 2))
        self.assertIsNone(rows[("quiz", 702)]["quiz"])
        self.assertEqual(rows[("assignment", 801)]["assignment"]["status"], "draft")
        f = m["footer"]
        self.assertEqual(f["activities"], {"known": 7, "recorded": 5})  # video, pdf, audio, quiz 701, draft submission
        self.assertEqual(f["quiz"]["known"], 2)
        self.assertEqual(f["quiz"]["recorded"], 1)
        self.assertEqual(f["quiz"]["mean_latest_score"], 60.0)
        self.assertEqual(f["assignment"]["submitted"], 0)
        self.assertEqual(f["recorded_seconds"], 150)  # untimed audio excluded
        self.assertFalse(m["attendance"]["available"])

    def test_matrix_requires_enrollment(self):
        self.assertEqual(self.client.get("/api/learners/103/courses/9001/matrix").status_code, 404)

    def test_query_count_bounded(self):
        with self.assertNumQueries(13):  # Includes one bulk timing read per module, never per learner.
            self.client.get("/api/learners")

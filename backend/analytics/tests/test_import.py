import hashlib
import shutil
import tempfile
from pathlib import Path

from django.test import TestCase

from analytics.importer.normalize import ValidationFailed, normalize
from analytics.importer.service import run_sync
from analytics.importer.sources import SourcePayload, SourceUnavailable, read_excel
from analytics.models import (
    Activity, Course, Enrollment, Learner, LessonRecord, QuizAttempt, QuizResult, Submission, SyncRun,
)

from .fixtures import build_tables, write_workbook


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class WorkbookFixtureMixin:
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmp = Path(tempfile.mkdtemp())
        cls.xlsx = write_workbook(cls.tmp / "synthetic.xlsx")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        super().tearDownClass()

    def counts(self):
        return {m.__name__: m.objects.count() for m in
                (Course, Learner, Enrollment, Activity, LessonRecord, QuizResult, QuizAttempt, Submission)}


class WorkbookLoadingTests(WorkbookFixtureMixin, TestCase):
    def test_workbook_loads_and_all_sheets_inspected(self):
        payload = read_excel(self.xlsx)
        self.assertEqual(set(payload.tables), set(build_tables()))
        ds = normalize(payload.tables)
        self.assertEqual({s["title"] for s in ds.sheets_seen}, set(build_tables()))
        self.assertIsNotNone(payload.source_modified_at)

    def test_source_file_not_modified(self):
        before = sha(self.xlsx)
        run_sync("excel", path=str(self.xlsx))
        run_sync("excel", path=str(self.xlsx))
        self.assertEqual(before, sha(self.xlsx))

    def test_missing_workbook_is_reported(self):
        with self.assertRaises(SourceUnavailable):
            read_excel(self.tmp / "missing.xlsx")


class NormalizationTests(TestCase):
    def setUp(self):
        self.ds = normalize(build_tables())

    def test_learner_deduplication(self):
        # learner 101 is in two modules, learner 102 has a duplicated row -> 3 learners, 4 enrollments
        self.assertEqual(len(self.ds.learners), 3)
        self.assertEqual(len(self.ds.enrollments), 4)
        self.assertTrue(any("duplicate learner/course" in w for w in self.ds.warnings))

    def test_enrollments_map_to_courses(self):
        self.assertEqual(set(self.ds.enrollments), {(101, 9001), (101, 9002), (102, 9001), (103, 9002)})
        self.assertEqual(self.ds.courses[9001]["title"], "Synthetic Module A")

    def test_missing_progress_is_none_not_zero(self):
        self.assertIsNone(self.ds.enrollments[(103, 9002)]["progress_pct"])
        self.assertEqual(self.ds.enrollments[(101, 9002)]["progress_pct"], 0.0)

    def test_untimed_lesson_has_no_duration(self):
        rec = self.ds.lesson_records[(101, 9001, "audio", 503)]
        self.assertIsNone(rec["recorded_seconds"])
        self.assertEqual(self.ds.lesson_records[(101, 9001, "video", 501)]["recorded_seconds"], 120)

    def test_duplicate_lesson_rows_not_double_counted(self):
        self.assertEqual(len(self.ds.lesson_records), 5)

    def test_html_entities_cleaned(self):
        self.assertEqual(self.ds.activities[(9001, "video", 501)]["title"], "V1 – Intro")

    def test_quiz_latest_attempt_logic(self):
        res = self.ds.quiz_results[(101, 9001, 701)]
        self.assertEqual(res["latest_attempt_number"], 2)
        self.assertEqual(res["latest_score"], 60.0)      # latest, not best
        self.assertEqual(res["latest_status"], "failed")
        self.assertEqual(res["best_score"], 90.0)
        self.assertTrue(res["ever_passed"])
        self.assertEqual(len(self.ds.quiz_attempts), 4)  # attempts preserved separately
        self.assertEqual(len(self.ds.quiz_results), 3)   # unique learner-quiz pairs

    def test_missing_scores_not_zero(self):
        tables = build_tables()
        tables["Al Fanar Quiz Attempts"][4][9] = None  # learner 102 quiz 702: score blank
        ds = normalize(tables)
        res = ds.quiz_results[(102, 9001, 702)]
        self.assertIsNone(res["latest_score"])
        self.assertIsNone(ds.quiz_attempts[(102, 9001, 702, 1)]["score"])

    def test_missing_required_sheet_fails_validation(self):
        tables = build_tables()
        del tables["Al Fanar Quiz Attempts"]
        with self.assertRaises(ValidationFailed):
            normalize(tables)

    def test_submission_count_mismatch_flagged(self):
        self.assertTrue(any("Submissions Count" in w for w in self.ds.warnings))


class IdempotencyAndSyncTests(WorkbookFixtureMixin, TestCase):
    def test_import_twice_no_duplicates(self):
        r1 = run_sync("excel", path=str(self.xlsx))
        self.assertEqual(r1.status, "success")
        first = self.counts()
        pks = set(Enrollment.objects.values_list("pk", flat=True))
        r2 = run_sync("excel", path=str(self.xlsx))
        self.assertEqual(r2.status, "success")
        self.assertFalse(r2.changed)
        self.assertEqual(first, self.counts())
        self.assertEqual(pks, set(Enrollment.objects.values_list("pk", flat=True)))
        self.assertEqual(first, {"Course": 2, "Learner": 3, "Enrollment": 4, "Activity": 7, "LessonRecord": 5,
                                 "QuizResult": 3, "QuizAttempt": 4, "Submission": 2})

    def test_update_in_place_not_duplicate(self):
        run_sync("excel", path=str(self.xlsx))
        pk = Enrollment.objects.get(learner__source_id=101, course__source_id=9001).pk
        tables = build_tables()
        tables["Al Fanar Students"][1][9] = 75.0  # progress changed in a later sync
        run = run_sync("google", fetcher=lambda: SourcePayload("google", "sheet-id", tables))
        self.assertEqual(run.status, "success")
        e = Enrollment.objects.get(learner__source_id=101, course__source_id=9001)
        self.assertEqual((e.pk, e.progress_pct), (pk, 75.0))
        self.assertEqual(Learner.objects.count(), 3)

    def test_google_failure_preserves_last_good_data(self):
        run_sync("excel", path=str(self.xlsx))
        before = self.counts()

        def failing():
            raise SourceUnavailable("Google Sheets read failed: simulated outage")

        run = run_sync("google", fetcher=failing)
        self.assertEqual(run.status, "failed")
        self.assertEqual(before, self.counts())
        self.assertEqual(SyncRun.objects.filter(status="success").first().source_type, "excel")

    def test_invalid_payload_preserves_last_good_data(self):
        run_sync("excel", path=str(self.xlsx))
        before = self.counts()
        bad = build_tables()
        del bad["Al Fanar Students"]
        run = run_sync("google", fetcher=lambda: SourcePayload("google", "x", bad))
        self.assertEqual(run.status, "failed")
        self.assertTrue(run.errors)
        self.assertEqual(before, self.counts())

    def test_google_disabled_without_credentials(self):
        with self.settings(GOOGLE_SHEETS_ENABLED=False):
            run = run_sync("google")
        self.assertEqual(run.status, "failed")
        self.assertIn("disabled", run.errors[0])

    def test_google_values_as_strings_normalize_identically(self):
        """Google returns numbers/dates as different primitive types; results must match Excel."""
        excel = normalize(build_tables())
        stringly = {t: [rows[0]] + [[None if v is None else (str(v) if not isinstance(v, str) else v) for v in r]
                                    for r in rows[1:]] for t, rows in build_tables().items()}
        google = normalize(stringly)
        self.assertEqual(excel.counts(), google.counts())
        self.assertEqual(excel.quiz_results, google.quiz_results)
        self.assertEqual(excel.enrollments.keys(), google.enrollments.keys())


class NonLearnerExclusionTests(TestCase):
    """Configured non-learner LMS accounts are skipped by ID; nobody else is affected."""

    def test_configured_ids(self):
        from analytics.importer.exclusions import NON_LEARNER_SOURCE_IDS
        self.assertEqual(set(NON_LEARNER_SOURCE_IDS), {1, 17, 1267, 976, 450, 762, 1057})

    def test_excluded_account_removed_everywhere_others_unchanged(self):
        from unittest import mock
        from analytics.importer import normalize as norm
        with mock.patch.dict(norm.NON_LEARNER_SOURCE_IDS, {}, clear=True):
            base = normalize(build_tables())
        with mock.patch.dict(norm.NON_LEARNER_SOURCE_IDS, {101: "test non-learner"}, clear=True):
            ds = normalize(build_tables())
        self.assertNotIn(101, ds.learners)
        self.assertEqual(set(ds.learners), set(base.learners) - {101})
        for name in ("enrollments", "lesson_records", "quiz_results", "quiz_attempts"):
            got = getattr(ds, name)
            self.assertFalse([k for k in got if k[0] == 101], name)
            self.assertEqual(got, {k: v for k, v in getattr(base, name).items() if k[0] != 101}, name)
        self.assertFalse([s for s in ds.submissions.values() if s["uid"] == 101])
        self.assertEqual(ds.excluded_non_learners["accounts"], 1)
        # Skipped rows are not reported as orphans, and export cross-checks stay consistent
        self.assertFalse([w for w in ds.warnings if "not in Students" in w])
        self.assertEqual([w for w in ds.warnings if w.startswith("Cross-check")],
                         [w for w in base.warnings if w.startswith("Cross-check")])

    def test_sync_drops_excluded_learner_from_api(self):
        from unittest import mock
        from django.contrib.auth.models import User
        from analytics.importer import normalize as norm
        from analytics.models import DashboardAccess
        tmp = Path(tempfile.mkdtemp())
        try:
            path = str(write_workbook(tmp / "s.xlsx"))
            run_sync("excel", path=path)
            with mock.patch.dict(norm.NON_LEARNER_SOURCE_IDS, {103: "test non-learner"}, clear=True):
                run_sync("excel", path=path)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertFalse(Learner.objects.filter(source_id=103).exists())
        u = User.objects.create_user("viewer", password="not-a-real-pass-123")
        DashboardAccess.objects.create(user=u)
        self.client.force_login(u)
        ids = {r["learner_id"] for r in self.client.get("/api/learners", HTTP_HOST="localhost").json()["results"]}
        self.assertEqual(ids, {101, 102})
        self.assertEqual(self.client.get("/api/learners/103", HTTP_HOST="localhost").status_code, 404)
        k = self.client.get("/api/dashboard/summary", HTTP_HOST="localhost").json()["kpis"]
        self.assertEqual(k["unique_learners"]["value"], 2)


class LearnerMergeTests(TestCase):
    """Verified duplicate accounts are attributed to one learner without losing any record."""

    def test_configured_merges(self):
        from analytics.importer.identities import MERGED_LEARNER_IDS
        self.assertEqual(MERGED_LEARNER_IDS, {1056: 760, 1058: 753, 1062: 748, 779: 1229,
                                             743: 1229, 746: 763, 1221: 763, 1222: 763,
                                             758: 1059, 1038: 1059})
        self.assertFalse(set(MERGED_LEARNER_IDS) & set(MERGED_LEARNER_IDS.values()))  # no chains

    def _norm(self, merges, tables=None):
        from unittest import mock
        from analytics.importer import normalize as norm
        with mock.patch.dict(norm.MERGED_LEARNER_IDS, merges, clear=True):
            return normalize(tables or build_tables())

    def test_merge_combines_modules_and_keeps_all_records(self):
        base = self._norm({})
        # 103 (only module 9002) is a duplicate of 102 (module 9001): disjoint activity, no collisions
        ds = self._norm({103: 102})
        self.assertEqual(set(ds.learners), {101, 102})
        self.assertEqual(ds.learners[102], base.learners[102])          # canonical identity kept
        self.assertEqual(ds.enrollments[(102, 9002)], base.enrollments[(103, 9002)])
        self.assertEqual(ds.enrollments[(102, 9001)], base.enrollments[(102, 9001)])
        for name in ("lesson_records", "quiz_results", "quiz_attempts"):
            self.assertEqual(len(getattr(ds, name)), len(getattr(base, name)), name)  # nothing lost
            self.assertFalse([k for k in getattr(ds, name) if k[0] == 103], name)
        self.assertEqual(len(ds.submissions), len(base.submissions))
        self.assertEqual(ds.merged_learners["accounts_merged"], 1)
        self.assertEqual(ds.enrollments.keys() - {(102, 9002)}, base.enrollments.keys() - {(103, 9002)})

    def test_same_module_enrollments_are_combined(self):
        from .fixtures import STUDENT_HDR, _student
        tables = build_tables()
        row = _student(103, 9001, 30.0, submissions=2)                # duplicate account, same module as 101
        row[STUDENT_HDR.index("User ID")] = 104.0
        row[STUDENT_HDR.index("Email")] = "learner104@example.invalid"
        tables["Al Fanar Students"].append(row)
        ds = self._norm({104: 101}, tables)
        base = self._norm({}, build_tables())
        e, b = ds.enrollments[(101, 9001)], base.enrollments[(101, 9001)]
        self.assertEqual(e["progress_pct"], max(30.0, b["progress_pct"]))   # higher LMS progress kept
        self.assertEqual(e["reported_submissions"], (b["reported_submissions"] or 0) + 2)
        self.assertNotIn(104, ds.learners)

    def test_owner_approved_overlap_preserves_evidence_and_attempts(self):
        ds = self._norm({102: 101})
        self.assertNotIn(102, ds.learners)
        self.assertIn(101, ds.learners)
        self.assertEqual(len(ds.quiz_attempts), 4)  # both source accounts' attempt #1 retained
        history = [(k, v) for k,v in ds.quiz_attempts.items() if k[:3] == (101, 9001, 701)]
        self.assertEqual(len(history), 3)
        self.assertEqual({v['source_learner_id'] for _,v in history}, {101, 102})
        self.assertEqual(ds.quiz_results[(101, 9001, 701)]['latest_score'], 70)
        self.assertEqual(ds.quiz_results[(101, 9001, 701)]['best_score'], 90)
        self.assertEqual(ds.merged_learners["accounts_merged"], 1)
        self.assertEqual(len([v for v in ds.evidence.values() if 'Lesson ID' in v['payload']]), 6)
        self.assertEqual(len(ds.lesson_records), 4)  # same video completion counts once

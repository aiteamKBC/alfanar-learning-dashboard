"""Optional checks against the real workbook (skipped when it is not present).

Totals are computed independently with openpyxl and compared with the importer
and API. No learner data is printed or stored in the test.
"""

import hashlib
from collections import defaultdict
from pathlib import Path

import openpyxl
from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase
from unittest import skipUnless

from analytics.importer.exclusions import NON_LEARNER_SOURCE_IDS
from analytics.importer.identities import MERGED_LEARNER_IDS
from analytics.importer.service import run_sync
from analytics.models import DashboardAccess

REAL = Path(settings.ALFANAR_EXCEL_PATH)


def independent_totals(path: Path) -> dict:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)

    def rows(name):
        it = wb[name].iter_rows(values_only=True)
        hdr = next(it)
        out = [dict(zip(hdr, r)) for r in it if any(c not in (None, "") for c in r)]
        out = [r for r in out if r.get("User ID") not in NON_LEARNER_SOURCE_IDS]  # documented non-learners
        for r in out:  # verified duplicate accounts count as one learner
            r["User ID"] = MERGED_LEARNER_IDS.get(r.get("User ID"), r.get("User ID"))
        return out

    st = rows("Al Fanar Students")
    qa = rows("Al Fanar Quiz Attempts")
    latest = {}
    for r in qa:
        k = (r["User ID"], r["Course ID"], r["Quiz ID"])
        if k not in latest or r["Attempt #"] > latest[k]["Attempt #"]:
            latest[k] = r
    sub = rows("Al Fanar Submissions")
    activity_enrol = set()
    for name in ("Al Fanar Lesson Details", "Al Fanar Quiz Attempts", "Al Fanar Submissions"):
        activity_enrol |= {(r["User ID"], r["Course ID"]) for r in rows(name)}
    best = {}  # one enrollment per learner/module; merged accounts keep the higher LMS progress
    for r in st:
        k, v = (r["User ID"], r["Course ID"]), r["Progress (%)"]
        if v is not None:
            best[k] = max(best.get(k, v), v)
    progress = list(best.values())
    by_type = defaultdict(int)
    for r in rows("Al Fanar Lesson Details"):
        by_type[r["Category"].lower()] += 1
    wb.close()
    return {
        "learners": len({r["User ID"] for r in st}),
        "courses": len({r["Course ID"] for r in st}),
        "enrollments": len({(r["User ID"], r["Course ID"]) for r in st}),
        "quiz_pairs": len(latest),
        "attempts": len(qa),
        "mean_latest": round(sum(r["Progress (%)"] for r in latest.values()) / len(latest), 1),
        "passed_latest": sum(1 for r in latest.values() if r["Status"] == "passed"),
        "submitted": sum(1 for r in sub if r["Status"] != "draft"),
        "lesson_records": sum(by_type.values()),
        "active_enrollments": len(activity_enrol),
        "mean_progress": round(sum(progress) / len(progress), 1),
    }


@skipUnless(REAL.is_file(), "real workbook not available")
class RealWorkbookTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.digest = hashlib.sha256(REAL.read_bytes()).hexdigest()
        cls.sync_run = run_sync("excel", path=str(REAL))
        cls.expected = independent_totals(REAL)
        cls.user = User.objects.create_user("viewer", password="not-a-real-pass-123")
        DashboardAccess.objects.create(user=cls.user)

    def test_import_success_and_source_unchanged(self):
        self.assertEqual(self.sync_run.status, "success", self.sync_run.errors)
        self.assertEqual(hashlib.sha256(REAL.read_bytes()).hexdigest(), self.digest)

    def test_metrics_match_independent_workbook_totals(self):
        self.client.force_login(self.user)
        k = self.client.get("/api/dashboard/summary").json()["kpis"]
        e = self.expected
        self.assertEqual(k["unique_learners"]["value"], e["learners"])
        self.assertEqual(k["modules"]["value"], e["courses"])
        self.assertEqual(k["enrollments"]["value"], e["enrollments"])
        self.assertEqual(k["quiz_completion"]["value"], e["quiz_pairs"])
        self.assertEqual(k["mean_latest_score"]["value"], e["mean_latest"])
        self.assertEqual(k["pass_rate"]["value"], e["passed_latest"])
        self.assertEqual(k["submissions"]["value"], e["submitted"])
        self.assertEqual(k["materials_completed"]["value"], e["lesson_records"])
        self.assertEqual(k["activity_coverage"]["value"], e["active_enrollments"])
        self.assertEqual(k["mean_progress"]["value"], e["mean_progress"])

    def test_reimport_is_idempotent(self):
        again = run_sync("excel", path=str(REAL))
        self.assertEqual(again.status, "success")
        self.assertFalse(again.changed)

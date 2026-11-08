"""Write a private, immutable decision ledger without changing attendance sources."""
import hashlib
import json
import os
from collections import Counter
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from analytics.attendance.metrics import AttendanceIndex
from analytics.attendance.reconciliation import RULE_VERSION, minimum_minutes


def reconciliation_report():
    index = AttendanceIndex()
    sessions = {s["id"]: s for group in index.sessions_by_course.values() for s in group}
    rows = [{"learner_source_id": uid, "course_source_id": sessions[sid]["meeting__course_source_id"],
             "lecture_date": sessions[sid]["session_date"].isoformat(), "session_id": sid,
             **decision, **index.decision_evidence[(uid, sid)]}
            for (uid, sid), decision in sorted(index.decisions.items())]
    counts = dict(Counter(r["reason_code"] for r in rows))
    payload = {"rule_version": RULE_VERSION, "minimum_minutes": minimum_minutes(), "observations": rows}
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"fingerprint": fingerprint, "counts": counts, **payload}


class Command(BaseCommand):
    help = "Audit effective attendance and write a private immutable decision ledger; never updates original evidence."

    def add_arguments(self, parser):
        parser.add_argument("--output", required=True, help="Private JSON path outside Git and the web root")

    def handle(self, *args, **options):
        report = reconciliation_report()
        target = Path(options["output"])
        payload = json.dumps(report, indent=2, sort_keys=True).encode()
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        changed = not target.exists()
        if not changed:
            if target.read_bytes() != payload:
                raise CommandError("The existing ledger differs; use a new private filename to preserve its history.")
        else:
            with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as fh:
                fh.write(payload)
        self.stdout.write(json.dumps({"changed": changed, "fingerprint": report["fingerprint"],
                                      "minimum_minutes": report["minimum_minutes"], "counts": report["counts"],
                                      "observations": len(report["observations"])}))

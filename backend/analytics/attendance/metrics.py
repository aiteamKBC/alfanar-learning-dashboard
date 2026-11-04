"""Attendance metrics derived from imported Teams records.

Definitions (exposed to the UI):
* Delivered session: a lecture occurrence (one Teams meeting series on one date)
  that at least TEAMS_MIN_LEARNERS_FOR_DELIVERED verified learners attended.
* Attended: a verified learner's merged presence in a delivered session is at
  least TEAMS_MIN_ATTENDANCE_MINUTES.
* Attendance % for an enrollment = attended delivered sessions / delivered
  sessions of that module. Only available for modules with a mapped Teams meeting.
* Live hours = merged recorded presence in delivered sessions.
"""

from __future__ import annotations

from collections import defaultdict

from django.conf import settings

from ..models import AttendanceImportRun, LectureSession, SessionAttendance


def definitions() -> dict:
    m, n = settings.TEAMS_MIN_ATTENDANCE_MINUTES, settings.TEAMS_MIN_LEARNERS_FOR_DELIVERED
    return {
        "delivered_sessions": f"Teams lecture occurrences (one meeting series on one date) for the module that at least {n} "
                              f"verified learners attended for {m}+ minutes. Brief link-opens and test calls are excluded. "
                              "Counts only meetings present in the imported Teams export.",
        "attendance": f"Delivered sessions attended (merged Teams presence of at least {m} minutes) ÷ delivered sessions of the "
                      "module. Learners are matched by exact email; trainers, organisers, external and unmatched participants are "
                      "excluded. Not available for modules without a mapped Teams meeting.",
        "live_hours": "Total merged Microsoft Teams presence in delivered sessions (overlapping joins from several devices counted once).",
    }


def _pct(a, b):
    return round(100.0 * a / b, 1) if b else None


class AttendanceIndex:
    """In-memory index over delivered sessions and verified attendance (small dataset, 2 queries)."""

    def __init__(self):
        sessions = list(LectureSession.objects.filter(delivered=True, meeting__course_source_id__isnull=False)
                        .values("id", "session_date", "starts_at", "ends_at", "verified_learners",
                                "meeting__course_source_id", "meeting__subject").order_by("session_date"))
        self.sessions_by_course: dict[int, list[dict]] = defaultdict(list)
        course_of = {}
        for s in sessions:
            self.sessions_by_course[s["meeting__course_source_id"]].append(s)
            course_of[s["id"]] = s["meeting__course_source_id"]
        # Learners with any verified Teams identity (matched in at least one occurrence).
        self.identified = set(SessionAttendance.objects.values_list("learner_source_id", flat=True).distinct())
        self.records: dict[tuple[int, int], dict] = {}  # (learner, session) -> record
        self.per_enrollment = defaultdict(lambda: {"attended": 0, "seconds": 0})
        for a in SessionAttendance.objects.filter(session_id__in=course_of.keys()).values(
                "session_id", "learner_source_id", "presence_seconds", "attended", "first_join", "last_leave", "interval_count"):
            self.records[(a["learner_source_id"], a["session_id"])] = a
            key = (a["learner_source_id"], course_of[a["session_id"]])
            self.per_enrollment[key]["seconds"] += a["presence_seconds"]
            if a["attended"]:
                self.per_enrollment[key]["attended"] += 1

    def has_data(self, course_sid: int) -> bool:
        return bool(self.sessions_by_course.get(course_sid))

    def enrollment(self, learner_sid: int, course_sid: int) -> dict:
        delivered = len(self.sessions_by_course.get(course_sid, []))
        if not delivered:
            return {"available": False, "delivered": None, "attended": None, "pct": None, "live_seconds": None,
                    "reason": "No mapped Microsoft Teams meeting with delivered sessions for this module"}
        e = self.per_enrollment.get((learner_sid, course_sid), {"attended": 0, "seconds": 0})
        return {"available": True, "delivered": delivered, "attended": e["attended"],
                "pct": _pct(e["attended"], delivered), "live_seconds": e["seconds"],
                "teams_identity": learner_sid in self.identified}

    def sessions_for(self, learner_sid: int, course_sid: int) -> list[dict]:
        out = []
        for n, s in enumerate(self.sessions_by_course.get(course_sid, []), start=1):
            rec = self.records.get((learner_sid, s["id"]))
            out.append({
                "number": n, "date": s["session_date"], "starts_at": s["starts_at"], "ends_at": s["ends_at"],
                "subject": s["meeting__subject"], "learners_attended": s["verified_learners"],
                "status": "attended" if rec and rec["attended"] else ("brief" if rec else "not_recorded"),
                "presence_seconds": rec["presence_seconds"] if rec else None,
                "first_join": rec["first_join"] if rec else None,
                "last_leave": rec["last_leave"] if rec else None,
                "joins": rec["interval_count"] if rec else None,
            })
        return out


def aggregate(index: AttendanceIndex, pairs: list[tuple[int, int]]) -> dict:
    """Totals over (learner_sid, course_sid) enrollments; only enrollments in modules with Teams data count."""
    covered = [(l, c) for l, c in pairs if index.has_data(c)]
    attended = sum(index.per_enrollment.get(p, {}).get("attended", 0) for p in covered)
    expected = sum(len(index.sessions_by_course[c]) for _, c in covered)
    seconds = sum(index.per_enrollment.get(p, {}).get("seconds", 0) for p in covered)
    courses = {c for _, c in pairs}
    return {
        "available": bool(covered),
        "attended": attended, "expected": expected, "pct": _pct(attended, expected),
        "live_seconds": seconds,
        "delivered_sessions": sum(len(index.sessions_by_course.get(c, [])) for c in courses),
        "enrollments_covered": len(covered), "enrollments_total": len(pairs),
        # Covered enrollments whose learner has no matched Teams identity at all (counted as not attended;
        # they may have joined under an unregistered email -> add verified aliases to resolve).
        "enrollments_without_teams_identity": sum(1 for l, _ in covered if l not in index.identified),
        "modules_without_data": sorted(c for c in courses if not index.has_data(c)),
    }


def import_status() -> dict | None:
    r = AttendanceImportRun.objects.first()
    ok = AttendanceImportRun.objects.filter(status=AttendanceImportRun.Status.SUCCESS).first()
    if r is None:
        return None
    return {
        "last_status": r.status, "last_run_at": r.finished_at, "last_errors": r.errors[:5],
        "last_success_at": ok.finished_at if ok else None,
        "source_ref": ok.source_ref if ok else r.source_ref,
        "source_modified_at": ok.source_modified_at if ok else None,
        "coverage_start": ok.coverage_start if ok else None, "coverage_end": ok.coverage_end if ok else None,
        "diagnostics": {k: v for k, v in (ok.diagnostics if ok else {}).items() if k != "load"},
        "warnings": ok.warnings if ok else [],
    }

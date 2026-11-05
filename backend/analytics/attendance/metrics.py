"""Attendance metrics derived from imported Teams records.

Definitions (exposed to the UI):
* Delivered session: a lecture occurrence (one Teams meeting series on one date)
  that at least TEAMS_MIN_LEARNERS_FOR_DELIVERED verified learners attended.
* Attended: a verified learner's merged presence in a delivered session is at
  least TEAMS_MIN_ATTENDANCE_MINUTES.
* Attendance % for an enrollment = attended delivered sessions / delivered
  sessions of that module. Only available for modules with a mapped Teams meeting and
  learners with a matched Teams identity; unmatched learners are unknown, not absent.
* Live hours = merged recorded presence in delivered sessions.
"""

from __future__ import annotations

from collections import defaultdict

from django.conf import settings

from ..course_labels import course_display_name
from ..models import AttendanceImportRun, LectureSession, SessionAttendance


def definitions() -> dict:
    m, n = settings.TEAMS_MIN_ATTENDANCE_MINUTES, settings.TEAMS_MIN_LEARNERS_FOR_DELIVERED
    return {
        "delivered_sessions": "Dated sessions with positive verified attendance evidence in the authoritative module "
                              f"workbook. For explicit legacy interval imports only: at least {n} verified learners "
                              f"with {m}+ merged minutes. Confirmed delivered lectures without attendance columns "
                              "are kept separate and never added to attendance denominators.",
        "delivered_lectures": "Lectures delivered in the module. Uses the count confirmed by the project owner where one "
                              "exists; otherwise dated sessions with positive evidence in the authoritative attendance "
                              "workbook. Confirmed delivery and measurable attendance are separate facts. "
                              "Binary matrices cannot verify Teams attendance duration.",
        "attendance": f"Eligible enrolled learners with verified source email identities only. Authoritative matrices: "
                      "1 = present, 0 = recorded absent, blank = Not Available. Present learner-sessions ÷ learner-sessions "
                      "with explicit 0/1 evidence. Missing cells, unidentified learners and confirmed lectures missing from "
                      "the workbook are excluded from both numerator and denominator. Positive evidence wins overlapping "
                      f"approved aliases. Legacy interval reports require {m}+ merged minutes; matrices have no duration "
                      "and cannot verify that threshold. Rates therefore measure recorded binary presence, not time-based attendance.",
        "live_hours": "Measured merged Teams intervals only. Authoritative matrices contain no durations: live hours are Not Available.",
    }


def _pct(a, b):
    return round(100.0 * a / b, 1) if b else None


class AttendanceIndex:
    """In-memory index over delivered sessions and verified attendance (small dataset, 2 queries)."""

    def __init__(self):
        sessions = list(LectureSession.objects.filter(delivered=True, meeting__course_source_id__isnull=False)
                        .values("id", "session_date", "starts_at", "ends_at", "verified_learners",
                                "meeting__course_source_id", "meeting__subject", "meeting__evidence_source").order_by("session_date"))
        self.sessions_by_course: dict[int, list[dict]] = defaultdict(list)
        course_of = {}
        for s in sessions:
            self.sessions_by_course[s["meeting__course_source_id"]].append(s)
            course_of[s["id"]] = s["meeting__course_source_id"]
        # Learners with any verified Teams identity (matched in at least one occurrence).
        self.identified = set(SessionAttendance.objects.values_list("learner_source_id", flat=True).distinct())
        self.records: dict[tuple[int, int], dict] = {}  # (learner, session) -> record
        self.per_enrollment = defaultdict(lambda: {"attended": 0, "seconds": 0, "timed": 0, "observed": 0})
        for a in SessionAttendance.objects.filter(session_id__in=course_of.keys()).values(
                "session_id", "learner_source_id", "presence_seconds", "attended", "first_join", "last_leave", "interval_count"):
            self.records[(a["learner_source_id"], a["session_id"])] = a
            key = (a["learner_source_id"], course_of[a["session_id"]])
            self.per_enrollment[key]["observed"] += 1
            if a["presence_seconds"] is not None:
                self.per_enrollment[key]["seconds"] += a["presence_seconds"]
                self.per_enrollment[key]["timed"] += 1
            if a["attended"]:
                self.per_enrollment[key]["attended"] += 1

    def has_data(self, course_sid: int) -> bool:
        return bool(self.sessions_by_course.get(course_sid))

    def enrollment(self, learner_sid: int, course_sid: int) -> dict:
        delivered = len(self.sessions_by_course.get(course_sid, []))
        if not delivered:
            return {"available": False, "delivered": None, "attended": None, "pct": None, "live_seconds": None,
                    "reason": "No mapped Microsoft Teams meeting with delivered sessions for this module"}
        if learner_sid not in self.identified:
            # Unknown, not absent: the learner may have joined under an unregistered email.
            return {"available": False, "delivered": delivered, "attended": None, "pct": None, "live_seconds": None,
                    "teams_identity": False,
                    "reason": "No matched Microsoft Teams identity for this learner, so attendance is unknown "
                              "(not counted as absent). A verified email alias would resolve it."}
        e = self.per_enrollment.get((learner_sid, course_sid), {"attended": 0, "seconds": 0, "timed": 0, "observed": 0})
        matrix = self.sessions_by_course[course_sid][0]['meeting__evidence_source'] == 'attendance_matrix'
        measured = e['observed'] if matrix else delivered
        return {"available": measured > 0, "delivered": delivered, "measured": measured,
                "missing": delivered - measured, "attended": e["attended"] if measured else None,
                "pct": _pct(e["attended"], measured), "live_seconds": e["seconds"] if e['timed'] else None,
                "teams_identity": learner_sid in self.identified,
                "evidence_source": 'attendance_matrix' if matrix else 'teams_intervals',
                "reason": "No explicit attendance cells in this module; attendance is Not Available" if not measured else ""}

    def sessions_for(self, learner_sid: int, course_sid: int) -> list[dict]:
        out = []
        for n, s in enumerate(self.sessions_by_course.get(course_sid, []), start=1):
            rec = self.records.get((learner_sid, s["id"]))
            out.append({
                "number": n, "date": s["session_date"], "starts_at": s["starts_at"], "ends_at": s["ends_at"],
                "subject": course_display_name(course_sid, s["meeting__subject"]), "learners_attended": s["verified_learners"],
                "status": ("attended" if rec['attended'] else "absent") if rec and s['meeting__evidence_source'] == 'attendance_matrix'
                          else ("unavailable" if not rec and s['meeting__evidence_source'] == 'attendance_matrix'
                                else ("attended" if rec and rec["attended"] else ("brief" if rec else "not_recorded"))),
                "presence_seconds": rec["presence_seconds"] if rec else None,
                "first_join": rec["first_join"] if rec else None,
                "last_leave": rec["last_leave"] if rec else None,
                "joins": rec["interval_count"] if rec else None,
            })
        return out


def aggregate(index: AttendanceIndex, pairs: list[tuple[int, int]]) -> dict:
    """Totals over (learner_sid, course_sid) enrollments; only enrollments in modules with Teams data count."""
    covered = [(l, c) for l, c in pairs if index.has_data(c)]
    # Only learners with a matched Teams identity are measured. Unmatched learners are unknown, not absent,
    # so they are excluded from both numerator and denominator and reported separately.
    measured = [(l, c) for l, c in covered if index.enrollment(l, c)["available"]]
    unmatched = [(l, c) for l, c in covered if l not in index.identified]
    attended = sum(index.per_enrollment.get(p, {}).get("attended", 0) for p in measured)
    expected = sum(index.enrollment(l, c).get('measured', 0) for l, c in measured)
    seconds = sum(index.per_enrollment.get(p, {}).get("seconds", 0) for p in measured)
    courses = {c for _, c in pairs}
    return {
        "available": bool(measured),
        "attended": attended, "expected": expected, "pct": _pct(attended, expected),
        "live_seconds": seconds if any(index.per_enrollment.get(p, {}).get('timed', 0) for p in measured) else None,
        "possible_learner_sessions": sum(len(index.sessions_by_course[c]) for _, c in covered),
        "missing_learner_sessions": sum(len(index.sessions_by_course[c]) for _, c in covered) - expected,
        "coverage_pct": _pct(expected, sum(len(index.sessions_by_course[c]) for _, c in covered)),
        "enrollments_without_cells": len(covered) - len(measured) - len(unmatched),
        "evidence_source": 'attendance_matrix' if any(s['meeting__evidence_source'] == 'attendance_matrix' for c in courses for s in index.sessions_by_course[c]) else 'teams_intervals',
        "delivered_sessions": sum(len(index.sessions_by_course.get(c, [])) for c in courses),
        "enrollments_covered": len(covered), "enrollments_measured": len(measured), "enrollments_total": len(pairs),
        "enrollments_without_teams_identity": len(unmatched),
        "learners_without_teams_identity": len({l for l, _ in unmatched}),
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

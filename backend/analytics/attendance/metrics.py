"""Shared effective attendance over retained matrix and verified timing evidence.

Rates use effective known observations; missing evidence never implies absence.
The reconciliation rule and original-source distinctions are exposed in definitions.
Legacy interval-only imports retain their original duration/delivery semantics.
"""

from __future__ import annotations

from collections import defaultdict

from django.conf import settings

from ..course_labels import course_display_name
from ..models import AttendanceImportRun, Enrollment, LectureSession, LectureTiming, SessionAttendance
from ..importer.exclusions import NON_LEARNER_SOURCE_IDS
from ..importer.identities import MERGED_LEARNER_IDS
from .reconciliation import decide, minimum_minutes


def definitions() -> dict:
    m, n = settings.TEAMS_MIN_ATTENDANCE_MINUTES, settings.TEAMS_MIN_LEARNERS_FOR_DELIVERED
    threshold = minimum_minutes()
    return {
        "delivered_sessions": "Dated sessions with positive verified attendance evidence in the authoritative module "
                              f"workbook. For explicit legacy interval imports only: at least {n} verified learners "
                              f"with {m}+ merged minutes. Confirmed delivered lectures without attendance columns "
                              "are kept separate and never added to attendance denominators.",
        "delivered_lectures": "Lectures delivered in the module. Uses the count confirmed by the project owner where one "
                              "exists; otherwise dated sessions with positive evidence in the authoritative attendance "
                              "workbook. Confirmed delivery and measurable attendance are separate facts. "
                              "Binary matrices cannot verify Teams attendance duration.",
        "attendance": "Present / known observations after source reconciliation. Unknown observations are excluded. "
                      "Coverage is known / possible observations within dated sessions. "
                      f"Timing-derived presence requires {threshold}+ merged minutes.",
        "live_hours": "Measured merged Teams intervals only. Authoritative matrices contain no durations: live hours are Not Available.",
    }


def _pct(a, b):
    return round(100.0 * a / b, 1) if b else None


class AttendanceIndex:
    """One shared effective attendance index for every dashboard view; sources stay immutable."""

    def __init__(self):
        sessions = list(LectureSession.objects.filter(delivered=True, meeting__course_source_id__isnull=False)
                        .values("id", "session_date", "starts_at", "ends_at", "verified_learners",
                                "meeting__course_source_id", "meeting__subject", "meeting__evidence_source").order_by("session_date"))
        self.sessions_by_course: dict[int, list[dict]] = defaultdict(list)
        course_of = {}
        for s in sessions:
            self.sessions_by_course[s["meeting__course_source_id"]].append(s)
            course_of[s["id"]] = s["meeting__course_source_id"]
        excluded = set(NON_LEARNER_SOURCE_IDS) | set(MERGED_LEARNER_IDS)
        eligible = (set(Enrollment.objects.exclude(learner__source_id__in=excluded)
                        .values_list("learner__source_id", "course__source_id")) if sessions else set())
        # Learners with verified matrix or timing identities; never display-name matching.
        self.identified = set(SessionAttendance.objects.exclude(learner_source_id__in=excluded)
                              .values_list("learner_source_id", flat=True).distinct())
        self.records: dict[tuple[int, int], dict] = {}  # (learner, session) -> record
        for a in SessionAttendance.objects.filter(session_id__in=course_of.keys()).values(
                "id", "session_id", "learner_source_id", "presence_seconds", "attended", "first_join", "last_leave", "interval_count"):
            if (a["learner_source_id"], course_of[a["session_id"]]) in eligible:
                self.records[(a["learner_source_id"], a["session_id"])] = a
        self.decisions = {}
        self.decision_evidence = {}
        matrix_dates = defaultdict(list)
        for s in sessions:
            if s["meeting__evidence_source"] == "attendance_matrix":
                matrix_dates[(s["meeting__course_source_id"], s["session_date"])].append(s["id"])
        from .timing import AMBIGUOUS_DATES
        timing = {}
        timing_rows = (LectureTiming.objects.filter(import_run__active=True, status=LectureTiming.Status.VERIFIED).values(
            "id", "course_source_id", "learner_source_id", "session_date", "duration_microseconds", "import_run__fingerprint")
            if matrix_dates else ())
        for t in timing_rows:
            cid, uid, day = t["course_source_id"], t["learner_source_id"], t["session_date"]
            if (uid, cid) in eligible and len(matrix_dates[(cid, day)]) == 1 and (cid, day) not in AMBIGUOUS_DATES:
                timing[(uid, matrix_dates[(cid, day)][0])] = t
                self.identified.add(uid)
        for uid, cid in sorted(eligible):
            for s in self.sessions_by_course[cid]:
                if s["meeting__evidence_source"] != "attendance_matrix":
                    continue
                key = (uid, s["id"])
                raw, t = self.records.get(key), timing.get(key)
                decision = decide(raw["attended"] if raw else None, t["duration_microseconds"] if t else None)
                self.decisions[key] = decision
                self.decision_evidence[key] = {"matrix_record_id": raw["id"] if raw else None,
                                              "timing_record_id": t["id"] if t else None,
                                              "timing_import_fingerprint": t["import_run__fingerprint"] if t else None}
                if decision["corrected"]:
                    # Replace only this in-memory effective record. The original DB row is untouched.
                    self.records[key] = {"session_id": s["id"], "learner_source_id": uid, "attended": True,
                                         "presence_seconds": None, "first_join": None, "last_leave": None, "interval_count": None}
        self.per_enrollment = defaultdict(lambda: {"attended": 0, "seconds": 0, "timed": 0, "observed": 0})
        self.present_by_session = defaultdict(int)
        for a in self.records.values():
            key = (a["learner_source_id"], course_of[a["session_id"]])
            self.per_enrollment[key]["observed"] += 1
            if a["presence_seconds"] is not None:
                self.per_enrollment[key]["seconds"] += a["presence_seconds"]
                self.per_enrollment[key]["timed"] += 1
            if a["attended"]:
                self.per_enrollment[key]["attended"] += 1
                self.present_by_session[a["session_id"]] += 1

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
                "evidence_source": 'reconciled_attendance' if matrix else 'teams_intervals',
                "reason": "No matrix observations or qualifying verified timing in this module; attendance is Not Available" if not measured else ""}

    def sessions_for(self, learner_sid: int, course_sid: int) -> list[dict]:
        out = []
        for n, s in enumerate(self.sessions_by_course.get(course_sid, []), start=1):
            rec = self.records.get((learner_sid, s["id"]))
            out.append({
                "number": n, "date": s["session_date"], "starts_at": s["starts_at"], "ends_at": s["ends_at"],
                "subject": course_display_name(course_sid, s["meeting__subject"]),
                "learners_attended": self.present_by_session[s["id"]] if s['meeting__evidence_source'] == 'attendance_matrix' else s["verified_learners"],
                "status": ("attended" if rec['attended'] else "absent") if rec and s['meeting__evidence_source'] == 'attendance_matrix'
                          else ("unavailable" if not rec and s['meeting__evidence_source'] == 'attendance_matrix'
                                else ("attended" if rec and rec["attended"] else ("brief" if rec else "not_recorded"))),
                "presence_seconds": rec["presence_seconds"] if rec else None,
                "first_join": rec["first_join"] if rec else None,
                "last_leave": rec["last_leave"] if rec else None,
                "joins": rec["interval_count"] if rec else None,
                "reconciliation": self.decisions.get((learner_sid, s["id"])),
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
        "evidence_source": 'reconciled_attendance' if any(s['meeting__evidence_source'] == 'attendance_matrix' for c in courses for s in index.sessions_by_course[c]) else 'teams_intervals',
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

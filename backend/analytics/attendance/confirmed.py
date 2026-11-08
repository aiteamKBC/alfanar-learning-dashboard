"""Delivered-lecture counts confirmed by the project owner.

A confirmed count overrides the evidence-derived count for display only. Imported sessions and
attendance records are never changed by it. Attendance continues to use the existing evidence
sessions and known observations, independently of the official delivered lecture count.

Only add an entry when the count has been confirmed by an accountable person. Never estimate.
"""

from __future__ import annotations

CONFIRMED_DELIVERED_LECTURES: dict[int, dict] = {
    # Planning, Controlling and Leading a Project
    83334: {
        "count": 16,
        "confirmed_by": "Project owner",
        "confirmed_on": "2026-11-05",
        "note": "16 delivered lectures officially confirmed by the project owner. "
                "Attendance uses the available evidence sessions and known observations separately.",
    },
    # Procurement Risk and Contract Management
    121642: {
        "count": 14,
        "confirmed_by": "Project owner",
        "confirmed_on": "2026-11-05",
        "note": "14 delivered lectures officially confirmed by the project owner. "
                "Attendance uses the available evidence sessions and known observations separately.",
    },
    # Operations and Information Management for Project Managers
    135945: {
        "count": 16,
        "confirmed_by": "Project owner",
        "confirmed_on": "2026-11-05",
        "note": "16 delivered lectures officially confirmed by the project owner. "
                "Attendance uses the available evidence sessions and known observations separately.",
    },
    # Advanced Project and Logistics Management
    154268: {
        "count": 12,
        "confirmed_by": "Project owner",
        "confirmed_on": "2026-11-05",
        "note": "12 delivered lectures explicitly confirmed by the project owner. The authoritative workbook "
                "has 9 dated attendance columns; 3 confirmed lectures have no attendance data. "
                "Binary presence does not verify a minimum duration. Missing evidence is not absence.",
    },
}


def delivered_lectures(course_sid: int, teams_sessions: int | None, evidence_source="teams", new_occurrences=0) -> dict:
    """Displayed delivered-lecture count with its provenance; the Teams count is kept alongside."""
    c = CONFIRMED_DELIVERED_LECTURES.get(course_sid)
    if c:
        if new_occurrences:
            return {"count": c["count"] + new_occurrences, "source": "owner_confirmed_plus_verified",
                    "historical_count": c["count"], "new_occurrences": new_occurrences,
                    "teams_sessions": teams_sessions, "confirmed_by": c["confirmed_by"], "confirmed_on": c["confirmed_on"],
                    "note": f"{c['count']} owner-confirmed historical lectures plus {new_occurrences} verified new lectures.",
                    "differs_from_teams": teams_sessions != c["count"] + new_occurrences}
        return {"count": c["count"], "source": "owner_confirmed", "teams_sessions": teams_sessions,
                "confirmed_by": c["confirmed_by"], "confirmed_on": c["confirmed_on"], "note": c["note"],
                "differs_from_teams": teams_sessions is not None and teams_sessions != c["count"]}
    return {"count": teams_sessions, "source": evidence_source if teams_sessions is not None else None,
            "teams_sessions": teams_sessions, "differs_from_teams": False}

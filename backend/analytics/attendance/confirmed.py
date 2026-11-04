"""Delivered-lecture counts confirmed by the project owner.

A confirmed count overrides the Teams-derived count for display only. Teams sessions and attendance
records are never changed by it: attendance keeps using the imported Teams sessions until the
specific session dates to exclude are verified.

Only add an entry when the count has been confirmed by an accountable person. Never estimate.
"""

from __future__ import annotations

CONFIRMED_DELIVERED_LECTURES: dict[int, dict] = {
    # Advanced Project and Logistics Management
    154268: {
        "count": 8,
        "confirmed_by": "Project owner",
        "confirmed_on": "2026-11-04",
        "note": "The Teams export contains 10 sessions that each look like a full lecture; the 2 extra dates "
                "have not been identified, so attendance still uses all 10 Teams sessions.",
    },
}


def delivered_lectures(course_sid: int, teams_sessions: int | None) -> dict:
    """Displayed delivered-lecture count with its provenance; the Teams count is kept alongside."""
    c = CONFIRMED_DELIVERED_LECTURES.get(course_sid)
    if c:
        return {"count": c["count"], "source": "owner_confirmed", "teams_sessions": teams_sessions,
                "confirmed_by": c["confirmed_by"], "confirmed_on": c["confirmed_on"], "note": c["note"],
                "differs_from_teams": teams_sessions is not None and teams_sessions != c["count"]}
    return {"count": teams_sessions, "source": "teams" if teams_sessions is not None else None,
            "teams_sessions": teams_sessions, "differs_from_teams": False}

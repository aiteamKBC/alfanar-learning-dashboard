"""Teams meeting -> LMS module mapping rules.

Rule 1 (automatic): the meeting subject equals an LMS module title (normalized).
Rule 2 (approved): explicit subject -> module rules below, each approved by the
project owner with recorded evidence. Anything else stays UNMAPPED for review.
"""

import re

APPROVED_RULES = [
    {
        "subject": "Level 7 Diploma in Project Management",
        "course_source_id": 83334,  # PCP Al-Fanar 2025
        "approved_on": "2026-11-04",
        "evidence": "17 of PCP's dated lecture recordings fall on this meeting's lecture days (0 on other "
                    "meetings' days); 27/28 matched attendees enrolled in PCP.",
    },
    {
        "subject": "Andrew Operations and Information Management for Project Managers",
        "course_source_id": 135945,  # Al Fanar - Operations and Information Management
        "approved_on": "2026-11-04",
        "evidence": "Subject names the module; LMS lesson titles read 'Operations and Information mgmt. for PM'; "
                    "dates fall within the module's LMS activity window; 24/25 matched attendees enrolled.",
    },
]


def normalize_title(value: str) -> str:
    v = re.sub(r"\s+", " ", str(value or "")).strip().lower()
    return re.sub(r"[^a-z0-9 ]", "", v)


def map_subject(subject: str, courses: dict[int, str]) -> tuple[int | None, str, str]:
    """Return (course_source_id, mapping_status, note)."""
    key = normalize_title(subject)
    exact = [cid for cid, title in courses.items() if normalize_title(title) == key]
    if len(exact) == 1:
        return exact[0], "exact_title", "Meeting subject equals the LMS module title"
    for rule in APPROVED_RULES:
        if normalize_title(rule["subject"]) == key and rule["course_source_id"] in courses:
            return rule["course_source_id"], "approved_rule", f"Approved {rule['approved_on']}: {rule['evidence']}"
    return None, "unmapped", "No exact title match and no approved rule; needs review"

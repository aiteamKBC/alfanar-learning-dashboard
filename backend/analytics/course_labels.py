"""Client-facing labels keyed by stable LMS IDs; source titles remain untouched."""

COURSE_DISPLAY_NAMES = {
    83334: "Planning, Controlling and Leading a Project",
    121642: "Procurement Risk and Contract Management",
    135945: "Operations and Information Management for Project Managers",
    154268: "Advanced Project and Logistics Management",
}


def course_display_name(source_id: int, source_title: str) -> str:
    return COURSE_DISPLAY_NAMES.get(source_id, source_title)

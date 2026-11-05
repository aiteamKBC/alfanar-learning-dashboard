"""Client-facing labels keyed by stable LMS IDs; source titles remain untouched."""

COURSE_DISPLAY_NAMES = {
    83334: "Planning, Controlling and Leading a Project",
    121642: "Procurement Risk and Contract Management",
    135945: "Operations and Information Management for Project Managers",
    154268: "Advanced Project and Logistics Management",
}

COURSE_DISPLAY_ORDER = (83334, 121642, 135945, 154268)
_COURSE_ORDER_INDEX = {source_id: index for index, source_id in enumerate(COURSE_DISPLAY_ORDER)}


def course_display_name(source_id: int, source_title: str) -> str:
    return COURSE_DISPLAY_NAMES.get(source_id, source_title)


def course_order_key(source_id: int, source_title: str = "") -> tuple:
    """Keep the client module sequence stable; other modules follow alphabetically."""
    return (_COURSE_ORDER_INDEX.get(source_id, len(COURSE_DISPLAY_ORDER)), source_title.casefold(), source_id)

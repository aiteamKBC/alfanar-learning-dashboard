"""Lecture-library attendance display, independent of official LMS/KPI rules."""

MIN_PRESENT_SECONDS = 10 * 60


def attendance_status(stored_status, duration_seconds):
    """Verified cumulative timing can establish presence without erasing source status."""
    if duration_seconds is not None and duration_seconds >= MIN_PRESENT_SECONDS:
        return 'present'
    return stored_status

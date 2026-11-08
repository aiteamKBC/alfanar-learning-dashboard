"""Non-destructive attendance decisions over exact-date verified timing and matrices.

This is one shared dashboard rule, not a mutation of either source. A qualifying
union of verified intervals is positive evidence. Missing/short timing never proves
absence or invalidates an explicit matrix presence. See docs/attendance-reconciliation.md.
"""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

RULE_VERSION = "verified-positive-evidence-v1"


def minimum_minutes():
    value = settings.ATTENDANCE_RECONCILIATION_MIN_MINUTES
    if type(value) is not int or not 1 <= value <= 1440:
        raise ImproperlyConfigured("ATTENDANCE_RECONCILIATION_MIN_MINUTES must be an integer from 1 to 1440")
    return value


def decide(matrix_present: bool | None, duration_microseconds: int | None) -> dict:
    """Duration is the verified importer union, never first-to-last span or row sum."""
    minutes = minimum_minutes()
    if matrix_present is not None and type(matrix_present) is not bool:
        raise ValueError("Matrix observation must be a boolean or missing")
    if duration_microseconds is not None and (type(duration_microseconds) is not int or duration_microseconds < 0):
        raise ValueError("Verified duration must be nonnegative integer microseconds")
    qualified = duration_microseconds is not None and duration_microseconds >= minutes * 60 * 1_000_000
    present, corrected, conflict, review = matrix_present, False, False, False
    source = "matrix" if matrix_present is not None else "unavailable"
    if matrix_present is True:
        if duration_microseconds is None:
            code, reason = "matrix_present_no_timing", "Matrix records present. Missing timing does not invalidate that observation."
        elif qualified:
            source = "matrix_and_timing"
            code, reason = "sources_agree_present", f"Matrix presence agrees with at least {minutes} minutes of verified Teams attendance."
        else:
            conflict = review = True
            code, reason = "matrix_present_short_timing", f"Matrix records present, but available timing is below {minutes} minutes. Timing may be incomplete; matrix presence is retained pending review."
    elif qualified:
        present, corrected, source = True, True, "verified_timing"
        conflict = matrix_present is False
        if conflict:
            code, reason = "timing_over_matrix_absence", f"At least {minutes} verified minutes establish presence despite a matrix absence. The original absence is retained; the positive-evidence rule resolves the dashboard status."
        else:
            code, reason = "timing_fills_missing", f"No matrix observation; at least {minutes} minutes of verified Teams attendance establish presence."
    elif matrix_present is False:
        code = "matrix_absent_short_timing" if duration_microseconds is not None else "matrix_absent_no_timing"
        reason = (f"Matrix records absent; the available timing is below {minutes} minutes and does not qualify as present."
                  if duration_microseconds is not None else "Matrix records absent. This absence comes from the matrix, not from missing timing.")
    elif duration_microseconds is not None:
        code, reason = "short_timing_unknown", f"Verified timing is below {minutes} minutes and there is no matrix observation. Attendance remains Not Available; absence is not inferred."
    else:
        code, reason = "no_evidence", "Neither a matrix observation nor verified timing is available. Attendance is Not Available."
    return {"rule_version": RULE_VERSION, "minimum_minutes": minutes,
            "matrix_status": "not_available" if matrix_present is None else ("present" if matrix_present else "absent"),
            "timing_seconds": duration_microseconds // 1_000_000 if duration_microseconds is not None else None,
            "timing_qualifies": qualified, "effective_present": present, "source": source,
            "corrected": corrected, "conflict": conflict, "needs_review": review,
            "reason_code": code, "reason": reason}

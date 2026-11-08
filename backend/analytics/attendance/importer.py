"""Read-only Microsoft Teams attendance importer.

Pipeline: read workbook -> classify participants -> de-duplicate and merge
join/leave intervals per learner and lecture occurrence -> apply the delivered-
session rule -> map meetings to modules -> atomic, idempotent load.

Only aggregate counts are stored in diagnostics; no personal data is logged.
"""

from __future__ import annotations

import csv
import datetime as dt
import logging
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ..importer.loader import _upsert
from ..models import (
    AttendanceImportRun, Course, Enrollment, LectureSession, Learner, LearnerIdentity, SessionAttendance, TeamsMeeting,
)
from .mapping import map_subject

log = logging.getLogger("analytics.attendance")

SHEETS = {
    "attendance": ("teams attendance", ["Email", "Role", "Report ID", "Attendance record ID"]),
    "intervals": ("join and leave", ["Email", "Join time (UTC)", "Leave time (UTC)", "Report ID", "Attendance record ID"]),
    "reports": ("teams reports", ["Meeting subject", "Meeting start (UTC)", "Meeting end (UTC)", "Report ID", "Meeting ID"]),
}
FREE_MAIL = {"gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com", "duck.com", "live.com"}


class AttendanceValidationError(Exception):
    def __init__(self, errors):
        super().__init__("; ".join(errors[:5]))
        self.errors = errors


def parse_ts(value) -> dt.datetime | None:
    """ISO-8601 UTC timestamps with up to 7 fractional digits (for example ...4429554Z)."""
    if value in (None, ""):
        return None
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    s = str(value).strip().rstrip("Z")
    s = re.sub(r"[+-]\d\d:\d\d$", "", s)
    base, _, frac = s.partition(".")
    out = dt.datetime.fromisoformat(base + (f".{frac[:6]}" if frac else ""))
    return out.replace(tzinfo=dt.timezone.utc)


def norm_email(value) -> str:
    return str(value or "").strip().lower()


def merge_intervals(intervals):
    """De-duplicate and merge overlapping or touching (start, end) intervals."""
    merged = []
    for start, end in sorted(set(intervals)):
        if end < start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(a, b) for a, b in merged]


def read_workbook(path: Path) -> dict[str, list[dict]]:
    import openpyxl

    if not path.is_file():
        raise AttendanceValidationError([f"Teams workbook not found: {path.name}"])
    with open(path, "rb") as fh:
        wb = openpyxl.load_workbook(fh, read_only=True, data_only=True)
        try:
            raw = {re.sub(r"\s+", " ", ws.title).strip().lower(): [list(r) for r in ws.iter_rows(values_only=True)]
                   for ws in wb.worksheets}
        finally:
            wb.close()
    tables, errors = {}, []
    for key, (title, required) in SHEETS.items():
        rows = raw.get(title)
        if rows is None:
            errors.append(f"Worksheet missing: '{title}'")
            continue
        # The export has title/description rows above the header; locate the header row.
        hdr_idx = None
        for i, r in enumerate(rows[:10]):
            cells = [str(c).strip() if c else "" for c in r]
            if all(h in cells for h in required):
                hdr_idx = i
                break
        if hdr_idx is None:
            errors.append(f"Worksheet '{title}' is missing headers: {', '.join(required)}")
            continue
        hdr = [str(c).strip() if c else "" for c in rows[hdr_idx]]
        tables[key] = [dict(zip(hdr, r)) for r in rows[hdr_idx + 1:] if any(c not in (None, "") for c in r)]
    if errors:
        raise AttendanceValidationError(errors)
    return tables


def load_aliases(path: Path, learner_ids: set[int]) -> tuple[dict[str, int], list[str]]:
    """Optional verified aliases CSV with columns teams_email,learner_id."""
    aliases, problems = {}, []
    if not path.is_file():
        return aliases, problems
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for n, row in enumerate(csv.DictReader(fh), start=2):
            email, lid = norm_email(row.get("teams_email")), str(row.get("learner_id") or "").strip()
            if not email or not lid.isdigit():
                problems.append(f"alias row {n}: needs teams_email and numeric learner_id")
            elif int(lid) not in learner_ids:
                problems.append(f"alias row {n}: learner_id not in registry")
            elif email in aliases and aliases[email] != int(lid):
                problems.append(f"alias row {n}: email aliased to two different learners")
            else:
                aliases[email] = int(lid)
    return aliases, problems


@dataclass
class AttendanceDataset:
    meetings: dict = field(default_factory=dict)    # meeting_id -> fields
    sessions: dict = field(default_factory=dict)    # (meeting_id, date) -> fields
    attendance: dict = field(default_factory=dict)  # (meeting_id, date, learner_id) -> fields
    diagnostics: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    coverage: tuple = (None, None)


def build_dataset(tables, learners_by_email, learner_names, aliases, courses, enrollments,
                  staff_domains, min_minutes, min_learners) -> AttendanceDataset:
    ds = AttendanceDataset()
    staff_domains = {d.lower() for d in staff_domains}
    learner_domains = {e.split("@")[-1] for e in learners_by_email if "@" in e} - FREE_MAIL

    # ---- Reports -> meetings and lecture occurrences --------------------------------
    report_info = {}
    subjects_per_meeting = defaultdict(set)
    for r in tables["reports"]:
        rid, mid = str(r.get("Report ID") or "").strip(), str(r.get("Meeting ID") or "").strip()
        start, end = parse_ts(r.get("Meeting start (UTC)")), parse_ts(r.get("Meeting end (UTC)"))
        if not rid or not mid or not start:
            continue
        report_info[rid] = {"meeting_id": mid, "start": start, "end": end or start}
        subjects_per_meeting[mid].add(str(r.get("Meeting subject") or "").strip())
    if not report_info:
        raise AttendanceValidationError(["No usable rows in 'Teams reports'"])

    for mid, subjects in subjects_per_meeting.items():
        subject = sorted(subjects)[0]
        if len(subjects) > 1:
            ds.warnings.append("A Teams meeting ID has more than one subject; the first is used for mapping")
        cid, status, note = map_subject(subject, courses)
        ds.meetings[mid] = {"subject": subject[:500], "course_source_id": cid, "mapping_status": status,
                            "evidence_source": "teams_intervals", "source_file": "",
                            "mapping_note": note[:500]}

    occ_reports = defaultdict(list)
    for info in report_info.values():
        occ_reports[(info["meeting_id"], info["start"].date())].append(info)

    # ---- Classify participants (per report + attendance record) ----------------------
    row_class, email_class = Counter(), defaultdict(set)
    record_learner, method = {}, {}
    organizer_learner_rows = 0
    for a in tables["attendance"]:
        rid, rec = str(a.get("Report ID") or ""), str(a.get("Attendance record ID") or "")
        email, role = norm_email(a.get("Email")), str(a.get("Role") or "").strip().lower()
        lid = learners_by_email.get(email) if email else None
        how = "email"
        if lid is None and email in aliases:
            lid, how = aliases[email], "alias"
        domain = email.split("@")[-1] if "@" in email else ""
        if lid is not None:
            cls = "learner"
            record_learner[(rid, rec)] = lid
            method[lid] = how
            if role == "organizer":
                organizer_learner_rows += 1
        elif not email:
            cls = "anonymous_or_external_no_email"
        elif role == "organizer":
            cls = "organizer"
        elif domain in staff_domains:
            cls = "training_provider_staff"
        elif domain in learner_domains:
            cls = "unmatched_learner_domain"  # possibly a learner using another address -> alias review
        else:
            cls = "unmatched_external"
        row_class[cls] += 1
        if email:
            email_class[cls].add(email)

    # ---- Intervals: de-duplicate exact rows, merge per learner and lecture -----------
    seen, dup_rows, orphan_rows = set(), 0, 0
    intervals = defaultdict(list)
    for r in tables["intervals"]:
        rid, rec = str(r.get("Report ID") or ""), str(r.get("Attendance record ID") or "")
        join, leave = parse_ts(r.get("Join time (UTC)")), parse_ts(r.get("Leave time (UTC)"))
        key = (rid, rec, join, leave)
        if key in seen:
            dup_rows += 1
            continue
        seen.add(key)
        lid = record_learner.get((rid, rec))
        if lid is None:
            continue  # not a verified learner
        info = report_info.get(rid)
        if info is None or join is None or leave is None:
            orphan_rows += 1
            continue
        intervals[(info["meeting_id"], info["start"].date(), lid)].append((join, leave))

    min_seconds = min_minutes * 60
    raw_interval_rows = sum(len(v) for v in intervals.values())
    merged_away = 0
    for (mid, day, lid), ivs in intervals.items():
        merged = merge_intervals(ivs)
        merged_away += len(ivs) - len(merged)
        if not merged:
            continue
        seconds = int(sum((b - a).total_seconds() for a, b in merged))
        ds.attendance[(mid, day, lid)] = {
            "presence_seconds": seconds, "interval_count": len(merged),
            "first_join": merged[0][0], "last_leave": merged[-1][1],
            "attended": seconds >= min_seconds, "match_method": method.get(lid, "email"),
        }

    verified_per_occ = Counter((mid, day) for (mid, day, _), a in ds.attendance.items() if a["attended"])
    for (mid, day), reps in occ_reports.items():
        verified = verified_per_occ.get((mid, day), 0)
        delivered = verified >= min_learners
        ds.sessions[(mid, day)] = {
            "starts_at": min(r["start"] for r in reps), "ends_at": max(r["end"] for r in reps),
            "report_count": len(reps), "verified_learners": verified, "delivered": delivered,
            "exclusion_reason": "" if delivered else
            f"Below delivered-session rule ({verified} learners with >= {min_minutes} min; need {min_learners})",
        }

    days = sorted(day for (_, day) in ds.sessions)
    ds.coverage = (days[0], days[-1]) if days else (None, None)

    # ---- Diagnostics (aggregate counts only) -----------------------------------------
    matched_ids = {lid for (_, _, lid) in ds.attendance}
    registry_ids = set(learners_by_email.values()) | set(aliases.values())
    unmatched_lms = [lid for lid in registry_ids if lid not in matched_ids]

    def norm(s):
        return re.sub(r"[^a-z]", "", str(s or "").lower())

    teams_names = {norm(a.get("Display name")) for a in tables["attendance"]
                   if (str(a.get("Report ID") or ""), str(a.get("Attendance record ID") or "")) not in record_learner}
    name_only = sum(1 for lid in unmatched_lms if norm(learner_names.get(lid)) and norm(learner_names.get(lid)) in teams_names)
    not_enrolled = sum(1 for (mid, _, lid) in ds.attendance
                       if ds.meetings[mid]["course_source_id"]
                       and (lid, ds.meetings[mid]["course_source_id"]) not in enrollments)
    delivered_n = sum(1 for s in ds.sessions.values() if s["delivered"])
    ds.diagnostics = {
        "rows": {"attendance": len(tables["attendance"]), "join_leave": len(tables["intervals"]),
                 "reports": len(tables["reports"])},
        "distinct_reports": len(report_info),
        "distinct_meetings": len(ds.meetings),
        "lecture_occurrences": len(ds.sessions),
        "delivered_sessions": delivered_n,
        "excluded_occurrences": len(ds.sessions) - delivered_n,
        "participant_rows_by_class": dict(row_class),
        "distinct_emails_by_class": {k: len(v) for k, v in email_class.items()},
        "matched_learners": len(matched_ids),
        "matched_by_alias": sum(1 for lid in matched_ids if method.get(lid) == "alias"),  # learners with an alias-matched record
        "registry_learners_without_teams_match": len(unmatched_lms),
        "name_only_candidates_not_accepted": name_only,
        "learner_rows_with_organizer_role": organizer_learner_rows,
        "duplicate_interval_rows_removed": dup_rows,
        "learner_intervals": raw_interval_rows,
        "intervals_merged_away": merged_away,
        "interval_rows_without_report": orphan_rows,
        "attendance_in_modules_not_enrolled": not_enrolled,
        "meeting_mappings": {m["subject"]: {"course_source_id": m["course_source_id"], "status": m["mapping_status"]}
                             for m in ds.meetings.values()},
        "thresholds": {"min_attendance_minutes": min_minutes, "min_learners_for_delivered": min_learners},
    }
    unmatched_domain = ds.diagnostics["distinct_emails_by_class"].get("unmatched_learner_domain")
    if unmatched_domain:
        ds.warnings.append(f"{unmatched_domain} Teams emails use a learner organisation domain but match no registry "
                           "learner; add verified aliases if they are learners")
    if any(m["mapping_status"] == "unmapped" for m in ds.meetings.values()):
        ds.warnings.append("Some Teams meetings are not mapped to a module and are excluded from module attendance")
    return ds


def load_dataset(ds: AttendanceDataset) -> dict:
    stats = {}
    meeting_pk, stats["meetings"] = _upsert(TeamsMeeting, ("meeting_id",), {(k,): v for k, v in ds.meetings.items()})
    session_pk, stats["sessions"] = _upsert(
        LectureSession, ("meeting_id", "session_date"),
        {(meeting_pk[(mid,)], day): v for (mid, day), v in ds.sessions.items()})
    _, stats["attendance"] = _upsert(
        SessionAttendance, ("session_id", "learner_source_id"),
        {(session_pk[(meeting_pk[(mid,)], day)], lid): v for (mid, day, lid), v in ds.attendance.items()})
    return stats


def registry_lookup():
    learners = list(Learner.objects.values_list("source_id", "email", "name"))
    by_email, dup_emails = {}, set()
    identities = [(sid, email) for sid, email, _ in learners]
    canonical_ids = {sid for sid, _, _ in learners}
    identities += list(LearnerIdentity.objects.filter(canonical_source_id__in=canonical_ids)
                       .values_list("canonical_source_id", "email"))
    for sid, email in identities:
        e = norm_email(email)
        if e:
            if e in by_email and by_email[e] != sid:
                dup_emails.add(e)
            by_email[e] = sid
    for e in dup_emails:  # ambiguous: one email shared by several registry learners -> never auto-match
        by_email.pop(e, None)
    return learners, by_email, dup_emails


def run_attendance_import(path=None) -> AttendanceImportRun:
    path = Path(path) if path else Path(settings.ATTENDANCE_DIRECTORY)
    run = AttendanceImportRun.objects.create(source_ref=path.name if path.is_file() else "Four authoritative module workbooks")
    try:
        if path.is_file():
            run.source_modified_at = dt.datetime.fromtimestamp(os.stat(path).st_mtime, tz=dt.timezone.utc)
        learners, by_email, dup_emails = registry_lookup()
        if not learners:
            raise AttendanceValidationError(["Learner registry is empty; import LMS data first"])
        aliases, alias_problems = load_aliases(Path(settings.TEAMS_ALIASES_PATH), {sid for sid, _, _ in learners})
        if alias_problems:
            raise AttendanceValidationError(alias_problems)
        courses = dict(Course.objects.values_list("source_id", "title"))
        enrollments = set(Enrollment.objects.values_list("learner__source_id", "course__source_id"))
        if path.is_dir():
            from .matrices import read_matrices, SOURCES
            ds = read_matrices(path, by_email, aliases, courses, enrollments)
            run.source_modified_at = dt.datetime.fromtimestamp(max((path / f).stat().st_mtime for f, _ in SOURCES.values()), tz=dt.timezone.utc)
        else:
            tables = read_workbook(path)
            ds = build_dataset(tables, by_email, {sid: name for sid, _, name in learners}, aliases, courses, enrollments,
                               settings.TEAMS_STAFF_DOMAINS, settings.TEAMS_MIN_ATTENDANCE_MINUTES,
                               settings.TEAMS_MIN_LEARNERS_FOR_DELIVERED)
        ds.diagnostics["ambiguous_registry_emails"] = len(dup_emails)
        with transaction.atomic():
            # A stale matrix or legacy interval export must not silently delete
            # verified dates and hide timing that still references those dates.
            existing = set(LectureSession.objects.filter(
                meeting__evidence_source="attendance_matrix"
            ).values_list("meeting__meeting_id", "session_date"))
            removed = existing - set(ds.sessions)
            if removed:
                raise AttendanceValidationError([
                    f"Attendance source would remove {len(removed)} existing matrix lecture dates. "
                    "Use the latest verified workbooks; previous attendance was preserved. "
                    "Calendar corrections require explicit evidence review."
                ])
            stats = load_dataset(ds)
        run.diagnostics = {**ds.diagnostics, "load": stats}
        run.warnings = ds.warnings
        run.coverage_start, run.coverage_end = ds.coverage
        run.changed = any(s["created"] or s["updated"] or s["deleted"] for s in stats.values())
        run.status = AttendanceImportRun.Status.SUCCESS
    except AttendanceValidationError as exc:
        run.status, run.errors = AttendanceImportRun.Status.FAILED, exc.errors[:20]
        log.warning("Teams attendance import failed validation; previous attendance kept")
    except Exception as exc:  # rolled back by the atomic block
        run.status, run.errors = AttendanceImportRun.Status.FAILED, [f"{type(exc).__name__}: {exc}"]
        log.exception("Teams attendance import failed; previous attendance kept")
    run.finished_at = timezone.now()
    run.save()
    return run

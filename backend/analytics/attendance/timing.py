"""Verified Teams timing overlay. Never writes attendance, lecture or LMS tables.

Mappings and evidenced email aliases live in a private JSON policy, not Git.
All source reports are considered; no duration threshold changes binary status.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from django.db import transaction

from ..importer.exclusions import NON_LEARNER_SOURCE_IDS
from ..importer.identities import MERGED_LEARNER_IDS
from ..models import Course, Enrollment, LectureSession, LectureTiming, TimingImport
from .importer import merge_intervals, norm_email, read_workbook, registry_lookup
from .mapping import map_subject, normalize_title

UTC = dt.timezone.utc
POLICY_VERSION = 1
# Owner-approved timing alias. Keep this separate from binary attendance mappings.
# Private Meeting IDs and organiser IDs belong only in the local timing policy.
APPROVED_TIMING_SUBJECTS = {
    normalize_title("Module 2-Procurement Risk and Contracts Management"): 121642,
}
# The four former Advanced holds were independently resolved on 2026-11-07:
# exact scheduled series, dated transcripts identifying sessions 4/5/6, and the
# dated session-11 teaching deck corroborate the reversed Excel headers. The
# private reconciliation bundle preserves the evidence. Each date still needs
# explicit approval in verified_lecture_dates; future unresolved dates stay held.
AMBIGUOUS_DATES: set[tuple[int, dt.date]] = set()


class TimingValidationError(ValueError):
    """Messages contain row identifiers/counts only, never participant information."""


def require(condition, message):
    if not condition:
        raise TimingValidationError(message)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str,
                                     separators=(",", ":")).encode()).hexdigest()


def timestamp(value):
    try:
        result = value if isinstance(value, dt.datetime) else dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        raise TimingValidationError("Invalid Teams timestamp") from None
    require(result.tzinfo is not None and result.utcoffset() is not None, "Teams timestamp must include a UTC offset")
    return result.astimezone(UTC)


def microseconds(delta):
    return ((delta.days * 86400 + delta.seconds) * 1_000_000) + delta.microseconds


def read_policy(path):
    try:
        policy = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raise TimingValidationError("Private timing policy is missing or invalid JSON") from None
    require(isinstance(policy, dict) and policy.get("version") == POLICY_VERSION, "Unsupported timing policy version")
    require(isinstance(policy.get("meetings"), list) and policy["meetings"], "Approved Meeting ID mappings are required")
    require(isinstance(policy.get("aliases", []), list), "Timing aliases must be a list")
    require(isinstance(policy.get("verified_lecture_dates"), dict), "Verified lecture dates are required")
    return policy


def prepare(source, policy_path):
    source = Path(source)
    require(source.is_file(), "Timing workbook is missing")
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    policy = read_policy(policy_path)
    tables = read_workbook(source)
    require(hashlib.sha256(source.read_bytes()).hexdigest() == source_sha, "Source changed while being read")
    return prepare_tables(tables, policy, {"source_name": source.name, "source_sha256": source_sha})


def prepare_tables(tables, policy, source_meta):
    """Shared verification for Excel and complete, verified Graph attendance records."""
    require(tables["reports"] and tables["attendance"] and tables["intervals"], "Timing workbook must not be empty")

    learners, by_email, ambiguous_emails = registry_lookup()
    eligible_ids = {sid for sid, _, _ in learners} - set(NON_LEARNER_SOURCE_IDS) - set(MERGED_LEARNER_IDS)
    by_email = {email: uid for email, uid in by_email.items() if uid in eligible_ids}
    enrollments = set(Enrollment.objects.filter(learner__source_id__in=eligible_ids)
                      .values_list("learner__source_id", "course__source_id"))
    courses = dict(Course.objects.values_list("source_id", "title"))
    calendar = list(LectureSession.objects.filter(delivered=True, meeting__evidence_source="attendance_matrix")
                    .values_list("meeting__course_source_id", "session_date"))
    require(calendar and eligible_ids, "Import verified LMS identities and binary lecture dates first")
    require(len(calendar) == len(set(calendar)), "More than one matrix lecture exists for a module/date")
    calendar = set(calendar)

    meetings = {}
    for row in policy["meetings"]:
        require(isinstance(row, dict), "Invalid meeting policy row")
        mid, cid, subject = row.get("meeting_id"), row.get("course_source_id"), row.get("approved_subject")
        require(isinstance(mid, str) and mid and mid not in meetings, "Meeting IDs must be unique and nonempty")
        require(type(cid) is int and cid in courses, "Mapped course does not exist")
        mapped_course = map_subject(subject, courses)[0] if isinstance(subject, str) else None
        if mapped_course is None and isinstance(subject, str):
            mapped_course = APPROVED_TIMING_SUBJECTS.get(normalize_title(subject))
        require(isinstance(subject, str) and mapped_course == cid,
                "Meeting policy conflicts with approved LMS subject mapping")
        known_reports = row.get("known_report_ids", [])
        require(isinstance(known_reports, list) and
                all(isinstance(rid, str) and rid and len(rid) <= 1024 for rid in known_reports) and
                len(known_reports) == len(set(known_reports)),
                "Known report IDs must be unique nonempty strings")
        meetings[mid] = (cid, normalize_title(subject))
    # Several explicitly approved series may serve one course. Each Meeting ID stays
    # unique; exact lecture dates and identities are validated below. Their intervals
    # share the same learner/course/date group, so overlaps are counted only once.
    verified_dates = set()
    try:
        for cid, days in policy["verified_lecture_dates"].items():
            require(isinstance(days, list), "Verified lecture dates must be lists")
            verified_dates.update((int(cid), dt.date.fromisoformat(day)) for day in days)
    except (ValueError, TypeError):
        raise TimingValidationError("Invalid verified lecture date") from None
    require(verified_dates and all(cid in courses for cid, day in verified_dates), "Verified date course does not exist")

    aliases = {}
    for row in policy.get("aliases", []):
        require(isinstance(row, dict), "Invalid timing alias row")
        email, uid = norm_email(row.get("email")), row.get("learner_source_id")
        identity, tenant = row.get("identity_id"), row.get("tenant_id")
        require(email and email not in aliases and email not in ambiguous_emails, "Duplicate or ambiguous timing alias")
        require(type(uid) is int and uid in eligible_ids, "Timing alias must target an included canonical learner")
        require(email not in by_email or by_email[email] == uid, "Timing alias conflicts with registered identity")
        require(isinstance(identity, str) and identity and isinstance(tenant, str) and tenant,
                "Timing alias needs its verified Teams Identity ID and Tenant ID")
        aliases[email] = (uid, identity, tenant)

    reports, participants, identity_links = {}, {}, defaultdict(set)
    counts = Counter()
    for row in tables["reports"]:
        rid, mid = str(row.get("Report ID") or ""), str(row.get("Meeting ID") or "")
        require(rid and mid, "Missing report or meeting identifier")
        start, end = timestamp(row.get("Meeting start (UTC)")), timestamp(row.get("Meeting end (UTC)"))
        require(end >= start, "Report ends before it starts")
        subject = normalize_title(row.get("Meeting subject"))
        if mid in meetings:
            require(subject == meetings[mid][1], "Known Meeting ID has an unapproved subject")
        report = {"mid": mid, "cid": meetings.get(mid, (None,))[0], "subject": subject, "start": start, "end": end}
        require(rid not in reports or reports[rid] == report, "Conflicting duplicate Report ID")
        reports[rid] = report
    counts["reports"] = len(reports)
    counts["unmapped_reports"] = sum(r["cid"] is None for r in reports.values())
    for row in tables["attendance"]:
        key = (str(row.get("Report ID") or ""), str(row.get("Attendance record ID") or ""))
        require(key[0] in reports and key[1], "Participant has an invalid report/record identifier")
        person = {"email": norm_email(row.get("Email")), "identity": str(row.get("Identity ID") or ""),
                  "tenant": str(row.get("Tenant ID") or "")}
        require(key not in participants or participants[key] == person, "Conflicting duplicate participant key")
        participants[key] = person
        uid = by_email.get(person["email"])
        if uid is not None and person["identity"] and person["tenant"]:
            identity_links[(person["identity"], person["tenant"])].add(uid)
    for uid, identity, tenant in aliases.values():
        # Graph may no longer return the old email that independently anchored an
        # approved alias. Read-only Excel anchors verify identity, never attendance.
        for anchor in tables.get("identity_anchors", []):
            anchor_uid = by_email.get(norm_email(anchor.get("Email")))
            if anchor_uid is not None and anchor.get("Identity ID") and anchor.get("Tenant ID"):
                identity_links[(str(anchor["Identity ID"]), str(anchor["Tenant ID"]))].add(anchor_uid)
        require(identity_links[(identity, tenant)] == {uid}, "Alias lacks an unambiguous registered Teams identity anchor")

    groups = defaultdict(list)
    for row_number, row in enumerate(tables["intervals"], start=1):
        counts["source_interval_rows"] += 1
        key = (str(row.get("Report ID") or ""), str(row.get("Attendance record ID") or ""))
        require(key in participants, "Interval has no matching participant record")
        person, report = participants[key], reports[key[0]]
        require(norm_email(row.get("Email")) == person["email"], "Interval and participant identities disagree")
        require(normalize_title(row.get("Meeting subject")) == report["subject"], "Interval subject disagrees with report")
        require(timestamp(row.get("Report start (UTC)")) == report["start"], "Interval report start disagrees with report")
        start, end = timestamp(row.get("Join time (UTC)")), timestamp(row.get("Leave time (UTC)"))
        require(end >= start, "Interval ends before it starts")
        # Report metadata has lower precision than interval timestamps in this export.
        require(start >= report["start"] - dt.timedelta(milliseconds=1) and
                end <= report["end"] + dt.timedelta(milliseconds=1), "Interval is outside its report window")
        seconds = row.get("Duration (seconds)")
        require(isinstance(seconds, (int, float)) and seconds == int(seconds) and
                int(seconds) == microseconds(end - start) // 1_000_000, "Interval duration does not agree with timestamps")
        if report["cid"] is None:
            counts["unmapped_interval_rows"] += 1
            continue
        uid, method = by_email.get(person["email"]), "registry"
        if uid is None and person["email"] in aliases:
            uid, identity, tenant = aliases[person["email"]]
            require((person["identity"], person["tenant"]) == (identity, tenant), "Alias Teams identity has changed")
            method = "verified_timing_alias"
        if uid is None:
            counts["unmatched_identity_interval_rows"] += 1
            continue
        cid, day = report["cid"], report["start"].date()
        if (uid, cid) not in enrollments:
            counts["not_enrolled_interval_rows"] += 1
            continue
        boundary = any(value.date() != day for value in (report["end"], start, end))
        groups[(cid, uid, day)].append({
            "start": start, "end": end, "boundary": boundary,
            "evidence": {"source_kind": "teams_excel", "source_sha256": source_meta["source_sha256"],
                         **row.get("_timing_provenance", {}),
                         "interval_row": row_number, "report_id": key[0], "attendance_record_id": key[1],
                         "meeting_id": report["mid"], "join": str(row["Join time (UTC)"]),
                         "leave": str(row["Leave time (UTC)"]), "reported_seconds": int(seconds), "match_method": method},
        })
    rows = []
    for (cid, uid, day), values in sorted(groups.items()):
        intervals = merge_intervals((v["start"], v["end"]) for v in values)
        status = classify_date(cid, day, calendar, verified_dates, boundary=any(v["boundary"] for v in values))
        counts[status] += 1
        rows.append({"course_source_id": cid, "learner_source_id": uid, "session_date": day, "status": status,
                     "first_join": intervals[0][0], "last_leave": intervals[-1][1],
                     "duration_microseconds": sum(microseconds(b - a) for a, b in intervals),
                     "visit_count": len(intervals), "provenance": [v["evidence"] for v in values]})
    require(rows, "No eligible timing pairs; previous import has been preserved")
    counts["pairs"] = len(rows)
    counts["verified_dates"] = len({(r["course_source_id"], r["session_date"]) for r in rows if r["status"] == "verified"})
    counts = {**{s: 0 for s in LectureTiming.Status.values}, **dict(counts)}
    counts["modules"] = {str(cid): dict(Counter(r["status"] for r in rows if r["course_source_id"] == cid)) for cid in sorted(courses)}
    meta = {**source_meta, "policy_sha256": digest(policy),
            "registry_sha256": digest([sorted(by_email.items()), sorted(ambiguous_emails), sorted(enrollments)]),
            "calendar_sha256": digest(sorted(calendar))}
    meta["fingerprint"] = digest([POLICY_VERSION, meta, rows])
    return meta, rows, counts


def classify_date(cid, day, calendar, verified_dates, *, boundary=False):
    if (cid, day) in AMBIGUOUS_DATES:
        return LectureTiming.Status.AMBIGUOUS_DATE
    if boundary:
        return LectureTiming.Status.BOUNDARY_REVIEW
    if (cid, day) not in calendar:
        return LectureTiming.Status.OFF_CALENDAR
    if (cid, day) not in verified_dates:
        return LectureTiming.Status.DATE_REVIEW
    return LectureTiming.Status.VERIFIED


def activate_snapshot(meta, rows, counts, *, dry_run=False):
    """Call inside an atomic transaction after validation; only timing tables change."""
    active = TimingImport.objects.select_for_update().filter(active=True).first()
    if active and active.fingerprint == meta["fingerprint"]:
        return {"changed": False, "dry_run": dry_run, **counts}
    if dry_run:
        return {"changed": True, "dry_run": True, **counts}
    run, created = TimingImport.objects.get_or_create(fingerprint=meta["fingerprint"], defaults={**meta, "counts": counts})
    if created:
        LectureTiming.objects.bulk_create([LectureTiming(import_run=run, **row) for row in rows])
    TimingImport.objects.filter(active=True).update(active=False)
    run.active = True
    run.save(update_fields=["active"])
    return {"changed": True, "dry_run": False, **counts}


def import_timing(source, policy_path, *, dry_run=False):
    with transaction.atomic():
        # Serialize snapshot activation. The unique active constraint also protects a first-import race.
        active = TimingImport.objects.select_for_update().filter(active=True).first()
        require(not active or active.counts.get("primary_source") != "microsoft_graph",
                "Graph timing is preferred; use import_graph_timing with the Excel fallback to preserve Graph history")
        meta, rows, counts = prepare(source, policy_path)
        return activate_snapshot(meta, rows, counts, dry_run=dry_run)


def attach_timing(sessions, learner_sid, course_sid):
    """Presentation-only fields. Existing attendance fields and summaries stay untouched."""
    records = {}
    if learner_sid not in NON_LEARNER_SOURCE_IDS and learner_sid not in MERGED_LEARNER_IDS:
        records = {r.session_date: r for r in LectureTiming.objects.filter(
            import_run__active=True, status=LectureTiming.Status.VERIFIED,
            learner_source_id=learner_sid, course_source_id=course_sid)}
    for session in sessions:
        record = records.get(session["date"])
        if (course_sid, session["date"]) in AMBIGUOUS_DATES:
            record = None
        session["timing"] = {
            "available": record is not None, "timezone": "UTC",
            "first_join": record.first_join if record else None,
            "last_leave": record.last_leave if record else None,
            "duration_seconds": record.duration_microseconds // 1_000_000 if record else None,
            "visits": record.visit_count if record else None,
        }
    return sessions

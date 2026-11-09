"""Graph-preferred timing with non-destructive Excel and prior-Graph fallbacks."""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import os
import urllib.parse
import uuid
from collections import Counter, defaultdict
from pathlib import Path

from django.db import transaction

from ..importer.exclusions import NON_LEARNER_SOURCE_IDS
from ..importer.identities import MERGED_LEARNER_IDS
from ..models import Course, Enrollment, LectureSession, LectureTiming, TimingImport
from .graph_client import GraphClient, GraphError, meeting_path
from .importer import merge_intervals, norm_email, read_workbook
from .timing import (TimingValidationError, activate_snapshot, classify_date, digest, microseconds,
                     prepare_tables, read_policy, require, timestamp)


def canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


def bindings(policy, tables):
    """Bind only approved Meeting IDs; recover organiser object IDs from source evidence."""
    result = []
    for mapping in policy["meetings"]:
        original = [r for r in tables["reports"] if str(r.get("Meeting ID")) == mapping["meeting_id"]]
        organizers = {str(r.get("Organizer ID") or "") for r in original}
        if mapping.get("organizer_id"):
            organizers.add(mapping["organizer_id"])
        require(len(organizers) == 1 and "" not in organizers, "A single verified organiser is required for each Meeting ID")
        organizer = next(iter(organizers))
        try:
            uuid.UUID(organizer)
        except (ValueError, TypeError, AttributeError):
            raise TimingValidationError("Invalid organiser object ID") from None
        result.append({**mapping, "organizer_id": organizer})
    return result


def validate_report(report, records):
    require(isinstance(report.get("id"), str) and report["id"], "Graph report ID is missing")
    start, end = timestamp(report.get("meetingStartDateTime")), timestamp(report.get("meetingEndDateTime"))
    require(end >= start, "Graph report window is invalid")
    total = report.get("totalParticipantCount")
    require(type(total) is int and total >= 0 and len(records) == total,
            "Graph participant collection is incomplete")
    for record in records:
        require(isinstance(record, dict) and record.get("id"), "Graph participant ID is missing")
        intervals = record.get("attendanceIntervals")
        require(isinstance(intervals, list), "Graph attendance intervals are missing")
        reported = record.get("totalAttendanceInSeconds")
        require(type(reported) is int and reported >= 0, "Graph total duration is invalid")
        seconds = 0
        for interval in intervals:
            a, b = timestamp(interval.get("joinDateTime")), timestamp(interval.get("leaveDateTime"))
            duration = interval.get("durationInSeconds")
            require(b >= a and type(duration) is int and duration == microseconds(b - a) // 1_000_000,
                    "Graph interval duration is invalid")
            require(a >= start - dt.timedelta(milliseconds=1) and b <= end + dt.timedelta(milliseconds=1),
                    "Graph interval is outside its report window")
            seconds += duration
        require(seconds == reported, "Graph interval list is incomplete")


def fetch_snapshot(client, policy, fallback, previous=None, *, progress=None):
    """List with pagination, then probe known report IDs omitted by the 50-report cap."""
    client.authenticate()
    result = {"version": 1, "api_version": "v1.0", "meetings": []}
    for mapping in bindings(policy, fallback):
        cid, mid = mapping["course_source_id"], mapping["meeting_id"]
        # A historical onlineMeeting lookup can return 404 while its attendance
        # artifacts remain readable. Never gate these requests on meeting metadata
        # or a join-URL lookup; use this series' verified organiser and Meeting ID.
        path = meeting_path(mapping["organizer_id"], mid)
        known = {str(r["Report ID"]) for r in fallback["reports"] if str(r["Meeting ID"]) == mid}
        # Private, independently verified report IDs recovered from original
        # recording/transcript metadata bypass the latest-50 listing limit.
        # Always fetch and validate their actual attendance records below;
        # a recording itself never establishes learner presence or duration.
        known.update(mapping.get("known_report_ids", []))
        if previous:
            for row in previous.records.all():
                known.update(p["report_id"] for p in row.provenance if p["meeting_id"] == mid)
        entry = {"course_source_id": cid, "meeting_id": mid, "organizer_id": mapping["organizer_id"],
                 "approved_subject": mapping["approved_subject"], "reports": [], "errors": [], "list_pages": 0}
        listed = {}
        try:
            values, entry["list_pages"] = client.collection(path + "/attendanceReports")
            listed = {r["id"]: r for r in values}
        except GraphError as exc:
            entry["errors"].append({**exc.diagnostic(), "operation": "list_reports"})
        entry["listed_reports"] = len(listed)
        for n, rid in enumerate(sorted(known | set(listed)), 1):
            report_path = path + "/attendanceReports/" + urllib.parse.quote(rid, safe="")
            try:
                report = listed[rid] if rid in listed else client.get(report_path)
                require(report.get("id") == rid, "Graph returned a different report ID")
                records, pages = client.collection(report_path + "/attendanceRecords")
                validate_report(report, records)
                entry["reports"].append({"report": report, "records": sorted(records, key=lambda r: r["id"]), "record_pages": pages})
            except GraphError as exc:
                entry["errors"].append({**exc.diagnostic(), "report_id": rid, "operation": "read_report"})
            except (TimingValidationError, TypeError, KeyError):
                entry["errors"].append({"status": 200, "code": "IncompleteOrInvalidReport", "report_id": rid, "operation": "validate_report"})
            if progress and n % 20 == 0:
                progress({"course_id": cid, "reports_checked": n, "complete_reports": len(entry["reports"])})
        result["meetings"].append(entry)
        if progress:
            progress({"course_id": cid, "listed_reports": len(listed), "complete_reports": len(entry["reports"]),
                      "unavailable_or_invalid": len(entry["errors"])})
    return result


def covers(candidate, previous):
    """A shorter/conflicting response cannot erase previously verified intervals."""
    merged = merge_intervals(candidate)
    return all(any(x <= a and y >= b for x, y in merged) for a, b in merge_intervals(previous))


def combine_tables(fallback, snapshot, fallback_sha, graph_sha):
    reports = {str(r["Report ID"]): copy.deepcopy(r) for r in fallback["reports"]}
    participants = {(str(r["Report ID"]), str(r["Attendance record ID"])): copy.deepcopy(r) for r in fallback["attendance"]}
    intervals = defaultdict(list)
    for row in fallback["intervals"]:
        row = copy.deepcopy(row)
        row["_timing_provenance"] = {"source_kind": "teams_excel", "source_sha256": fallback_sha}
        intervals[(str(row["Report ID"]), str(row["Attendance record ID"]))].append(row)
    stats = Counter()
    for meeting in snapshot["meetings"]:
        mid, subject = meeting["meeting_id"], meeting["approved_subject"]
        for item in meeting["reports"]:
            report, records = item["report"], item["records"]
            rid = report["id"]
            old_report = reports.get(rid)
            if old_report and (str(old_report["Meeting ID"]) != mid or
                               timestamp(old_report["Meeting start (UTC)"]) != timestamp(report["meetingStartDateTime"]) or
                               timestamp(old_report["Meeting end (UTC)"]) != timestamp(report["meetingEndDateTime"])):
                stats["conflicting_reports_preserved"] += 1
                continue
            # Reject a truncated report even if its advertised participant count is smaller.
            old_ids = {record for report_id, record in participants if report_id == rid}
            if not old_ids.issubset({str(r["id"]) for r in records}):
                stats["incomplete_reports_preserved"] += 1
                continue
            if not records:
                stats["empty_reports_preserved"] += 1
                continue
            reports[rid] = {"Report ID": rid, "Meeting ID": mid, "Meeting subject": subject,
                            "Meeting start (UTC)": report["meetingStartDateTime"], "Meeting end (UTC)": report["meetingEndDateTime"],
                            "Organizer ID": meeting["organizer_id"]}
            stats["accepted_graph_reports"] += 1
            report_sha = digest(item)
            for record in records:
                key = (rid, str(record["id"]))
                previous = intervals.get(key, [])
                identity = record.get("identity") or {}
                old_person = participants.get(key)
                candidate = [(timestamp(i["joinDateTime"]), timestamp(i["leaveDateTime"])) for i in record["attendanceIntervals"]]
                prior = [(timestamp(i["Join time (UTC)"]), timestamp(i["Leave time (UTC)"])) for i in previous]
                if old_person and (str(old_person.get("Identity ID") or "") != str(identity.get("id") or "") or
                                   str(old_person.get("Tenant ID") or "") != str(identity.get("tenantId") or "") or
                                   norm_email(old_person.get("Email")) != norm_email(record.get("emailAddress"))):
                    stats["identity_conflicts_preserved"] += 1
                    continue
                if not candidate or not covers(candidate, prior):
                    stats["incomplete_or_conflicting_records_preserved"] += 1
                    continue
                person = {"Report ID": rid, "Attendance record ID": key[1], "Email": record.get("emailAddress"),
                          "Identity ID": identity.get("id"), "Tenant ID": identity.get("tenantId"), "Role": record.get("role")}
                participants[key] = person
                intervals[key] = [{"Report ID": rid, "Attendance record ID": key[1], "Email": person["Email"],
                                   "Meeting subject": subject, "Report start (UTC)": report["meetingStartDateTime"],
                                   "Join time (UTC)": i["joinDateTime"], "Leave time (UTC)": i["leaveDateTime"],
                                   "Duration (seconds)": i["durationInSeconds"],
                                   "_timing_provenance": {"source_kind": "microsoft_graph", "source_sha256": graph_sha,
                                                          "report_sha256": report_sha,
                                                          "report_start": report["meetingStartDateTime"], "report_end": report["meetingEndDateTime"]}}
                                  for i in record["attendanceIntervals"]]
                stats["accepted_graph_participant_records"] += 1
    tables = {"reports": [reports[k] for k in sorted(reports)],
              "attendance": [participants[k] for k in sorted(participants)],
              "intervals": [r for k in sorted(intervals) for r in intervals[k]],
              "identity_anchors": fallback["attendance"]}
    return tables, dict(stats)


def retain_previous_graph(rows, previous, policy):
    """Expired/truncated Graph history survives even when it never existed in Excel."""
    if not previous or previous.counts.get("primary_source") != "microsoft_graph":
        return rows, 0
    from ..identity import source_enrollments
    eligible = source_enrollments()
    calendar = set(LectureSession.objects.filter(delivered=True, meeting__evidence_source="attendance_matrix")
                   .values_list("meeting__course_source_id", "session_date"))
    verified = {(int(c), dt.date.fromisoformat(d)) for c, days in policy["verified_lecture_dates"].items() for d in days}
    approved = {(m["meeting_id"], m["course_source_id"]) for m in policy["meetings"]}
    combined = {(r["course_source_id"], r["learner_source_id"], r["session_date"]): copy.deepcopy(r) for r in rows}
    retained = 0
    for old in previous.records.all():
        if (old.learner_source_id, old.course_source_id) not in eligible:
            continue
        evidence = [p for p in old.provenance if p.get("source_kind") == "microsoft_graph" and
                    (p["meeting_id"], old.course_source_id) in approved]
        if not evidence:
            continue
        key = (old.course_source_id, old.learner_source_id, old.session_date)
        row = combined.setdefault(key, {"course_source_id": key[0], "learner_source_id": key[1], "session_date": key[2], "provenance": []})
        by_record = defaultdict(list)
        for p in row["provenance"]:
            by_record[(p["meeting_id"], p["report_id"], p["attendance_record_id"])].append(p)
        old_records = defaultdict(list)
        for p in evidence:
            old_records[(p["meeting_id"], p["report_id"], p["attendance_record_id"])].append(p)
        for record_key, history in old_records.items():
            current = by_record.get(record_key, [])
            current_intervals = [(timestamp(p["join"]), timestamp(p["leave"])) for p in current]
            historical_intervals = [(timestamp(p["join"]), timestamp(p["leave"])) for p in history]
            if (current and all(p.get("source_kind") == "microsoft_graph" for p in current)
                    and covers(current_intervals, historical_intervals)):
                continue
            by_record[record_key] = history
            retained += 1
        row["provenance"] = [p for k in sorted(by_record) for p in by_record[k]]
        intervals = merge_intervals((timestamp(p["join"]), timestamp(p["leave"])) for p in row["provenance"])
        row.update(first_join=intervals[0][0], last_leave=intervals[-1][1], visit_count=len(intervals),
                   duration_microseconds=sum(microseconds(b - a) for a, b in intervals),
                   status=classify_date(key[0], key[2], calendar, verified,
                       boundary=any(timestamp(p[k]).date() != key[2] for p in row["provenance"]
                                    for k in ("join", "leave", "report_start", "report_end") if p.get(k))))
    return [combined[k] for k in sorted(combined)], retained


def source_counts(rows):
    totals = Counter(r["status"] for r in rows)
    result = {status: totals[status] for status in LectureTiming.Status.values}
    result["pairs"] = len(rows)
    result["verified_dates"] = len({(r["course_source_id"], r["session_date"]) for r in rows if r["status"] == "verified"})
    result["modules"] = {str(cid): dict(Counter(r["status"] for r in rows if r["course_source_id"] == cid))
                         for cid in sorted(Course.objects.values_list("source_id", flat=True))}
    sources = Counter()
    for row in rows:
        if row["status"] != "verified":
            continue
        kinds = {p.get("source_kind", "teams_excel") for p in row["provenance"]}
        label = "mixed" if len(kinds) > 1 else next(iter(kinds))
        sources[label] += 1
    result["verified_pair_sources"] = dict(sources)
    return result


def import_graph_timing(source, policy_path, cache_dir, *, dry_run=False, client=None, progress=None):
    source = Path(source)
    require(source.is_file(), "Historical Teams fallback workbook is missing")
    fallback_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    fallback, policy = read_workbook(source), read_policy(policy_path)
    require(hashlib.sha256(source.read_bytes()).hexdigest() == fallback_sha, "Excel fallback changed while being read")
    # Validate the untouched fallback/identity policy before making any Graph calls.
    prepare_tables(fallback, policy, {"source_name": source.name, "source_sha256": fallback_sha})
    previous = TimingImport.objects.filter(active=True).first()
    snapshot = fetch_snapshot(client or GraphClient.from_environment(), policy, fallback, previous, progress=progress)
    payload = canonical_bytes(snapshot)
    graph_sha = hashlib.sha256(payload).hexdigest()
    cache = Path(cache_dir)
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = cache / f"graph-{graph_sha}.json"
    if target.exists():
        require(hashlib.sha256(target.read_bytes()).hexdigest() == graph_sha, "Cached Graph evidence has changed")
    else:
        # Write only private source evidence. No token or credential is in the snapshot.
        with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as fh:
            fh.write(payload)
    tables, selection = combine_tables(fallback, snapshot, fallback_sha, graph_sha)
    errors = Counter((e["status"], e["code"]) for m in snapshot["meetings"] for e in m["errors"])
    diagnostics = {"graph_reports": sum(len(m["reports"]) for m in snapshot["meetings"]),
                   "graph_listed_reports": sum(m["listed_reports"] for m in snapshot["meetings"]),
                   "graph_errors": [{"status": status, "code": code, "count": count} for (status, code), count in sorted(errors.items())],
                   "graph_snapshot_sha256": graph_sha, "excel_fallback_sha256": fallback_sha, **selection}
    with transaction.atomic():
        active = TimingImport.objects.select_for_update().filter(active=True).first()
        if not selection.get("accepted_graph_participant_records"):
            return {**(active.counts if active else {}), **diagnostics, "changed": False, "dry_run": dry_run,
                    "graph_status": "unavailable_existing_timing_preserved"}
        meta, rows, counts = prepare_tables(tables, policy,
            {"source_name": "Microsoft Graph v1.0 + historical Teams timing", "source_sha256": digest([graph_sha, fallback_sha])})
        rows, retained = retain_previous_graph(rows, active, policy)
        counts.update(source_counts(rows))
        counts.update(diagnostics)
        counts.update(primary_source="microsoft_graph", graph_status="available", cached_graph_records_retained=retained)
        # Retrieval timestamps and tokens are deliberately excluded from identity/fingerprints.
        meta["fingerprint"] = digest(["graph-timing-v1", {k: v for k, v in meta.items() if k != "fingerprint"}, rows])
        return activate_snapshot(meta, rows, counts, dry_run=dry_run)

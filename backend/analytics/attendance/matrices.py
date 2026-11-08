"""Authoritative owner-supplied attendance matrices. Never infer duration from activity columns."""
from __future__ import annotations

import datetime as dt
import hashlib
import re
from collections import Counter, defaultdict
from pathlib import Path

from .importer import AttendanceDataset, AttendanceValidationError, norm_email

# Explicit source mapping: worksheet names are misleading (Operations says 'PCP Alfnar').
SOURCES = {
    154268: ("Advanced Project and Logistics Management.xlsx", "Advanced Project"),
    135945: ("Andrew Operations and Information Management for Project Managers.xlsx", "PCP Alfnar"),
    83334: ("PCP Al-Fanar 2025.xlsx", "PCP Al-Fanar 2025"),
    121642: ("Risk Management.xlsx", "Risk Management"),
}


def header_date(value, companion=None):
    """These four templates use day/month text, but Excel auto-converted ambiguous headers as m/d.

    Companion '(act)' text states the intended date. Advanced has no companion dates: its
    auto-converted headers use the same reversed Excel convention, between explicit Sep/Oct dates.
    This rule is confined to these named matrix sources; interval timestamps remain untouched.
    """
    if isinstance(companion, str) and '(act)' in companion.lower() and (match := re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})", companion.strip())):
        d, m, y = map(int, match.groups())
        return dt.date(y, m, d)
    if isinstance(value, (dt.date, dt.datetime)):
        return dt.date(value.year, value.day, value.month)
    if isinstance(value, str) and re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", value.strip()):
        return dt.datetime.strptime(value.strip(), "%d/%m/%Y").date()
    return None


def read_matrices(directory: Path, by_email, aliases, courses, enrollments):
    import openpyxl
    ds = AttendanceDataset()
    diagnostics = {}
    for cid, (filename, sheet_name) in SOURCES.items():
        path = directory / filename
        if not path.is_file() or cid not in courses:
            raise AttendanceValidationError([f"Required authoritative source or LMS module missing: {filename} ({cid})"])
        source_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        with path.open('rb') as fh:
            wb = openpyxl.load_workbook(fh, read_only=True, data_only=True)
            try:
                sheets = {s.title.strip(): list(s.values) for s in wb}
            finally:
                wb.close()
        if hashlib.sha256(path.read_bytes()).hexdigest() != source_sha256:
            raise AttendanceValidationError([f"Attendance source changed while being read: {filename}"])
        rows = sheets.get(sheet_name)
        if not rows or len(rows) < 3 or rows[0][:2] != ('Name', 'Email'):
            raise AttendanceValidationError([f"Invalid attendance matrix: {filename}"])
        mid = f"matrix:{cid}"
        ds.meetings[mid] = {"subject": courses[cid], "course_source_id": cid, "mapping_status": "approved_rule",
                            "mapping_note": "Owner-supplied authoritative module workbook (2026-11-05)",
                            "evidence_source": "attendance_matrix", "source_file": filename}
        columns = []
        for col, value in enumerate(rows[0]):
            if str(rows[1][col] or '').strip().lower() != 'attendance':
                continue
            day = header_date(value, rows[0][col + 1] if col + 1 < len(rows[0]) else None)
            if day:
                columns.append((col, day))
        if not columns or len({d for _, d in columns}) != len(columns):
            raise AttendanceValidationError([f"No dated columns or duplicate dates in {filename}"])
        days = {d for _, d in columns}
        counts = Counter()
        unmatched, ineligible = set(), set()
        cells = defaultdict(list)

        def observe(email, day, value):
            email = norm_email(email)
            if not email or value in (None, ''):
                counts['missing_cells'] += 1
                return
            if value not in (0, 1, 0.0, 1.0):
                raise AttendanceValidationError([f"Non-binary attendance flag in {filename}, {day}"])
            lid = by_email.get(email, aliases.get(email))
            if lid is None:
                unmatched.add(email)
                return
            if (lid, cid) not in enrollments:
                ineligible.add(lid)
            cells[(day, lid)].append(bool(value))
            counts['matched_source_cells'] += 1

        for row in rows[2:]:
            if not row[1]:
                continue
            counts['registry_rows'] += 1
            for col, day in columns:
                observe(row[1], day, row[col])
        # Supplementary presence rows are evidence only with an exact registered email.
        # guessedMatch / similarityScore are deliberately never used.
        for name, extra in sheets.items():
            if 'unregistered' not in name.lower() or not extra:
                continue
            hdr = [str(x or '').strip() for x in extra[0]]
            if not all(x in hdr for x in ('present', 'Date', 'AttendanceEmail')):
                raise AttendanceValidationError([f"Invalid supplemental sheet in {filename}"])
            for row in extra[1:]:
                if not any(x is not None for x in row):
                    continue
                day = row[hdr.index('Date')]
                if isinstance(day, dt.datetime):
                    day = day.date()  # supplemental Date values are actual dates, not header auto-conversions
                elif isinstance(day, str):
                    day = dt.datetime.strptime(day, '%d/%m/%Y').date()
                if day not in days:
                    raise AttendanceValidationError([f"Supplemental attendance date has no matching column in {filename}: {day}"])
                observe(row[hdr.index('AttendanceEmail')], day, row[hdr.index('present')])
        for (day, lid), flags in cells.items():
            if len(set(flags)) > 1:
                counts['conflicting_flags_positive_evidence_kept'] += 1
            counts['duplicate_cells_collapsed'] += len(flags) - 1
            ds.attendance[(mid, day, lid)] = {
                'presence_seconds': None, 'interval_count': None, 'first_join': None, 'last_leave': None,
                'attended': any(flags), 'match_method': 'email' if lid in by_email.values() else 'alias',
            }
        positive = Counter(day for (m, day, _), a in ds.attendance.items() if m == mid and a['attended'])
        for day in sorted(days):
            ds.sessions[(mid, day)] = {
                'starts_at': None, 'ends_at': None, 'report_count': 1, 'verified_learners': positive[day],
                'delivered': positive[day] > 0,
                'exclusion_reason': '' if positive[day] else 'No verified positive attendance evidence',
            }
        diagnostics[str(cid)] = {'source_file': filename, 'source_sha256': source_sha256,
                                 'worksheets': list(sheets), 'dated_sessions': len(days),
                                 'session_dates': [str(d) for d in sorted(days)], **counts,
                                 'unmatched_emails': len(unmatched), 'matched_learners_not_enrolled': len(ineligible),
                                 'matched_learners': len({lid for _, lid in cells})}
        if unmatched:
            ds.warnings.append(f"Module {cid}: {len(unmatched)} source emails have no verified LMS identity; not attributed")
        if counts['conflicting_flags_positive_evidence_kept']:
            ds.warnings.append(f"Module {cid}: conflicting attendance flags reconciled using positive evidence; raw files retained")
    days = sorted(day for _, day in ds.sessions)
    ds.coverage = (days[0], days[-1])
    ds.diagnostics = {'sources': diagnostics, 'lecture_occurrences': len(ds.sessions),
                      'delivered_sessions': sum(s['delivered'] for s in ds.sessions.values()),
                      'matched_learners': len({l for _, _, l in ds.attendance}),
                      'evidence_source': 'attendance_matrix', 'duration_available': False,
                      'attendance_rule': 'Explicit 1 = present; 0 = recorded absent; blank = Not Available. Positive evidence wins overlapping aliases. No duration threshold can be verified.'}
    ds.warnings.append('Matrices contain binary attendance, not Teams durations. Missing cells are excluded from rates; no live hours can be verified.')
    return ds

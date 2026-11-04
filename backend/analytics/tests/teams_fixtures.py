"""Synthetic (fictional) Microsoft Teams attendance workbook mirroring the real export layout."""

from pathlib import Path

import openpyxl

ATT_HDR = ["Display name", "Email", "Total attendance (seconds)", "Role", "Report start (UTC)", "Meeting subject",
           "Report ID", "Attendance record ID", "Identity ID", "Tenant ID", "Identity type"]
JL_HDR = ["Display name", "Email", "Join time (UTC)", "Leave time (UTC)", "Duration (seconds)", "Report start (UTC)",
          "Meeting subject", "Report ID", "Attendance record ID"]
REP_HDR = ["Meeting subject", "Meeting start (UTC)", "Meeting end (UTC)", "Total participant count", "Report ID",
           "External event information", "Meeting ID", "Meeting join URL", "Organizer ID"]

SUBJ_A = "Synthetic Module A"      # equals LMS course 9001 title -> exact mapping
SUBJ_X = "Unmapped Workshop"       # no title match, no approved rule -> unmapped

REPORTS = [
    # subject, start, end, report id, meeting id
    (SUBJ_A, "2026-01-05T09:00:00.000Z", "2026-01-05T11:00:00.000Z", "R1", "MEET-A"),
    (SUBJ_A, "2026-01-05T11:30:00.000Z", "2026-01-05T11:40:00.000Z", "R1b", "MEET-A"),  # same day -> same lecture
    (SUBJ_A, "2026-01-07T09:00:00.000Z", "2026-01-07T09:01:00.000Z", "R2", "MEET-A"),   # link-open only
    (SUBJ_X, "2026-01-06T09:00:00.000Z", "2026-01-06T10:00:00.000Z", "R3", "MEET-X"),
]

# report, record, display name, email, role, [(join, leave)]
PARTICIPANTS = [
    ("R1", "rec-101-a", "Test Learner One", "LEARNER101@example.invalid", "Presenter",   # case-insensitive email
     [("2026-01-05T09:00:00.0000000Z", "2026-01-05T09:30:00.0000000Z")]),
    ("R1", "rec-101-b", "Test Learner One", "learner101@example.invalid", "Presenter",   # 2nd device, overlaps
     [("2026-01-05T09:20:00.0000000Z", "2026-01-05T09:40:00.0000000Z")]),
    ("R1", "rec-102", "Test Learner Two", "learner102@example.invalid", "Presenter",
     [("2026-01-05T09:00:00.0000000Z", "2026-01-05T09:30:00.0000000Z"),
      ("2026-01-05T09:00:00.0000000Z", "2026-01-05T09:30:00.0000000Z")]),               # exact duplicate row
    ("R1", "rec-org", "Trainer Organiser", "organiser@kentbusinesscollege.com", "Organizer",
     [("2026-01-05T08:55:00.0000000Z", "2026-01-05T11:00:00.0000000Z")]),
    ("R1", "rec-staff", "Co Trainer", "cotrainer@kentbusinesscollege.com", "Presenter",
     [("2026-01-05T09:00:00.0000000Z", "2026-01-05T10:00:00.0000000Z")]),
    ("R1", "rec-ext", "Guest Speaker", "guest@other.org", "Presenter",
     [("2026-01-05T09:00:00.0000000Z", "2026-01-05T09:45:00.0000000Z")]),
    ("R1", "rec-anon", "Anonymous", None, "Presenter",
     [("2026-01-05T09:00:00.0000000Z", "2026-01-05T09:45:00.0000000Z")]),
    # Display name equals a registry learner but the email is unknown -> name-only, must NOT match
    ("R1", "rec-alt", "Test Learner Two", "l2-alt@example.invalid", "Presenter",
     [("2026-01-05T10:00:00.0000000Z", "2026-01-05T10:20:00.0000000Z")]),
    ("R1b", "rec-101-c", "Test Learner One", "learner101@example.invalid", "Presenter",
     [("2026-01-05T11:30:00.0000000Z", "2026-01-05T11:35:00.0000000Z")]),
    ("R2", "rec-101-d", "Test Learner One", "learner101@example.invalid", "Presenter",
     [("2026-01-07T09:00:00.0000000Z", "2026-01-07T09:01:00.0000000Z")]),
    ("R3", "rec-103", "Test Learner Three", "learner103@example.invalid", "Presenter",
     [("2026-01-06T09:00:00.0000000Z", "2026-01-06T10:00:00.0000000Z")]),
]


def write_teams_workbook(path: Path, reports=REPORTS, participants=PARTICIPANTS, drop_sheet=None) -> Path:
    from analytics.attendance.importer import parse_ts

    start_of = {r[3]: r[1] for r in reports}
    subj_of = {r[3]: r[0] for r in reports}
    att_rows, jl_rows = [], []
    for rid, rec, name, email, role, ivs in participants:
        total = sum(int((parse_ts(b) - parse_ts(a)).total_seconds()) for a, b in ivs)
        att_rows.append([name, email, total, role, start_of[rid], subj_of[rid], rid, rec, rec, "tenant", "#identity"])
        for a, b in ivs:
            jl_rows.append([name, email, a, b, int((parse_ts(b) - parse_ts(a)).total_seconds()), start_of[rid],
                            subj_of[rid], rid, rec])
    sheets = {
        "Teams attendance": [["Microsoft Teams attendance"], ["Original records"], ATT_HDR, *att_rows],
        "Join and leave": [["Microsoft Teams join and leave"], ["One row per interval"], JL_HDR, *jl_rows],
        "Teams reports": [["Microsoft Teams report metadata"], ["Report summaries"], REP_HDR,
                          *[[s, a, b, 1, rid, "[]", mid, "https://example.invalid/join", "org"] for s, a, b, rid, mid in reports]],
    }
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for title, rows in sheets.items():
        if title == drop_sheet:
            continue
        ws = wb.create_sheet(title)
        for r in rows:
            ws.append(r)
    wb.save(path)
    return path

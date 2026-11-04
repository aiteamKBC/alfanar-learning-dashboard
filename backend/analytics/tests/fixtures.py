"""Synthetic (fully fictional) workbook builder mirroring the real export's structure."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import openpyxl

STUDENT_HDR = ["User ID", "Name", "Email", "Course", "Course ID", "Course Category", "Tutor ID", "Tutor Name",
               "Tutor Email", "Progress (%)", "Quizzes Completed", "Quiz Time Seconds", "Quiz Time",
               "Videos Completed", "Video Time Seconds", "Video Time", "Text Lessons Completed",
               "Text Time Seconds", "Text Time", "PDFs Completed", "PDF Time Seconds", "PDF Time",
               "Materials Completed", "Material Time Seconds", "Material Time", "Total Time Seconds",
               "Total Time", "Submissions Count", "Last Login", "Registered At", "Evidence Count",
               "Evidence Links", "Certificate Link", "Other Al Fanar Modules"]
QUIZ_HDR = ["User ID", "Name", "Email", "Course", "Course ID", "Progress (%)", "Quiz ID", "Quiz Title",
            "Questions Count", "Attempts Count", "Best Progress (%)", "Actual Time Complete"]
ATTEMPT_HDR = ["User ID", "Name", "Email", "Course", "Course ID", "Quiz ID", "Quiz Title", "Attempt #",
               "Attempt ID", "Progress (%)", "Status", "Date", "Created At"]
LESSON_HDR = ["User ID", "Name", "Email", "Course", "Course ID", "Category", "Lesson ID", "Lesson Title",
              "Raw LMS Type", "Classification", "Records", "All Durations Available", "Recorded Seconds",
              "Recorded Time", "Session #", "Session Record ID", "Progress (raw)", "Start Time", "End Time",
              "Session Seconds", "Session Time", "Time Source"]
ASSIGN_HDR = ["User ID", "Name", "Email", "Course", "Course ID", "Assignment ID", "Assignment Title", "Submission Count"]
SUB_HDR = ["User ID", "Name", "Email", "Course", "Course ID", "Assignment ID", "Assignment Title", "Submission #",
           "Submission ID", "Status", "Grade", "Created At", "Updated At", "Uploaded Post ID", "Course Match"]

COURSES = {9001: "Synthetic Module A", 9002: "Synthetic Module B"}
LEARNERS = {101: "Test Learner One", 102: "Test Learner Two", 103: "Test Learner Three"}


def _student(uid, cid, progress, submissions=0):
    row = dict.fromkeys(STUDENT_HDR)
    row.update({"User ID": float(uid), "Name": LEARNERS[uid], "Email": f"learner{uid}@example.invalid",
                "Course": COURSES[cid], "Course ID": float(cid), "Course Category": "Synthetic",
                "Tutor ID": 1.0, "Tutor Name": "Tutor", "Progress (%)": progress,
                "Submissions Count": float(submissions),
                "Registered At": dt.datetime(2025, 10, 1, 9, 0, 0), "Evidence Count": 0.0})
    return [row[h] for h in STUDENT_HDR]


def _lesson(uid, cid, cat, lid, title, seconds, start, timed=True):
    raw = {"Video": "video", "PDF": "pdf", "Audio": "audio", "Reading": "text"}[cat]
    r = dict.fromkeys(LESSON_HDR)
    r.update({"User ID": uid, "Name": LEARNERS[uid], "Email": "x", "Course": COURSES[cid], "Course ID": cid,
              "Category": cat, "Lesson ID": lid, "Lesson Title": title, "Raw LMS Type": raw,
              "Classification": "confirmed" if raw == "text" else "not_reading", "Records": 1,
              "All Durations Available": "Yes" if timed else "No",
              "Recorded Seconds": seconds if timed else 0, "Session #": 1, "Start Time": start,
              "End Time": start + (seconds if timed else 0),
              "Session Seconds": seconds if timed else None,
              "Time Source": "start_end_difference" if timed else "unavailable"})
    return [r[h] for h in LESSON_HDR]


def _quiz(uid, cid, qid, title, attempts, best, questions=10):
    r = dict.fromkeys(QUIZ_HDR)
    r.update({"User ID": uid, "Name": LEARNERS[uid], "Email": "x", "Course": COURSES[cid], "Course ID": cid,
              "Quiz ID": qid, "Quiz Title": title, "Questions Count": questions, "Attempts Count": attempts,
              "Best Progress (%)": best, "Actual Time Complete": "No"})
    return [r[h] for h in QUIZ_HDR]


def _attempt(uid, cid, qid, n, score, status, created):
    r = dict.fromkeys(ATTEMPT_HDR)
    r.update({"User ID": uid, "Name": LEARNERS[uid], "Email": "x", "Course": COURSES[cid], "Course ID": cid,
              "Quiz ID": qid, "Quiz Title": f"Quiz {qid}", "Attempt #": n, "Progress (%)": score,
              "Status": status, "Created At": created})
    return [r[h] for h in ATTEMPT_HDR]


def build_tables(overrides: dict | None = None) -> dict[str, list[list]]:
    """Return sheet tables. Expected totals are documented in tests."""
    students = [STUDENT_HDR,
                _student(101, 9001, 50.0, submissions=0),  # reported count deliberately wrong (real-data quirk)
                _student(101, 9002, 0.0),
                _student(102, 9001, 100.0, submissions=1),
                _student(103, 9002, None),                  # missing progress -> None, not 0
                [None] * len(STUDENT_HDR),                   # blank row
                _student(102, 9001, 100.0, submissions=1)]   # duplicate enrollment row
    t0 = 1_790_000_000
    lessons = [LESSON_HDR,
               _lesson(101, 9001, "Video", 501, "V1 &#8211; Intro", 120, t0),
               _lesson(101, 9001, "PDF", 502, "P1 Slides", 30, t0 + 200),
               _lesson(101, 9001, "Audio", 503, "Podcast 1", 0, t0 + 300, timed=False),
               _lesson(102, 9001, "Video", 501, "V1 &#8211; Intro", 60, t0 + 400),
               _lesson(102, 9001, "Reading", 504, "Reading 1", 45, t0 + 500),
               _lesson(102, 9001, "Reading", 504, "Reading 1", 45, t0 + 500)]  # duplicate row
    quizzes = [QUIZ_HDR,
               _quiz(101, 9001, 701, "Quiz 701", 2, 90.0),
               _quiz(102, 9001, 701, "Quiz 701", 1, 70.0),
               _quiz(102, 9001, 702, "Quiz 702", 1, 100.0, questions=5)]
    attempts = [ATTEMPT_HDR,
                _attempt(101, 9001, 701, 1, 90.0, "passed", "2026-10-01 10:00:00"),
                _attempt(101, 9001, 701, 2, 60.0, "failed", "2026-10-01 11:00:00"),  # latest is lower than best
                _attempt(102, 9001, 701, 1, 70.0, "passed", "2026-10-02 10:00:00"),
                _attempt(102, 9001, 702, 1, 100.0, "passed", "2026-10-03 10:00:00")]
    assignments = [ASSIGN_HDR,
                   [101, "x", "x", COURSES[9001], 9001, 801, "Assignment A", 1],
                   [102, "x", "x", COURSES[9001], 9001, 801, "Assignment A", 1]]

    def sub(uid, sid, status, grade):
        r = dict.fromkeys(SUB_HDR)
        r.update({"User ID": uid, "Name": "x", "Email": "x", "Course": COURSES[9001], "Course ID": 9001,
                  "Assignment ID": 801, "Assignment Title": "Assignment A", "Submission #": 1,
                  "Submission ID": sid, "Status": status, "Grade": grade, "Updated At": 1_780_000_000})
        return [r[h] for h in SUB_HDR]

    submissions = [SUB_HDR, sub(101, 9901, "draft", None), sub(102, 9902, "passed", 88.0)]
    tables = {
        "Al Fanar Students": students,
        "Al Fanar Quizzes": quizzes,
        "Al Fanar Quiz Attempts": attempts,
        "Al Fanar Lesson Details": lessons,
        "Al Fanar Assignments": assignments,
        "Al Fanar Submissions": submissions,
        "Al Fanar Other Lessons": [LESSON_HDR],
    }
    tables.update(overrides or {})
    return tables


def write_workbook(path: Path, tables: dict[str, list[list]] | None = None) -> Path:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for title, rows in (tables or build_tables()).items():
        ws = wb.create_sheet(title)
        for r in rows:
            ws.append(r)
    wb.save(path)
    return path

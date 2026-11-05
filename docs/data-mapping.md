# Data mapping: Alfanar Endpoint workbook

Source: `data/Alfanar Endpoint.xlsx`. It is read-only and never committed. It has 15 worksheets, all inspected by the importer (`backend/analytics/importer/normalize.py`). Figures are from the import on 2026-11-04.

## Worksheets

| Worksheet | Rows | Grain | Used for |
|---|---|---|---|
| Al Fanar Students | 123 (+41 blank) | learner × course | **Master** for learners, courses and enrollments, plus LMS `Progress (%)` |
| Al Fanar Quizzes | 1,322 | learner × quiz | Quiz items, attempts count, best score |
| Al Fanar Quiz Attempts | 1,446 | learner × quiz × attempt | Attempt history, latest attempt, pass status |
| Al Fanar Lesson Details | 4,585 | learner × lesson | **Master** for lesson completions (video/pdf/audio/reading) |
| Al Fanar Videos / PDFs / Audio / Readings | 1,850 / 1,697 / 406 / 632 | learner × lesson | Cross-check only (subsets of Lesson Details) |
| Al Fanar All Activities | 5,955 | union | Cross-check only: equals Lesson Details + Quizzes + Submissions |
| Al Fanar Assignments | 48 | learner × assignment | Assignment items |
| Al Fanar Submissions | 48 | submission | Submission status and grade |
| Al Fanar Reading Sessions | 632 | learner × reading | Not loaded (duplicates Readings timing) |
| Al Fanar Coverage / Diagnostics | 123 | learner × course | Not loaded (derived export diagnostics) |
| Al Fanar Other Lessons | 0 | n/a | Empty |

The union sheets are never loaded, so nothing is double-counted. The importer checks on every run that the per-type sheets and All Activities reconcile with the loaded totals. All of them reconcile exactly.

## Identifiers and relationships

- `User ID` → Learner (44 unique in the export; 42 after excluding 2 non-learner accounts, see below). `Course ID` → Course (4). A learner in several modules is one Learner with several Enrollments (121 after the exclusion).
- **Non-learner accounts:** User IDs listed in `backend/analytics/importer/exclusions.py` (a staff/manager account and a training-provider account, confirmed by the project owner) are skipped in every sheet during normalization. They never become learners, enrollments or learner activity, and so are also absent from the Teams learner registry. The source is never modified.
- Quiz IDs (120), Lesson IDs (356) and Assignment IDs (4) are unique per course. An Activity's key is `(course, kind, source_id)`.
- Every activity row matches an enrollment; there are 0 orphan rows.
- No exact duplicate rows exist. The importer still de-duplicates on natural keys and logs a warning for any duplicate it skips.

## Semantics and decisions

| Topic | Decision |
|---|---|
| Course progress | `Students.Progress (%)` is shown as the LMS-reported figure. It is not a qualification outcome. |
| Lesson completion | Each Lesson Details row counts as one recorded completion. This matches `Students.Videos/PDFs/Text Lessons Completed` exactly. |
| Quiz "completed" | At least one attempt. This matches `Students.Quizzes Completed`. |
| Latest quiz score | The score on the highest `Attempt #`. Attempt # is the LMS sequence; in 9 cases `Created At` ordering disagrees. Best score is kept separately. |
| Pass | The source attempt `Status == passed`. Pass marks vary per quiz (67% passed in one quiz while 90% failed in another), so no threshold is applied. |
| Submissions | The Submissions sheet is the source of truth. `Students.Submissions Count` disagrees for 40 enrollments. "Submitted" excludes `draft`. |
| Assignment grade | `Submissions.Grade` is an assignment grade, not a module grade. Blank means not graded. |
| Durations | Used only when `Time Source = start_end_difference`. Otherwise the value is null, never 0. Recorded spans are open-to-close times with extreme outliers (median 6–30 s; sessions over 4 h make up more than 80% of the total), so time is **not** used as an executive KPI. |
| Timestamps | The export states no timezone, so all times are treated as UTC. Lesson start/end values are Unix epochs. |
| Missing values | Blank stays null and is shown as "—", "No record" or "No attempts". It is never treated as 0 or as a failure. |

## What cannot be calculated reliably

- **Scheduled sessions / attendance.** `Session #` is always 1 and `Session Record ID` is empty. There is no attendance field. The matrix is therefore activity-based, and attendance is shown as *Not available*.
- **Official curriculum size.** The export only contains items at least one learner interacted with. "Known activities" (the completion denominators) are the items seen in the export for each module, so they are a lower bound. `Coverage Note` confirms: "Only recorded student activities".
- **Quiz time.** `Actual Time` is blank, and the quiz times are fixed estimates (3 min per question), so they are not used.
- **Certificates / formal completion.** The certificate links are not loaded or shown.
- **Source freshness.** The workbook has no internal modified property, so the file's mtime is used and labelled as such. For Google Sheets, the latest activity timestamp is shown, because the spreadsheet modified time would need a Drive scope.

## Data-quality notes (flagged by importer, not modified)

- 40 enrollments have a `Submissions Count` that differs from the actual submission rows.
- User IDs 743, 779 and 1229 normalize to the same identity. They look like duplicate LMS accounts but are kept separate.
- Many `Name` values are username-style strings (for example `first-lastalfanar-com`), exactly as they appear in the source.

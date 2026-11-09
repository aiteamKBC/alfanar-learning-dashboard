# Learner Directory metrics and module downloads

The directory and its authenticated download endpoint share `metrics.learner_directory`.
They read the current stored LMS projection and approved timing evidence. No source
sync, database migration, new dependency, or Google Sheets write is involved. Existing
owner merges, unmerges, archives and restores determine the active identities and
enrollments. No fixed learner-count assumption is used.

## Definitions

| Column | Definition |
|---|---|
| Average Quiz Score | Arithmetic mean of each learner/quiz's latest valid score, from the existing `QuizResult` projection. Retains source attempt ordering and approved merged-account history. Earlier retries and best scores are not averaged. No valid scores means N/A. |
| Quizzes attempted | Distinct learner/quiz results, counted once per quiz; denominator is distinct quizzes known from the LMS export for the selected module. Percentage is attempted / known × 100, rounded once to one decimal. |
| Materials completed | Existing recorded lesson completions for videos, PDFs, audio and readings. Denominator is the corresponding distinct known lesson catalogue. Excludes quizzes and assignments. Percentage is completed / known × 100. |
| Assignments | Distinct learner/assignment pairs with a non-draft submission / known assignments; repeated submissions do not inflate the numerator. |
| Attendance evidence (%) | Existing effective Present observations / known attendance observations × 100. This is the same percentage already displayed in the directory; it is not timing coverage or attendance across unknown sessions. |
| Total attendance hours | Sum of the selected module's displayed verified session durations, using the existing merged non-overlapping intervals and identity projection. Same total as Executive Overview and the learner Live Sessions footer. Does not use the elapsed first-join/last-leave span or the LMS timer. Missing, held, off-calendar and ambiguous timing is excluded; no verified durations means N/A. Verified zero remains zero, and a positive duration under a minute displays `<1m`. |
| LMS progress | Existing LMS progress for the selected module. All Modules retains the mean of non-missing enrollment progress values. |

Known-item totals are the observed LMS catalogue, not an invented full curriculum.
Unknown/zero denominators display N/A. A known denominator with no recorded completions
shows zero completions. All Modules sums distinct learner/course/item pairs across the
learner's enrollments, weights quiz scores by valid quiz results (not module means),
and sums verified module duration totals. Other modules never enter a selected-module row.

## Export behavior

The Export dialog selects Planning, Procurement Risk, Operations and/or Advanced in
the approved module order. It defaults to the directory's selected module, or all four
when All Modules is selected. Select All and Clear Selection are available.

- Excel downloads one `.xlsx` workbook with one sheet per selected module. Short,
  readable sheet titles fit Excel's 31-character limit; each row contains the full
  approved module display name and the stable LMS learner ID.
- CSV is available for exactly one module, encoded as UTF-8 with BOM.
- Exports include all currently enrolled learners in the selected modules, independent
  of directory search, status filters, sorting and pagination. Empty modules retain
  their sheet and column headings.
- The 15 stable columns are learner ID, learner name, module name, LMS progress,
  attendance evidence percentage, quizzes attempted, available quizzes, quiz completion
  percentage, average quiz score, materials completed, applicable materials, material
  completion percentage, assignments submitted, available assignments, verified duration.
- Counts, IDs and percentage values are numeric where known. Percentage values are on
  the 0–100 scale with an Excel percent-label format. Assignment counts are separate
  numeric columns. Missing values are explicit N/A.
- Excel durations are numeric day fractions with an elapsed-hours/minutes format, so
  totals over 24 hours stay accurate and exact seconds are retained. CSV durations use
  the same whole-minute labels as the UI. A short verified visit is displayed `<1m`.
- Excel string cells are explicitly literal strings, never formulas. CSV prefixes
  potentially executable string fields with an apostrophe, including leading controls
  and whitespace before formula characters. Numeric columns remain numeric.

`GET /api/learners/export?courses=83334,121642&file_type=xlsx` uses the existing session
and dashboard authorization. The server validates the module IDs and format, rejects
multi-module CSV, reads all sheets in a single database transaction, and returns a
private, non-cacheable attachment. It never stores generated files on the server or
reuses a browser's stale table data. Downloads contain learner data: keep them private.

## Validation and deployment

Focused tests cover matrix/overview/footer consistency, module scoping, retries,
known/missing denominators, native Excel cells, CSV, formula injection, authentication,
empty modules, identity changes, overlap unions, missing/held timing and fresh data.
Real-browser verification uses an isolated copy of the local data and the production
frontend build; private screenshots and downloaded workbooks stay outside Git.

For an already installed server, pin the commit supplied in the release report, build
the frontend, and restart only Gunicorn. Use the existing maintenance/rollback process
and a clean checkout. Keep any existing private backup and previous frontend build.
No identity restoration, attendance import, migration, dependency installation or
Teams/Sheets synchronization is required for this UI release.

```bash
set -euo pipefail
cd /var/www/alfanar-learning-dashboard
test -z "$(git status --porcelain)"
previous=$(git rev-parse HEAD)
git fetch origin main
git switch --detach RELEASE_COMMIT_FROM_REPORT
cd frontend
npm run build
sudo systemctl restart alfanar-gunicorn
sudo systemctl is-active alfanar-gunicorn
```

Verify a selected module's directory, CSV and Excel download using the existing login.
If validation fails, return to the saved `$previous` commit, rebuild that frontend and
restart Gunicorn; this release changes no database data or schema. Production commands
are supplied for the operator and are not run during local release preparation.

# Attendance reconciliation (2026-11-05)

The four owner-supplied module workbooks in `ATTENDANCE_DIRECTORY` (default `data/`) are authoritative. The old `PCP_Al_Fanar_Teams_Original_Data 1.xlsx` is comparison-only; its dates and intervals are never combined with the new matrices. Google Sheets remains the read-only primary LMS source.

| Workbook | Worksheet | LMS course ID | Dated sessions | Lectures displayed |
|---|---|---:|---:|---:|
| Advanced Project and Logistics Management.xlsx | Advanced Project | 154268 | 9 | **12 owner confirmed** |
| Andrew Operations and Information Management for Project Managers.xlsx | PCP Alfnar | 135945 | 14 | **16 owner confirmed** |
| PCP Al-Fanar 2025.xlsx | PCP Al-Fanar 2025 | 83334 | 16 | **16 owner confirmed** |
| Risk Management.xlsx | Risk Management | 121642 | 12 | **14 owner confirmed** |

Operations' worksheet name is misleading; explicit workbook mapping takes precedence. All worksheets, including both supplementary `Unregistered` sheets, are inspected. PCP profile fields, LMS progress, overall attendance columns and fractional `Activity` cells are not session attendance evidence.

All four delivered lecture counts are officially owner-confirmed. The display order remains Planning (83334), Procurement Risk (121642), Operations (135945), Advanced (154268). The confirmed counts are **16, 14, 16, 12**; the separate attendance evidence session counts remain **16, 12, 14, 9**. Confirmations do not add sessions or change attendance numerators, denominators, rates or coverage. Differences remain explained in information tooltips without restoring the removed Overview attendance descriptions.

## Attendance and coverage

All matrices use the same rule: explicit **1 = recorded present**, **0 = recorded absent**, **blank = Not Available**. A date with positive verified presence supplies attendance evidence for a delivered session. The files contain no join/leave intervals or durations, so they cannot verify a ten-minute threshold or live hours. Duration values remain unavailable.

Attendance rate = present learner-sessions / learner-sessions with explicit binary evidence, restricted to current LMS enrollments and verified source email identities. Missing cells, unidentified learners and unlisted delivered lectures are excluded from both numerator and denominator. **Advanced's 100% describes its positive recorded cells, not complete cohort attendance**: its workbook has no explicit absence cells. Evidence coverage appears beside the rate.

Coverage = known eligible learner-session cells / (eligible enrollments × dated source sessions). It measures source completeness. Advanced's three additional confirmed lectures have no attendance columns and are not added to the measurable denominator.

| Module | Present / known | Attendance | Known / possible cells | Coverage | Enrollments with cells / enrolled |
|---|---:|---:|---:|---:|---:|
| Advanced | 128 / 128 | 100.0% | 128 / 225 | 56.9% | 17 / 25 |
| Operations | 211 / 282 | 74.8% | 282 / 364 | 77.5% | 21 / 26 |
| PCP | 308 / 336 | 91.7% | 336 / 416 | 80.8% | 21 / 26 |
| Risk | 247 / 286 | 86.4% | 286 / 324 | 88.3% | 26 / 27 |
| Total | 894 / 1,032 | 86.6% | 1,032 / 1,329 | 77.7% | 85 / 104 |

Counts reflect the local live Google sync and workbooks on 2026-11-05. Refresh the audit when sources change.

## Dates

The templates mix day/month/year text with Excel headers auto-converted using month/day interpretation. Companion `(act)` text explicitly supplies the intended day/month/year date and takes precedence. Advanced has no companion date text; its ambiguous Excel headers use the same reversed template convention, producing a chronological September–November sequence. This is a source-format interpretation, not independently confirmed scheduling evidence. Supplementary `Unregistered.Date` cells are actual event dates and are not reversed.

Advanced dates: 16 and 21 September; 5, 7, 12, 21, 26 and 28 October; 2 November 2026. The three missing lecture dates are not inferred. Review original headers if exact scheduling dates are challenged.

## Identities and overlaps

`LearnerIdentity` retains all allowed source LMS emails under canonical IDs, including prior verified duplicates and owner-approved groups. Matching uses trimmed lowercase exact emails or existing verified aliases. Emails shared by unrelated canonical IDs are ambiguous and never matched. Names, `guessedMatch` and similarity scores never authorize a match.

Evidence for a canonical learner/module/date counts once. Operations has 35 overlapping cells and nine conflicting flags. Positive evidence wins an explicit zero; diagnostics record conflicts and unchanged raw workbooks retain every source value. Evidence outside an LMS enrollment is stored but excluded from learner statistics. Excluded accounts, staff and unverified external addresses contribute no learner attendance.

Three Advanced source emails and two each in Operations and PCP remain unmatched; Risk has none. One ambiguous address has two plausible LMS accounts, so no new merge or guessed alias is introduced. Operations supplementary rows also contain excluded accounts, staff, external participants and note-taking bots, which do not contribute learner evidence.

## Safe refresh

```powershell
.\.venv\Scripts\python backend/manage.py sync_alfanar --source google
.\.venv\Scripts\python backend/manage.py import_teams_attendance
.\.venv\Scripts\python backend/manage.py audit_dashboard
```

All four files are required. Validation completes before one atomic database replacement; any invalid or missing file preserves existing attendance. Repeated unchanged imports perform zero domain-table writes (run-log rows still record execution). Reimport after registry or identity-rule changes. Original workbooks are read-only and never saved.

The legacy interval parser remains explicitly available with `--path file.xlsx` for diagnostics. Its separate rule is ten merged minutes for attendance and five verified learners per delivered occurrence. **Do not use the old export on the presentation database**: explicit legacy import replaces the current attendance dataset.

`audit_dashboard` reads raw LMS rows and Excel cells independently of both normalizers, compares KPIs, module charts, directory/profiles, activity matrices and every attendance cell, and verifies retained academic evidence. Output defaults to ignored `data/reconciliation.json`. Never publish the data folder.

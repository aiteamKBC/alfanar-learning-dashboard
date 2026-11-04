# Microsoft Teams attendance

Source: `data/PCP_Al_Fanar_Teams_Original_Data 1.xlsx`. It is a raw, unfiltered Teams export that is read-only and git-ignored. It is separate from the LMS data, and the LMS/Google sync never reads, deletes or overwrites attendance.

## Workbook structure (verified)

| Worksheet | Rows | Grain | Key fields |
|---|---|---|---|
| Teams attendance | 1,233 | participant record per report | Display name, Email, Total attendance (s), Role (`Organizer`/`Presenter`), Report ID, Attendance record ID, Identity/Tenant ID |
| Join and leave | 1,578 | one join→leave interval | Email, Join/Leave time (UTC, 7-digit fractions), Duration (s), Report ID, Attendance record ID |
| Teams reports | 147 | one Teams attendance report | Meeting subject, Meeting start/end (UTC), Report ID, **Meeting ID**, Organizer ID |

Each sheet has two title rows above its header. Per-record totals equal the sum of their join/leave durations in all 1,233 rows. There are 0 exact duplicate rows.

**Role does not identify learners.** Learners appear as `Presenter`, and trainers appear as `Organizer` or `Presenter`. The export has no module field; the meeting subject is the only module signal.

## Lecture occurrences

- 3 meeting series (Meeting ID), 147 reports and 52 meeting-days.
- One series can produce many reports per day (up to 16), so a **report is not a lecture**.
- **Lecture occurrence** = one Meeting ID on one UTC date. All of that day's reports are merged into it.
- **Delivered session** = an occurrence that at least `TEAMS_MIN_LEARNERS_FOR_DELIVERED` (5) verified learners attended for at least `TEAMS_MIN_ATTENDANCE_MINUTES` (10). This excludes link-opens and test calls.
  - Real lecture days in this export: 14 to 27 learners, 67 to 400 minutes.
  - Excluded days: 8 learners or fewer, and no more than 6 learner-minutes in total.
- **Result:** 36 delivered sessions and 16 excluded occurrences, which stay stored with an exclusion reason.

## Learner matching

1. Exact email after trimming and lowercasing, against the dashboard learner registry. An email shared by two registry learners is treated as ambiguous and never matched.
2. Optional verified aliases in `data/teams_email_aliases.csv` (`teams_email,learner_id`). This file is git-ignored, and each row must reference an existing learner. Invalid alias files fail the import.
3. Name-only matches are never accepted. They are only counted.

Everyone else is excluded from learner totals:

| Class | Rule |
|---|---|
| organizer | `Role = Organizer` |
| training_provider_staff | email domain in `TEAMS_STAFF_DOMAINS` (`kentbusinesscollege.com`) |
| unmatched_learner_domain | learner organisation domain but not in the registry. **Review for aliases** |
| unmatched_external | other domains |
| anonymous_or_external_no_email | no email |

Current results:
- 30 learners matched by exact email.
- 14 registry learners have no Teams match.
- 4 distinct unmatched learner-domain emails (20 rows) are flagged for review.
- 1 name-only candidate was not accepted.

## Interval normalisation

- Exact duplicate interval rows are removed.
- All intervals for one learner in one occurrence are merged, across reports and across identities. 27 learners joined from more than one Teams identity, and 43 overlapping intervals were merged.
- Presence is the length of the merged union.
- **Attended** means merged presence of at least 10 minutes in a delivered session. Shorter presence counts as a "brief join" and is recorded but not counted as attended.

## Module mapping

| Teams meeting subject | LMS module | Rule |
|---|---|---|
| Advanced Project and Logistics Management | Advanced Project and Logistics Management | exact title |
| Level 7 Diploma in Project Management | PCP Al-Fanar 2025 | approved 2026-11-04: 17 of PCP's dated lecture recordings fall on its lecture days |
| Andrew Operations and Information Management for Project Managers | Al Fanar - Operations and Information Management | approved 2026-11-04 |
| (none) | Al Fanar-Procurement Risk | **No Teams data, shown as Not available** |

Rules live in `backend/analytics/attendance/mapping.py`. Any meeting that matches neither rule is stored as `unmapped` and excluded from module figures until a rule is approved.

## Metrics

- **Delivered sessions per module:** PCP 17, Operations 9, Advanced 10.
- **Attendance %** = attended delivered sessions ÷ delivered sessions of each enrolled module. It is only calculated for modules with a mapped meeting. Overall: 666 / 1,103 = **60.4%**.
- **Live hours** = merged presence in delivered sessions (about 1,339 h).
- **Caveats:**
  - 18 covered enrollments belong to learners with no matched Teams identity, and they count as not attended. Verified aliases would resolve them.
  - PCP has dated recordings for 19 Jan to 2 Feb 2026 that fall after the Teams export ends (14 Jan 2026), so the export may be missing later PCP sessions.
- The dashboard totals were reconciled against an independent calculation from the raw workbook, and all figures matched exactly.

## Refresh mechanism

The XLSX is a static export and does not update by itself.

1. Export a fresh Teams attendance workbook with the same three sheets. Replace the file in `data/`, or point `TEAMS_ATTENDANCE_PATH` at the new file.
2. Run:
   ```powershell
   cd backend; ..\.venv\Scripts\python manage.py import_teams_attendance
   ```
3. Re-run it after the LMS learner registry changes, because matching uses the registry at import time.

The import is idempotent: an unchanged file produces zero writes. A failed import (missing sheet, missing headers, invalid aliases) keeps the previous attendance. Results appear under `teams_attendance` in `GET /api/sync/status`.

**Automatic updates are not supported by this source.** Making it automatic would need a scheduled export or a read-only Microsoft Graph integration (`OnlineMeetingArtifact.Read.All` or the attendance-report API), which requires tenant admin consent. That is not implemented.

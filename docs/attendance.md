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
- **Date range (UTC):** earliest record 4 Nov 2025, latest 4 Nov 2026. Per series:
  - Level 7 Diploma in Project Management (PCP): 4 Nov 2025 to 14 Jan 2026
  - Andrew Operations and Information Management: 27 Jul to 16 Sep 2026 (delivered sessions 27 Jul to 26 Aug)
  - Advanced Project and Logistics Management: 27 Sep to 4 Nov 2026 (delivered sessions 28 Sep to 4 Nov)
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
- 14 registry learners have no Teams match. Their attendance is **unknown, not absent**: they are shown as "Unknown · no Teams match" and excluded from attendance numerators and denominators.
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
- **Attendance %** = attended delivered sessions ÷ delivered sessions of each enrolled module, summed over enrollments of learners with a matched Teams identity. It is only calculated for modules with a mapped meeting.
  - Numerator: 666 learner-sessions with at least 10 minutes of merged presence.
  - Denominator: 901 = delivered sessions × enrollments, over the 73 covered enrollments whose learner has a matched Teams identity.
  - Overall: 666 / 901 = **73.9%**.
  - Not counted: 18 covered enrollments (14 learners, 202 learner-sessions) with no matched Teams identity. Counting them as absent would give 666 / 1,103 = 60.4%, which is a lower bound, not the attendance rate.
- **Live hours** = merged presence in delivered sessions (about 1,339 h).
- **Caveats:**
  - 18 covered enrollments belong to learners with no matched Teams identity. They are reported as unknown and excluded from the rate. Verified aliases would bring them in.
  - The export runs to 4 Nov 2026, but the PCP meeting series has no reports after 14 Jan 2026. PCP has dated LMS recordings for 19 Jan to 2 Feb 2026, so any later PCP sessions held under this meeting are missing from the export, or ran under a different meeting link.
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

## Owner-confirmed delivered lectures

The module card shows **Delivered Lectures**: the count confirmed by the project owner where one exists (`backend/analytics/attendance/confirmed.py`), otherwise the Teams delivered-session count. A confirmation is display-only and never edits Teams sessions or attendance records.

| Module | Teams sessions | Confirmed | Displayed | Attendance basis |
|---|---|---|---|---|
| Advanced Project and Logistics Management | 10 | **8** (project owner, 2026-11-04) | 8 | 10 Teams sessions |
| PCP Al-Fanar 2025 | 17 | — | 17 (Teams) | 17 Teams sessions |
| Al Fanar - Operations and Information Management | 9 | — | 9 (Teams) | 9 Teams sessions |
| Al Fanar-Procurement Risk | none | — | Not available | n/a |

- **Advanced, 10 vs 8:** all 10 Teams sessions (28 Sep, 5, 7, 12, 19, 21, 26, 28 Oct, 2 and 4 Nov 2026) look like full lectures. Each has 15–19 learners present for 10+ minutes, a median presence of 109–160 minutes, and a trainer present. The LMS cannot identify the 2 extra dates either: its 8 "LIVE" videos are parts (P1–P3) of only 3 recorded lessons, not 8 lectures. Attendance therefore still uses all 10 Teams sessions. When the owner names the 2 dates, they can be excluded and attendance recalculated.
- **Operations, 10 Aug 2026:** counted under the rule (12 learners for 10+ min). However, no trainer or organiser stayed for more than 3 minutes, and learner-minutes were about 15% of a normal day, so it is flagged for confirmation.
- **Not covered by the export:** about 7 earlier Operations lectures (LIVE recordings viewed 4–22 Jul 2026, before the meeting series starts), 6 dated PCP recordings (1 Nov 2025 and 19 Jan to 2 Feb 2026), and all of Procurement Risk. These are not added to any count without confirmation.

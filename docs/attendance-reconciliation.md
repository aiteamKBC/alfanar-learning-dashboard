# Attendance reconciliation rule

This local change supersedes the timing-only display rule in the previous release.
It deliberately updates effective attendance and its percentages/coverage while
retaining the original matrices, SessionAttendance rows and immutable timing snapshots.
All dashboard views use one AttendanceIndex: Overview, charts, learner directory,
profiles, module summaries and Live Sessions, including each lecture's learner count.

## Rule established before applying corrections

Version: `verified-positive-evidence-v1`. Default minimum: **30 merged minutes per
learner, module and exact lecture date**, configurable through
`ATTENDANCE_RECONCILIATION_MIN_MINUTES`. The project owner explicitly approved this
rule on 8 November 2026, including treating verified Teams attendance of 30+ minutes
as Present when the original matrix says Absent. It is not a claim of full-lecture
completion. Thirty minutes excludes accidental/short connections without assuming
official lecture durations, which are not available consistently. It is distinct from
the older 10-minute legacy Teams-import rule; it does not change the original import.

Only active, verified timing on a unique existing matrix lecture date qualifies.
The timing importer has already checked the approved exact Meeting ID/organizer/course
mapping, canonical identity/email alias and UTC date. Non-enrolled, excluded or merged
source accounts, unapproved reports, off-calendar, ambiguous and boundary records never
qualify. Graph is preferred; the already verified historical Teams Excel fallback is
equally admissible timing evidence. Never match by display name, nearby date or count.

Duration is the union of non-overlapping verified intervals across all reports and
repeat joins. Gaps, duplicate intervals and overlapping devices cannot inflate it.
The threshold uses integer microseconds, including the exact 30-minute boundary.

| Matrix observation | Verified duration | Effective attendance | Decision |
|---|---|---|---|
| Missing | At least 30 minutes | Present | Fill missing observation from verified timing |
| Absent | At least 30 minutes | Present | Resolve by positive evidence; retain the matrix absence label and private decision reason |
| Present | At least 30 minutes | Present | Sources agree |
| Present | Below 30 minutes | Present, review flag | Timing may be incomplete; do not silently invalidate original presence |
| Absent | Below 30 minutes or missing | Recorded absent | Preserve explicit matrix evidence; never infer absence from missing timing |
| Missing | Below 30 minutes or missing | Not Available | Insufficient evidence to establish presence; never infer absence |
| Present | Missing | Present | Preserve matrix evidence |

The 30-minute threshold qualifies timing-derived presence, not every matrix-present
observation. The latter remains authoritative positive evidence until reviewed. A
short observed interval cannot prove there were no other attendance records. These
conflicts remain visible and explicitly unresolved instead of downgrading attendance.

## Percentages, coverage and evidence history

Attendance = effective present / effective known (present + explicit matrix absence).
Coverage = effective known / enrolled-learner × existing dated-session pairs. Unknown
observations are excluded from the rate, but retained in the coverage denominator.
Undated official lectures remain outside that denominator; official counts stay
16/14/16/12. No new lecture dates or absences are created. Timing remains 1,048 visible
learner–lecture pairs on 51/58 official lectures, including real short intervals.

Corrections are an in-memory overlay, so repeated imports/reads cannot compound them
or destroy original evidence. Each decision exposes a safe reason and original matrix
status in the API; corrected rows and unresolved conflicts have visible UI labels.
Private source IDs, report/meeting IDs, emails and raw provenance stay off the API.

## Safe source selection and repeat imports

Set `ATTENDANCE_DIRECTORY` to the latest approved four-workbook directory. The verified
Operations workbook contains 16 dated sessions, including 24 and 26 August 2026; the
older 14-session copy must not be used. The local configuration selects
`data/operations-attendance-correction/workbooks`. Original workbooks remain untouched.

Matrix imports reject any source that would remove existing matrix lecture dates,
before writing session or attendance records. An intentional calendar correction
requires a separate evidence review. Each successful import records the SHA-256 of
each source workbook and rejects a file that changes while being read. Repeated
LMS, matrix and Graph timing imports must retain the decision ledger and review flags.

The approved 30-minute rule and detailed QA findings are recorded in the private
audit report. The dashboard retains concise source and Review labels; no audit
paragraphs, warning boxes or banners are added. The owner approval resolves the
previous policy sign-off blocker; it does not resolve incomplete source evidence. The 16
matrix-present/short-timing conflicts remain review items, not automatic absences.

Write an immutable private decision ledger for every local audit or later authorized
timing/matrix refresh:

```bash
python manage.py audit_attendance_reconciliation --output /private/new-decision-ledger.json
```

It records every enrolled learner/date decision, rule version, threshold, original
matrix row reference, verified timing row/snapshot reference, effective status and
reason. Repeating with identical evidence reports `changed=false`; changed decisions
require a new filename. Never add these ledgers to Git or serve them publicly.

Changing the minimum changes effective attendance. Record and validate a new ledger
and report the before/after impact. The older production timing release guard expects
unchanged binary-derived dashboard KPIs and will intentionally reject this new behavior.
Do not deploy this change using that old release package. Use the final release and
revised expected results documented in `docs/presentation-release.md` when production
deployment is authorized. Release preparation alone does not authorize deployment.

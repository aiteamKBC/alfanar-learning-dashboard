# Data mapping and KPI definitions

Google Sheets is the primary LMS source, using the unchanged `spreadsheets.readonly` integration. `data/Alfanar Endpoint.xlsx` is an older snapshot and test fixture. Source rows and workbooks are never modified. See [attendance.md](attendance.md) for the separate attendance import.

| Worksheet | Grain and purpose |
|---|---|
| Students | Source learner/module enrollment and reported progress |
| Lesson Details | Video, PDF, audio and reading completion evidence |
| Quizzes | Source learner/quiz summary and best score |
| Quiz Attempts | Attempt sequence, score and pass status |
| Assignments | Known assignment catalogue |
| Submissions | Submission ID, status, grade and timestamp |
| Videos, PDFs, Audio, Readings, All Activities | Cross-checks only; never additional facts |
| Coverage, Diagnostics, Reading Sessions, Other Lessons | Inspected; never additional academic facts |

## Identity reconciliation

`importer/exclusions.py` contains six excluded source IDs: the two prior non-learners and four newly approved exclusions. Every academic sheet applies these exclusions before canonicalization; no Google row is deleted.

`importer/identities.py` preserves the four prior verified duplicate mappings and adds the owner-approved groups, including formerly unresolved ID 743. Mappings have no chains. No unapproved account is merged. `LearnerIdentity` retains every allowed source account and email under its canonical ID so future syncs maintain matching across approved aliases.

One canonical learner/module pair creates one enrollment. Overlapping enrollments retain maximum source progress, earliest registration and latest login. Original progress values and source counters remain in `AcademicEvidence`; source summary counters do not drive completion KPIs.

`AcademicEvidence` stores every allowed Students, Lesson Details, Quizzes, Quiz Attempts and Submissions row with original account, canonical ID, sheet and row number. Conflicting values remain reviewable. Canonical lessons count once per learner/module/item; the actual source row with the greatest available duration supplies representative timing. No artificial start/end span is created. The current live source has 4,579 valid lesson rows and 4,575 distinct canonical completions.

Attempts preserve original account and attempt numbers. When merged accounts overlap on a quiz, every separate attempt remains and receives a deterministic chronological display sequence with original numbering retained. Single-account quizzes retain source attempt order even when timestamps disagree. Every submission ID and grade remains; activity matrices expose full submission histories alongside the latest record.

## KPI definitions

| Metric | Calculation |
|---|---|
| Learners | Distinct canonical learner IDs in selected modules |
| Enrollments | Distinct canonical learner/module pairs |
| Active in LMS | Any recorded lesson, attempt or assignment record, including drafts; does not measure overall engagement |
| Learner status filter | Classifies each learner across selected modules; Overview and Learners use the same scope |
| Mean LMS progress | Mean non-null canonical enrollment progress; overlapping source progress uses maximum |
| Known curriculum | Distinct exported items or assignment catalogue; lower bound of official curriculum |
| Quiz completion | Attempted learner/quiz results / (enrollments × known quizzes per module) |
| Latest quiz score | Latest graded attempt by source sequence, or chronology across merged overlapping accounts |
| Mean latest score | Mean raw latest scores, rounded once at final aggregation, including learner rollups |
| Quiz pass rate | Latest graded results with source status `passed` / all graded results; no invented pass threshold |
| Assignment submissions | Non-draft records; drafts reported separately |
| Assignment coverage | Distinct learner/assignment pairs with any non-draft submission / possible pairs |
| Materials completed | Distinct canonical learner/lesson completions |
| Activity coverage | Enrollments with any recorded LMS activity / enrollments |
| Recorded time | Unverified LMS open-to-close timers; not verified study hours |
| Delivered lectures | Owner-confirmed count where supplied; otherwise dated sessions with positive attendance evidence; provenance displayed |

Missing scores are not zero or failures. Missing attendance is **Not Available**. Submission rows take precedence over reported counts, which differ for 37 current canonical enrollments. Raw-versus-distinct cross-check differences from approved overlaps are explicitly reconciled by the independent audit.

On 2026-11-05 the live local reconciliation yields 28 learners, 104 enrollments, four modules, 25 active in LMS, 120 distinct quizzes, 1,304 learner/quiz results, 1,427 attempts, 4,575 distinct lesson completions and 45 submission records (42 non-draft, three drafts). Mean LMS progress is 38.4%, mean latest quiz score 95.3%, and 1,287 / 1,304 graded results passed (98.7%). Sixty lesson timers exceed eight hours; timer totals are unverified.

Run `python backend/manage.py audit_dashboard` to reproduce the independent source audit. It reads fresh Google data by default, accepts `--snapshot` for captured source JSON, fails on discrepancies and writes an aggregate local report under ignored `data/`.

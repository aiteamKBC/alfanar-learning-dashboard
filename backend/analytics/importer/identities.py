"""Verified duplicate LMS accounts of the same learner: duplicate User ID -> canonical User ID.

During normalization every row of a duplicate account is attributed to the canonical account, so the
learner appears once and all of their module progress and activity is kept. The source workbook /
Google Sheet is never modified; removing an entry and re-syncing restores the separate accounts.

Only add a pair when verified by evidence or explicitly approved by the project owner.
Overlapping academic evidence is retained in AcademicEvidence; summaries count distinct items.
Keep personal names out of this file (public repo).

Evidence (audit 2026-11-05) for each pair:
* Duplicate registered in the 2026-04-09 batch with a misspelling of the canonical account's
  corporate email, and has no LMS activity and no Teams presence.
* The two accounts were never present in the same Teams session.
* For 1056 and 1062: the canonical account's Teams identity attended Advanced lectures, a module
  enrolled only on the duplicate, which shows it is the same learner's enrollment.
"""

MERGED_LEARNER_IDS: dict[int, int] = {
    1056: 760,   # misspelled-surname email, 2026-04-09 batch, no activity
    1058: 753,   # misspelled-surname email, 2026-04-09 batch, no activity
    1062: 748,   # truncated-surname email, 2026-04-09 batch, no activity
    # Username-style Gmail encoding the canonical corporate address. Its PCP activity (Nov 2025 to
    # Mar 2026) matches the canonical email attending 16 PCP lectures; never co-present in Teams.
    779: 1229,
    # Explicit owner decisions, 2026-11-05. Canonical IDs have no mapping chains.
    743: 1229,
    746: 763,
    1221: 763,
    1222: 763,
    758: 1059,
    1038: 1059,
}

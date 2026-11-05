"""LMS accounts that appear in the export but are not learners.

Rows for these User IDs are skipped during normalization, so they never become learners, enrollments
or learner activity in the dashboard (and are therefore also absent from the Teams learner registry).
The source workbook / Google Sheet is never modified: removing an entry here and re-syncing restores them.

Match is by stable LMS User ID only (never by name), so no other learner can be excluded by mistake.
Only add an entry confirmed by the project owner. Keep personal names out of this file (public repo).
"""

NON_LEARNER_SOURCE_IDS: dict[int, str] = {
    1: "Training-provider account (display name is a Kent Business College domain), confirmed not a learner (2026-11-05)",
    17: "Staff / manager account, confirmed not a learner by the project owner (2026-11-05)",
}

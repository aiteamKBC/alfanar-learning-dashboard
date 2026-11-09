"""Historical team-created identity mappings, retained only for controlled reversal.

These are not active policy. Original LMS source IDs are the default; future
identity decisions require the authorized owner workflow and a persistent audit.
The comments below describe the superseded decisions, not new match evidence.
"""

LEGACY_MANUAL_MERGES: dict[int, int] = {
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

# Historical mappings are audit evidence only; owner decisions are stored in the database.
MERGED_LEARNER_IDS: dict[int, int] = {}

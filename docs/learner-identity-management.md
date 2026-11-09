# Learner identity management

Original eligible LMS accounts are the default. The former hard-coded team merges are
retained solely as reversal evidence in `LEGACY_MANUAL_MERGES`; they are never applied
to imports. Configured non-learner exclusions still apply.

## Owner workflow

Executive Overview → Learner-by-module progress includes selection, Select All, Clear
Selection, Merge and Delete for authorized owners/staff. Selection uses stable source
IDs and survives module filtering. Merge previews list originals and modules, allow
the primary identity to be chosen, and show affected evidence and resulting counts.
Nothing changes until confirmation. Delete archives; Manage identities provides
Restore, Unmerge and history. Undoing a nested merge restores its direct parent groups;
those groups can then be unmerged separately.

Viewers have no controls and receive 403 from identity APIs. An active dashboard grant
plus staff status or `DashboardAccess.can_manage_identities` is required; superusers
are permitted. The existing explicit `provision_owner` command grants this capability.
The restoration command can grant it to an existing authorized owner with `--owner`.
It never changes passwords or grants staff/superuser privileges.

## Persistence and rules

Migration `analytics.0007_owner_identity_management` adds immutable, hash-addressed LMS
snapshots, an original source identity registry, owner groups/memberships, identity
revision state, preview/operation history and a private evidence archive. The existing
normalized LMS tables are the current active projection. Deleting projection rows
does not delete original source snapshots, attendance intervals or archived decisions.

The normalizer's existing rules still apply: overlapping progress uses the highest
source percentage, never a sum or average; activities use distinct course/kind/item
IDs; source account/attempt numbers are retained. Unmerge rebuilds from original rows.
Raw Teams and matrix records keep original IDs. Presentation projects them through the
owner mapping, unions overlapping intervals, and retains contradictory matrix flags
for review while applying the existing positive-evidence rule. Missing timing never
establishes absence. Ambiguous emails remain unmatched even within an approved group.

Original-account timing without a verified enrollment is retained in the private
review archive, not reassigned to a different source account. A merge does not approve
held identity/calendar evidence; those holds need their own evidence review. The prior
1,372 decisions, including 160 corrections and 16 Review flags, remain archived along
with the full prior timing snapshot. Current totals are recomputed for the restored
identities and therefore differ from the former consolidated totals.

## API and concurrency

- `GET /api/identities`: owner registry and applied operation history.
- `POST /api/identities/preview`: `{kind, learner_ids, primary_id?}`. Kinds are
  `merge`, `unmerge`, `archive`, `restore`; response includes a random preview token.
- `POST /api/identities/confirm`: `{token}`. Requires the same authenticated actor.

Session authentication and CSRF protect mutations. Confirmations use a transaction and
compare-and-swap revision write, including on SQLite. Previews expire after 15 minutes
or a source/identity change. Repeating a confirmed token returns its original result.
Concurrent or stale requests fail without partial source/projection changes. Failed
rebuilds roll back membership, archive and audit changes together.

LMS sync captures original rows, preserves owner mappings/archive state, and rebuilds
the projection in one transaction. An unchanged source does not advance the identity
version. Disappearance of a merged primary fails safely and requires undo/review.
Teams ingestion matches the original registry, including archived originals, so it
cannot reactivate archived learners or rewrite owner mappings. Display and delivery
quorum calculations use the active identity projection. The 23:00 Africa/Cairo timer,
publisher configuration and Graph settings are unchanged.

## Attendance export transition

The existing eight tab schemas, tab IDs and stable learner/lecture keys are retained.
Snapshots include the source hash, identity revision, original evidence and operation
audit hash. Strict validation compares the active projection to approved source policy
instead of requiring the obsolete 27/103 baseline after controlled restoration.

The publisher requires the existing destination state and unchanged workbook ownership,
headers and LMS structure. Removed learner/enrollment/attendance keys remain at their
existing row positions with `Active`/`Eligible=false`. Inactive summary rows have
`Enrolled=0` and no current attendance numerators/denominators. Before applying a new
generation, the previous state is archived privately under `history/`. Current rows
may update at the same key; original effective values and provenance remain in that
generation's private snapshot/state. Manual review columns are untouched. Never sum
inactive attendance rows. A lower identity revision is refused: undo/restore must be
a new audited operation. Never delete/reinitialize the destination state.

No live Google write is required for identity management. This release's transition
was rehearsed offline against the privately saved published state. Production rollout
must use the existing publisher's normal guarded process, not create replacement tabs.

See [the production runbook](presentation-release.md) for backup, dry-run, restoration,
verification and rollback. No migration automatically restores production identities.

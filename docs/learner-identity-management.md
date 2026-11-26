# Learner identity management

Original eligible LMS accounts are the default. The former hard-coded team merges are
retained solely as reversal evidence in `LEGACY_MANUAL_MERGES`; they are never applied
to imports. Configured non-learner exclusions still apply.

## Owner workflow

Executive Overview → Learner-by-module progress includes selection, Select All, Clear
Selection, Merge and Delete for the active `owner_review` account. Selection uses stable source
IDs and survives module filtering. Merge previews list originals and modules, allow
the primary identity to be chosen, and show affected evidence and resulting counts.
Nothing changes until confirmation. Delete archives; Manage identities provides
Restore, Unmerge and history. Undoing a nested merge restores its direct parent groups;
those groups can then be unmerged separately.

Only the authenticated, active account with the exact username `owner_review` can
manage identities. It can log in and use the existing dashboard without a separate
DashboardAccess/Owner Grant. Legacy grant expiry or revocation does not apply to this
account; deactivate the user itself to revoke access. All other accounts, including
staff, superusers and accounts with legacy management grants, are denied identity APIs
and receive no management controls. Anonymous and inactive users are also denied.

Migration 0007 and its legacy grant field remain unchanged for schema compatibility;
the field no longer authorizes identity actions. No provisioning command is needed.
The restoration command requires the existing active `owner_review` account and records
it as the audit actor. Optional `--owner` accepts only `owner_review`. No account, grant,
password or staff/superuser status is changed. `apply_reviewed_identity_restoration`
uses an existing private dry-run plan and backup, retaining the original plan hash and
all source-validation, transaction and rollback safeguards.

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

## Advanced Research Methods (live LMS) learners

ARM learners are read live from the LMS and are never copied into the dashboard
database. Their original identity ID is `2000000 + LMS learner ID` (for example LMS
learner 1275 is `2001275`). Merge and Unmerge accept them in any combination with
dashboard learners, and either source may be the primary.

- Persistence: a confirmed merge stores only the LMS learner IDs, as
  `LmsIdentityMembership` rows on the new identity group (migration
  `analytics.0013_lms_identity_membership`). A link applies while its group is
  active. Unmerge deactivates the group; the links stay as history. LMS names, emails,
  attendance, progress, quizzes and submissions are never stored. Previews and history
  store IDs only; names are read live when shown.
- Display: owner links decide first, then an exact email that belongs to exactly one
  dashboard identity, then the account's own ARM ID. All accounts of one identity are
  shown as one learner under the primary's ID in every view.
- Existing owner merges: each member's email can bring in its own LMS account. If
  756 + 1055 are merged, the account with 756's email and the account with 1055's
  email both show under 756, with no new merge stored. Each email admits one LMS
  account, the lowest unlinked LMS ID. Other accounts with the same email, or with
  the email of an owner-linked account, stay separate. The result does not depend on
  LMS row order.
- An ARM primary with dashboard members: the dashboard records are projected under the
  ARM ID and keep the dashboard name, because LMS names are not stored. An ARM-only
  group shows the primary account's live LMS name.
- Combined ARM figures: a distinct activity is counted once; quiz attempts and
  submissions are the union of the accounts' records. For each lecture, one stored
  LMS attendance record is shown as-is: present before absent, then the longest
  attended duration, then the lowest LMS learner ID. Durations are never summed
  across accounts.
- Merging a row also links any LMS account shown in it by email match, so it stays
  with the merged learner. After Unmerge that account goes back to its email match.
- Preview and confirm read the LMS roster (read-only). If the LMS cannot be read, an
  action involving ARM learners is refused. If the accounts change between preview
  and confirm, the preview must be repeated. Delete/Restore apply to dashboard records
  only; ARM learners cannot be archived. Excluded LMS learners (272, 593, 1213) cannot
  be selected.

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

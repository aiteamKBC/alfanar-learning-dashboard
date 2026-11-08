# Attendance master export: setup, activation and rollback

The exporter publishes existing verified Django attendance into eight additional Google Sheets tabs. It never imports attendance, changes LMS records or calculates new identities. The existing LMS adapter keeps its `spreadsheets.readonly` scope. The writer reuses the configured service-account file with a separate `https://www.googleapis.com/auth/spreadsheets` scope.

**Disabled by default. Do not enable live writes until the upstream LMS publisher is verified to preserve the eight attendance tabs.** Editor sharing does not establish publisher compatibility. This release does not install or activate a server timer automatically.

## Destination and ownership

The intended live master is the same spreadsheet identified by `GOOGLE_SHEETS_SPREADSHEET_ID`. Its original 15 `Al Fanar ...` LMS tabs are never targeted by write requests. First publication requires `--initialize`, creates only the following new tabs, and refuses to adopt existing attendance tabs without a matching private exporter state:

| Tab | Purpose |
|---|---|
| AT_Modules | Four approved course IDs, names, ordering and official historical counts |
| AT_Learners | Canonical learner IDs and registered identities |
| AT_Enrollments | Stable learner/course pairs |
| AT_Lectures | Dated history, separately undated official slots, and verified/pending future occurrences |
| AT_Attendance | One eligible learner/lecture row, original evidence, effective status and actual timing |
| AT_Summary | Learner/module and module aggregates from the same exported decisions |
| AT_Review | Unresolved timing/calendar/identity evidence and short-visit reviews |
| AT_SyncLog | Verified publication generation, source hash, counts and source-as-of time |

The four manual columns at the end of AT_Review (`OwnerDecision`, `ApprovedBy`, `ApprovedAtUTC`, `DecisionEvidenceRef`) belong to reviewers. The exporter never writes their data cells. Do not enter decisions into blank rows: each decision requires an existing stable ReviewID. Editing a review note does **not** approve an identity or change dashboard attendance. Apply approved source corrections through the existing reviewed process.

Machine-maintained columns and headers are protected for the service account; workbook owners retain Google's inherent ownership privileges. Do not override those protections or run a second writer. The exporter checks ownership markers, stable tab IDs, keys, headers and previous managed values before each publication. Human notes in additional columns are left untouched. An unexpected modification stops publication; it is not silently overwritten.

## Exact configuration

These are server-side environment settings; never add credentials, spreadsheet IDs, learner data or private archives to Git. No new credential is necessary if the existing service account has the correct Editor sharing.

```dotenv
MASTER_ATTENDANCE_ENABLED=false
MASTER_ATTENDANCE_SPREADSHEET_ID=<same ID as GOOGLE_SHEETS_SPREADSHEET_ID for live>
MASTER_ATTENDANCE_TARGET_KIND=live
MASTER_ATTENDANCE_LIVE_WRITE_APPROVED=false
MASTER_ATTENDANCE_PUBLISHER_VERIFIED=false
MASTER_ATTENDANCE_SERVICE_ACCOUNT_FILE=
MASTER_ATTENDANCE_DIRECTORY=/var/lib/alfanar-master-attendance
MASTER_ATTENDANCE_IDENTITY_REVIEW_FILE=
```

An empty writer credential path reuses `GOOGLE_SERVICE_ACCOUNT_FILE`. An empty identity-review path exports all unresolved database evidence without adding the optional private investigation candidate archive. To retain that archive too, transfer the reviewed JSON privately and set `MASTER_ATTENDANCE_IDENTITY_REVIEW_FILE` to its absolute path. It only creates review entries; it cannot establish attendance or approve aliases.

For an explicitly authorized separate test file use `MASTER_ATTENDANCE_TARGET_KIND=test` and its ID. Test mode rejects the live LMS ID. Live mode requires all three enable/approval/publisher flags, the exact live LMS ID, and usable credentials. The publisher flag means actual source inspection or an accountable publisher-owner confirmation has established that refreshes update only the existing named LMS tabs and preserve added `AT_` tabs. It is not a workaround for missing evidence.

Google Drive API is not required for attendance writes or private full-grid LMS snapshots. If Drive metadata reads are unavailable, do not claim file ownership/capabilities have been API-verified. Working Editor/write access is established only by an explicitly authorized successful Sheets write and read-back; merely obtaining a write-scoped token is insufficient.

## Local preparation and validation

From the repository root, using the existing virtual environment:

```powershell
.\.venv\Scripts\python.exe backend\manage.py export_master_attendance --output data\master-attendance-export
.\.venv\Scripts\python.exe backend\manage.py test analytics.tests.test_master_export analytics.tests.test_attendance_reconciliation analytics.tests.test_teams_discovery
```

The default command only builds a private local snapshot. It does not call Google. The snapshot gate checks the historical baseline: 27 learners, 103 enrollments, 58 official lectures, 53 dated lectures, 1,372 decisions, 1,048 timings on 51 dates, 160 corrections, 16 flags and 347 pending historical pairs. Each original matrix cell is traced back to the approved workbook/sheet/row/column, and must agree with the imported binary record. Missing source files, old Operations matrices, changed identities or changed baseline totals stop export before cloud access. New individually verified Teams occurrences are exported separately without rebasing historical totals.

The snapshot includes a private canonical registry, original timing provenance/intervals, pending evidence, new occurrence/report revisions, and SHA-256 references. The four original matrix workbooks are copied unchanged beside it. These files contain personal data and belong outside the web root, with restricted filesystem permissions. `snapshots/<generation>/SHA256SUMS` verifies that generation's JSON and original files. The duration column uses the existing merged interval duration, never first-to-last span or binary attendance.

The source decision remains Present/Absent/Not Available. A Review flag is a separate field. A matrix Present with short timing remains Present with Review; a matrix Absent with a short visit remains Absent; an unknown short visit stays Not Available. Review-tab issue counts include pending dates, short visits and other evidence and must not be mistaken for the 16 historical attendance flags. Five official undated slots have blank dates and no attendance rows.

## Controlled server preparation — commands for a later approved change

Do not run these commands until the code release, private files and activation have been reviewed. No production access or deployment is part of implementation.

Actual installation: `/var/www/alfanar-learning-dashboard`; backend `backend/`; existing Python executable `backend/venv/bin/python`; dashboard service `alfanar-gunicorn`; SQLite `backend/db.sqlite3`. Verify these paths on the server before a later authorized rollout. This feature adds no migration, no package dependency beyond the already installed Google client stack, and no attendance re-import. It does not require restarting gunicorn to export attendance.

```bash
set -euo pipefail
cd /var/www/alfanar-learning-dashboard
git status --short
# Stop here if there are unreviewed source changes.
release_backup="/var/lib/alfanar-release-backups/master-$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 0700 "$release_backup" /var/lib/alfanar-master-attendance
git rev-parse HEAD > "$release_backup/previous-commit"
cp --preserve=mode backend/.env "$release_backup/backend.env"
cp --preserve=mode /etc/systemd/system/alfanar-teams-discovery.service "$release_backup/teams.service"
backend/venv/bin/python - "$release_backup/database.sqlite3" <<'PY'
import sqlite3, sys
from pathlib import Path
source = sqlite3.connect(Path('backend/db.sqlite3').resolve().as_uri() + '?mode=ro', uri=True)
target = sqlite3.connect(sys.argv[1])
with target:
    source.backup(target)
target.close(); source.close()
PY
git fetch origin main
git checkout main
git pull --ff-only origin main
```

The deployment operator must verify that the new HEAD is the reviewed release commit. Transfer any optional identity-review JSON and required approved matrix workbooks privately using SCP; do not put them in Git or public/static directories. Keep the existing corrected attendance directory and Graph policies intact. Verify supplied checksums before use.

Before activating the writer, configure the values above in `backend/.env` with writing disabled. Load the existing service environment using the installation's approved mechanism; if `backend/.env` is a shell-compatible dotenv file, this is:

```bash
cd /var/www/alfanar-learning-dashboard/backend
set -a
. ./.env
set +a
venv/bin/python manage.py check
venv/bin/python manage.py export_master_attendance
```

Do not print `.env` or use shell tracing. Settings read the project-root `.env` for local development; production commands must receive the actual backend service environment. Loading an unrelated development `.env` is not sufficient.

## First historical publication — separate explicit activation

Prerequisites: verified publisher behavior, owner-authorized write to the intended workbook, correct Editor ACL, private archive directory, reviewed local baseline, and a quiet window without publisher/manual edits. Keep automatic writing disabled until the first publication and repeat verification succeed.

Set publisher verification and live approval to true **only after the prerequisites actually hold**. For a one-off invocation, set `MASTER_ATTENDANCE_ENABLED=true` in that command's environment while leaving the persistent configuration disabled:

```bash
MASTER_ATTENDANCE_ENABLED=true venv/bin/python manage.py export_master_attendance --write --initialize
MASTER_ATTENDANCE_ENABLED=true venv/bin/python manage.py export_master_attendance --write
```

The second result must be `unchanged`. Before live mutation, the command captures all 15 original LMS sheets with cells, formulas, formats and structure into `lms-backups/<UTC timestamp>/`, then re-reads to confirm stable preflight content. After atomic attendance publication it verifies the original LMS fingerprint again. Any failed preservation check stops the command and requires investigation of publisher/concurrent edits; it never attempts to restore or edit LMS tabs automatically.

The eight attendance tables and SyncLog generation publish in one Sheets `batchUpdate`, so a rejected request cannot leave half a generation. Current full history is approximately 2.4 MB serialized; the exporter fails closed above its reviewed 8 MiB cap instead of splitting publication into partially visible writes. Larger future exports require a reviewed staging design. Google recommends small requests and documents atomic batch semantics: [Sheets batch updates](https://developers.google.com/workspace/sheets/api/guides/batchupdate), [usage limits](https://developers.google.com/workspace/sheets/api/limits).

Read-back verifies every managed value, row count, header, stable key and hash. Text is written with `stringValue`, so source names beginning with `=` cannot become spreadsheet formulas. A successful read-back receipt is saved privately under `destinations/<destination hash>/verified-<generation>.json`.

## Daily stage — later approved activation only

The repository service unit adds a second `ExecStart` after `sync_teams_lectures`. The service remains `Type=oneshot`; a failed first command prevents the second. The existing timer remains **23:00 Africa/Cairo**. No new scheduler, cron job or LMS sync is introduced.

Only after a controlled first publication, repeat verification and an observed upstream LMS refresh preserving all eight tabs, persist `MASTER_ATTENDANCE_ENABLED=true`. Then install only the reviewed service update:

```bash
cd /var/www/alfanar-learning-dashboard
install -m 0644 deploy/alfanar-teams-discovery.service /etc/systemd/system/alfanar-teams-discovery.service
systemctl daemon-reload
systemctl cat alfanar-teams-discovery.service
systemctl list-timers alfanar-teams-discovery.timer --no-pager
```

Do not change/reinstall the timer or create another schedule. An operator can run the already-approved service once for a controlled check; this run is not automatic deployment permission. The writer is a no-op when disabled. Exports use committed evidence and perform no Graph calls, historical import, identity merge or LMS update. Delayed reports remain under the existing discovery retry/checkpoint behavior; failed Teams discovery does not publish a misleading fresh master generation.

## Recovery and rollback

- Google errors never roll back the verified Django database. The old attendance generation remains until a complete atomic write succeeds. On a transport timeout after commit, the durable pending plan is reconciled by read-back before any retry; it does not blindly add sheets/append duplicate rows.
- A private destination-scoped plan is written before mutation. Preserve `destinations/`, `snapshots/` and `lms-backups/` when moving hosts. Losing the destination state is not a reason to reinitialize over populated tabs: the command refuses that operation.
- Reads use bounded retry for throttling/5xx/network failure. Writes retry only after comparing remote state with the pre-write and intended post-write generations. Permission errors fail immediately. Retry limits leave the plan for the existing service retry or a deliberate rerun. `last-failure.json` contains only sanitized diagnostics.
- An unexpected tab/key/header/machine-cell change stops writes. A Sheets batch is atomic but is not a compare-and-swap transaction against concurrent human edits. Protected machine ranges plus one writer are required; workbook owners must not bypass protections during publication. Manual review columns remain untouched.
- To stop exporting, set `MASTER_ATTENDANCE_ENABLED=false` in the backend environment. The Teams job can continue on its existing timer. If a source rollback is required, restore the saved service unit and run `systemctl daemon-reload`; do not disable the Teams timer or revert the SQLite database merely to remove this export stage.
- Restore only the eight exporter-owned tabs and the matching private exporter state from the last verified generation using an owner-reviewed procedure. Never restore the entire live workbook over newer LMS data. The immutable snapshots are the recovery evidence; there is no automatic destructive rollback command.

If publisher compatibility is still unverified, the valid deliverable is the disabled implementation plus locally validated private snapshots. No live write, production activation, or claim of working Google write access may be made in that state.

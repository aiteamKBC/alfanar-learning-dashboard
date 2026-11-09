# Verified identity-management release - 9 November 2026

This is the authoritative deployment runbook for the identity-restoration release.
It supersedes the former consolidated identity baseline. These are instructions for
an authorized operator; release preparation does not access Production.

| Item | Required value |
|---|---|
| Application | `/var/www/alfanar-learning-dashboard` |
| Backend / database | `backend/` / `backend/db.sqlite3` |
| Service | `alfanar-gunicorn.service` |
| Last verified Python / environment | `backend/venv/bin/python` / `backend/.env` |
| Schema migration | `analytics.0007_owner_identity_management` (after 0006) |
| Original eligible learners / enrollments | 37 / 113; seven source non-learners remain excluded |
| Manual mappings reversed | Ten; original stable LMS IDs restored |
| Current verified timing | 1,004 pairs across 51 of 58 official lectures |
| Source timing retained | All prior snapshots; 76 prior pairs have held intervals for identity/enrollment review |
| Current attendance decisions | 1,509; 255 timing-derived corrections; 12 short-timing Review flags |
| Historical evidence retained | 1,372 prior decisions, including all 160 prior corrections and 16 Review flags |
| Official lecture counts | Planning 16, Procurement Risk 14, Operations 16, Advanced 12 |
| Dependencies | No new Python or Node dependency |

A source account's timing does not transfer to another account solely because they
were previously merged. Held evidence is preserved privately pending review. Do not
force the old 27/103 or 1,048 visible-pair totals onto restored identities.

**Git deploys** application code, migration, synthetic tests, UI and documentation.
**Private SCP transfers** original LMS snapshot, four matrices, Teams Excel fallback,
Graph cache, timing policy, expected counts and the operator helper. The private package
contains no credentials or database. Never transfer the local development database to
Production. Preserve existing private evidence and publisher destination state.

The exposed Google service-account key must be rotated and its replacement verified
before production rollout. Do not put the replacement in Git or in this release package.

## 1. Transfer and verify the private package

The delivery report supplies the commit hash and exact local paths. In PowerShell:

```powershell
$release = 'D:\alfanar-learning-dashboard\data\identity-management-20261109\release'
scp -- "$release\alfanar-identities-private-20261109.tar.gz" "$release\TRANSFER-SHA256SUMS" "$release\RELEASE_COMMIT" root@72.62.133.105:/root/
```

In the operator's production shell:

```bash
set -euo pipefail
umask 077
app=/var/www/alfanar-learning-dashboard
python="$app/backend/venv/bin/python"
env_file="$app/backend/.env"
db="$app/backend/db.sqlite3"
release=/var/lib/alfanar-identity-release-20261109
backup=/var/lib/alfanar-identity-backups/$(date -u +%Y%m%dT%H%M%SZ)
service=alfanar-gunicorn.service
cd /root
sha256sum -c TRANSFER-SHA256SUMS
install -d -m 700 "$release" "$backup"
tar -xzf alfanar-identities-private-20261109.tar.gz -C "$release"
cd "$release"
sha256sum -c SHA256SUMS
commit=$(cat /root/RELEASE_COMMIT)
test -x "$python"
test -f "$env_file"
test -f "$db"
```

Do not use `set -x` or print environment files. Stop if checksums, paths or prerequisites
fail. The restore command requires the cache and Excel hash of the currently active
production timing snapshot. A mismatch is a blocker requiring updated evidence, not a
reason to weaken validation. Existing post-cutover occurrence attendance also requires
original-identity reconciliation before restoration; the command refuses to carry forward
legacy consolidated occurrence records. The verified local baseline has none. The packaged original source capture is a private fallback;
recapture the current LMS read-only before the dry run below.

## 2. Backup and pin the source

Use a maintenance window with no active LMS/Teams job. Inspect job state and postpone
if a job is running. Keep the existing 23:00 Africa/Cairo timer, service definitions,
publisher configuration and destination state unchanged. Do not run an export/publish
command during this release procedure.

```bash
cd "$app"
test -z "$(git status --porcelain)"
git rev-parse HEAD > "$backup/PREVIOUS_COMMIT"
git fetch origin main
git merge-base --is-ancestor "$commit" origin/main
systemctl stop "$service"
"$python" - "$db" "$backup/db.sqlite3" <<'PY'
import sqlite3, sys
from pathlib import Path
source, target = map(Path, sys.argv[1:])
with sqlite3.connect(source.resolve().as_uri()+'?mode=ro', uri=True) as src, sqlite3.connect(target) as dst:
    src.backup(dst)
    assert dst.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
PY
cp --preserve=mode "$env_file" "$backup/backend.env"
if test -f "$app/.env"; then cp --preserve=mode "$app/.env" "$backup/root.env"; fi
if test -d "$app/frontend/dist"; then tar -czf "$backup/frontend-dist.tar.gz" -C "$app/frontend" dist; fi
sha256sum "$backup/db.sqlite3" > "$backup/database.sha256"
git switch --detach "$commit"
cd "$app/frontend"
npm ci
npm run build
cd "$app"
```

Retain a private copy of the configured attendance-export directory and its destination
state using the existing production runbook path. This procedure never writes it, but
it must remain available for the next guarded export and rollback audit.

## 3. Migrate, capture, and review the dry run

The helper reads the existing backend environment without printing credentials and
refuses any database other than the expected local production SQLite path.

```bash
"$python" "$release/release_manage.py" --app "$app" --env-file "$env_file" manage migrate --noinput
"$python" "$release/release_manage.py" --app "$app" --env-file "$env_file" capture
"$python" "$release/release_manage.py" --app "$app" --env-file "$env_file" manage restore_original_identities \
  --lms-json "$release/current-lms.json" \
  --timing-source "$release/teams-timing.xlsx" \
  --timing-policy "$release/timing-policy.json" \
  --graph-cache "$release/graph-cache.json" \
  --matrices "$release/attendance" \
  --report "$release/dry-run-private.json"
```

Review the exact original identity set, 37/113 counts, ten reversed mappings, 1,004
verified pairs, 51 timed dates, held evidence and unchanged lecture calendar in the
private report. The command rolls back all data changes in dry-run mode. It refuses
unrecognized identity relationships. If current production/source evidence differs
from this release baseline, stop for reconciliation and a new reviewed plan.

## 4. Apply the reviewed restoration

Enter the username of the existing authorized dashboard owner. This grants identity
management only, without changing that account's password or staff status.

```bash
read -r -p 'Existing dashboard owner username: ' owner
plan=$("$python" -c 'import json,sys; print(json.load(open(sys.argv[1]))["plan_sha256"])' "$release/dry-run-private.json")
"$python" "$release/release_manage.py" --app "$app" --env-file "$env_file" manage restore_original_identities \
  --lms-json "$release/current-lms.json" \
  --timing-source "$release/teams-timing.xlsx" \
  --timing-policy "$release/timing-policy.json" \
  --graph-cache "$release/graph-cache.json" \
  --matrices "$release/attendance" \
  --report "$release/applied-private.json" \
  --apply --expected-plan "$plan" --backup "$backup/db.sqlite3" --owner "$owner"
"$python" "$release/release_manage.py" --app "$app" --env-file "$env_file" verify
"$python" "$release/release_manage.py" --app "$app" --env-file "$env_file" manage check
systemctl start "$service"
systemctl is-active "$service"
```

The controlled operation rebuilds the active LMS projection and matrix attribution
from unchanged original sources, activates an original-identity timing snapshot, and
retains the prior timing snapshot, raw matrix projection and decision ledger. No online
Graph request is necessary for the historical restoration. No original workbook is
modified. Verification also requires the configured attendance directory to contain the
same four verified sources, preventing a later import of the older Operations file. Do not run a separate legacy identity/canonical timing import.

In the actual dashboard, verify 37 learners/113 enrollments; inspect an original learner
with quiz activity and timing; compare the module table and attendance footer. Verify
owner-only selection/preview and that a viewer receives no management controls. Do not
create a real production merge merely to smoke-test. New source syncs must keep owner
archive/membership decisions. Future exports use the existing owned eight tabs and
persisted state, retaining superseded keys inactive; never reinitialize them.

## 5. Rollback

Rollback is an operator decision. Keep the service stopped if verification fails. Preserve
the failed-state database for audit; restore both source commit and the database backup.
Do not reverse migration 0007 against a database containing owner decisions, and do not
publish a lower identity revision to Sheets. If owner actions occurred after rollout,
coordinate rollback so their audit evidence is retained and replayed deliberately.

```bash
systemctl stop "$service"
"$python" - "$db" "$backup/failed-state.sqlite3" <<'PY'
import sqlite3, sys
from pathlib import Path
source, target = map(Path, sys.argv[1:])
with sqlite3.connect(source.resolve().as_uri()+'?mode=ro', uri=True) as src, sqlite3.connect(target) as dst:
    src.backup(dst)
PY
cd "$app"
git switch --detach "$(cat "$backup/PREVIOUS_COMMIT")"
"$python" - "$backup/db.sqlite3" "$db" <<'PY'
import sqlite3, sys
from pathlib import Path
source, target = map(Path, sys.argv[1:])
with sqlite3.connect(source.resolve().as_uri()+'?mode=ro', uri=True) as src, sqlite3.connect(target) as dst:
    assert src.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    src.backup(dst)
PY
if test -f "$backup/frontend-dist.tar.gz"; then tar -xzf "$backup/frontend-dist.tar.gz" -C "$app/frontend"; fi
systemctl start "$service"
systemctl is-active "$service"
```

Environment and publisher configuration were not changed by this procedure. Never
restore the exposed credential from an environment backup after key rotation. Keep all
private backup, review and release directories outside the web root with restricted
permissions. No production command above is executed by local release preparation.

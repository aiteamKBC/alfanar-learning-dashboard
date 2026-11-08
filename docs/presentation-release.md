# Final verified dashboard release — 8 November 2026

This is the authoritative runbook for the final timing and attendance reconciliation release. It replaces the former
`/opt`, PostgreSQL and `alfanar-api` examples. These commands are for the authorized
production operator. Local release preparation does not connect to or deploy production.

## Release contract

| Item | Verified value |
|---|---|
| Application | `/var/www/alfanar-learning-dashboard` |
| Backend / SQLite | `backend/` / `backend/db.sqlite3` |
| Service | `alfanar-gunicorn.service` |
| Last confirmed account | user `root`, group `www-data` |
| Last confirmed Python / environment | `backend/venv/bin/python` / `backend/.env` |
| Learners / enrollments | 27 / 103 |
| Official lectures, in module order | Planning 16; Risk 14; Operations 16; Advanced 12 |
| Verified timing | 1,048 learner–lecture pairs on 51 of 58 official lectures |
| Visible pairs by module | Planning 367; Risk 238; Operations 292; Advanced 151 |
| Pending timing | 347 off-calendar pairs; no lecture rows created |
| Approved attendance rule | 30+ merged verified minutes = Present, including matrix Absent; owner approved 8 November 2026 |
| Reconciliation | 160 corrections (156 missing + four matrix absences); 16 short-timing Review flags retained |
| Migration | `analytics.0005_lecture_timing`, separate timing tables |
| Dependencies | No new Python/Node dependency; existing lockfiles retained |

**Git:** source, migration 0005, Graph integration, reconciliation rules, API/UI,
synthetic tests and this runbook. **Private SCP:** unchanged Teams Excel 3 fallback,
six-series policy, verified Graph snapshot, all four current attendance workbooks, expected results, read-only deployment
verifier and supporting evidence. Never put workbooks, policies, private Meeting/Organizer
IDs, caches, learner data, credentials, backups or private archives in Git/web roots.
The private bundle contains no `.env`, database backup or Graph credential. Never
transfer the local database to production.

The bundle includes `RELEASE_COMMIT`, `SHA256SUMS`, `expected-release.json`,
`release_guard.py`, `timing-policy.json`, `PCP_Al_Fanar_Teams_Original_Data 3.xlsx`,
`attendance/`, `cache/`, `owner-approval.json` and `evidence/`. The external `TRANSFER-SHA256SUMS` verifies transfer integrity;
compare its commit with the delivery report too. The internal manifest covers every
payload file. Caches are immutable provenance: the importer does not bootstrap an empty
production timing database from an arbitrary cache file. If live historical access
cannot reproduce the verified release, stop; do not weaken the checks.

Seven official slots remain unavailable: Risk 9 June and Operations 1 July 2026,
plus two undated Risk and three undated Advanced slots. Do not invent undated rows
or reassign Risk 8/10 June evidence to 9 June. This release includes no LMS sync,
calendar correction, identity edit or scheduler addition. Original matrix observations
stay immutable; the shared attendance index applies the approved positive-timing rule.
The previous timing-only package/guard is incompatible with these approved attendance
outcomes. Use this final package and its verifier together.

## 1. Private SCP transfer

These Windows PowerShell commands are for the operator; they have not been executed
against production. Transfer only these three files:

```powershell
$releaseDir = 'D:\alfanar-learning-dashboard\data\final-release-20261108'
ssh root@72.62.133.105 'umask 077; mkdir -p /root/alfanar-final-upload-20261108; chmod 700 /root/alfanar-final-upload-20261108'
scp -- "$releaseDir\alfanar-dashboard-private-20261108.tar.gz" "$releaseDir\TRANSFER-SHA256SUMS" "$releaseDir\RELEASE_COMMIT" root@72.62.133.105:/root/alfanar-final-upload-20261108/
```

The archive includes the four verified workbooks, with the corrected 16-session
Operations file. The existing production import was confirmed as 16 sessions.
The baseline guard must verify its exact original observations and calendar before
deployment. No matrix re-import is needed if that check passes. If it fails, stop and
investigate the difference rather than overwrite production evidence. Preserve all
existing attendance directories and private timing caches.

## 2. Verify paths, pinned source and private payload

Start a root SSH shell and keep it for the following steps. Do not use `set -x`, print
environment values, or discard existing local source changes.

```bash
set -euo pipefail
umask 077
app=/var/www/alfanar-learning-dashboard
python="$app/backend/venv/bin/python"
env_file="$app/backend/.env"
db="$app/backend/db.sqlite3"
service=alfanar-gunicorn.service
upload=/root/alfanar-final-upload-20261108
test "$(id -u)" = 0
test -d "$app/.git" && test -x "$python"
test -f "$env_file" && test -f "$db"
test -f "$app/frontend/dist/index.html"
systemctl show "$service" -p LoadState -p WorkingDirectory -p EnvironmentFiles -p User -p Group --no-pager
test "$(systemctl show "$service" -p LoadState --value)" = loaded
test "$(systemctl show "$service" -p WorkingDirectory --value)" = "$app/backend"
test "$(systemctl show "$service" -p User --value)" = root
systemctl show "$service" -p EnvironmentFiles --value | grep -F -- "$env_file" >/dev/null

cd "$upload"
chmod 600 ./*
sha256sum -c TRANSFER-SHA256SUMS
release_commit=$(tr -d '\r\n' < RELEASE_COMMIT)
[[ "$release_commit" =~ ^[0-9a-f]{40}$ ]]
printf 'Requested commit; compare with delivery report: %s\n' "$release_commit"
git -C "$app" status --short
test -z "$(git -C "$app" status --porcelain)"
test "$(git -C "$app" branch --show-current)" = main
git -C "$app" fetch origin main
git -C "$app" cat-file -e "$release_commit^{commit}"
git -C "$app" merge-base --is-ancestor "$release_commit" origin/main
git -C "$app" merge-base --is-ancestor HEAD "$release_commit"

private="/var/lib/alfanar/releases/$release_commit"
test ! -e "$private"
install -d -m 700 "$private"
tar -tzf "$upload/alfanar-dashboard-private-20261108.tar.gz"
tar --no-same-owner -xzf "$upload/alfanar-dashboard-private-20261108.tar.gz" -C "$private"
find "$private" -type d -exec chmod 700 {} +
find "$private" -type f -exec chmod 600 {} +
cd "$private"
sha256sum -c SHA256SUMS
test "$(tr -d '\r\n' < RELEASE_COMMIT)" = "$release_commit"
```

Stop if paths, service account, branch or checksums differ; investigate the actual
configuration before proceeding. Do not reset the checkout or overwrite local changes.

## 3. Stop writers and back up

Use a maintenance window. Pause external cron/CI/manual LMS or attendance writers too.
The commands record active Alfanar units and later restore only those units; they do not
create a scheduler. Keep writers stopped until all import and acceptance checks pass.

```bash
stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup="/var/backups/alfanar/final-$stamp"
test ! -e "$backup"
install -d -m 700 "$backup"
printf '%s\n' "$backup" > "$private/LAST_BACKUP"
git -C "$app" rev-parse HEAD > "$backup/previous-commit"
systemctl list-timers --all --no-pager
systemctl list-units --all --type=service --type=timer --state=active --plain --no-legend 'alfanar*' | awk '{print $1}' > "$backup/active-units.txt"
mapfile -t active_units < "$backup/active-units.txt"
if ((${#active_units[@]})); then systemctl stop "${active_units[@]}"; fi
systemctl stop "$service"
test "$(systemctl is-active "$service" || true)" != active
cp -a "$env_file" "$backup/backend.env"
tar -czf "$backup/frontend-dist.tar.gz" -C "$app/frontend" dist
tar --exclude=alfanar-learning-dashboard/backend/venv \
    --exclude=alfanar-learning-dashboard/.venv \
    --exclude=alfanar-learning-dashboard/frontend/node_modules \
    -czf "$backup/installation.tar.gz" -C /var/www alfanar-learning-dashboard
"$python" - "$db" "$backup/database.sqlite3" <<'PY'
import pathlib, sqlite3, sys
src, dst = map(pathlib.Path, sys.argv[1:])
assert src.is_file() and not dst.exists()
a = sqlite3.connect(src.resolve().as_uri() + '?mode=ro', uri=True)
b = sqlite3.connect(dst)
a.backup(b)
assert b.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
b.close(); a.close()
print('SQLite backup integrity: ok')
PY
chmod 600 "$backup"/*
(cd "$backup" && sha256sum database.sqlite3 backend.env frontend-dist.tar.gz installation.tar.gz > SHA256SUMS && sha256sum -c SHA256SUMS)
```

Load the existing backend dotenv securely. Do not shell `source` it or copy the local
`.env` over it. This helper excludes the file-only dashboard-owner username/password.

```bash
run_app_python() {
  "$python" - "$env_file" "$app/backend" "$@" <<'PY'
import os, runpy, sys
from dotenv import dotenv_values
env_file, backend, script, *args = sys.argv[1:]
for key, value in dotenv_values(env_file).items():
    if value is not None and key.lower() not in {'username', 'password'}:
        os.environ[key] = value
os.chdir(backend)
sys.path.insert(0, backend)
sys.argv = [script, *args]
runpy.run_path(script, run_name='__main__')
PY
}
run_manage() { run_app_python "$app/backend/manage.py" "$@"; }
guard() { run_app_python "$private/release_guard.py" "$@"; }
guard snapshot --app "$app" --database "$db" \
  --expected "$private/expected-release.json" --output "$backup/before.json"
```

The baseline must pass before updating source. It checks 27 learners, 103 enrollments,
official lecture counts, exact original binary observations and 53 dated lectures,
and saves hashes of every non-timing analytics table. Verification permits the approved
effective-attendance changes while requiring all original attendance, academic and
identity tables to remain identical. No local database is transferred.

## 4. Source, environment and migration

```bash
git -C "$app" merge --ff-only "$release_commit"
test "$(git -C "$app" rev-parse HEAD)" = "$release_commit"
test -z "$(git -C "$app" status --porcelain)"
```

Add missing Graph settings through the existing secure server configuration process
to `backend/.env`; use `sudoedit "$env_file"` only if needed. Preserve database,
Google, authentication and HTTPS settings. Never put values in command arguments,
frontend variables, Git or the upload archive. Required names are:

```dotenv
MICROSOFT_GRAPH_TENANT_ID=
MICROSOFT_GRAPH_CLIENT_ID=
MICROSOFT_GRAPH_CLIENT_SECRET=
MICROSOFT_GRAPH_SCOPE=https://graph.microsoft.com/.default
MICROSOFT_GRAPH_BASE_URL=https://graph.microsoft.com/v1.0
```

Set these two non-secret values in the existing backend environment. The command
preserves other values; the original file is already backed up. The private path
selects the verified 16-session Operations workbook for future default imports.

```bash
"$python" - "$env_file" "$private/attendance" <<'PY'
import sys
from dotenv import set_key
set_key(sys.argv[1], 'ATTENDANCE_DIRECTORY', sys.argv[2], quote_mode='always')
set_key(sys.argv[1], 'ATTENDANCE_RECONCILIATION_MIN_MINUTES', '30', quote_mode='never')
PY
chmod 600 "$env_file"
```

Check for conflicting systemd environment overrides without printing secrets:

```bash
"$python" - "$service" <<'PY'
import shlex, subprocess, sys
values = shlex.split(subprocess.check_output(['systemctl', 'show', sys.argv[1], '-p', 'Environment', '--value'], text=True))
blocked = {'ATTENDANCE_DIRECTORY', 'ATTENDANCE_RECONCILIATION_MIN_MINUTES'}
assert not any(v.split('=', 1)[0] in blocked for v in values), 'Review conflicting attendance service overrides before proceeding.'
print('Attendance service override check: ok')
PY
```

The app needs admin-consented `OnlineMeetingArtifact.Read.All` and an application
access policy permitting the original organizers in the private policy. All six
approved series worked locally without additional permission. A meeting metadata 404
does not establish attendance failure. See Microsoft's
[attendance endpoint requirements](https://learn.microsoft.com/en-us/graph/api/meetingattendancereport-list?view=graph-rest-1.0)
and [application access policies](https://learn.microsoft.com/en-us/graph/cloud-communication-online-meeting-application-access-policy).

```bash
run_manage check
run_manage showmigrations analytics
# Existing application schema must be 0004 or 0005; investigate earlier pending migrations.
run_manage migrate analytics 0005 --plan
run_manage migrate analytics 0005 --noinput
run_manage collectstatic --noinput
```

Migration 0005 creates only timing tables. No package upgrade or Python dependency
installation is needed. Do not run `sync_alfanar` or `import_teams_attendance`.

## 5. Timing dry run, activation and unchanged repeat

```bash
run_manage import_graph_timing \
  --source "$private/PCP_Al_Fanar_Teams_Original_Data 3.xlsx" \
  --config "$private/timing-policy.json" --cache-dir "$private/cache" --dry-run \
  > "$backup/timing-dry-run.json"
guard result --expected "$private/expected-release.json" --input "$backup/timing-dry-run.json"

run_manage import_graph_timing \
  --source "$private/PCP_Al_Fanar_Teams_Original_Data 3.xlsx" \
  --config "$private/timing-policy.json" --cache-dir "$private/cache" \
  > "$backup/timing-import.json"
guard result --expected "$private/expected-release.json" --input "$backup/timing-import.json"
guard verify --app "$app" --database "$db" \
  --expected "$private/expected-release.json" --baseline "$backup/before.json"

run_manage import_graph_timing \
  --source "$private/PCP_Al_Fanar_Teams_Original_Data 3.xlsx" \
  --config "$private/timing-policy.json" --cache-dir "$private/cache" \
  > "$backup/timing-repeat.json"
guard result --expected "$private/expected-release.json" --input "$backup/timing-repeat.json" --repeat
guard verify --app "$app" --database "$db" \
  --expected "$private/expected-release.json" --baseline "$backup/before.json"
```

Every guard must print `PASS`. On failure, leave the service stopped and investigate
or roll back; do not force counts. Verification compares exact timing rows, timestamps
and durations as well as 1,048 visible pairs, 51 dates, 347 pending pairs, all original
non-timing tables, 160 corrections, 16 reviews, every learner decision and all final
module/overall KPIs and charts. The two dated missing timing lectures stay unavailable; the
five undated slots receive no fabricated dates. Repeat must return `changed=false`.
Dry run writes only private Graph evidence. Incomplete first-deployment Graph results
cannot pass the release checks; existing verified history is preserved on refresh.

Record the immutable private decision ledger after the successful import:

```bash
run_manage audit_attendance_reconciliation --output "$backup/attendance-decisions.json"
run_manage audit_attendance_reconciliation --output "$backup/attendance-decisions.json"
# The second ledger write must report changed=false.
```

## 6. Frontend and acceptance

```bash
(cd "$app/frontend" && npm ci && npm run build)
test -f "$app/frontend/dist/index.html"
nginx -t
systemctl start "$service"
systemctl is-active --quiet "$service"
systemctl status "$service" --no-pager
```

Sign in at the existing HTTPS dashboard. Check Learner Detail → Module → Live Sessions
in all modules, recovered dates, repeated joins, UTC labels and total duration. Check
recorded absence with qualifying timing and missing matrix observations: verified
30+ merged minutes must show Present, including the four original matrix absences.
Original absence labels and the 16 short-timing Review flags remain concise. Use
the verified learner examples in the private QA evidence. Risk 9 June and Operations
1 July timing fields must show `Not Available`; valid matrix attendance still applies. Do not
invent rows for the five undated slots. Confirm module order, 16/14/16/12 lectures and
removal of Overview attendance descriptions. Anonymous timing access stays forbidden.

| Module | Effective present / known | Attendance | Evidence coverage | Timing dates |
|---|---:|---:|---:|---:|
| Planning | 369 / 397 | 92.9% | 95.4% (397 / 416) | 16 / 16 official |
| Procurement Risk | 255 / 290 | 87.9% | 89.5% (290 / 324) | 11 / 14 official |
| Operations | 307 / 394 | 77.9% | 94.7% (394 / 416) | 15 / 16 official |
| Advanced | 149 / 149 | 100% of known observations | 69.0% (149 / 216) | 9 / 12 official |

Overall effective attendance is 1,080/1,230 = 87.8%, evidence coverage 89.7%. Original
binary records remain unchanged; only the approved reconciliation affects displayed
attendance. Mean LMS progress remains 38.8%, and the registry 27 learners/103 enrollments.
Keep the approved clean UI: no new explanatory paragraphs, warnings or banners;
detailed findings belong in private QA evidence. Keep the official 58
lectures distinct from 53 existing attendance dates and 51 dates with timing.
After acceptance, resume only previously active units and external writers:

```bash
if ((${#active_units[@]})); then systemctl start "${active_units[@]}"; fi
git -C "$app" rev-parse HEAD
```

Retain the private policy, workbook, immutable cache, evidence and matching backup.
Nothing private may be served over HTTP. Future refreshes use the dedicated Graph
importer; this release adds no automatic polling job.

## 7. Rollback

Stop all writers and restore the matching database, source, environment and frontend
together. Reverse migration alone is not a backup restore. These commands use the shell
variables above. After reconnecting, restore the variables and read the backup path
from `$private/LAST_BACKUP` before proceeding.

```bash
systemctl stop "$service"
mapfile -t active_units < "$backup/active-units.txt"
if ((${#active_units[@]})); then systemctl stop "${active_units[@]}"; fi
(cd "$backup" && sha256sum -c SHA256SUMS)
old_commit=$(tr -d '\r\n' < "$backup/previous-commit")
[[ "$old_commit" =~ ^[0-9a-f]{40}$ ]]
test -z "$(git -C "$app" status --porcelain)"
git -C "$app" switch --detach "$old_commit"
cp -a "$backup/backend.env" "$env_file"
test ! -e "$backup/failed-db"
install -d -m 700 "$backup/failed-db"
for file in "$db" "$db-wal" "$db-shm" "$db-journal"; do
  if test -e "$file"; then mv -- "$file" "$backup/failed-db/"; fi
done
cp "$backup/database.sqlite3" "$db"
chown --reference="$backup/failed-db/db.sqlite3" "$db"
chmod --reference="$backup/failed-db/db.sqlite3" "$db"
"$python" - "$db" <<'PY'
import pathlib, sqlite3, sys
c = sqlite3.connect(pathlib.Path(sys.argv[1]).resolve().as_uri() + '?mode=ro', uri=True)
assert c.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
c.close()
print('Restored SQLite integrity: ok')
PY
test ! -e "$backup/failed-dist"
if test -d "$app/frontend/dist"; then mv -- "$app/frontend/dist" "$backup/failed-dist"; fi
tar -xzf "$backup/frontend-dist.tar.gz" -C "$app/frontend"
run_manage check
run_manage collectstatic --noinput
guard snapshot --app "$app" --database "$db" \
  --expected "$private/expected-release.json" --output "$backup/restored.json"
cmp "$backup/before.json" "$backup/restored.json"
nginx -t
systemctl start "$service"
systemctl is-active --quiet "$service"
if ((${#active_units[@]})); then systemctl start "${active_units[@]}"; fi
```

Keep failed artifacts private for diagnosis. Rollback leaves the checkout on the verified
old detached commit; do not switch to `main` until the failed release is resolved. Retain
the installation archive and SQLite backup under the existing retention policy.

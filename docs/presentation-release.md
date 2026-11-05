# Presentation release: Ubuntu VPS procedure

This procedure is for the production operator. Preparing and pushing the verified Git release does not deploy it. Keep database backups, workbooks, audit reports and credentials outside Git and outside nginx's web root. Preserve existing authentication and Google credentials.

The final [presentation release check](release-check.md) documents the visible attendance scope and focused validation. Use the refreshed source archive and matching checksum containing those frontend changes.

## 1. Prepare a source-only release locally

The approved application source, migration, tests and documentation are committed to GitHub `main`. Use the exact full commit hash in the delivery report; do not deploy a later unreviewed commit. The local source-only archive `data/presentation-source.tar.gz` is exported from that verified commit after a Git security check. It includes lockfiles and `.env.example` and excludes `.env`, secrets, source workbooks, databases, reports, screenshots, dependencies and build output. Its checksum is in `data/release-SHA256SUMS`; the four workbooks have a separate `data/attendance-SHA256SUMS`. Verify archive contents with `tar -tzf data/presentation-source.tar.gz` before transfer.

**Git deployment:** application source and documentation come from the verified Git commit. **Private SCP transfer:** the four attendance workbooks and checksum manifests remain outside Git. The source-only archive may also be transferred as a verified fallback/reference, but do not extract it over a Git-managed installation. Existing production credentials and database files stay on the server.

Run local validation first:

```powershell
.\.venv\Scripts\python backend/manage.py test analytics --noinput
Push-Location frontend
npm run build
Pop-Location
.\.venv\Scripts\python backend/manage.py audit_dashboard
```

## 2. Transfer files over SSH

Replace `deployuser@vps` with the authorized SSH destination. Authenticate using SSH keys; do not copy `.env`, database files or Google credentials. Create a private temporary upload directory:

```powershell
ssh deployuser@vps "umask 077; mkdir -p ~/alfanar-presentation-upload"
scp -- "data/presentation-source.tar.gz" "data/release-SHA256SUMS" "data/attendance-SHA256SUMS" "data/Advanced Project and Logistics Management.xlsx" "data/Andrew Operations and Information Management for Project Managers.xlsx" "data/PCP Al-Fanar 2025.xlsx" "data/Risk Management.xlsx" deployuser@vps:alfanar-presentation-upload/
```

In an SSH shell on Ubuntu, verify the encrypted transfer and protect the upload:

```bash
cd ~/alfanar-presentation-upload
chmod 700 .
chmod 600 ./*
sha256sum -c release-SHA256SUMS
sha256sum -c attendance-SHA256SUMS
```

## 3. Stop writers and back up the existing installation

The commands assume the existing service user, paths and PostgreSQL database from the repository templates. Adjust the database name if the current installation differs. For managed/remote PostgreSQL, use its existing secured backup mechanism instead of local peer authentication. Never pass a database password on the command line.

```bash
set -euo pipefail
app=/opt/alfanar-learning-dashboard
stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup=/var/backups/alfanar/$stamp
sudo systemctl stop alfanar-sync alfanar-api
sudo install -d -m 700 "$backup"
sudo tar --exclude=.venv --exclude=frontend/node_modules -czf "$backup/installation.tar.gz" -C /opt alfanar-learning-dashboard
sudo cp -a /etc/alfanar-dashboard/env "$backup/env"
sudo chmod 600 "$backup/env" "$backup/installation.tar.gz"
set -o pipefail
sudo -u postgres pg_dump -Fc alfanar_dashboard | sudo tee "$backup/database.dump" >/dev/null
sudo chmod 600 "$backup/database.dump"
sudo pg_restore --list "$backup/database.dump" >/dev/null
```

If production uses SQLite instead, replace the `pg_dump`/`pg_restore --list` lines with SQLite's online backup command (the services are already stopped): `sudo sqlite3 "$app/backend/db.sqlite3" ".backup '$backup/database.sqlite3'"` and `sudo sqlite3 "$backup/database.sqlite3" 'PRAGMA integrity_check;'`. Require `ok`. Keep backups private; the installation archive may contain older private data and credentials.

## 4. Install source and private attendance files

Set `release_commit` to the exact full hash supplied in the delivery report. These commands require the existing checkout to be clean and `main` to fast-forward to that release; stop and investigate any mismatch or local source changes. Do not reset or overwrite local changes.

```bash
release_commit=REPLACE_WITH_FULL_VERIFIED_COMMIT_HASH
sudo -u alfanar git -C "$app" status --short
sudo -u alfanar git -C "$app" diff --quiet
sudo -u alfanar git -C "$app" diff --cached --quiet
sudo -u alfanar git -C "$app" fetch origin main
test "$(sudo -u alfanar git -C "$app" rev-parse origin/main)" = "$release_commit"
sudo -u alfanar git -C "$app" switch main
sudo -u alfanar git -C "$app" merge --ff-only "$release_commit"
test "$(sudo -u alfanar git -C "$app" rev-parse HEAD)" = "$release_commit"
sudo install -d -m 700 -o alfanar -g alfanar /var/lib/alfanar/attendance
for file in \
  "Advanced Project and Logistics Management.xlsx" \
  "Andrew Operations and Information Management for Project Managers.xlsx" \
  "PCP Al-Fanar 2025.xlsx" \
  "Risk Management.xlsx"; do
  sudo install -m 600 -o alfanar -g alfanar "$HOME/alfanar-presentation-upload/$file" "/var/lib/alfanar/attendance/$file"
done
sudoedit /etc/alfanar-dashboard/env
```

Set `ATTENDANCE_DIRECTORY=/var/lib/alfanar/attendance`. Retain existing database, authentication, HTTPS and read-only Google configuration. Do not point at the old combined export. If a verified alias CSV already exists, preserve its secure path. Keep the directory inaccessible to the web server.

Use a transient systemd unit to load the existing environment without printing credentials or shell-parsing `.env` values:

```bash
manage() {
  sudo systemd-run --quiet --wait --pipe --collect --uid=alfanar \
    --property=WorkingDirectory="$app/backend" \
    --property=EnvironmentFile=/etc/alfanar-dashboard/env \
    "$app/.venv/bin/python" manage.py "$@"
}
sudo -u alfanar "$app/.venv/bin/pip" install -r "$app/backend/requirements-production.txt"
manage migrate --noinput
manage check --deploy
manage collectstatic --noinput
manage sync_alfanar --source google
manage import_teams_attendance
manage audit_dashboard --output /var/lib/alfanar/attendance/reconciliation.json
manage sync_alfanar --source google
manage import_teams_attendance
```

Require successful sync/import and zero audit discrepancies. If the live source did not change, the repeated sync and attendance import must report `changed=False`. Source changes can legitimately alter the current totals; investigate rather than forcing the historical 28/104 baseline. Compare all four source hashes again after import. Run backend tests in a disposable test database before reopening; never grant the production role unnecessary test database privileges solely for this check.

```bash
cd "$app/frontend"
sudo -u alfanar npm ci
sudo -u alfanar npm run build
sudo systemctl start alfanar-api
sudo systemctl start alfanar-sync
sudo systemctl status alfanar-api alfanar-sync --no-pager
sudo nginx -t
```

No nginx configuration change is required. Keep exactly one scheduler. Sign in through HTTPS and check Executive Overview, module cards, Active in LMS filters, learner directory, all three canonical profiles, activity matrices and tooltips. Advanced must show 12 owner-confirmed lectures alongside nine attendance sessions; unknown attendance and durations must show Not Available. Check authenticated `/api/sync/status`; anonymous learner access must remain forbidden.

## 5. Rollback if validation fails

Keep both services stopped. Restore the installation archive and private environment from the backup; restore the matching database backup using the operator's established PostgreSQL recovery procedure (or SQLite backup while stopped). Code and database must roll back together because this release adds schema fields and evidence tables. Restore previous attendance files/configuration too. Restart the two services only after checking the restored installation. Do not fall back to an older LMS workbook to hide a Google sync failure.

After success, remove the temporary upload directory using its verified literal path, retain the private backup per the existing retention policy, and retain the source files needed for future imports. Never serve uploads, reports or backups over HTTP.

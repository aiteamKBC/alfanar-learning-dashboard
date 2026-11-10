# Isolated Lecture Library storage

Executive Overview, Learners, authentication, sessions, identities, LMS records,
attendance and existing automation retain SQLite. Only the independent
`lecture_library` app uses PostgreSQL. `DATABASE_URL` cannot select Django's
`default` database.

## Configuration

Set privately in `/var/www/alfanar-learning-dashboard/backend/.env`, the service
environment file:

```dotenv
DASHBOARD_SQLITE_PATH=/var/www/alfanar-learning-dashboard/backend/db.sqlite3
LECTURE_LIBRARY_DATABASE_URL=postgresql://USER:PASSWORD@HOST/DATABASE?sslmode=require
LECTURE_LIBRARY_DATABASE_SCHEMA=alfanar_lecture_library
```

The SQLite default is `backend/db.sqlite3`; relative overrides resolve from the
repository root. Legacy PostgreSQL `DATABASE_URL` is accepted ONLY as the library
URL when the explicit library URL is unset. Set the explicit URL for deployment.
Legacy `DATABASE_SCHEMA` does not control either database. The library rejects
`public` and `alfanar_dashboard` schemas. Its schema must be dedicated.
Without a library URL, library endpoints return HTTP 503; dashboard endpoints
remain available. SSL URL options are retained. Neon pooler hosts use the
corresponding direct endpoint for the schema-specific session connection.

## Relations and migrations

PostgreSQL holds `lecture_library_lecturecapture`,
`lecture_library_lecturelearner`, `lecture_library_lectureresource` and its own
`django_migrations` ledger. The independent `lecture_library.0001_initial`
migration has no analytics dependency. Authentication stays in SQLite.

Library-internal foreign keys stay in PostgreSQL. Course/learner references are
stable LMS source IDs, resolved against the current SQLite registry in application
code. There are no cross-database foreign keys or SQL joins.

`analytics.0012_retire_legacy_lecture_state` removes the legacy library models
from Django's analytics state. If their SQLite tables exist, it removes only
foreign-key constraints to the live course/learner registry. SQLite may rebuild
these tables to remove constraints, retaining every column name and value.
Internal archive relations remain. This prevents archived library evidence from
blocking a later approved identity deletion. Backup and row verification are
mandatory. No table is dropped, no SQLite row is transferred, and no dashboard
calculation is changed.

`transfer_sqlite_to_postgres` is disabled in all modes. Do not dump/load the
dashboard into PostgreSQL. The new library starts empty; import only an approved
private library evidence JSON with `import_lecture_library`. Legacy SQLite rows
remain archived; old library UUID URLs are not automatically transferred.
LMS, attendance and Teams timing re-imports are not required.

## Production deployment (operator only)

Use a maintenance window without in-flight imports, syncs or identity edits and
without scheduled jobs due during the window. Do not reconfigure or restart
Teams synchronization. Confirm the existing service uses
`backend/venv/bin/python`; adjust the variable to its actual ExecStart interpreter
if needed. Replace `REVIEWED_RELEASE_COMMIT` with the verified hash.

```bash
set -euo pipefail
umask 077
app=/var/www/alfanar-learning-dashboard
python="$app/backend/venv/bin/python"
env_file="$app/backend/.env"
service=alfanar-gunicorn.service
commit=REVIEWED_RELEASE_COMMIT
backup="/var/lib/alfanar-backups/database-isolation-$(date -u +%Y%m%dT%H%M%SZ)"
cd "$app"
test -z "$(git status --porcelain)"
test -f backend/db.sqlite3
test -x "$python"
test -f "$env_file"
mkdir -m 700 -p "$backup"
git rev-parse HEAD > "$backup/commit"
cp -p "$env_file" "$backup/backend.env"
tar -czf "$backup/frontend-dist.tar.gz" -C frontend dist
git fetch origin main
git merge-base --is-ancestor "$commit" origin/main
systemctl stop "$service"
"$python" - "$app/backend/db.sqlite3" "$backup/db.sqlite3" <<'PY'
import sqlite3, sys
from pathlib import Path
source, destination = map(Path, sys.argv[1:])
assert source.is_file() and not destination.exists()
with sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True) as src:
    with sqlite3.connect(destination) as dst:
        src.backup(dst)
        assert dst.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
PY
sha256sum "$backup/db.sqlite3" "$backup/backend.env" "$backup/frontend-dist.tar.gz" > "$backup/SHA256SUMS"
git switch --detach "$commit"
"$python" -m pip install -r backend/requirements-production.txt
```

Privately edit `backend/.env` to set the three keys above, preserving other
settings. The PostgreSQL role must be allowed to create/own the dedicated schema
and tables. Do not print the URL or source dotenv as shell code. Use this helper
so commands read the same environment file as Gunicorn:

```bash
manage() { "$python" "$app/deploy/manage_with_env.py" --env-file "$env_file" "$@"; }
manage verify_database_isolation --output "$backup/sqlite-before.json"
manage prepare_lecture_database
manage migrate --database=default --plan
```

Review the plan before continuing: analytics 0012 and its recorded historical
dependencies are expected. Unexpected pending migrations need separate review.

```bash
manage migrate --database=default --noinput
manage prepare_lecture_database --apply
manage prepare_lecture_database --verify-only
manage verify_database_isolation --compare "$backup/sqlite-before.json" --library
manage check --deploy
npm --prefix frontend ci
npm --prefix frontend run build
manage collectstatic --noinput
```

Preparation refuses schemas with unrelated tables, serializes preparation and
applies only the independent library migration in a PostgreSQL transaction.
Repeated preparation is safe. A manifest mismatch is a stop condition.

Optional: import the approved private library evidence JSON using its actual
server path. Without an import the new library is empty. This does not import
dashboard, LMS or attendance records.

```bash
lecture_source=/PRIVATE/PATH/approved-lecture-evidence.json
test -f "$lecture_source"
manage import_lecture_library --source "$lecture_source"
manage verify_database_isolation --compare "$backup/sqlite-before.json" --library
```

Then start only the application service:

```bash
systemctl start "$service"
systemctl is-active "$service"
```

Sign in with the existing owner account. Verify Overview, Learners, exports and
learner details against pre-deployment values, directory scrolling, and library
list/detail access. Do not restore identities, create accounts, reset passwords,
rerun attendance imports or activate automation for this change.

## Rollback

Stop the app and restore the previous code, environment and frontend. Before
restarting old code, explicitly pin its `DATABASE_URL` to SQLite and clear
`DATABASE_SCHEMA`; the previous settings otherwise redirect the dashboard to
PostgreSQL. Keep the private backup and new library schema. Migration 0012 retains
old columns and values, allowing old code to read the archived SQLite library.

```bash
systemctl stop "$service"
git switch --detach "$(cat "$backup/commit")"
cp -p "$backup/backend.env" "$env_file"
"$python" - "$env_file" "$app/backend/db.sqlite3" <<'PY'
import sys
from dotenv import set_key
set_key(sys.argv[1], 'DATABASE_URL', 'sqlite:///' + sys.argv[2])
set_key(sys.argv[1], 'DATABASE_SCHEMA', '')
PY
tar -xzf "$backup/frontend-dist.tar.gz" -C frontend
"$python" -m pip install -r backend/requirements-production.txt
# Run the prior release's collectstatic procedure if it serves collected assets.
systemctl start "$service"
systemctl is-active "$service"
```

Do not automatically restore the SQLite backup: that could discard later writes.
If row verification failed during maintenance, investigate before restarting;
restore only under an explicit recovery decision. Do not drop the new PostgreSQL
library schema or transfer it back to SQLite.

## Local validation

Use `--settings=config.test_settings` for synthetic two-database tests; this
configuration cannot connect to remote URLs. Rehearse with a disposable local
PostgreSQL instance and a backup copy of SQLite. Check table inventories,
cross-database relations, legacy archive preservation, repeated imports and
unchanged dashboard responses.

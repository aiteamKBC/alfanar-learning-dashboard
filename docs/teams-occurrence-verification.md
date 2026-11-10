# Verify an already discovered Teams lecture

New lectures are canonical `TeamsOccurrence` rows, with merged intervals in
`TeamsOccurrenceAttendance`. `AttendanceIndex` exposes each verified occurrence
as `auto:<id>` in the existing reporting pipeline. It does not require duplicate
`LectureSession`, `SessionAttendance` or `LectureTiming` records. Those tables
retain historical evidence. Backfilling a discovered occurrence there can count
the lecture or duration twice and change the historical discovery baseline.

The directory uses the approved historical count plus distinct verified,
post-cutover occurrences. Presence requires the approved 30-minute union of
intervals for an eligible, projected learner identity. Short visits remain review
evidence; missing timing remains unknown. A run ending `partial` commits individually
verified occurrences and preserves unrelated unmatched reports for review. It is
not a reason to discard the verified lecture or classify all timing rows as Present.

## Read-only verification

Use `audit_teams_occurrence --occurrence-id <id>` with the same environment as
Gunicorn. The command reads only the default SQLite database, makes no Graph calls
and writes no records. Output contains aggregate counts, the database path and
reporting validation issues, without learner names, emails or private meeting IDs.
It checks stored interval unions, current eligible identities, canonical inclusion,
the approved attendance threshold and directory denominators. It is a reporting
diagnostic for an already verified occurrence, not permission to approve a meeting
or a replacement for the source-validation gates in discovery.

An empty query against historical tables does not establish a missing lecture.
If this check passes but the web application disagrees, verify its deployed commit,
Gunicorn working directory/environment and default SQLite path. A stale process or
different database must be demonstrated, not presumed. Do not repair the discrepancy
by editing attendance records. The directory CSV reads the same `/api/learners`
All Modules values and needs no separate attendance import.

## Operator deployment (not executed by development validation)

This change adds a read-only diagnostic and regression coverage; it changes no
attendance calculations, database schema or dependencies. Set `release_commit` to
the reported verified commit. Run in one shell on the application server:

```bash
set -euo pipefail
umask 077
app=/var/www/alfanar-learning-dashboard
python="$app/backend/venv/bin/python"
cd "$app"
test -n "${release_commit:?Set the verified release commit first}"
test "$(git branch --show-current)" = main
test -z "$(git status --porcelain)"
git fetch origin main
git merge-base --is-ancestor HEAD "$release_commit"
git merge-base --is-ancestor "$release_commit" origin/main

backup="/var/backups/alfanar/occurrence-check-$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 700 "$backup"
git rev-parse HEAD > "$backup/previous-commit"
tar -czf "$backup/frontend-dist.tar.gz" -C frontend dist
"$python" - "$backup/db.sqlite3" <<'PY'
import pathlib, sqlite3, sys
source = pathlib.Path('backend/db.sqlite3').resolve()
target = pathlib.Path(sys.argv[1])
assert source.is_file() and not target.exists()
a = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)
b = sqlite3.connect(target)
a.backup(b)
assert b.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
b.close(); a.close()
PY
(cd "$backup" && sha256sum db.sqlite3 frontend-dist.tar.gz > SHA256SUMS && sha256sum -c SHA256SUMS)
git merge --ff-only "$release_commit"
test "$(git rev-parse HEAD)" = "$release_commit"

run_manage() {
  "$python" - "$@" <<'PY'
import os, pathlib, runpy, sys
from dotenv import dotenv_values
app = pathlib.Path('/var/www/alfanar-learning-dashboard')
for key, value in dotenv_values(app / 'backend/.env').items():
    if value is not None and key.lower() not in {'username', 'password'}:
        os.environ[key] = value
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
os.chdir(app / 'backend'); sys.path.insert(0, str(app / 'backend'))
import django
django.setup()
from django.conf import settings
db = settings.DATABASES['default']
assert db['ENGINE'] == 'django.db.backends.sqlite3'
assert pathlib.Path(db['NAME']).resolve() == app / 'backend/db.sqlite3'
sys.argv = ['manage.py', *sys.argv[1:]]
runpy.run_path('manage.py', run_name='__main__')
PY
}
run_manage check
run_manage migrate --check --database default
# Stop if migrations are pending; follow the existing isolation runbook separately.
run_manage audit_teams_occurrence --occurrence-id 1
(cd frontend && npm ci && npm run build)
systemctl restart alfanar-gunicorn
systemctl is-active --quiet alfanar-gunicorn
run_manage audit_teams_occurrence --occurrence-id 1
```

For the November 9 evidence independently verified locally, the expected audit is
Advanced (154268), 16 timing pairs, 16 Present, 0 short visits and 9 unknown among
25 enrollees; the official Advanced total becomes 13 (12 historical + 1 new).
Verify these against the actual production identity state and evidence, rather
than overwriting any values if they differ. All-modules denominators rise by one
only for Advanced enrollees. Numerators rise by one only for qualifying learners.
Check the same learner in Live Sessions, the directory and the one-click CSV.

No Excel transfer, Graph sync, historical attendance re-import, Google Sheets
publication, PostgreSQL migration or scheduler change is required for an occurrence
already persisted and verified. The operator should investigate unrelated partial-run
diagnostics separately, preserving their pending evidence.

If application rollback is necessary, use the recorded previous source commit and
frontend artifact, then restart Gunicorn. This release has no data writes, so do not
overwrite a live database with the backup and lose subsequent legitimate syncs:

```bash
test -z "$(git status --porcelain)"
(cd "$backup" && sha256sum -c SHA256SUMS)
git switch --detach "$(cat "$backup/previous-commit")"
test ! -e "$backup/failed-dist"
mv frontend/dist "$backup/failed-dist"
tar -xzf "$backup/frontend-dist.tar.gz" -C frontend
systemctl restart alfanar-gunicorn
systemctl is-active --quiet alfanar-gunicorn
```

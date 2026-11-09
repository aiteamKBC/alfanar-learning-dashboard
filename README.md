# Alfanar Learning Dashboard

Learning analytics dashboard for Kent Business College. It is built with Django REST Framework, Vue 3, TypeScript, Vite, Tailwind and ECharts.

The primary LMS source is read-only Google Sheets; `data/Alfanar Endpoint.xlsx` is an older local snapshot. Attendance comes from four separate owner-supplied module workbooks. See [docs/data-mapping.md](docs/data-mapping.md) for definitions and [docs/attendance.md](docs/attendance.md) for evidence coverage.

## Prerequisites (Windows)

- Python 3.12+ (available as `py`)
- Node.js 20+
- The workbook at `data\Alfanar Endpoint.xlsx`. It is git-ignored and never modified.

## First-time setup (PowerShell, from the project root)

```powershell
py -m venv .venv
.\.venv\Scripts\python -m pip install -r backend\requirements.txt
Copy-Item .env.example .env        # then set DJANGO_SECRET_KEY in .env
cd backend
..\.venv\Scripts\python manage.py migrate
..\.venv\Scripts\python manage.py sync_alfanar --source excel
..\.venv\Scripts\python manage.py dashboard_user add <username> --set-password   # dashboard-only login
cd ..\frontend
npm install
```

## Run locally (two terminals)

```powershell
# Terminal 1: API on http://127.0.0.1:8000
cd backend; ..\.venv\Scripts\python manage.py runserver 127.0.0.1:8000

# Terminal 2: UI on http://127.0.0.1:5173 (proxies /api to Django)
cd frontend; npm run dev
```

Open http://127.0.0.1:5173 and sign in with a dashboard account.

Keep Django's default auto-reloader enabled during local development. A backend
started with `--noreload` continues serving its previously loaded Python code after
a pull or edit, even when Vite shows the new frontend. Restart that local backend
using the command above when API fields are missing. Rebuilding the frontend alone
does not update the running API. No data import or migration is needed for this
runtime mismatch. Check the actual API on port 8000 and the proxied browser view,
not only an isolated test server.

## Dashboard accounts

Dashboard access requires an authenticated user with an active dashboard grant. Dashboard-only users are never staff or superusers, so they cannot use Django Admin. Passwords are entered through a hidden prompt and never passed as command-line arguments.

```powershell
cd backend
..\.venv\Scripts\python manage.py dashboard_user add owner_review --temporary --days 14 --set-password
..\.venv\Scripts\python manage.py dashboard_user set-password owner_review
..\.venv\Scripts\python manage.py dashboard_user add jane@company.com --email jane@company.com --set-password
..\.venv\Scripts\python manage.py dashboard_user disable owner_review
..\.venv\Scripts\python manage.py dashboard_user list
```

Temporary grants expire automatically. `disable` also ends the user's active sessions.

**Owner account from `.env`:** put `username=` and `password=` (lowercase) in the root `.env`, then run:

```powershell
cd backend; ..\.venv\Scripts\python manage.py provision_owner
```

This creates or updates an active, dashboard-only, temporary (14-day) account and stores only the password hash. It runs only when you invoke it; nothing reads these keys on requests or syncs. Re-running it with an unchanged password keeps existing sessions signed in. Changing the password in `.env` and re-running updates it, which signs out that account's existing sessions.

## Tests and build

```powershell
cd backend;  ..\.venv\Scripts\python manage.py test analytics
cd frontend; npm run build
```

Tests use a synthetic workbook. If the real workbook is present, they also reconcile its totals against figures computed independently with openpyxl and check that the file's hash is unchanged.

## Data synchronisation

```powershell
# One-off import (idempotent: re-running makes zero writes when the source is unchanged)
..\.venv\Scripts\python manage.py sync_alfanar --source excel

# Google Sheets (read-only scope), every 5 minutes, foreground loop
..\.venv\Scripts\python manage.py run_sync_scheduler --source google --interval 300
```

- Each sync reads, validates and then loads inside one database transaction. If the read or validation fails, the last good dataset stays in place and the failure is recorded in `SyncRun` (see `GET /api/sync/status`).
- **Google setup:** share the spreadsheet with the service account as **Viewer**. Keep the JSON key in `secrets/` (git-ignored) or outside the repository, then set `GOOGLE_SHEETS_ENABLED=true`, `GOOGLE_SHEETS_SPREADSHEET_ID` (alias `GOOGLE_SHEETS_ID`) and `GOOGLE_SERVICE_ACCOUNT_FILE` in `.env`. The only scope requested is `spreadsheets.readonly`, and the code never calls any write API.
- The scheduler holds an exclusive lock, so a second instance refuses to start. In production, run it as the single `deploy/alfanar-sync.service` unit.

## Microsoft Teams attendance

This is a separate, read-only source. The LMS/Google sync never changes it. All four authoritative module workbooks must be present in `ATTENDANCE_DIRECTORY` (default `data/`). The previous combined export is comparison-only. Refresh and independently verify with:

```powershell
cd backend; ..\.venv\Scripts\python manage.py import_teams_attendance
..\.venv\Scripts\python manage.py audit_dashboard
```

Matching rules, module mappings, the delivered-session rule and known caveats are documented in [docs/attendance.md](docs/attendance.md).

## API (all require an authenticated session)

| Endpoint | Purpose |
|---|---|
| `GET /api/dashboard/summary?course=&engagement=` | KPI cards with denominators and definitions |
| `GET /api/dashboard/charts?course=&engagement=&activity_type=` | Chart datasets and the learner × module matrix |
| `GET /api/filters` | Modules, activity types, learner status options |
| `GET /api/learners?search=&course=&engagement=&ordering=&page=&page_size=` | Learner directory |
| `GET /api/learners/{id}` and `/courses` | Learner profile and per-module progress |
| `GET /api/learners/{id}/courses/{course_id}/matrix` | Activity matrix with footer totals |
| `GET /api/sync/status` | Last run, last successful sync and source timestamps |
| `GET /api/auth/session`, `POST /api/auth/login`, `POST /api/auth/logout` | Session authentication |

No endpoint changes learner data.

## Production

See [docs/presentation-release.md](docs/presentation-release.md) for this release's secure workbook transfer, backup, migration, validation and rollback steps. [docs/deployment.md](docs/deployment.md) covers general installation and accounts; [deploy/](deploy) contains systemd and nginx templates.

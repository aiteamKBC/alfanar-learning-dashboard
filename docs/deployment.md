# Deployment and access guide (company VPS, Ubuntu)

For the attendance and identity presentation release, follow the complete [presentation release procedure](presentation-release.md), including source packaging, encrypted workbook transfer, checksums, database backup, migration, independent audit and rollback.

Templates are in [`deploy/`](../deploy). Nothing in this repository changes production by itself. Every step below has to be run deliberately by an authorised operator.

## 1. Pre-deployment checklist

| Check | Required state | How to verify |
|---|---|---|
| HTTPS | A valid certificate, and HTTP redirects to HTTPS | `curl -I http://<domain>` returns 301 to https; open `https://<domain>` |
| `DJANGO_DEBUG` | `false` | `manage.py check --deploy` shows no W018 |
| `DJANGO_SECRET_KEY` | Long and random, different from dev | Startup fails if it is missing while DEBUG is false (by design) |
| `DJANGO_ALLOWED_HOSTS` | Exactly the dashboard domain | Requests with another Host header get a 400 |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://<domain>` | Sign-in works through the browser |
| `DJANGO_SECURE_COOKIES` | `true` (secure session/CSRF cookies, SSL redirect, HSTS) | Cookies carry the `Secure` flag in browser dev tools |
| `DJANGO_BEHIND_PROXY` | `true` only behind nginx, which sets `X-Forwarded-Proto` | No redirect loop |
| API authentication | Anonymous requests to `/api/learners` get 403 | `curl -s -o /dev/null -w '%{http_code}' https://<domain>/api/learners` returns `403` |
| Databases | Existing SQLite remains `default`; only Lecture Library uses its dedicated PostgreSQL alias/schema | Follow [database isolation](postgresql-storage.md); never transfer dashboard data |
| Google credentials | JSON key at `/etc/alfanar-dashboard/google-sa.json`, owned by `alfanar`, mode `600`, **not** in the repo or web root | `ls -l /etc/alfanar-dashboard/` |
| Frontend | `npm ci && npm run build` → nginx serves `frontend/dist`; SPA fallback to `index.html` | Deep link `https://<domain>/learners/<learner-id>` loads |
| Admin static | `manage.py collectstatic` → `/static/` | `/admin/login/` is styled |
| Exactly one scheduler | `alfanar-sync.service` enabled on **one** host; no cron job also calling `sync_alfanar` | `systemctl status alfanar-sync`; `crontab -l` for all users; the lock file rejects a second copy |
| Restart/recovery | Both units `Restart=on-failure` and `enable`d; a failed sync keeps the last good dataset | `systemctl kill alfanar-sync` → it restarts; `/api/sync/status` |

Do not assume the deployment works just because the domain opens. Run every row above against the live host.

## 2. Install (first time, after authorisation)

```bash
sudo adduser --system --group --home /opt/alfanar-learning-dashboard alfanar
sudo -u alfanar git clone <private-repo-url> /opt/alfanar-learning-dashboard
cd /opt/alfanar-learning-dashboard
sudo -u alfanar python3 -m venv .venv
sudo -u alfanar .venv/bin/pip install -r backend/requirements-production.txt
sudo install -d -m 750 -o alfanar -g alfanar /etc/alfanar-dashboard
sudo install -m 600 -o alfanar -g alfanar /path/to/env /etc/alfanar-dashboard/env               # see .env.example
sudo install -m 600 -o alfanar -g alfanar /path/to/google-sa.json /etc/alfanar-dashboard/google-sa.json
cd backend
sudo -u alfanar env $(sudo cat /etc/alfanar-dashboard/env | xargs) ../.venv/bin/python manage.py migrate
sudo -u alfanar env $(sudo cat /etc/alfanar-dashboard/env | xargs) ../.venv/bin/python manage.py collectstatic --noinput
sudo -u alfanar env $(sudo cat /etc/alfanar-dashboard/env | xargs) ../.venv/bin/python manage.py sync_alfanar --source google
cd ../frontend && npm ci && npm run build
sudo cp ../deploy/*.service /etc/systemd/system/ && sudo systemctl daemon-reload
sudo systemctl enable --now alfanar-api alfanar-sync
sudo cp ../deploy/nginx-alfanar.conf /etc/nginx/sites-available/alfanar   # edit the domain first
sudo ln -s /etc/nginx/sites-available/alfanar /etc/nginx/sites-enabled/ && sudo nginx -t && sudo systemctl reload nginx
```

Set these in the production env file: `GOOGLE_SHEETS_ENABLED=true`, `GOOGLE_SHEETS_SPREADSHEET_ID=…` and `GOOGLE_SERVICE_ACCOUNT_FILE=/etc/alfanar-dashboard/google-sa.json`. The service account only needs **Viewer** access on the spreadsheet.

## 3. Dashboard accounts

Account types:
- **Dashboard-only users:** never staff and never superuser. They can use the Executive Overview and Learners pages, and have no Django Admin access.
- **Staff/superusers:** used only for administration.

All passwords are entered through a hidden prompt, or through stdin for automation. They are never passed on the command line.

Run these from `backend/` on the target host. In production, prefix each command with the env file, as in section 2.

```bash
# Temporary owner-review account (expires after 14 days)
python manage.py dashboard_user add owner_review --name "Owner Review" --temporary --days 14 --set-password

# Reset its password, or extend it (re-running "add" refreshes the expiry)
python manage.py dashboard_user set-password owner_review

# When authorised emails arrive: one permanent account per approved address
python manage.py dashboard_user add jane.doe@company.com --email jane.doe@company.com --name "Jane Doe" --set-password

# Then remove the temporary account (deactivates it, revokes access, ends its sessions)
python manage.py dashboard_user disable owner_review

python manage.py dashboard_user list
```

On Windows (local dev), replace `python` with `..\.venv\Scripts\python`.

## 4. Operations

- **Health:** `GET /api/sync/status` (authenticated), or the status panel on the Executive Overview.
- **Logs:** `journalctl -u alfanar-sync -f` and `journalctl -u alfanar-api -f`.
- **Recovery:** a failed read or validation never changes data. Fix the cause (credentials, sharing, sheet structure) and the next 5-minute cycle recovers on its own.

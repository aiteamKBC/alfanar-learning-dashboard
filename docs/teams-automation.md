# Automated Teams lecture discovery and attendance

This release extends the verified historical release with migration 0006 and one
Teams scheduler. Source publication is authorized after technical checks pass.
Production activation is a separate operator action and requires credential rotation
and successful production preflight. No production access or deployment is performed
as part of local implementation/testing. The historical package in the existing
presentation runbook does not contain this migration or scheduler.

## Data contract and delivery gates

Migration `0006_teams_discovery` adds separate private discovery, occurrence, report
revision, learner timing and run/checkpoint tables. It does not backfill or modify
the 58 owner-confirmed historical lectures, 53 dated matrix lectures, 1,048 verified
historical timing pairs, 160 historical corrections or 16 existing review flags.
Original matrices, timing imports, identities, enrollments and academic tables stay
untouched. Daily synchronization never reads or imports an Excel workbook.

An explicit UTC `automation.cutover_at` in the private configuration separates the
verified historical baseline from new delivery. It is persisted at initialization
and cannot be changed silently. No pre-cutover report creates a new lecture or fills
an undated historical slot. Backfill across this boundary requires a separate review.

Every run processes all four stable course IDs: 83334, 121642, 135945 and 154268.
Known series retain their approved course and organizer. New series require an exact
approved subject, a calendar join URL resolved through Graph, and an organizer already
approved for that course. A similar title, nearby date or invitation alone is insufficient.
Unknown organizers/subjects remain unassigned; no new learner or course is created.

One occurrence is identified by course plus calendar `iCalUId`, which Graph defines
as unique per recurrence across calendars. It is not identified by a day or report ID.
Reports must uniquely overlap that occurrence's scheduled window in the exact verified
series. Repeated reports/joins are merged. Two non-overlapping same-day lectures remain
separate. Overlapping ambiguous calendars or deliveries remain held for private review.

Only non-cancelled occurrences with complete reports, at least two hours since the
reported end, and the existing `TEAMS_MIN_LEARNERS_FOR_DELIVERED` quorum (default five)
of enrolled canonical learners each having 30+ verified merged minutes become delivered.
This conservative delivery gate is separate from the approved individual attendance rule.
Small/short meetings, rehearsals with unapproved subjects and invitations without actual
delivery evidence cannot inflate counts. A cancelled/rescheduled previously verified
delivery is preserved and flagged privately, not silently deleted.

Module counts are historical owner baseline plus verified new occurrences. Dated
denominators are original matrix occurrences plus those new occurrences. Historical
undated slots are never duplicated as new dated lectures. All dashboard views use
the same combined occurrence index.

## Attendance and evidence

Canonical registry emails and retained identity aliases are reused. The additional
approved timing alias requires its exact verified Teams identity and tenant. Ambiguous
emails, reused identities, unfamiliar display names and non-enrolled accounts cannot
become learner observations.

First Join/Last Leave are actual UTC timestamps. Duration is the union of verified
intervals across repeat reports/joins; overlapping intervals count once and gaps do
not count. Cross-midnight attendance stays on one occurrence. The UI adds the leave
date only when needed to distinguish a different UTC day.

For new occurrences, 30+ minutes is Present, a shorter verified visit is Review,
and missing evidence is Not Available. A complete participant report is not itself
an explicit negative observation: no new rule infers absence from nonappearance.
Existing explicit matrix absence remains valid unless overridden by approved 30+
minute timing. The original 160 corrections and their provenance stay unchanged.

Raw report revisions are retained privately, including incomplete responses. Previously
verified intervals cannot be erased by an empty, shorter or incomplete reply. Shorter
complete revisions retain earlier evidence and flag review. New longer evidence can
qualify a previously short visit. No technical banner or administrative UI is added.

## Graph discovery and its boundaries

The existing application needs calendar read, online-meeting read and attendance-artifact
read access for approved organizers/readers. No new application is created. The local
preflight verifies actual endpoints rather than inferring access only from token roles.

The owner has confirmed that future Alfanar invitations will reach the designated
calendar reader. The release policy uses that one verified reader as the primary
discovery source. This is an owner-confirmed future routing contract, not a claim of
complete historical calendar coverage. An unavailable historical organizer calendar
is no longer queried or required. Original approved organizer IDs remain unchanged
for all six attendance series, including both Planning and both Risk series.
No new lecture in a module is a normal result and leaves its dashboard data unchanged.

- Read paginated `GET /users/{reader}/calendarView` in seven-day windows. An exact
  calendar join URL resolves a new ID through `GET /users/{organizer}/onlineMeetings?$filter=JoinWebUrl eq '...'`.
- Poll `GET /users/{organizer}/onlineMeetings/{meeting}/attendanceReports` and each
  report's `attendanceRecords`. Existing series do not depend on meeting metadata
  succeeding; metadata 404 is independent of artifact availability.
- Retain the previously approved late-Planning invitation URL in the private series
  binding. That exact URL resolves the known series even when its metadata is 404;
  no organizer ID is inferred from an unrelated invitation or display name.
- Persist report IDs and retrieve them directly after they disappear from the list.
  Calendar checkpoints use a 30-day rolling lookback, retain failed ranges and catch
  up missed runs. Pending events and occurrence IDs survive the lookback window.
- The listing API returns at most the latest 50 reports. Unknown report IDs lost
  during a sufficiently long outage cannot be recovered by pretending pagination
  is unlimited. Such occurrences remain pending. Access to future meetings hosted
  by an unapproved organizer, an inaccessible mailbox or without an accessible
  calendar invitation is not guaranteed; those scope gaps must be resolved explicitly.

References: Microsoft's [attendance report API](https://learn.microsoft.com/en-us/graph/api/meetingattendancereport-list?view=graph-rest-1.0),
[calendarView API](https://learn.microsoft.com/en-us/graph/api/calendar-list-calendarview?view=graph-rest-1.0),
[onlineMeeting lookup](https://learn.microsoft.com/en-us/graph/api/onlinemeeting-get?view=graph-rest-1.0),
and [event identifiers](https://learn.microsoft.com/en-us/graph/api/resources/event?view=graph-rest-1.0).

## Private configuration

Start from the verified timing policy. Preserve its approved meetings, subject/course
mappings and identity aliases. Fill each existing series' organizer from verified
source evidence, not from a name search. Add this object using actual approved private
values; this example intentionally contains no usable identifiers:

```json
{
  "automation": {
    "cutover_at": "<verified-baseline-cutover-in-UTC>",
    "calendar_readers": ["<owner-approved-primary-reader-object-ID>"],
    "lookback_days": 30,
    "grace_minutes": 120
  }
}
```

Keep the entire policy, aliases, meeting/organizer IDs, Graph evidence and database
outside Git and web roots. The final local deployment files are
`data/teams-automation-release-20261108/private/discovery-policy.json` and its
`SHA256SUMS`. Transfer only those two files. They contain no application credential.
Never transfer a simulation policy, local database, timing cache or QA directory.
The existing `TEAMS_DISCOVERY_CONFIG` selects this file; a UPN variable from another
project does not activate or override Alfanar's reader list.

## Scheduling and failure recovery

One Teams service/timer processes every module. Existing Google Sheets scheduling is
preserved. The local LMS template is an independent continuous five-minute service;
the actual production scheduler must be inspected by the authorized operator before
installation because implementation does not access production.

`deploy/alfanar-teams-discovery.timer` uses:

```ini
OnCalendar=*-*-* 23:00:00 Africa/Cairo
Persistent=true
AccuracySec=1s
```

The named IANA timezone follows Cairo DST. A missed timer activation is caught up
after boot. The oneshot service retries failures after 15 minutes, limited to three
starts in two hours; the next daily activation remains available. Graph requests
independently have bounded retries for 429/5xx/network failure and one token refresh
for 401. Permissions failures are reported without exposing server messages/tokens.

The shared OS lock `TEAMS_SYNC_LOCK_FILE` prevents concurrent CLI/timer runs and is
released if the process dies. All activation writes are transactional. Interrupted
runs are identified on the next run; durable checkpoints are not advanced for failed
calendar ranges. Registry changes during retrieval abort activation safely.
Logs contain aggregate counts and safe error codes. Private report payloads and
operational details remain in the database, never the client UI.

## Credential rotation prerequisite

The previously exposed client secret must be rotated before production activation.
The existing application and permissions are reused. An authorized tenant administrator
creates a new secret in Microsoft Entra: App registrations, select the existing app,
Certificates & secrets, Client secrets, New client secret. Record its expiration and
place its one-time value in the approved secret store. Never paste it into chat, a
shell command, a report or Git. See Microsoft's
[credential renewal procedure](https://learn.microsoft.com/en-us/entra/identity/monitoring-health/recommendation-renew-expiring-application-credential).

On the server, the authorized operator updates only the existing environment key
using the hidden prompt below. Run in an interactive, non-recorded secure terminal;
do not use shell tracing. This writes neither a CLI argument nor a log containing
the secret. The environment backup for deployment must be taken **after** rotation
so rollback cannot restore the exposed secret.

```bash
umask 077
/var/www/alfanar-learning-dashboard/backend/venv/bin/python - <<'PY'
import getpass, os, warnings
from dotenv import set_key
warnings.simplefilter('error', getpass.GetPassWarning)
with open('/dev/tty', 'r+') as tty:
    value = getpass.getpass('New Microsoft Graph secret: ', stream=tty)
    confirmation = getpass.getpass('Confirm new secret: ', stream=tty)
if not value or value != confirmation:
    raise SystemExit('Secret confirmation failed; environment unchanged')
path = '/var/www/alfanar-learning-dashboard/backend/.env'
set_key(path, 'MICROSOFT_GRAPH_CLIENT_SECRET', value, quote_mode='always')
os.chmod(path, 0o600)
del value, confirmation
print('Graph credential updated securely; endpoint verification is still required.')
PY
```

Run the authenticated dry run below with the new credential, then remove the exposed
old credential in Entra after successful verification. Confirm every consumer of
the existing app uses the replacement credential before revocation. Update the local
development secret through its secure configuration channel as well. Do not grant
additional permissions merely because a historical organizer mailbox is unavailable.
The new oneshot service reads the environment on each run. The rollout starts the
application service with the updated environment; do not activate the timer until
rotation/revocation and all required endpoint checks have been confirmed.

## Authorized production rollout (not executed during local work)

Use `/var/www/alfanar-learning-dashboard`, `backend/venv/bin/python`,
`backend/.env`, `backend/db.sqlite3`, and `alfanar-gunicorn.service`.
Execute this section only as the authorized production operator. Local source
publication does not execute any of these server commands.

1. Complete credential rotation and verify the designated calendar plus all original
   attendance organizer paths. The retired organizer calendar is not a release gate;
   the primary reader and attendance endpoints are. Retain the owner's future invitation
   routing decision in the private policy.
2. Prepare an approved pinned source commit and privately transfer only the real
   discovery policy plus its SHA-256 manifest. Never transfer a local `.env`, database,
   simulation fixture or original workbooks for this daily workflow.
3. Verify the service's actual working directory/environment/Python and existing LMS
   scheduling. Verify no other job runs `sync_teams_lectures`. Do not disable or replace
   the LMS scheduler. Use the coordinated maintenance backup procedure below.

Privately transfer the two release inputs from Windows PowerShell (operator action,
not performed by this implementation):

```powershell
scp -r "D:\alfanar-learning-dashboard\data\teams-automation-release-20261108\private" root@72.62.133.105:/root/alfanar-teams-automation-release
```

The destination directory must not exist before this copy, to avoid nesting ambiguity.
It should contain only `discovery-policy.json` and `SHA256SUMS` afterward. The policy
will be installed outside the repository/web root. No Excel transfer or historical
attendance re-import is required.

Run as the authorized operator in one root shell, after private transfer and approval:

```bash
set -euo pipefail
umask 077
app=/var/www/alfanar-learning-dashboard
python="$app/backend/venv/bin/python"
env_file="$app/backend/.env"
db="$app/backend/db.sqlite3"
service=alfanar-gunicorn.service
policy=/var/lib/alfanar/teams/discovery-policy.json
incoming=/root/alfanar-teams-automation-release
test -f "$incoming/discovery-policy.json" && test -f "$incoming/SHA256SUMS"
chmod 700 "$incoming"
chmod 600 "$incoming/discovery-policy.json" "$incoming/SHA256SUMS"
(cd "$incoming" && sha256sum -c SHA256SUMS)
install -d -m 700 /var/lib/alfanar/teams
test ! -e "$policy" # Initial activation only; preserve an installed policy before a later update.
install -m 600 "$incoming/discovery-policy.json" "$policy"
test -x "$python" && test -f "$env_file" && test -f "$db" && test -f "$policy"
systemctl show "$service" -p WorkingDirectory -p EnvironmentFiles --no-pager
systemctl list-timers --all --no-pager
systemctl list-units --all 'alfanar*' --no-pager
test -z "$(git -C "$app" status --porcelain)"
test "$(git -C "$app" branch --show-current)" = main
# Set release_commit to the separately approved release hash; not the old historical release.
test "${release_commit:?Set the approved release commit first}"
git -C "$app" fetch origin main
git -C "$app" merge-base --is-ancestor "$release_commit" origin/main
git -C "$app" merge-base --is-ancestor HEAD "$release_commit"
backup="/var/backups/alfanar/teams-$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 700 "$backup"
git -C "$app" rev-parse HEAD > "$backup/previous-commit"
# Coordinate the short maintenance window with existing LMS writers; retain their
# prior state and resume the same scheduler. No scheduler replacement is authorized.
systemctl stop "$service"
cp -a "$env_file" "$backup/backend.env"
cp -a "$policy" "$backup/discovery-policy.json"
tar -czf "$backup/frontend-dist.tar.gz" -C "$app/frontend" dist
"$python" - "$db" "$backup/database.sqlite3" <<'PY'
import pathlib, sqlite3, sys
src, dst = map(pathlib.Path, sys.argv[1:])
assert src.is_file() and not dst.exists()
a = sqlite3.connect(src.resolve().as_uri() + '?mode=ro', uri=True)
b = sqlite3.connect(dst); a.backup(b)
assert b.execute('pragma integrity_check').fetchone()[0] == 'ok'
b.close(); a.close()
PY
(cd "$backup" && sha256sum database.sqlite3 backend.env discovery-policy.json frontend-dist.tar.gz > SHA256SUMS && sha256sum -c SHA256SUMS)
git -C "$app" merge --ff-only "$release_commit"
test "$(git -C "$app" rev-parse HEAD)" = "$release_commit"
"$python" - "$env_file" "$policy" <<'PY'
import sys
from dotenv import set_key
set_key(sys.argv[1], 'TEAMS_DISCOVERY_CONFIG', sys.argv[2], quote_mode='always')
set_key(sys.argv[1], 'TEAMS_SYNC_LOCK_FILE', '/var/lib/alfanar-teams/sync.lock', quote_mode='always')
set_key(sys.argv[1], 'ATTENDANCE_RECONCILIATION_MIN_MINUTES', '30', quote_mode='never')
PY
chmod 600 "$env_file" "$policy"
install -d -m 700 /var/lib/alfanar-teams
run_manage() {
  "$python" - "$env_file" "$app/backend" "$@" <<'PY'
import os, runpy, sys
from dotenv import dotenv_values
env, backend, *args = sys.argv[1:]
for key, value in dotenv_values(env).items():
    if value is not None and key.lower() not in {'username', 'password'}:
        os.environ[key] = value
os.chdir(backend); sys.path.insert(0, backend)
sys.argv = ['manage.py', *args]; runpy.run_path('manage.py', run_name='__main__')
PY
}
run_manage check
run_manage migrate analytics 0006 --plan
run_manage migrate analytics 0006 --noinput
run_manage sync_teams_lectures --dry-run > "$backup/discovery-dry-run.json"
# Any nonzero exit/partial discovery must be investigated before enabling the timer.
# Verify new credentials, then revoke the exposed old credential in Entra before continuing.
run_manage audit_attendance_reconciliation --output "$backup/historical-decisions.json"
run_manage sync_teams_lectures > "$backup/discovery-first-run.json"
run_manage sync_teams_lectures > "$backup/discovery-repeat.json"
run_manage audit_dashboard --discovery-config "$policy" --output "$backup/source-reconciliation.json"
(cd "$app/frontend" && npm ci && npm run build)
run_manage collectstatic --noinput
nginx -t
systemctl start "$service"
systemctl is-active --quiet "$service"
# Verify client-facing counts and all four module views before scheduler activation.
install -m 0644 "$app/deploy/alfanar-teams-discovery.service" /etc/systemd/system/
install -m 0644 "$app/deploy/alfanar-teams-discovery.timer" /etc/systemd/system/
systemd-analyze verify /etc/systemd/system/alfanar-teams-discovery.service /etc/systemd/system/alfanar-teams-discovery.timer
systemd-analyze calendar --iterations=4 '*-*-* 23:00:00 Africa/Cairo'
systemctl daemon-reload
systemctl enable --now alfanar-teams-discovery.timer
systemctl list-timers alfanar-teams-discovery.timer --no-pager
```

Restore the pre-existing LMS writer state after verification. New counts may rise only
for independently verified post-cutover lectures; the historical ledger must still
retain all 160 corrections, 16 reviews, 1,048 pairs and 51 historical timing dates.
No new package dependency is introduced. Every server command above is an operator
instruction, not evidence that the production configuration was accessed or tested.

## Post-deployment verification and monitoring

The dry run, initial run and repeat must report success and process all four modules.
An empty module is normal. The repeat must add no duplicate lectures or timing rows
when Graph evidence is unchanged. Compare the source audit and historical ledger:
27 learners, 103 enrollments, 58 historical lectures, 53 historical dated lectures,
1,048 historical timing pairs, 160 corrections and 16 existing review flags remain.
Only independently verified post-cutover occurrences may increase delivered counts.
Inspect all four module cards and a learner's Live Sessions without adding UI warnings.

```bash
systemctl is-enabled alfanar-teams-discovery.timer
systemctl list-timers alfanar-teams-discovery.timer --no-pager
systemctl show alfanar-teams-discovery.service -p Result -p ExecMainStatus --no-pager
journalctl -u alfanar-teams-discovery.service --since '2 days ago' --no-pager
```

A completed oneshot service is normally inactive between runs. Check Result and its
last exit status, not continuous activity. Logs contain aggregate counts and error
codes; pending occurrences must remain pending until valid reports arrive. Investigate
authentication/permission failures, unresolved series and repeated partial runs in the
private database/run reports. No calendar reader failure is silently ignored. Report
retention and missing invitations still bound what can be discovered; the owner-confirmed
routing contract does not turn absent evidence into verified attendance.

## Rollback

Keep the coordinated writer maintenance window active. Disable only the new Teams
timer. Restore the matching database, source, environment and frontend together;
reversing migration alone would destroy the newly captured private report evidence.

```bash
if systemctl cat alfanar-teams-discovery.timer >/dev/null 2>&1; then
  systemctl disable --now alfanar-teams-discovery.timer
fi
if systemctl cat alfanar-teams-discovery.service >/dev/null 2>&1; then
  systemctl stop alfanar-teams-discovery.service
fi
systemctl stop "$service"
(cd "$backup" && sha256sum -c SHA256SUMS)
test -z "$(git -C "$app" status --porcelain)"
old_commit=$(tr -d '\r\n' < "$backup/previous-commit")
git -C "$app" switch --detach "$old_commit"
cp -a "$backup/backend.env" "$env_file"
install -d -m 700 "$backup/failed-database"
for file in "$db" "$db-wal" "$db-shm" "$db-journal"; do
  if test -e "$file"; then mv -- "$file" "$backup/failed-database/"; fi
done
cp "$backup/database.sqlite3" "$db"
chown --reference="$backup/failed-database/db.sqlite3" "$db"
chmod --reference="$backup/failed-database/db.sqlite3" "$db"
test ! -e "$backup/failed-dist"
mv -- "$app/frontend/dist" "$backup/failed-dist"
tar -xzf "$backup/frontend-dist.tar.gz" -C "$app/frontend"
run_manage check
run_manage collectstatic --noinput
nginx -t
systemctl start "$service"
systemctl is-active --quiet "$service"
```

Verify restored SQLite integrity and historical totals, then resume the unchanged LMS
scheduler. Retain failed data privately for diagnosis. This leaves source detached at
the previously deployed commit until the release issue is resolved.

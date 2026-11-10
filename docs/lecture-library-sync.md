# Automatic future lecture-library updates

`sync_lecture_library` collects future attendance, recording references and transcript
metadata, then calls the existing library importer. Its optional incremental mode
uses the same identity registry, enrolled cohort, 30-minute attendance threshold,
five-learner lecture gate, daily grouping, interval union and in-meeting speaker
matching. It does not update official attendance/KPI tables, Google Sheets, users,
identities, courses or enrollments. The existing Teams discovery service/timer stays
independent. No schema migration or frontend change is needed.

## Private configuration

Keep the config, cumulative source and status outside the repository/web root, in a
root-owned `0700` directory. Use the existing approved discovery policy; do not
infer organizers, bindings or subjects from display names. Example structure only:

```json
{
  "policy": "/private/approved-discovery-policy.json",
  "source": "/private/cumulative-library-evidence.json",
  "since": "<fixed UTC or offset-aware timestamp after the approved baseline>"
}
```

Seed `source` from the exact approved imported evidence snapshot, preserving its
historical sources, reports, media, transcript contents and optional verified speaker
bindings. If verified bindings were used, configure `speaker_bindings` with their
private path. `since` is a fixed cutover; once a live run saves it, changing it fails.
New discovery starts at that time, and import writes only that Cairo day and later.
Older/undated records remain untouched. Hidden days stay hidden, including after a
new attendance report changes a day's preferred source key. Existing daily URLs
remain stable. New days with no positive presence stay held; short visits remain
visible under the existing review rule.

## Collection and recovery

- Poll every hour. Completed reports/files wait the policy's existing grace
  period (normally two hours) before import; this is not live meeting monitoring.
- Check all approved series even when calendar lookup or meeting metadata fails.
  New series require an exact approved subject and invitation URL resolved under
  an already approved organizer. Unknown/ambiguous series are held for review.
  If a series' media list returns 404, use the organizer's last-30-day media index
  and retain only assets with that exact approved `meetingId`; titles never bind files.
- Calendar lookup covers the rolling last 30 days after cutover. Reports are
  paginated and previously known IDs are retried directly. Graph's list exposes
  only its latest 50 reports; unknown IDs lost during a long outage require review.
- Merge new lists into the cumulative source; omissions never delete recordings,
  transcripts or known reports. Incomplete/shorter attendance revisions preserve
  previous complete evidence. A database update that would remove learners/media
  or reduce verified timing/speech fails atomically.
- Retry missing transcript content on every run. Existing readable content remains
  available; timestamps and exact speaker labels use the original metadata parser
  contract, without guessing identities or assigning silence as zero.
- Transcript metadata uses Graph's authenticated `metadataContent` endpoint;
  recording references continue through the existing authenticated media endpoints.
  No signed playback URLs/tokens or raw attendee lists enter logs.
- A private snapshot is replaced atomically before import, so successful reads
  survive a later DB failure. A private `.status.json` records aggregate counts and
  safe issue codes. Partial runs retain good evidence but exit nonzero for retry.
  The OS lock prevents overlapping runs; interrupted runs can safely repeat.

Graph contracts: [transcript metadata](https://learn.microsoft.com/en-us/graph/api/calltranscript-get?view=graph-rest-1.0)
and [attendance report listing](https://learn.microsoft.com/en-us/graph/api/meetingattendancereport-list?view=graph-rest-1.0).

## Production activation

Back up the library and approved source first. Inspect the existing schedulers and
ensure no other job calls this command. The launcher loads the existing backend
environment privately and enforces read-only access to the production SQLite
registry. Run a dry run before enabling the new timer:

```sh
app=/var/www/alfanar-learning-dashboard
"$app/backend/venv/bin/python" "$app/deploy/run_lecture_library_sync.py" \
  --config /var/lib/alfanar-lecture-library-sync/config.json --dry-run
```

Compare dashboard/registry and library values against the baseline. After a
successful controlled run, install only the new `alfanar-lecture-library.service`
and `.timer`, validate them with `systemd-analyze verify`, reload systemd and enable
the new timer. Do not replace/restart the existing Teams discovery timer. The new
job retries failures after 15 minutes, up to three starts in two hours; the hourly
timer remains the next opportunity. Inspect `systemctl status`, journal and the
private status file for partial-source issues before declaring coverage complete.

Rollback: disable only `alfanar-lecture-library.timer`, stop its service if running,
and revert the source release. Successful new library evidence can remain. Restore
the library backup together with its matching cumulative snapshot only if data
rollback is needed; do not restore SQLite or change the existing discovery service.

Validation uses synthetic evidence and two in-memory SQLite test stores:

```sh
python backend/manage.py test analytics.tests.test_lecture_library_sync \
  analytics.tests.test_lecture_library analytics.tests.test_graph_timing \
  analytics.tests.test_teams_discovery --settings=config.test_settings
```

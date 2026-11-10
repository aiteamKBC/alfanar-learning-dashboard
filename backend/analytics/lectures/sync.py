"""Collect future Teams evidence; use the existing library importer unchanged by default.

The cumulative private snapshot retains reports/media through listing omissions.
Only the library database is written; historical and manually hidden days are held.
"""
from __future__ import annotations

import copy
import datetime as dt
import html
import json
import os
import tempfile
import urllib.parse
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from django.utils import timezone

from ..attendance.discovery import CALENDAR_FIELDS, calendar_time, load_config, subject_course
from ..attendance.graph_client import GraphClient, GraphError, meeting_path
from ..management.commands.run_sync_scheduler import ProcessLock
from .importer import CAIRO, SUBJECTS, import_source, timestamp, union
from .library import registry
from .media import open_recording


class SyncInProgress(Exception):
    pass


def atomic_json(path, value):
    """Replace private checkpoints atomically, including after an interrupted run."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.lecture-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf8') as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(',', ':'))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def parse_metadata(content):
    """Same metadata cues/absolute speaker timestamps used by the original import."""
    segments, plain, vtt, cue_time = [], [], ['WEBVTT', ''], ''
    for line in content.splitlines():
        if '-->' in line:
            cue_time = line.strip()
            continue
        if not line.lstrip().startswith('{'):
            continue
        try:
            cue = json.loads(line)
        except ValueError:
            continue
        if not isinstance(cue, dict):
            continue
        text, name = str(cue.get('spokenText') or '').strip(), str(cue.get('speakerName') or '').strip()
        if not text:
            continue
        plain.append('[' + cue_time + '] ' + (name or 'Unknown speaker') + ': ' + text)
        if cue_time:
            vtt.extend([cue_time, '<v ' + html.escape(name or 'Unknown speaker', quote=True) + '>' + html.escape(text) + '</v>', ''])
        try:
            a, b = timestamp(cue.get('startDateTime')), timestamp(cue.get('endDateTime'))
            if name and a and b and b > a:
                segments.append({'name': name, 'start': a.isoformat(), 'end': b.isoformat()})
        except (ValueError, TypeError):
            pass
    if not plain:
        raise ValueError('Transcript metadata has no readable speech cues')
    return segments, {'text': '\n'.join(plain), 'vtt': '\n'.join(vtt)}


def retains_records(previous, incoming):
    """An empty/shorter complete revision must not erase earlier verified spans."""
    current = {r['id']: r for r in incoming}
    for old in previous:
        new = current.get(old['id'])
        if not new or new.get('emailAddress') != old.get('emailAddress') or new.get('identity') != old.get('identity'):
            return False
        spans = union([(timestamp(x['joinDateTime']), timestamp(x['leaveDateTime'])) for x in new.get('attendanceIntervals', [])])
        for interval in old.get('attendanceIntervals', []):
            a, b = timestamp(interval['joinDateTime']), timestamp(interval['leaveDateTime'])
            if not any(x <= a and y >= b for x, y in spans):
                return False
    return True


def collect(source, policy, since, *, client, now):
    """All network operations are read-only; diagnostics contain counts/codes only."""
    data = copy.deepcopy(source)
    counts, issues = Counter(), Counter()
    series = {(s['organizer_id'], s['meeting_id']): s for s in data.get('series', [])}
    approved = {}
    for binding in policy['meetings']:
        approved.setdefault(binding['course_source_id'], set()).add(binding['organizer_id'])
        key = binding['organizer_id'], binding['meeting_id']
        if key not in series:
            series[key] = {'organizer_id': key[0], 'meeting_id': key[1], 'subject': binding['approved_subject'],
                           'join_url': binding.get('join_url', ''), 'recordings': [], 'transcripts': []}
    subjects = dict(SUBJECTS)
    subjects.update({m['approved_subject']: m['course_source_id'] for m in policy['meetings']})

    def issue(name, error=None):
        issues[name + (':' + str(error.status) + ':' + error.code if error else '')] += 1

    # Exact subject, exact invitation URL, and an already approved organizer.
    for reader in policy['automation']['calendar_readers']:
        start = max(since, now - dt.timedelta(days=30))
        while start < now:
            end = min(start + dt.timedelta(days=7), now)
            query = urllib.parse.urlencode({'startDateTime': start.isoformat(), 'endDateTime': end.isoformat(),
                                           '$select': CALENDAR_FIELDS, '$top': '100'})
            try:
                events, _ = client.collection('/users/' + urllib.parse.quote(reader, safe='') + '/calendarView?' + query)
            except GraphError as exc:
                issue('calendar', exc)
                break
            for event in events:
                cid = subject_course(event.get('subject'), policy)
                url = (event.get('onlineMeeting') or {}).get('joinUrl')
                if cid is None or event.get('isCancelled') is not False or event.get('isDraft') is not False or not url or not event.get('isOnlineMeeting'):
                    continue
                try:
                    if calendar_time(event['start']) < since or calendar_time(event['end']) > now:
                        continue
                except (KeyError, ValueError, TypeError):
                    issue('invalid_calendar_event')
                    continue
                if any(s.get('join_url') == url and subjects.get(s['subject']) == cid for s in series.values()):
                    continue
                matches = []
                for owner in sorted(approved[cid]):
                    query = urllib.parse.urlencode({'$filter': "JoinWebUrl eq '" + url.replace("'", "''") + "'"})
                    try:
                        meetings, _ = client.collection('/users/' + owner + '/onlineMeetings?' + query)
                        for meeting in meetings:
                            actual = ((((meeting.get('participants') or {}).get('organizer') or {}).get('identity') or {}).get('user') or {}).get('id')
                            if meeting.get('joinWebUrl') == url and subject_course(meeting.get('subject'), policy) == cid and actual == owner:
                                matches.append((owner, meeting['id']))
                    except GraphError as exc:
                        issue('meeting_lookup', exc)
                if len(set(matches)) != 1:
                    issue('unresolved_series')
                    continue
                key = matches[0]
                canonical_subject = next(m['approved_subject'] for m in policy['meetings'] if m['course_source_id'] == cid)
                series.setdefault(key, {'organizer_id': key[0], 'meeting_id': key[1], 'subject': canonical_subject,
                                       'join_url': url, 'recordings': [], 'transcripts': []})
            start = end

    reports = {(r['meeting_id'], r['report_id']): r for r in
               data.get('previous', {}).get('attendance', {}).get('reports', []) + data.get('extra_reports', [])}
    records = data.setdefault('attendance_records', {})
    contents = data.setdefault('transcript_contents', {})
    timed = {t['transcript_id']: t for t in data.get('timed_transcripts', []) if t.get('transcript_id')}
    grace = dt.timedelta(minutes=policy['automation'].get('grace_minutes', 120))
    owner_media = {}

    def media_fallback(owner, mid, kind):
        # Expired series can return 404 for media while attendance still works.
        # The organizer index must still match the exact approved meeting ID.
        cache_key = owner, kind
        if cache_key not in owner_media:
            function = 'getAllRecordings' if kind == 'recordings' else 'getAllTranscripts'
            start = max(since, now - dt.timedelta(days=30)).strftime('%Y-%m-%dT%H:%M:%SZ')
            end = now.strftime('%Y-%m-%dT%H:%M:%SZ')
            path = '/users/' + owner + '/onlineMeetings/' + function
            path += "(meetingOrganizerUserId='" + owner + "',startDateTime=" + start + ',endDateTime=' + end + ')'
            owner_media[cache_key], _ = client.collection(path)
            counts['organizer_media_indexes_read'] += 1
        return [asset for asset in owner_media[cache_key] if asset.get('meetingId') == mid]
    for (owner, mid), item in series.items():
        cid = subjects.get(item['subject'])
        if cid not in approved or owner not in approved[cid]:
            continue
        base = meeting_path(owner, mid)
        listed = []
        try:
            listed, _ = client.collection(base + '/attendanceReports')
            counts['report_lists_read'] += 1
        except GraphError as exc:
            issue('report_list', exc)
        ids = {r['id'] for r in listed if not r.get('meetingEndDateTime') or timestamp(r['meetingEndDateTime']) >= since}
        # Direct known-ID retry survives the Graph latest-50 list limit.
        ids.update(rid for (meeting, rid), r in reports.items() if meeting == mid and timestamp(r.get('end')) and timestamp(r['end']) >= since)
        for rid in sorted(ids):
            path = base + '/attendanceReports/' + urllib.parse.quote(rid, safe='')
            try:
                report = client.get(path)
                a, b = timestamp(report.get('meetingStartDateTime')), timestamp(report.get('meetingEndDateTime'))
                if not a or not b or a < since or b <= a or b + grace > now:
                    continue
                rows, _ = client.collection(path + '/attendanceRecords')
                if type(report.get('totalParticipantCount')) is not int or len(rows) != report['totalParticipantCount']:
                    issue('incomplete_report')
                    continue
                raw_key = mid + '|' + rid
                if not retains_records(records.get(raw_key, []), rows):
                    issue('shorter_report_preserved')
                    continue
                reports[(mid, rid)] = {'subject': item['subject'], 'meeting_id': mid, 'organizer_id': owner,
                    'report_id': rid, 'start': a.isoformat(), 'end': b.isoformat(), 'expected': len(rows)}
                records[raw_key] = rows
                counts['complete_reports_read'] += 1
            except GraphError as exc:
                issue('report', exc)
        for kind in ('recordings', 'transcripts'):
            assets = {x['id']: x for x in item.get(kind, [])}
            try:
                latest, _ = client.collection(base + '/' + kind)
            except GraphError as exc:
                latest = []
                if exc.status == 404:
                    try:
                        latest = media_fallback(owner, mid, kind)
                    except GraphError as fallback_error:
                        issue(kind + '_organizer_index', fallback_error)
                else:
                    issue(kind, exc)
            for asset in latest:
                created = timestamp(asset.get('createdDateTime'))
                end = timestamp(asset.get('endDateTime'))
                if not created or created < since or created > now or end and end + grace > now:
                    continue
                # Missing fields in a later listing never erase known metadata.
                assets[asset['id']] = {**assets.get(asset['id'], {}), **asset}
            item[kind] = list(assets.values())
            if kind == 'recordings':
                for asset in assets.values():
                    created = timestamp(asset.get('createdDateTime'))
                    if not created or created < since or asset.get('content_available') is True:
                        continue
                    stub = SimpleNamespace(organizer_id=owner, meeting_id=mid, source_id=asset['id'], provider='teams')
                    try:
                        response = open_recording(stub, 'bytes=0-0', client=client)
                        try:
                            available = response.status in (200, 206) and bool(response.read(1))
                        finally:
                            response.close()
                        asset['content_available'] = available
                        if available:
                            counts['recordings_verified'] += 1
                        else:
                            issue('recording_content_unavailable')
                    except (GraphError, OSError):
                        asset['content_available'] = False
                        issue('recording_content_unavailable')
                continue
            for tid, asset in assets.items():
                created = timestamp(asset.get('createdDateTime'))
                if not created or created < since or contents.get(tid, {}).get('text'):
                    continue
                try:
                    content = client.get(base + '/transcripts/' + urllib.parse.quote(tid, safe='') + '/metadataContent', text=True)
                    segments, rendered = parse_metadata(content)
                    contents[tid] = rendered
                    timed[tid] = {'subject': item['subject'], 'meeting_id': mid, 'call_id': asset.get('callId'),
                                  'transcript_id': tid, 'readable': True, 'segments': segments}
                    counts['transcripts_downloaded'] += 1
                except GraphError as exc:
                    issue('transcript_content', exc)
                except ValueError:
                    issue('invalid_transcript_metadata')
    data['series'] = list(series.values())
    data.setdefault('previous', {}).setdefault('attendance', {})['reports'] = list(reports.values())
    data['extra_reports'] = []
    data['timed_transcripts'] = list(timed.values())
    return data, subjects, dict(counts), dict(issues)


def run_sync(config_path, *, dry_run=False, client=None, now=None):
    config = json.loads(Path(config_path).read_text(encoding='utf-8-sig'))
    since = timestamp(config['since'])
    now = now or timezone.now()
    if not since or since > now:
        raise ValueError('A fixed past library cutover is required')
    policy, _ = load_config(config['policy'])
    source_path = Path(config['source'])
    lock = ProcessLock(Path(config_path).with_suffix('.lock'))
    if not lock.acquire():
        raise SyncInProgress('Another library synchronization is running')
    try:
        source = json.loads(source_path.read_text(encoding='utf8'))
        checkpoint = source.get('library_automation')
        if checkpoint and timestamp(checkpoint['since']) != since:
            raise ValueError('The library cutover is immutable')
        before = registry()
        fingerprint = lambda r: (sorted((uid, x.source_id, x.email, x.name) for uid, x in r[0].items()),
                                  sorted(r[1].items()), sorted((cid, sorted(lids)) for cid, lids in r[2].items()))
        data, subjects, counts, issues = collect(source, policy, since, client=client or GraphClient.from_environment(), now=now)
        if fingerprint(before) != fingerprint(registry()):
            raise ValueError('The learner registry changed during collection; retry')
        data['library_automation'] = {'since': since.isoformat(), 'last_collected_at': now.isoformat()}
        # Retain successful reads even if a later database transaction fails.
        # A repeated run is idempotent and cannot delete earlier DB evidence.
        if not dry_run:
            atomic_json(source_path, data)
        with tempfile.TemporaryDirectory(prefix='lecture-candidate-', dir=source_path.parent) as folder:
            candidate = Path(folder) / 'source.json'
            atomic_json(candidate, data)
            result = import_source(candidate, config.get('speaker_bindings'), incremental_since=since.astimezone(CAIRO).date(),
                                   subject_courses=subjects, dry_run=dry_run)
        result.update(status='partial' if issues else 'success', dry_run=dry_run, collected=counts, issues=issues)
        if not dry_run:
            atomic_json(Path(config_path).with_suffix('.status.json'), {**result, 'finished_at': now.isoformat()})
        return result
    finally:
        lock.release()

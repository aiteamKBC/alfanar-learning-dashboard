"""Allowlisted Sheets v4 writer with private durable plans and atomic publication."""
from __future__ import annotations

import copy
import datetime as dt
import json
import random
import time
from pathlib import Path

from googleapiclient.errors import HttpError

from .master_snapshot import (HEADERS, REVIEW_MACHINE_COLUMNS, SCHEMA_VERSION,
                              ExportError, digest, encoded, private_json, require)

WRITER_SCOPE = 'https://www.googleapis.com/auth/spreadsheets'
OWNER_KEY = 'alfanar_attendance_export'
OWNER_VALUE = f'schema-{SCHEMA_VERSION}'
MAX_BATCH_BYTES = 8 * 1024 * 1024
LMS_TITLES = {'Al Fanar '+name for name in ('Students','Quizzes','Quiz Attempts','Diagnostics','All Activities',
    'Lesson Details','Videos','PDFs','Audio','Other Lessons','Assignments','Submissions','Coverage','Readings','Reading Sessions')}


def width(tab):
    return REVIEW_MACHINE_COLUMNS if tab == 'AT_Review' else len(HEADERS[tab])


def letter(number):
    text = ''
    while number:
        number, rest = divmod(number-1, 26); text = chr(65+rest) + text
    return text


def cell(value):
    # Explicit typed values prevent formula injection from names, notes and source strings.
    if value == '' or value is None: return {}
    key = 'boolValue' if isinstance(value, bool) else ('numberValue' if isinstance(value, (int, float)) else 'stringValue')
    return {'userEnteredValue': {key: value}}


def machine_values(tab, rows):
    columns = HEADERS[tab][:width(tab)]
    return [[r.get(h, '') for h in columns] for r in rows]


class GoogleSheets:
    """No Drive scope, no new credentials; OAuth writer separate from the LMS reader."""
    def __init__(self, credentials_file, spreadsheet_id, *, sleep=time.sleep):
        import httplib2
        import google_auth_httplib2
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        credentials = service_account.Credentials.from_service_account_file(credentials_file, scopes=[WRITER_SCOPE])
        self.identity = credentials.service_account_email
        self.spreadsheet_id = spreadsheet_id
        self.sleep = sleep
        self.api = build('sheets', 'v4', http=google_auth_httplib2.AuthorizedHttp(credentials,
            http=httplib2.Http(timeout=90)), cache_discovery=False).spreadsheets()

    def execute(self, factory, *, write=False):
        # Never blindly retry an uncertain mutation. Publisher reconciles it by reading back.
        for attempt in range(4):
            try: return factory().execute(num_retries=0)
            except HttpError as exc:
                code = exc.resp.status
                retryable = code == 429 or code >= 500
                if write or not retryable or attempt == 3:
                    raise SheetsFailure(code, retryable, exc.resp.get('retry-after')) from None
                self.sleep(SheetsFailure(code, True, exc.resp.get('retry-after')).retry_after or min(30, 2**attempt + random.random()))
            except (TimeoutError, OSError):
                if write or attempt == 3: raise SheetsFailure(0, True) from None
                self.sleep(2**attempt)
            except Exception:
                raise ExportError('Google authentication or transport failed; credentials and response text withheld') from None

    def read(self):
        meta = self.execute(lambda: self.api.get(spreadsheetId=self.spreadsheet_id,
            fields='spreadsheetId,sheets(properties,developerMetadata,protectedRanges)'))
        owned = [s for s in meta.get('sheets', []) if s['properties']['title'] in HEADERS]
        values = {}
        if owned:
            tabs = [s['properties']['title'] for s in owned]
            response = self.execute(lambda: self.api.values().batchGet(spreadsheetId=self.spreadsheet_id,
                ranges=[f"'{tab}'!A:{letter(len(HEADERS[tab]))}" for tab in tabs],
                valueRenderOption='FORMULA', dateTimeRenderOption='SERIAL_NUMBER'))
            for tab, data in zip(tabs, response.get('valueRanges', [])):
                values[tab] = data.get('values', [])
        return {'sheets': meta.get('sheets', []), 'values': values}

    def batch(self, body):
        return self.execute(lambda: self.api.batchUpdate(spreadsheetId=self.spreadsheet_id, body=body), write=True)

    def capture_lms(self, directory):
        """No mutations: capture all original sheet structures/cells before live publication."""
        meta = self.execute(lambda: self.api.get(spreadsheetId=self.spreadsheet_id, includeGridData=False))
        sheets = [s for s in meta.get('sheets', []) if s['properties']['title'] not in HEADERS]
        require({s['properties']['title'] for s in sheets} == LMS_TITLES and len(sheets) == 15,
                'The live workbook must retain the exact 15 approved LMS tabs')
        result = {}
        for s in sheets:
            title = s['properties']['title']; sid = s['properties']['sheetId']
            data = self.execute(lambda title=title: self.api.get(spreadsheetId=self.spreadsheet_id,
                ranges=["'"+title.replace("'", "''")+"'"], includeGridData=True))
            require(len(data.get('sheets', [])) == 1 and data['sheets'][0]['properties']['sheetId'] == sid,
                    'LMS source sheet changed during capture')
            sheet = data['sheets'][0]
            # Exclude calculated render caches; original formulas, input values, formats,
            # notes, validations, charts and sheet structure stay in the fingerprint.
            stable = copy.deepcopy(sheet)
            for grid in stable.get('data', []):
                for row in grid.get('rowData', []):
                    for c in row.get('values', []):
                        for derived in ('effectiveValue', 'formattedValue', 'effectiveFormat', 'hyperlink'): c.pop(derived, None)
            result[str(sid)] = {'title': title, 'sha256': digest(stable), 'index': s['properties']['index']}
            if directory is not None: private_json(Path(directory)/f'lms-{sid}.json', sheet)
        if directory is not None: private_json(Path(directory)/'manifest.json', {'destination': self.spreadsheet_id, 'sheets': result})
        return result


class SheetsFailure(ExportError):
    def __init__(self, status, retryable=False, retry_after=None):
        self.status, self.retryable = status, retryable
        try: self.retry_after = min(60, max(0, int(retry_after or 0)))
        except (TypeError, ValueError): self.retry_after = 0
        super().__init__(f'Google Sheets request failed (HTTP {status}); private plan retained')


def unowned_structure(remote):
    return [{k: s['properties'][k] for k in ('sheetId', 'title', 'index')}
            for s in remote['sheets'] if s['properties']['title'] not in HEADERS]


def normalized(remote):
    """Ignore only explicitly human-owned review decision cells, never headers or keys."""
    result = {}
    for tab, raw in remote['values'].items():
        require(raw and (raw[0] + [''] * len(HEADERS[tab]))[:len(HEADERS[tab])] == HEADERS[tab], 'Workbook export headers changed')
        rows = [(r + [''] * width(tab))[:width(tab)] for r in raw[1:]]
        while rows and not any(v != '' for v in rows[-1]): rows.pop()
        require(all(r[0] for r in rows) and len({r[0] for r in rows}) == len(rows), 'Workbook contains blank or duplicate export keys')
        # Manual data in an otherwise empty row must never be overwritten by an append.
        if tab == 'AT_Review':
            require(all(r and r[0] for r in raw[1:] if any(v != '' for v in r)), 'Review decisions have no stable row key')
        result[tab] = rows
    return result


def bindings(remote):
    return {s['properties']['title']: s['properties']['sheetId'] for s in remote['sheets'] if s['properties']['title'] in HEADERS}


def check_ownership(remote, ids):
    require(bindings(remote) == ids, 'Owned attendance tabs were renamed, removed or replaced')
    for s in remote['sheets']:
        tab = s['properties']['title']
        if tab not in HEADERS: continue
        require(any(m.get('metadataKey') == OWNER_KEY and m.get('metadataValue') == OWNER_VALUE for m in s.get('developerMetadata', [])), 'Attendance tab ownership marker missing')
        require(any(p.get('description') == OWNER_KEY and not p.get('warningOnly', False)
                    and p.get('range', {}).get('sheetId') == ids[tab]
                    and p.get('range', {}).get('startColumnIndex', 0) == 0
                    and p.get('range', {}).get('endColumnIndex') == width(tab)
                    and 'endRowIndex' not in p.get('range', {}) for p in s.get('protectedRanges', [])),
                'Attendance machine columns are no longer protected')


def assert_expected(remote, saved):
    require(unowned_structure(remote) == saved['unowned'], 'Existing LMS/workbook tab structure changed; publication stopped')
    check_ownership(remote, saved['ids'])
    require(normalized(remote) == saved['tables'], 'Unexpected edits in machine-maintained attendance cells')


class Publisher:
    """Caller holds a process lock; state and pending plans are scoped to one exact destination."""
    def __init__(self, client, directory, *, sleep=time.sleep):
        self.client = client; self.sleep = sleep
        self.directory = Path(directory) / 'destinations' / digest(client.spreadsheet_id)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state_path = self.directory / 'state.json'
        self.pending_path = self.directory / 'pending.json'

    def read_file(self, path):
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None

    def publish(self, snapshot, *, initialize=False):
        try:
            return self._publish(snapshot, initialize=initialize)
        except ExportError as exc:
            private_json(self.directory/'last-failure.json', {'generation': snapshot['generation'],
                'at': dt.datetime.now(dt.timezone.utc).isoformat(), 'error': type(exc).__name__,
                'http_status': getattr(exc, 'status', None), 'message': str(exc)})
            raise

    def _publish(self, snapshot, *, initialize=False):
        pending = self.read_file(self.pending_path)
        if pending:
            self.apply(pending)
        state = self.read_file(self.state_path)
        remote = self.client.read()
        if state:
            require(state['destination'] == self.client.spreadsheet_id, 'Destination state mismatch')
            assert_expected(remote, state)
            if state['generation'] == snapshot['generation']:
                return {'status': 'unchanged', 'generation': snapshot['generation'], 'rows': snapshot['totals']['attendance_rows']}
        else:
            require(initialize, 'First publication requires explicit initialization')
            require(not bindings(remote), 'Refusing to adopt or overwrite pre-existing attendance tabs')
        plan = self.plan(snapshot, remote, state)
        private_json(self.pending_path, plan)
        self.apply(plan)
        return {'status': 'published', 'generation': snapshot['generation'], 'rows': snapshot['totals']['attendance_rows']}

    def plan(self, snapshot, remote, state):
        ids = bindings(remote)
        requests = []
        desired = {}; source_tables = copy.deepcopy(snapshot['tables'])
        source_tables['AT_SyncLog'] = [dict(zip(HEADERS['AT_SyncLog'], [snapshot['generation'], snapshot['source_as_of'],
            snapshot['generation'], snapshot['generation'], snapshot['policy_version'], 'Verified', 4,
            len(source_tables['AT_Lectures']), snapshot['totals']['attendance_rows'], snapshot['totals']['review_rows']]))]
        used = {s['properties']['sheetId'] for s in remote['sheets']}
        for tab, columns in HEADERS.items():
            old = state['tables'][tab] if state else []
            incoming = machine_values(tab, source_tables[tab]); bykey = {r[0]: r for r in incoming}
            rows = []
            for row in old:
                if row[0] in bykey: rows.append(bykey.pop(row[0]))
                elif tab == 'AT_SyncLog': rows.append(row)
                elif tab == 'AT_Review':
                    retained = list(row); retained[HEADERS[tab].index('State')] = 'NoLongerActive'; rows.append(retained)
                else: raise ExportError('Export would remove existing source rows; explicit reconciliation required')
            rows.extend(bykey.values()); desired[tab] = rows
            if tab not in ids:
                sid = 100000 + len(used)
                while sid in used: sid += 1
                used.add(sid); ids[tab] = sid
                requests.append({'addSheet': {'properties': {'sheetId': sid, 'title': tab,
                    'gridProperties': {'rowCount': max(100, len(rows)+10), 'columnCount': max(26, len(columns)), 'frozenRowCount': 1}}}})
                requests.append({'createDeveloperMetadata': {'developerMetadata': {'metadataKey': OWNER_KEY,
                    'metadataValue': OWNER_VALUE, 'location': {'sheetId': sid}, 'visibility': 'DOCUMENT'}}})
                requests.append({'addProtectedRange': {'protectedRange': {'description': OWNER_KEY,
                    'range': {'sheetId': sid, 'startColumnIndex': 0, 'endColumnIndex': width(tab)},
                    'warningOnly': False, 'editors': {'users': [self.client.identity]}}}})
                if tab == 'AT_Review':
                    requests.append({'addProtectedRange': {'protectedRange': {'description': OWNER_KEY+' headers',
                        'range': {'sheetId': sid, 'startRowIndex': 0, 'endRowIndex': 1},
                        'warningOnly': False, 'editors': {'users': [self.client.identity]}}}})
                requests.append({'updateCells': {'start': {'sheetId': sid, 'rowIndex': 0, 'columnIndex': 0},
                    'rows': [{'values': [cell(v) for v in columns]}], 'fields': 'userEnteredValue'}})
                requests.append({'repeatCell': {'range': {'sheetId': sid, 'startRowIndex': 0, 'endRowIndex': 1},
                    'cell': {'userEnteredFormat': {'textFormat': {'bold': True}, 'wrapStrategy': 'WRAP'}}, 'fields': 'userEnteredFormat'}})
            else:
                sid = ids[tab]
                props = next(s['properties'] for s in remote['sheets'] if s['properties']['sheetId'] == sid)
                if props['gridProperties']['rowCount'] < len(rows)+1:
                    requests.append({'appendDimension': {'sheetId': sid, 'dimension': 'ROWS', 'length': len(rows)+10-props['gridProperties']['rowCount']}})
            # Consecutive changed rows are one patch; review manual columns are never part of it.
            start = None; changed = []
            def flush():
                if changed:
                    requests.append({'updateCells': {'start': {'sheetId': sid, 'rowIndex': start+1, 'columnIndex': 0},
                        'rows': [{'values': [cell(v) for v in row]} for row in changed], 'fields': 'userEnteredValue'}})
            for i, row in enumerate(rows):
                if i < len(old) and row == old[i]:
                    flush(); start = None; changed = []
                else:
                    if start is None: start = i
                    changed.append(row)
            flush()
        body = {'requests': requests}
        require(len(encoded(body)) <= MAX_BATCH_BYTES, 'Atomic export exceeds the reviewed batch limit; previous generation retained')
        after = {'destination': self.client.spreadsheet_id, 'generation': snapshot['generation'],
                 'ids': ids, 'unowned': unowned_structure(remote), 'tables': desired}
        # This whitelist is checked before any transport mutation.
        allowed = set(ids.values())
        for req in requests:
            kind, value = next(iter(req.items()))
            sid = (value.get('sheetId') or value.get('start', {}).get('sheetId') or value.get('range', {}).get('sheetId')
                   or value.get('properties', {}).get('sheetId') or value.get('developerMetadata', {}).get('location', {}).get('sheetId')
                   or value.get('protectedRange', {}).get('range', {}).get('sheetId'))
            require(kind in {'addSheet','createDeveloperMetadata','addProtectedRange','updateCells','repeatCell','appendDimension'} and sid in allowed,
                    'Disallowed workbook mutation')
        return {'destination': self.client.spreadsheet_id, 'generation': snapshot['generation'], 'before': state,
                'unowned': unowned_structure(remote), 'after': after, 'body': body, 'body_hash': digest(body)}

    def apply(self, plan):
        require(plan['destination'] == self.client.spreadsheet_id and digest(plan['body']) == plan['body_hash'], 'Private publication plan is corrupt or belongs to another destination')
        for attempt in range(4):
            remote = self.client.read()
            require(unowned_structure(remote) == plan['unowned'], 'Existing LMS tab structure changed before publication')
            # An earlier uncertain write may already have atomically completed.
            if bindings(remote) == plan['after']['ids']:
                check_ownership(remote, plan['after']['ids'])
                if normalized(remote) == plan['after']['tables']:
                    self.finish(plan); return
            if plan['before']: assert_expected(remote, plan['before'])
            else: require(not bindings(remote), 'Unexpected partial initialization; refusing to overwrite')
            try:
                self.client.batch(plan['body'])
            except SheetsFailure as exc:
                if not exc.retryable: raise
                # Always inspect state after uncertain writes, even on the last attempt.
                remote = self.client.read()
                if bindings(remote) == plan['after']['ids']:
                    check_ownership(remote, plan['after']['ids'])
                    if normalized(remote) == plan['after']['tables']:
                        assert_expected(remote, plan['after']); self.finish(plan); return
                if plan['before']: assert_expected(remote, plan['before'])
                else: require(not bindings(remote) and unowned_structure(remote) == plan['unowned'], 'Uncertain write changed unexpected workbook data')
                if attempt == 3: raise
                self.sleep(exc.retry_after or min(30, 2**attempt + random.random()))
                continue
            assert_expected(self.client.read(), plan['after'])
            self.finish(plan); return
        raise ExportError('Publication retry budget exhausted')

    def finish(self, plan):
        private_json(self.state_path, plan['after'])
        private_json(self.directory / ('verified-'+plan['generation']+'.json'),
                     {'generation': plan['generation'], 'readback_hash': digest(plan['after']['tables']),
                      'rows': {k: len(v) for k, v in plan['after']['tables'].items()}, 'unowned_structure_unchanged': True})
        self.pending_path.unlink(missing_ok=True)

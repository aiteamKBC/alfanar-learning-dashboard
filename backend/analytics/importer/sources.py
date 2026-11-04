"""Read-only source adapters.

Each adapter returns a `SourcePayload` whose `tables` map worksheet title to a
list of rows (first row = header). The Excel and Google adapters produce the
same shape, so both feed the same normalizer.
"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field
from pathlib import Path

GOOGLE_READONLY_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"


class SourceUnavailable(Exception):
    """The source cannot be read (missing file, no credentials, API error)."""


@dataclass
class SourcePayload:
    source_type: str
    source_ref: str
    tables: dict[str, list[list]]
    source_modified_at: dt.datetime | None = None
    notes: list[str] = field(default_factory=list)


def read_excel(path: str | Path) -> SourcePayload:
    """Read every worksheet of an XLSX file without modifying it."""
    import openpyxl

    path = Path(path)
    if not path.is_file():
        raise SourceUnavailable(f"Workbook not found: {path.name}")

    # Open the file handle in binary read mode; openpyxl never writes in read_only mode.
    with open(path, "rb") as fh:
        wb = openpyxl.load_workbook(fh, read_only=True, data_only=True)
        try:
            tables = {ws.title: [list(r) for r in ws.iter_rows(values_only=True)] for ws in wb.worksheets}
        finally:
            wb.close()

    # openpyxl substitutes "now" when the workbook lacks dcterms:modified, so read core.xml directly.
    modified, note = _core_modified(path), "Source timestamp is the workbook's recorded last-modified property."
    if modified is None:
        modified = dt.datetime.fromtimestamp(os.stat(path).st_mtime, tz=dt.timezone.utc)
        note = ("Workbook has no internal modified property; source timestamp is the file's "
                "last-modified time on disk.")

    return SourcePayload(
        source_type="excel",
        source_ref=path.name,
        tables=tables,
        source_modified_at=modified,
        notes=[note],
    )


def _core_modified(path: Path) -> dt.datetime | None:
    import re
    import zipfile

    try:
        with zipfile.ZipFile(path) as z:
            if "docProps/core.xml" not in z.namelist():
                return None
            xml = z.read("docProps/core.xml").decode("utf-8", "replace")
    except (zipfile.BadZipFile, OSError):
        return None
    m = re.search(r"<dcterms:modified[^>]*>([^<]+)</dcterms:modified>", xml)
    if not m:
        return None
    try:
        return dt.datetime.fromisoformat(m.group(1).strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def read_google_sheet(spreadsheet_id: str, service_account_file: str) -> SourcePayload:
    """Fetch every worksheet using a service account with the read-only scope only."""
    if not spreadsheet_id:
        raise SourceUnavailable("GOOGLE_SHEETS_SPREADSHEET_ID is not configured.")
    if not service_account_file or not Path(service_account_file).is_file():
        raise SourceUnavailable("Google service account credentials file is not configured or not found.")

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError as exc:  # pragma: no cover
        raise SourceUnavailable("google-api-python-client / google-auth are not installed.") from exc

    try:
        creds = service_account.Credentials.from_service_account_file(
            service_account_file, scopes=[GOOGLE_READONLY_SCOPE]
        )
        service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        sheets_api = service.spreadsheets()
        meta = sheets_api.get(
            spreadsheetId=spreadsheet_id, fields="properties.title,sheets.properties.title"
        ).execute()
        titles = [s["properties"]["title"] for s in meta.get("sheets", [])]
        if not titles:
            raise SourceUnavailable("Spreadsheet has no worksheets.")
        quoted = ["'" + t.replace("'", "''") + "'" for t in titles]
        resp = sheets_api.values().batchGet(
            spreadsheetId=spreadsheet_id,
            ranges=quoted,
            valueRenderOption="UNFORMATTED_VALUE",
            dateTimeRenderOption="FORMATTED_STRING",
            majorDimension="ROWS",
        ).execute()
    except SourceUnavailable:
        raise
    except Exception as exc:  # network / auth / permission errors
        raise SourceUnavailable(f"Google Sheets read failed: {type(exc).__name__}: {exc}") from exc

    tables: dict[str, list[list]] = {}
    for title, vr in zip(titles, resp.get("valueRanges", [])):
        rows = vr.get("values", [])
        width = max((len(r) for r in rows), default=0)
        tables[title] = [list(r) + [None] * (width - len(r)) for r in rows]

    return SourcePayload(
        source_type="google",
        source_ref=spreadsheet_id,
        tables=tables,
        source_modified_at=None,
        notes=[
            "Spreadsheet modification time requires Drive metadata scope and is not requested; "
            "freshness is reported from the latest recorded activity timestamp instead."
        ],
    )

"""Synchronisation orchestration: read -> normalize/validate -> atomic load.

If reading or validation fails, the database is not touched and the last
successful dataset remains in place. The SyncRun row records the outcome.
"""

from __future__ import annotations

import datetime as dt
import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ..models import SyncRun
from .loader import load_dataset
from .normalize import ValidationFailed, normalize
from .sources import SourcePayload, SourceUnavailable, read_excel, read_google_sheet

log = logging.getLogger("analytics.sync")

STALE_RUN_MINUTES = 15


class SyncInProgress(Exception):
    pass


def fetch_source(source_type: str, path: str | None = None) -> SourcePayload:
    if source_type == "excel":
        return read_excel(path or settings.ALFANAR_EXCEL_PATH)
    if source_type == "google":
        if not settings.GOOGLE_SHEETS_ENABLED:
            raise SourceUnavailable("Google Sheets sync is disabled (GOOGLE_SHEETS_ENABLED=false).")
        return read_google_sheet(settings.GOOGLE_SHEETS_SPREADSHEET_ID, settings.GOOGLE_SERVICE_ACCOUNT_FILE)
    raise ValueError(f"Unknown source type: {source_type}")


def run_sync(source_type: str, path: str | None = None, fetcher=None) -> SyncRun:
    """Run one synchronisation. `fetcher` may be injected for testing."""
    cutoff = timezone.now() - dt.timedelta(minutes=STALE_RUN_MINUTES)
    SyncRun.objects.filter(status=SyncRun.Status.RUNNING, started_at__lt=cutoff).update(
        status=SyncRun.Status.FAILED, finished_at=timezone.now(), errors=["Run abandoned (timed out)"]
    )
    if SyncRun.objects.filter(status=SyncRun.Status.RUNNING).exists():
        raise SyncInProgress("Another synchronisation is already running.")

    run = SyncRun.objects.create(source_type=source_type)
    try:
        payload = fetcher() if fetcher else fetch_source(source_type, path)
        run.source_ref = payload.source_ref
        run.source_modified_at = payload.source_modified_at
        ds = normalize(payload.tables)
        run.warnings = payload.notes + ds.warnings
        run.counts = ds.counts()
        run.latest_activity_at = ds.latest_activity_at
        run.fingerprint = ds.fingerprint()

        # Always reconcile (cheap, idempotent): unchanged source => zero writes.
        with transaction.atomic():
            stats = load_dataset(ds)
        run.changed = any(s["created"] or s["updated"] or s["deleted"] for s in stats.values())
        run.counts["load"] = stats
        run.errors = ds.errors  # non-fatal row errors
        run.status = SyncRun.Status.SUCCESS
    except ValidationFailed as exc:
        run.status = SyncRun.Status.FAILED
        run.errors = exc.errors[:50]
        run.warnings = (run.warnings or []) + exc.warnings
        log.warning("Sync validation failed (%s): %d errors; previous dataset kept", source_type, len(exc.errors))
    except SourceUnavailable as exc:
        run.status = SyncRun.Status.FAILED
        run.errors = [str(exc)]
        log.warning("Sync source unavailable (%s): %s", source_type, exc)
    except Exception as exc:  # unexpected: transaction rolled back
        run.status = SyncRun.Status.FAILED
        run.errors = [f"{type(exc).__name__}: {exc}"]
        log.exception("Sync failed (%s); previous dataset kept", source_type)
    run.finished_at = timezone.now()
    run.save()
    return run

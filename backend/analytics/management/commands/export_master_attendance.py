"""Export retained attendance; no Teams/LMS imports and no database writes."""
import json
import datetime as dt
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from analytics.attendance.master_snapshot import build_snapshot, save_snapshot, ExportError, require
from analytics.attendance.master_writer import GoogleSheets, Publisher
from analytics.management.commands.run_sync_scheduler import ProcessLock


def validate_destination():
    require(settings.MASTER_ATTENDANCE_ENABLED, 'Attendance writer is disabled')
    require(settings.MASTER_ATTENDANCE_SPREADSHEET_ID, 'Explicit attendance destination is required')
    require(settings.MASTER_ATTENDANCE_TARGET_KIND in ('test', 'live'), 'Destination kind must be test or live')
    if settings.MASTER_ATTENDANCE_TARGET_KIND == 'test':
        require(settings.MASTER_ATTENDANCE_SPREADSHEET_ID != settings.GOOGLE_SHEETS_SPREADSHEET_ID,
                'A test destination cannot be the live LMS spreadsheet')
    else:
        require(settings.MASTER_ATTENDANCE_LIVE_WRITE_APPROVED, 'Live attendance writing has not been approved')
        require(settings.MASTER_ATTENDANCE_PUBLISHER_VERIFIED, 'LMS publisher preservation behavior is unverified; live writes disabled')
        require(settings.MASTER_ATTENDANCE_SPREADSHEET_ID == settings.GOOGLE_SHEETS_SPREADSHEET_ID,
                'Live attendance destination must be the approved LMS master')
    require(Path(settings.MASTER_ATTENDANCE_SERVICE_ACCOUNT_FILE).is_file(), 'Configured attendance writer credentials are unavailable')


class Command(BaseCommand):
    help = 'Build a private attendance snapshot; write only with an explicitly configured approved destination.'

    def add_arguments(self, parser):
        parser.add_argument('--write', action='store_true')
        parser.add_argument('--initialize', action='store_true', help='Explicitly create the eight attendance tabs on first publication')
        parser.add_argument('--if-enabled', action='store_true', help='Scheduled stage: exit successfully when disabled')
        parser.add_argument('--output', help='Private output/state directory; never place in the web root or Git')

    def handle(self, *args, **options):
        if options['if_enabled'] and not settings.MASTER_ATTENDANCE_ENABLED:
            self.stdout.write(json.dumps({'status': 'disabled'})); return
        directory = Path(options['output'] or settings.MASTER_ATTENDANCE_DIRECTORY).resolve()
        if options['write']:
            try: validate_destination()
            except ExportError as exc: raise CommandError(str(exc)) from None
        lock = ProcessLock(directory / 'export.lock')
        if not lock.acquire(): raise CommandError('Another attendance export is running')
        try:
            snapshot = build_snapshot()
            save_snapshot(snapshot, directory)
            result = {'status': 'snapshot_only', 'generation': snapshot['generation'], 'totals': snapshot['totals']}
            if options['write']:
                client = GoogleSheets(settings.MASTER_ATTENDANCE_SERVICE_ACCOUNT_FILE, settings.MASTER_ATTENDANCE_SPREADSHEET_ID)
                before = None
                if settings.MASTER_ATTENDANCE_TARGET_KIND == 'live':
                    backup = directory/'lms-backups'/dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
                    before = client.capture_lms(backup)
                    require(client.capture_lms(None) == before, 'LMS content changed during preflight; no publication attempted')
                result.update(Publisher(client, directory).publish(snapshot, initialize=options['initialize']))
                if before is not None:
                    require(client.capture_lms(None) == before, 'LMS preservation check failed; stop and investigate external refresh; no LMS restoration attempted')
                    result['lms_tabs_preserved'] = 15
            self.stdout.write(json.dumps(result, sort_keys=True))
        except ExportError as exc:
            raise CommandError(str(exc)) from None
        except Exception as exc:
            raise CommandError('Attendance export failed safely: '+type(exc).__name__+'; inspect private evidence') from None
        finally:
            lock.release()

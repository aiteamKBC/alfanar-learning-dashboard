r"""Import / synchronise the Alfanar learning export.

    python manage.py sync_alfanar --source excel [--path "..\data\Alfanar Endpoint.xlsx"]
    python manage.py sync_alfanar --source google
"""

from django.core.management.base import BaseCommand, CommandError

from analytics.importer.service import SyncInProgress, run_sync
from analytics.models import SyncRun


class Command(BaseCommand):
    help = "Synchronise learning data from the Excel workbook or Google Sheets (read-only)."

    def add_arguments(self, parser):
        parser.add_argument("--source", choices=["excel", "google"], default="excel")
        parser.add_argument("--path", help="Override workbook path (excel only)")

    def handle(self, *args, source, path=None, **opts):
        try:
            run = run_sync(source, path=path)
        except SyncInProgress as exc:
            raise CommandError(str(exc))
        self.stdout.write(f"Sync #{run.pk} [{run.source_type}] status={run.status} changed={run.changed}")
        for k, v in run.counts.items():
            if k != "load":
                self.stdout.write(f"  {k}: {v}")
        if isinstance(run.counts.get("load"), dict):
            for k, s in run.counts["load"].items():
                self.stdout.write(f"  load.{k}: +{s['created']} ~{s['updated']} -{s['deleted']}")
        for w in run.warnings:
            self.stdout.write(self.style.WARNING(f"  warning: {w}"))
        for e in run.errors[:20]:
            self.stdout.write(self.style.ERROR(f"  error: {e}"))
        if run.status != SyncRun.Status.SUCCESS:
            raise CommandError("Synchronisation failed; the previous dataset was preserved.")

import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from analytics.attendance.graph_client import GraphError
from analytics.attendance.graph_timing import import_graph_timing
from analytics.attendance.importer import AttendanceValidationError
from analytics.attendance.timing import TimingValidationError


class Command(BaseCommand):
    help = "Import Microsoft Graph timing with private Excel/cached Graph fallbacks; never changes attendance or LMS data."

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True, help="Private historical Teams workbook fallback")
        parser.add_argument("--config", required=True, help="Private approved timing policy")
        parser.add_argument("--cache-dir", default=str(settings.PROJECT_ROOT / "data/graph-timing/cache"),
                            help="Private Graph source snapshots, outside the web root and Git")
        parser.add_argument("--dry-run", action="store_true", help="Retrieve and validate evidence without changing the database")

    def handle(self, *args, **options):
        try:
            result = import_graph_timing(options["source"], options["config"], options["cache_dir"],
                dry_run=options["dry_run"], progress=lambda value: self.stderr.write(json.dumps(value)))
        except (GraphError, TimingValidationError, AttendanceValidationError) as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(json.dumps(result, sort_keys=True))

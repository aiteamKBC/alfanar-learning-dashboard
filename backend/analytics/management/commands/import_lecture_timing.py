import json

from django.core.management.base import BaseCommand, CommandError

from analytics.attendance.importer import AttendanceValidationError
from analytics.attendance.timing import TimingValidationError, import_timing


class Command(BaseCommand):
    help = "Import verified Teams timing only; preserve binary attendance and retain pending evidence."

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True, help="Private original Teams workbook")
        parser.add_argument("--config", required=True, help="Private verified timing policy JSON")
        parser.add_argument("--dry-run", action="store_true", help="Validate and count without changing the database")

    def handle(self, *args, **options):
        try:
            result = import_timing(options["source"], options["config"], dry_run=options["dry_run"])
        except (TimingValidationError, AttendanceValidationError) as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(json.dumps(result, sort_keys=True))

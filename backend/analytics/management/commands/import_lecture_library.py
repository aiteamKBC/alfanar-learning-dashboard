"""Import a private collected snapshot into the additive lecture library."""
import json
from django.core.management.base import BaseCommand, CommandError
from analytics.lectures.importer import import_source


class Command(BaseCommand):
    help = "Import lecture resources and cohort-matched attendance; preserves LMS and historical KPI tables."

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True)
        parser.add_argument("--speaker-bindings", help="Optional owner-verified Graph speaker identity bindings")

    def handle(self, *args, **options):
        try:
            result = import_source(options["source"], options["speaker_bindings"])
        except (ValueError, KeyError, OSError) as exc:
            raise CommandError(f"Lecture evidence could not be imported: {type(exc).__name__}") from None
        self.stdout.write(json.dumps(result))

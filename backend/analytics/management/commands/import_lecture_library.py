"""Import a private collected snapshot into the additive lecture library."""
import json
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from analytics.lectures.importer import import_source
from analytics.lectures.recordings import build_duration_index
from analytics.lectures.sync import atomic_json


class Command(BaseCommand):
    help = "Import lecture resources and cohort-matched attendance; preserves LMS and historical KPI tables."

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True)
        parser.add_argument("--speaker-bindings", help="Optional owner-verified Graph speaker identity bindings")

    def handle(self, *args, **options):
        try:
            result = import_source(options["source"], options["speaker_bindings"])
            atomic_json(settings.LECTURE_RECORDING_DURATIONS_FILE,
                        build_duration_index(json.loads(Path(options['source']).read_text(encoding='utf8'))))
        except (ValueError, KeyError, OSError) as exc:
            raise CommandError(f"Lecture evidence could not be imported: {type(exc).__name__}") from None
        self.stdout.write(json.dumps(result))

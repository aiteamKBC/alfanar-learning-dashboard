"""Build display-duration metadata; no database or original media writes."""
import json
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from analytics.lectures.recordings import build_duration_index
from analytics.lectures.sync import atomic_json


class Command(BaseCommand):
    help = 'Index exact recording durations from an approved private evidence snapshot.'

    def add_arguments(self, parser):
        parser.add_argument('--source', required=True)
        parser.add_argument('--output')

    def handle(self, *args, **options):
        try:
            source_path = Path(options['source'])
            output = Path(options['output'] or settings.LECTURE_RECORDING_DURATIONS_FILE)
            if source_path.resolve() == output.resolve():
                raise CommandError('The duration index must not overwrite its original evidence source.')
            source = json.loads(source_path.read_text(encoding='utf8'))
            value = build_duration_index(source)
            atomic_json(output, value)
        except (OSError, ValueError, KeyError, TypeError):
            raise CommandError('Could not build the private recording-duration index.') from None
        self.stdout.write(json.dumps({'recordings_indexed': len(value['durations']), 'database_changed': False}))

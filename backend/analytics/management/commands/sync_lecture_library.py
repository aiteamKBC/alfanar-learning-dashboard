"""Additive future library refresh, separate from official Teams/KPI discovery."""
import json
import os
from django.core.management.base import BaseCommand, CommandError
from analytics.lectures.sync import run_sync


class Command(BaseCommand):
    help = 'Collect future Teams attendance/media using the existing lecture-library rules.'

    def add_arguments(self, parser):
        parser.add_argument('--config', default=os.environ.get('LECTURE_LIBRARY_SYNC_CONFIG'))
        parser.add_argument('--dry-run', action='store_true', help='Read Graph and roll back library writes.')

    def handle(self, *args, **options):
        if not options['config']:
            raise CommandError('Set LECTURE_LIBRARY_SYNC_CONFIG or --config to the private policy file.')
        try:
            result = run_sync(options['config'], dry_run=options['dry_run'])
        except Exception as exc:
            # Neither arbitrary DB errors nor Graph bodies/URLs enter logs.
            raise CommandError('Library synchronization stopped safely: ' + type(exc).__name__) from None
        self.stdout.write(json.dumps(result, sort_keys=True))
        if result['status'] != 'success':
            raise CommandError('Library synchronization retained prior evidence; some sources need retry.')

"""Apply an existing private restoration plan without recapturing source evidence."""
import json
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from analytics.attendance.timing import digest


class Command(BaseCommand):
    help = 'Apply the already reviewed identity plan with the existing verified backup and private package.'

    def add_arguments(self, parser):
        parser.add_argument('--release-dir', required=True)
        parser.add_argument('--backup', required=True, help='Exact path to the existing verified SQLite backup.')

    def handle(self, *args, **options):
        release = Path(options['release_dir'])
        try:
            reviewed = json.loads((release / 'dry-run-private.json').read_text(encoding='utf-8'))
            plan = {key: reviewed[key] for key in (
                'before', 'after', 'source_sha256', 'matrix_sha256', 'timing_fingerprint')}
            # The existing command hashes integer LMS IDs before JSON serializes
            # them as object keys. Restore their type to retain the reviewed hash.
            plan['before']['manual_mappings'] = {
                int(key): value for key, value in plan['before']['manual_mappings'].items()}
            valid = reviewed['applied'] is False and digest(plan) == reviewed['plan_sha256']
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            valid = False
        if not valid:
            raise CommandError('The existing reviewed dry-run plan is missing or invalid; no restoration attempted.')
        call_command('restore_original_identities',
            lms_json=str(release / 'current-lms.json'),
            timing_source=str(release / 'teams-timing.xlsx'),
            timing_policy=str(release / 'timing-policy.json'),
            graph_cache=str(release / 'graph-cache.json'),
            matrices=str(release / 'attendance'),
            report=str(release / 'applied-private.json'),
            apply=True, expected_plan=reviewed['plan_sha256'], backup=options['backup'],
            owner='owner_review', stdout=self.stdout, stderr=self.stderr)

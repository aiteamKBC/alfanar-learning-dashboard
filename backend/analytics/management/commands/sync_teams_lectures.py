"""One unattended, locked pass over every approved module; no production scheduling here."""
import json
import os
from django.core.management.base import BaseCommand, CommandError
from analytics.attendance.discovery import DiscoveryInProgress, run_discovery
from analytics.attendance.graph_client import GraphError
from analytics.attendance.timing import TimingValidationError


class Command(BaseCommand):
    help = 'Discover and verify new Teams lecture occurrences for all four modules.'

    def add_arguments(self, parser):
        parser.add_argument('--config', default=os.environ.get('TEAMS_DISCOVERY_CONFIG'))
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        if not options['config']:
            raise CommandError('TEAMS_DISCOVERY_CONFIG or --config must point to the private approved configuration')
        try:
            result = run_discovery(options['config'], dry_run=options['dry_run'])
        except DiscoveryInProgress as exc:
            raise CommandError(str(exc)) from None
        except GraphError as exc:
            raise CommandError(str(exc)) from None
        except TimingValidationError as exc:
            raise CommandError(str(exc)) from None
        except Exception as exc:
            # Arbitrary DB/HTTP exception text can contain private identifiers.
            raise CommandError('Teams synchronization failed safely: ' + type(exc).__name__) from None
        self.stdout.write(json.dumps(result, sort_keys=True))
        if result['status'] != 'success':
            raise CommandError('Teams synchronization completed with unresolved discovery gaps; inspect private run diagnostics')

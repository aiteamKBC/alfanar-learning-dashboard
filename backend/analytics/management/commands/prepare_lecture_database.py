"""Create/migrate ONLY the dedicated PostgreSQL Lecture Library schema."""
import io
import json
from django.conf import settings
from django.core.management import BaseCommand, CommandError, call_command
from django.db import connections, transaction
from config.database_router import LIBRARY_ALIAS
from lecture_library.storage import inspect_schema


class Command(BaseCommand):
    help = 'Inspect library storage; --apply creates/migrates only its isolated PostgreSQL schema.'

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument('--apply', action='store_true')
        mode.add_argument('--verify-only', action='store_true')

    def handle(self, *args, **options):
        if connections['default'].vendor != 'sqlite':
            raise CommandError('Dashboard default must remain SQLite.')
        try:
            tables = inspect_schema(require_ready=options['verify_only'])
            if options['apply']:
                connection = connections[LIBRARY_ALIAS]
                schema = settings.LECTURE_LIBRARY_DATABASE_SCHEMA
                with transaction.atomic(using=LIBRARY_ALIAS):
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', ['alfanar-library:' + schema])
                        inspect_schema()
                        cursor.execute(f'CREATE SCHEMA IF NOT EXISTS {connection.ops.quote_name(schema)}')
                    call_command('migrate', 'lecture_library', database=LIBRARY_ALIAS, interactive=False,
                                 verbosity=0, stdout=io.StringIO(), stderr=io.StringIO())
                    tables = inspect_schema(require_ready=True)
            self.stdout.write(json.dumps({'database': LIBRARY_ALIAS, 'schema': settings.LECTURE_LIBRARY_DATABASE_SCHEMA,
                'tables': sorted(tables), 'applied': options['apply'], 'verified': options['apply'] or options['verify_only']}))
        except CommandError:
            raise
        except Exception as exc:
            raise CommandError(f'Lecture database preparation failed ({type(exc).__name__}); no dashboard data was transferred. Check private server logs/configuration.') from None

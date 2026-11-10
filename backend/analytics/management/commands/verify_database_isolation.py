"""Read-only SQLite row manifest and optional library schema check for deployment."""
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from django.conf import settings
from django.core.management import BaseCommand, CommandError
from lecture_library.storage import inspect_schema


class Command(BaseCommand):
    help = 'Verify existing SQLite data without opening it for writes; optionally compare a pre-deploy manifest.'
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument('--output')
        parser.add_argument('--compare')
        parser.add_argument('--library', action='store_true')

    def handle(self, *args, **options):
        config = settings.DATABASES['default']
        path = Path(config['NAME']).resolve()
        if config['ENGINE'] != 'django.db.backends.sqlite3' or not path.is_file():
            raise CommandError('Expected an existing dashboard SQLite database; no database was created.')
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
            if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise CommandError('Dashboard SQLite integrity check failed.')
            tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'analytics_%'")]
            manifest = {}
            for table in sorted(tables):
                quoted = '"' + table.replace('"', '""') + '"'
                columns = sorted(row[1] for row in db.execute(f'PRAGMA table_info({quoted})'))
                selected = ', '.join('"' + column.replace('"', '""') + '"' for column in columns)
                rows = db.execute(f'SELECT {selected} FROM {quoted}').fetchall()
                hashes = sorted(hashlib.sha256(json.dumps(row, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest() for row in rows)
                manifest[table] = {'columns': columns, 'rows': len(rows), 'sha256': hashlib.sha256('\n'.join(hashes).encode()).hexdigest()}
            if not {'analytics_learner', 'analytics_enrollment'} <= set(tables):
                raise CommandError('This SQLite file is not the existing dashboard registry.')
        result = {'sqlite': str(path), 'tables': manifest}
        if options['compare']:
            baseline = json.loads(Path(options['compare']).read_text(encoding='utf-8'))
            if baseline['sqlite'] != result['sqlite'] or baseline['tables'] != manifest:
                raise CommandError('SQLite path or existing analytics rows changed; stop and inspect the private baseline. No rows were overwritten.')
        if options['library']:
            try:
                inspect_schema(require_ready=True)
            except CommandError:
                raise
            except Exception as exc:
                raise CommandError(f'Lecture Library verification failed ({type(exc).__name__}).') from None
        if options['output']:
            with Path(options['output']).open('x', encoding='utf-8') as file:
                json.dump(result, file, indent=2)
        self.stdout.write(json.dumps({'sqlite': str(path), 'learners': manifest['analytics_learner']['rows'],
            'enrollments': manifest['analytics_enrollment']['rows'], 'tables_verified': len(manifest),
            'baseline_matches': bool(options['compare']), 'library_verified': options['library']}))

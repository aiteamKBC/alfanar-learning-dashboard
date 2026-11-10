"""Production library job: load the existing backend environment privately."""
import argparse
import os
import sys
from pathlib import Path

from dotenv import dotenv_values


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    app = Path(__file__).resolve().parent.parent
    for key, value in dotenv_values(app / 'backend/.env').items():
        if value is not None and key.lower() not in {'username', 'password'}:
            os.environ[key] = value
    os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'
    sys.path.insert(0, str(app / 'backend'))
    import django
    django.setup()
    from django.db import connections
    from django.core.management import call_command
    default = connections['default']
    if default.vendor != 'sqlite' or Path(default.settings_dict['NAME']).resolve() != app / 'backend/db.sqlite3':
        raise SystemExit('Unexpected production registry; no synchronization executed.')
    # Enforced for this process only; the dashboard's other connections are unchanged.
    default.cursor().execute('PRAGMA query_only=ON')
    call_command('sync_lecture_library', config=args.config, dry_run=args.dry_run)


if __name__ == '__main__':
    main()

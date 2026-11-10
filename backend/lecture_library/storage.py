"""Inspect only the dedicated library schema; never import dashboard rows."""
from django.conf import settings
from django.core.management.base import CommandError
from django.db import connections
from config.database_router import LIBRARY_ALIAS

LIBRARY_TABLES = {'lecture_library_lecturecapture', 'lecture_library_lecturelearner', 'lecture_library_lectureresource'}


def inspect_schema(require_ready=False):
    connection = connections[LIBRARY_ALIAS]
    if connection.vendor != 'postgresql':
        raise CommandError('Configure the Lecture Library PostgreSQL URL first.')
    schema = settings.LECTURE_LIBRARY_DATABASE_SCHEMA
    with connection.cursor() as cursor:
        cursor.execute('SELECT table_name FROM information_schema.tables WHERE table_schema=%s', [schema])
        tables = {row[0] for row in cursor.fetchall()}
        if tables - LIBRARY_TABLES - {'django_migrations'}:
            raise CommandError('The library schema contains unrelated tables. Use a separate empty schema; nothing was transferred or overwritten.')
        if require_ready:
            if not LIBRARY_TABLES <= tables:
                raise CommandError('Lecture Library migrations are not complete.')
            cursor.execute('SELECT current_schema()')
            if cursor.fetchone()[0] != schema:
                raise CommandError('Lecture Library search_path is not isolated.')
            for table in LIBRARY_TABLES:
                for constraint in connection.introspection.get_constraints(cursor, table).values():
                    if constraint.get('foreign_key') and constraint['foreign_key'][0] not in LIBRARY_TABLES:
                        raise CommandError('A library foreign key points outside the isolated library tables.')
    return tables

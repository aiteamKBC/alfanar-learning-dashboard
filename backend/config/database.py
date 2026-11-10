"""Database location settings; independent of analytics and identity policies."""
import re
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured


def validate_schema(value):
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", value or "") or value.startswith("pg_") or value == "information_schema":
        raise ImproperlyConfigured("DATABASE_SCHEMA must be a simple lowercase PostgreSQL schema name.")
    return value


def database_from_url(url, schema=""):
    database = dj_database_url.parse(url, conn_max_age=60)
    if schema:
        validate_schema(schema)
        if database["ENGINE"] != "django.db.backends.postgresql":
            raise ImproperlyConfigured("DATABASE_SCHEMA requires PostgreSQL.")
        host = database.get("HOST", "")
        if host.endswith(".neon.tech") and "-pooler." in host:
            # The same Neon endpoint's direct route supports the session state
            # required for a dedicated schema; transaction pooling does not.
            database["HOST"] = host.replace("-pooler.", ".", 1)
        options = database.setdefault("OPTIONS", {})
        # Keep SSL and other URL options. An explicit search_path isolates all
        # Django tables from public and from other applications in the database.
        options["options"] = (options.get("options", "") + f" -c search_path={schema}").strip()
    return database


def isolated_databases(base_dir, environ):
    """DATABASE_URL can never select the dashboard database."""
    path = Path(environ.get('DASHBOARD_SQLITE_PATH') or base_dir / 'db.sqlite3')
    if not path.is_absolute():
        path = base_dir.parent / path
    databases = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': path.resolve()}}
    # Legacy PostgreSQL URLs are accepted ONLY for the new library alias.
    url = environ.get('LECTURE_LIBRARY_DATABASE_URL', '').strip()
    legacy = environ.get('DATABASE_URL', '').strip()
    if not url and legacy.startswith(('postgres://', 'postgresql://', 'pgsql://')):
        url = legacy
    schema = environ.get('LECTURE_LIBRARY_DATABASE_SCHEMA') or 'alfanar_lecture_library'
    validate_schema(schema)
    if schema in {'public', 'alfanar_dashboard'}:
        raise ImproperlyConfigured('Lecture Library requires its own schema, not public or alfanar_dashboard.')
    if url:
        try:
            database = database_from_url(url, schema)
        except (ValueError, ImproperlyConfigured):
            raise ImproperlyConfigured('Invalid Lecture Library PostgreSQL configuration.') from None
        if database['ENGINE'] != 'django.db.backends.postgresql':
            raise ImproperlyConfigured('LECTURE_LIBRARY_DATABASE_URL must use PostgreSQL.')
        databases['lecture_library'] = database
    else:
        # Fail closed for the library; never fall back to SQLite.
        databases['lecture_library'] = {'ENGINE': 'django.db.backends.dummy'}
    return databases, schema

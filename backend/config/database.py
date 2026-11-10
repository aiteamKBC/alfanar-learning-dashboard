"""Database location settings; independent of analytics and identity policies."""
import re

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

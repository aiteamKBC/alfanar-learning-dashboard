"""Exact storage transfer: no normalization, reconciliation, sync or identity changes."""
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from django.apps import apps
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connections


def snapshot_sqlite(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not source.is_file() or source == destination or destination.exists():
        raise ValueError("A valid source and a new, separate backup path are required.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Opening mode=ro prevents even a missing source from becoming an empty DB.
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original:
        if original.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("SQLite integrity check failed.")
        with closing(sqlite3.connect(destination)) as backup:
            original.backup(backup)
    return destination


def transfer_models():
    return sorted((model for model in apps.get_models(include_auto_created=True)
                   if model._meta.managed and not model._meta.proxy), key=lambda model: model._meta.db_table)


def manifest(alias, models):
    result = {}
    for model in models:
        fields = model._meta.local_concrete_fields
        hashes = []
        for row in model._base_manager.using(alias).values_list(*(f.attname for f in fields)).iterator(chunk_size=500):
            payload = json.dumps(row, cls=DjangoJSONEncoder, sort_keys=True, ensure_ascii=False,
                                 separators=(",", ":")).encode("utf8")
            hashes.append(hashlib.sha256(payload).hexdigest())
        # Database collations may order rows differently; PKs are part of each
        # complete row hash, so this still detects every value/identity change.
        digest = hashlib.sha256(("\n".join(sorted(hashes))).encode()).hexdigest()
        result[model._meta.db_table] = {"rows": len(hashes), "sha256": digest}
    return result


def copy_rows(source, target, models):
    connection = connections[target]
    quote = connection.ops.quote_name
    with connection.cursor() as cursor:
        for model in models:
            fields = model._meta.local_concrete_fields
            columns = ", ".join(quote(f.column) for f in fields)
            placeholders = ", ".join(["%s"] * len(fields))
            sql = f"INSERT INTO {quote(model._meta.db_table)} ({columns}) VALUES ({placeholders})"
            batch = []
            rows = model._base_manager.using(source).values_list(*(f.attname for f in fields)).iterator(chunk_size=500)
            for row in rows:
                # Raw INSERT bypasses auto_now/pre_save and all model/business
                # callbacks, preserving the original timestamps and identifiers.
                batch.append(tuple(f.get_db_prep_save(value, connection) for f, value in zip(fields, row)))
                if len(batch) == 500:
                    cursor.executemany(sql, batch)
                    batch = []
            if batch:
                cursor.executemany(sql, batch)

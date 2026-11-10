"""Create an isolated schema and migrate an immutable copy of the local database."""
import io
import json
import uuid
from pathlib import Path

from django.conf import settings
from django.core.management import BaseCommand, CommandError, call_command
from django.core.management.color import no_style
from django.db import connections, transaction
from django.db.migrations.loader import MigrationLoader
from django.utils import timezone

from config.database import validate_schema
from analytics.database_transfer import copy_rows, manifest, snapshot_sqlite, transfer_models


class Command(BaseCommand):
    help = "Dry-run an exact SQLite copy to an isolated PostgreSQL schema; --apply writes only into an empty schema."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--source", default=str(settings.BASE_DIR / "db.sqlite3"))
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--apply", action="store_true")
        mode.add_argument("--verify-only", action="store_true")

    def handle(self, *args, **options):
        self.phase = "configuration"
        self.committed = False
        try:
            self.transfer(options)
        except CommandError:
            raise
        except Exception as exc:
            # Raw driver errors can include connection details. Never echo them.
            cause = exc.__cause__ or exc
            code = getattr(cause, "sqlstate", None) or "none"
            outcome = "Destination was already committed; use --verify-only." if self.committed else "No destination data was committed."
            raise CommandError(f"Database transfer stopped at {self.phase} ({type(exc).__name__}, SQLSTATE={code}). {outcome}") from None

    def transfer(self, options):
        target = connections["default"]
        schema = validate_schema(getattr(settings, "DATABASE_SCHEMA", ""))
        if target.vendor != "postgresql" or schema == "public":
            raise CommandError("Use PostgreSQL and a dedicated DATABASE_SCHEMA, not public.")
        stamp = timezone.now().strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        directory = settings.PROJECT_ROOT / "data" / "database-transfer" / stamp
        self.phase = "source_snapshot"
        snapshot = snapshot_sqlite(options["source"], directory / "source.sqlite3")
        alias = "transfer_source"
        connections.databases[alias] = {
            "ENGINE": "django.db.backends.sqlite3", "NAME": snapshot.as_uri() + "?mode=ro",
            "OPTIONS": {"uri": True}, "TIME_ZONE": None, "CONN_MAX_AGE": 0,
            "CONN_HEALTH_CHECKS": False, "AUTOCOMMIT": True, "ATOMIC_REQUESTS": False,
            "USER": "", "PASSWORD": "", "HOST": "", "PORT": "", "TEST": {},
        }
        try:
            self.phase = "source_migration_check"
            loader = MigrationLoader(connections[alias])
            loader.check_consistent_history(connections[alias])
            if set(loader.graph.nodes) - set(loader.applied_migrations):
                raise CommandError("Apply the current project migrations to the SQLite source before copying.")
            models = transfer_models()
            self.phase = "source_manifest"
            expected = manifest(alias, models)
            self.phase = "target_inspection"
            with target.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relkind IN ('r','p','v','m','S','f')", [schema])
                occupied = cursor.fetchone()[0] > 0
            report = {"schema": schema, "mode": "verify" if options["verify_only"] else "apply" if options["apply"] else "dry_run",
                      "source_backup": str(snapshot), "source_tables": expected, "target_empty": not occupied}
            if options["verify_only"]:
                if not occupied or manifest("default", models) != expected:
                    raise CommandError("Destination and source do not match; no data was changed.")
                report["verified"] = True
            elif occupied:
                raise CommandError("Destination schema is not empty; no existing data will be overwritten. Use --verify-only to compare.")
            elif options["apply"]:
                with transaction.atomic(using="default"):
                    with target.cursor() as cursor:
                        cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ["alfanar-transfer:" + schema])
                        cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {target.ops.quote_name(schema)}")
                        cursor.execute(f"SET LOCAL search_path TO {target.ops.quote_name(schema)}")
                        # Recheck after the lock to guard a concurrent transfer.
                        cursor.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=%s", [schema])
                        if cursor.fetchone()[0]:
                            raise CommandError("Destination changed during preparation; copy cancelled.")
                    self.phase = "destination_migrations"
                    call_command("migrate", database="default", interactive=False, verbosity=0, stdout=io.StringIO(), stderr=io.StringIO())
                    with target.cursor() as cursor:
                        cursor.execute("SET CONSTRAINTS ALL DEFERRED")
                        # Only Django's newly generated metadata exists here.
                        # Restore the source IDs along with its account permissions.
                        cursor.execute('DELETE FROM "auth_permission"')
                        cursor.execute('DELETE FROM "django_content_type"')
                    self.phase = "row_copy"
                    copy_rows(alias, "default", models)
                    with target.cursor() as cursor:
                        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
                        for sql in target.ops.sequence_reset_sql(no_style(), models):
                            cursor.execute(sql)
                    self.phase = "row_verification"
                    if manifest("default", models) != expected:
                        raise CommandError("Row verification failed; the entire destination transaction was rolled back.")
                report["verified"] = True
                self.committed = True
            self.phase = "report_write"
            (directory / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf8")
            self.stdout.write(json.dumps({"mode": report["mode"], "schema": schema,
                "tables": len(expected), "rows": sum(x["rows"] for x in expected.values()),
                "verified": report.get("verified", False), "report": str(directory / "verification.json")}))
        finally:
            connections[alias].close()
            del connections[alias]
            connections.databases.pop(alias, None)

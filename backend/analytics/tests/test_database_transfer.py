import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, TestCase

from analytics.database_transfer import manifest, snapshot_sqlite
from analytics.models import Learner
from config.database import database_from_url, validate_schema


class DatabaseConfigurationTests(SimpleTestCase):
    def test_schema_preserves_ssl_and_url_options(self):
        config = database_from_url("postgresql://synthetic:password@localhost/example?sslmode=require", "alfanar_dashboard")
        self.assertEqual(config["OPTIONS"]["sslmode"], "require")
        self.assertEqual(config["OPTIONS"]["options"], "-c search_path=alfanar_dashboard")

    def test_no_schema_preserves_existing_connection_behavior(self):
        config = database_from_url("postgresql://synthetic:password@localhost/example")
        self.assertNotIn("options", config.get("OPTIONS", {}))

    def test_neon_schema_uses_same_endpoint_direct_route(self):
        url = "postgresql://synthetic:password@ep-example-pooler.region.aws.neon.tech/example?sslmode=require"
        pooled = database_from_url(url)
        direct = database_from_url(url, "alfanar_dashboard")
        self.assertEqual(pooled["HOST"], "ep-example-pooler.region.aws.neon.tech")
        self.assertEqual(direct["HOST"], "ep-example.region.aws.neon.tech")
        for field in ("USER", "PASSWORD", "NAME", "PORT"):
            self.assertEqual(pooled[field], direct[field])
        self.assertEqual(direct["OPTIONS"]["sslmode"], "require")

    def test_invalid_schema_cannot_inject_connection_options(self):
        for value in ("", "pg_catalog", "information_schema", "public,other", "x -c other=value", 'x";drop schema public', "a" * 64):
            with self.subTest(value=value), self.assertRaises(ImproperlyConfigured):
                validate_schema(value)

    def test_snapshot_does_not_modify_original_and_cannot_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, backup = Path(tmp) / "source.sqlite3", Path(tmp) / "backup.sqlite3"
            with closing(sqlite3.connect(source)) as connection, connection:
                connection.execute("CREATE TABLE evidence (id INTEGER PRIMARY KEY, value TEXT)")
                connection.execute("INSERT INTO evidence VALUES (12, 'retained evidence')")
            original = source.read_bytes()
            snapshot_sqlite(source, backup)
            self.assertEqual(source.read_bytes(), original)
            with closing(sqlite3.connect(backup)) as connection:
                self.assertEqual(connection.execute("SELECT * FROM evidence").fetchall(), [(12, "retained evidence")])
            for first, second in ((source, source), (source, backup), (Path(tmp) / "missing.sqlite3", backup)):
                with self.assertRaises(ValueError):
                    snapshot_sqlite(first, second)


class DataManifestTests(TestCase):
    def test_same_row_count_does_not_hide_a_changed_value_or_primary_key(self):
        row = Learner.objects.create(source_id=123, name="Synthetic learner", email="synthetic@example.invalid")
        before = manifest("default", [Learner])
        Learner.objects.filter(pk=row.pk).update(email="changed@example.invalid")
        changed = manifest("default", [Learner])
        self.assertEqual(before["analytics_learner"]["rows"], changed["analytics_learner"]["rows"])
        self.assertNotEqual(before["analytics_learner"]["sha256"], changed["analytics_learner"]["sha256"])

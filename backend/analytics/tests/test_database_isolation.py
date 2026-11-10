import io
import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from unittest import mock

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command, CommandError
from django.db import connections, models
from django.db.migrations.executor import MigrationExecutor
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.contrib.auth.models import User

from config.database import isolated_databases
from config.database_router import DatabaseRouter
from analytics.models import Course, Learner, Enrollment
from lecture_library.models import LectureCapture, LectureLearner, LectureResource


class ConfigurationTests(SimpleTestCase):
    def test_legacy_postgres_url_cannot_redirect_dashboard_or_reuse_old_schema(self):
        base = Path('/application/backend')
        db, schema = isolated_databases(base, {'DATABASE_URL': 'postgresql://test:test@localhost/example', 'DATABASE_SCHEMA': 'alfanar_dashboard'})
        self.assertEqual(db['default']['ENGINE'], 'django.db.backends.sqlite3')
        self.assertEqual(db['default']['NAME'], (base / 'db.sqlite3').resolve())
        self.assertEqual(db['lecture_library']['ENGINE'], 'django.db.backends.postgresql')
        self.assertEqual(schema, 'alfanar_lecture_library')

    def test_explicit_paths_take_precedence_and_library_cannot_use_sqlite(self):
        base = Path('/application/backend')
        db, _ = isolated_databases(base, {'DASHBOARD_SQLITE_PATH': 'private/dashboard.sqlite3',
            'DATABASE_URL': 'postgresql://test:test@localhost/old', 'LECTURE_LIBRARY_DATABASE_URL': 'postgresql://test:test@localhost/new'})
        self.assertEqual(db['lecture_library']['NAME'], 'new')
        self.assertEqual(db['default']['NAME'], (base.parent / 'private/dashboard.sqlite3').resolve())
        with self.assertRaises(ImproperlyConfigured):
            isolated_databases(base, {'LECTURE_LIBRARY_DATABASE_URL': 'sqlite:///wrong.sqlite3'})
        for schema in ['public', 'alfanar_dashboard', 'pg_catalog', 'x,public']:
            with self.subTest(schema=schema), self.assertRaises(ImproperlyConfigured):
                isolated_databases(base, {'LECTURE_LIBRARY_DATABASE_SCHEMA': schema})

    def test_unconfigured_library_never_falls_back_to_sqlite(self):
        db, _ = isolated_databases(Path('/application/backend'), {'DATABASE_URL': 'sqlite:///old-test.sqlite3'})
        self.assertEqual(db['lecture_library']['ENGINE'], 'django.db.backends.dummy')
        self.assertEqual(db['default']['NAME'].name, 'db.sqlite3')

    def test_only_library_models_can_migrate_to_postgres(self):
        router = DatabaseRouter()
        for model in apps.get_models():
            library = model._meta.app_label == 'lecture_library'
            self.assertEqual(router.db_for_read(model), 'lecture_library' if library else 'default')
            self.assertEqual(router.db_for_write(model), 'lecture_library' if library else 'default')
            self.assertEqual(router.allow_migrate('lecture_library', model._meta.app_label, model._meta.model_name), library)
            self.assertEqual(router.allow_migrate('default', model._meta.app_label, model._meta.model_name), not library)
        self.assertFalse(router.allow_migrate('default', 'analytics', 'lecturecapture'))
        self.assertFalse(router.allow_migrate('lecture_library', 'analytics'))
        self.assertFalse(router.allow_relation(Course(), LectureCapture()))
        self.assertTrue(router.allow_relation(LectureCapture(), LectureResource()))

    def test_transfer_command_is_disabled_before_any_connection_or_write(self):
        for option in [[], ['--apply'], ['--verify-only']]:
            with self.subTest(option=option), self.assertRaisesMessage(CommandError, 'transfer is disabled'):
                call_command('transfer_sqlite_to_postgres', *option)


class StorageBoundaryTests(TestCase):
    databases = {'default', 'lecture_library'}

    def setUp(self):
        self.course = Course.objects.create(id=22, source_id=83334, title='Planning')
        self.learner = Learner.objects.create(id=44, source_id=100, name='Synthetic learner', email='synthetic@example.invalid')
        Enrollment.objects.create(course=self.course, learner=self.learner)
        self.lecture = LectureCapture.objects.create(source_key='synthetic', course_source_id=83334, source_fingerprint='test')
        LectureLearner.objects.create(lecture=self.lecture, learner_source_id=100, source_emails=[self.learner.email], attendance='present')
        self.client.force_login(User.objects.create_user('owner_review'))

    def test_separate_tables_and_only_internal_library_foreign_keys(self):
        default = set(connections['default'].introspection.table_names())
        library = set(connections['lecture_library'].introspection.table_names())
        self.assertIn('analytics_learner', default)
        self.assertNotIn('analytics_lecturecapture', default)
        self.assertFalse(any(t.startswith('lecture_library_') for t in default))
        self.assertEqual(library, {'django_migrations', *(m._meta.db_table for m in (LectureCapture, LectureLearner, LectureResource))})
        for model in (LectureCapture, LectureLearner, LectureResource):
            for field in model._meta.get_fields():
                if field.is_relation:
                    self.assertEqual(field.related_model._meta.app_label, 'lecture_library')

    def test_registry_is_joined_by_source_ids_not_database_primary_keys(self):
        payload = self.client.get(f'/api/lectures/{self.lecture.pk}').json()
        self.assertEqual(payload['course_id'], 83334)
        self.assertEqual([r['learner_id'] for r in payload['participants']], [100])
        self.assertEqual(self.client.get('/api/lectures?course=83334').json()['count'], 1)
        self.assertEqual(self.client.get('/api/lectures?course=22').json()['count'], 0)

    def test_dashboard_and_identity_deletion_do_not_need_library_connection(self):
        with mock.patch.object(connections['lecture_library'], 'cursor', side_effect=AssertionError('Unexpected library query')):
            for url in ['/api/learners', '/api/dashboard/summary', '/api/filters']:
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200, url)
            self.learner.delete()
            self.course.delete()
        self.assertEqual(LectureLearner.objects.count(), 1)  # evidence retained, never cascaded from SQLite
        self.assertEqual(self.client.get(f'/api/lectures/{self.lecture.pk}').status_code, 404)

    def test_recreated_registry_primary_keys_do_not_break_or_misattribute_library(self):
        self.learner.delete()
        replacement = Learner.objects.create(id=90, source_id=100, name='Synthetic learner', email='synthetic@example.invalid')
        Enrollment.objects.create(course=self.course, learner=replacement)
        payload = self.client.get(f'/api/lectures/{self.lecture.pk}').json()
        self.assertEqual(payload['participants'][0]['attendance'], 'present')
        replacement.email = 'different@example.invalid'; replacement.save()
        payload = self.client.get(f'/api/lectures/{self.lecture.pk}').json()
        self.assertEqual(payload['participants'][0]['attendance'], 'not_available')

    def test_unconfigured_library_returns_503_without_affecting_dashboard(self):
        with mock.patch.dict(settings.DATABASES, {'lecture_library': {**settings.DATABASES['lecture_library'], 'ENGINE': 'django.db.backends.dummy'}}):
            self.assertEqual(self.client.get('/api/lectures').status_code, 503)
            self.assertEqual(self.client.get('/api/learners').status_code, 200)


class LegacyArchiveMigrationTests(TransactionTestCase):
    def test_populated_legacy_sqlite_tables_retain_every_value_and_release_registry_fks(self):
        connection = connections['default']
        prior = [('analytics', '0011_merge_lecture_library_and_owner_identity')]
        latest = [('analytics', '0012_retire_legacy_lecture_state')]
        executor = MigrationExecutor(connection); executor.migrate(prior)
        old = executor.loader.project_state(prior).apps
        legacy = [old.get_model('analytics', name) for name in ['LectureCapture', 'LectureLearner', 'LectureResource']]
        try:
            with connection.schema_editor() as editor:
                for model in legacy: editor.create_model(model)
            course = Course.objects.create(source_id=83334, title='Synthetic course')
            learner = Learner.objects.create(source_id=123, name='Synthetic learner')
            capture = legacy[0].objects.using('default').create(source_key='retained', course_id=course.pk, source_fingerprint='unchanged')
            legacy[1].objects.using('default').create(lecture_id=capture.pk, learner_id=learner.pk, duration_seconds=123.4, source_emails=['test@example.invalid'])
            legacy[2].objects.using('default').create(lecture_id=capture.pk, source_id='source', kind='transcript', text_content='retained evidence')
            def rows():
                with connection.cursor() as cursor:
                    result = {}
                    for model in legacy:
                        columns = sorted(field.column for field in model._meta.local_concrete_fields)
                        selected = ', '.join(connection.ops.quote_name(column) for column in columns)
                        cursor.execute(f'SELECT {selected} FROM {connection.ops.quote_name(model._meta.db_table)}')
                        result[model._meta.db_table] = cursor.fetchall()
                    return result
            before = rows()
            MigrationExecutor(connection).migrate(latest)
            self.assertEqual(rows(), before)
            with connection.cursor() as cursor:
                for model in legacy:
                    constraints = connection.introspection.get_constraints(cursor, model._meta.db_table)
                    self.assertFalse(any(c.get('foreign_key', (None,))[0] in {'analytics_course', 'analytics_learner'} for c in constraints.values() if c.get('foreign_key')))
            learner.delete(); course.delete()
            self.assertEqual(rows(), before)
        finally:
            MigrationExecutor(connection).migrate(latest)
            with connection.schema_editor() as editor:
                for model in reversed(legacy): editor.delete_model(model)


class DeploymentManifestTests(SimpleTestCase):
    def test_manifest_detects_changed_values_and_missing_database_without_creating_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'synthetic.sqlite3'
            manifest = Path(directory) / 'before.json'
            config = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': path}}
            with mock.patch.dict(settings.DATABASES, config):
                with self.assertRaisesMessage(CommandError, 'existing dashboard SQLite'):
                    call_command('verify_database_isolation', stdout=io.StringIO())
                self.assertFalse(path.exists())
                with closing(sqlite3.connect(path)) as db, db:
                    db.execute('CREATE TABLE analytics_learner (id INTEGER, name TEXT)')
                    db.execute('CREATE TABLE analytics_enrollment (id INTEGER, learner_id INTEGER)')
                    db.execute("INSERT INTO analytics_learner VALUES (1, 'Synthetic learner')")
                    db.execute('INSERT INTO analytics_enrollment VALUES (1, 1)')
                call_command('verify_database_isolation', output=str(manifest), stdout=io.StringIO())
                call_command('verify_database_isolation', compare=str(manifest), stdout=io.StringIO())
                with closing(sqlite3.connect(path)) as db, db:
                    db.execute("UPDATE analytics_learner SET name='Different synthetic learner'")
                with self.assertRaisesMessage(CommandError, 'existing analytics rows changed'):
                    call_command('verify_database_isolation', compare=str(manifest), stdout=io.StringIO())
                self.assertEqual(json.loads(manifest.read_text())['tables']['analytics_learner']['rows'], 1)

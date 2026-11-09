"""Authorization and saved-plan dispatch; all accounts and plans are synthetic."""
import io
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase

from analytics.attendance.timing import digest
from analytics.models import DashboardAccess


class RestorationOwnerTests(TestCase):
    def run_restore(self, **kwargs):
        return call_command('restore_original_identities',
            lms_json='unused', timing_source='unused', timing_policy='unused',
            graph_cache='unused', matrices='unused', report='unused',
            stdout=io.StringIO(), **kwargs)

    def test_missing_or_inactive_owner_refused_without_creating_account_or_grant(self):
        with self.assertRaisesMessage(CommandError, 'must be active'):
            self.run_restore()
        self.assertFalse(get_user_model().objects.exists())
        owner = get_user_model().objects.create_user('owner_review', is_active=False)
        with self.assertRaisesMessage(CommandError, 'must be active'):
            self.run_restore()
        owner.refresh_from_db()
        self.assertFalse(owner.is_active)
        self.assertFalse(DashboardAccess.objects.exists())

    def test_other_account_is_refused_even_with_staff_superuser_and_grant(self):
        other = get_user_model().objects.create_user('synthetic-admin', is_staff=True, is_superuser=True)
        DashboardAccess.objects.create(user=other, can_manage_identities=True)
        with self.assertRaisesMessage(CommandError, 'requires the existing owner_review'):
            self.run_restore(owner=other.username)

    def test_active_owner_without_grant_still_requires_backup_and_reviewed_plan(self):
        owner = get_user_model().objects.create_user('owner_review')
        password = owner.password
        with self.assertRaisesMessage(CommandError, 'Apply requires a verified backup'):
            self.run_restore(apply=True)
        owner.refresh_from_db()
        self.assertEqual(owner.password, password)
        self.assertFalse(DashboardAccess.objects.exists())


class ReviewedPlanHelperTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.release = Path(self.tmp.name)
        self.plan = {'before': {'learners': 2, 'manual_mappings': {12: 2, 3: 2}}, 'after': {'learners': 3},
            'source_sha256': 'a' * 64, 'matrix_sha256': 'b' * 64, 'timing_fingerprint': 'c' * 64}
        self.reviewed = {**self.plan, 'plan_sha256': digest(self.plan), 'applied': False}
        self.path = self.release / 'dry-run-private.json'

    def run_helper(self):
        call_command('apply_reviewed_identity_restoration', release_dir=str(self.release),
            backup='existing-verified-backup.sqlite3', stdout=io.StringIO())

    def test_dispatches_existing_reviewed_plan_without_recapture_or_rewrite(self):
        self.path.write_text(json.dumps(self.reviewed), encoding='utf-8')
        before = self.path.read_bytes()
        with patch('analytics.management.commands.apply_reviewed_identity_restoration.call_command') as invoke:
            self.run_helper()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.release.iterdir()), [self.path])
        invoke.assert_called_once()
        args, kwargs = invoke.call_args
        self.assertEqual(args, ('restore_original_identities',))
        self.assertEqual(kwargs['owner'], 'owner_review')
        self.assertTrue(kwargs['apply'])
        self.assertEqual(kwargs['expected_plan'], self.reviewed['plan_sha256'])
        self.assertEqual(kwargs['backup'], 'existing-verified-backup.sqlite3')
        for key, filename in (('lms_json', 'current-lms.json'), ('timing_source', 'teams-timing.xlsx'),
            ('timing_policy', 'timing-policy.json'), ('graph_cache', 'graph-cache.json'),
            ('matrices', 'attendance'), ('report', 'applied-private.json')):
            self.assertEqual(kwargs[key], str(self.release / filename))

    def test_invalid_missing_changed_or_already_applied_plan_never_dispatched(self):
        for data in (None, '{invalid', '[]', '{}',
            json.dumps({**self.reviewed, 'applied': True}),
            json.dumps({**self.reviewed, 'after': {'learners': 999}})):
            with self.subTest(data=data):
                if data is not None:
                    self.path.write_text(data, encoding='utf-8')
                with patch('analytics.management.commands.apply_reviewed_identity_restoration.call_command') as invoke:
                    with self.assertRaisesMessage(CommandError, 'reviewed dry-run plan is missing or invalid'):
                        self.run_helper()
                    invoke.assert_not_called()

import io
import os
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from analytics.models import DashboardAccess

PW = "Synthetic-Owner-Pass-7319!"  # test-only value


class ProvisionOwnerTests(TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.env = Path(self.dir.name) / ".env"

    def tearDown(self):
        self.dir.cleanup()

    def write_env(self, text):
        self.env.write_text(text, encoding="utf-8")

    def run_cmd(self):
        out = io.StringIO()
        call_command("provision_owner", "--env-file", str(self.env), stdout=out)
        return out.getvalue()

    def test_creates_restricted_owner_from_env_file(self):
        self.write_env(f"DJANGO_DEBUG=true\nusername=owner_review\npassword={PW}\n")
        out = self.run_cmd()
        self.assertNotIn(PW, out)  # password never echoed
        u = User.objects.get(username="owner_review")
        self.assertTrue(u.is_active)
        self.assertFalse(u.is_staff)
        self.assertFalse(u.is_superuser)
        self.assertTrue(u.check_password(PW))
        self.assertNotEqual(u.password, PW)  # stored as a hash
        a = DashboardAccess.objects.get(user=u)
        self.assertTrue(a.is_temporary and a.is_valid())
        # Login works and the dashboard data is reachable; admin is not.
        r = self.client.post("/api/auth/login", {"username": "owner_review", "password": PW}, content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get("/api/learners").status_code, 200)
        self.assertEqual(self.client.get("/admin/").status_code, 302)

    def test_reads_file_not_os_environ(self):
        """On Windows os.environ['username'] is the OS login (USERNAME); it must be ignored."""
        self.write_env(f"username=owner_review\npassword={PW}\n")
        with mock.patch.dict(os.environ, {"USERNAME": "windows-login", "username": "windows-login"}):
            self.run_cmd()
        self.assertTrue(User.objects.filter(username="owner_review").exists())
        self.assertFalse(User.objects.filter(username="windows-login").exists())

    def test_missing_or_empty_values_fail_safely(self):
        for text in ("username=owner_review\n", f"password={PW}\n", "username=\npassword=\n"):
            self.write_env(text)
            with self.assertRaises(CommandError) as ctx:
                self.run_cmd()
            self.assertNotIn(PW, str(ctx.exception))
        self.assertFalse(User.objects.exists())

    def test_rerun_does_not_invalidate_existing_session(self):
        self.write_env(f"username=owner_review\npassword={PW}\n")
        self.run_cmd()
        self.client.post("/api/auth/login", {"username": "owner_review", "password": PW}, content_type="application/json")
        out = self.run_cmd()  # same password -> no re-hash -> session stays valid
        self.assertIn("unchanged", out)
        self.assertEqual(self.client.get("/api/learners").status_code, 200)

    def test_password_change_only_via_explicit_run(self):
        self.write_env(f"username=owner_review\npassword={PW}\n")
        self.run_cmd()
        self.write_env("username=owner_review\npassword=Another-Synthetic-Pass-2846!\n")
        # Requests alone never re-provision:
        self.client.get("/api/auth/session")
        self.assertTrue(User.objects.get(username="owner_review").check_password(PW))
        self.run_cmd()
        self.assertTrue(User.objects.get(username="owner_review").check_password("Another-Synthetic-Pass-2846!"))

    def test_refuses_superuser(self):
        User.objects.create_superuser("owner_review", password=PW)
        self.write_env(f"username=owner_review\npassword={PW}\n")
        with self.assertRaises(CommandError):
            self.run_cmd()

    def test_credentials_not_loaded_into_process_environment(self):
        from django.conf import settings  # settings already loaded the real .env
        self.assertTrue(settings.configured)
        real_env = Path(settings.PROJECT_ROOT) / ".env"
        if real_env.is_file():
            from dotenv import dotenv_values
            pw = dotenv_values(real_env).get("password")
            if pw:
                self.assertNotIn(pw, os.environ.values())

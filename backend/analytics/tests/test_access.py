import datetime as dt
import io
import sys
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from analytics.models import DashboardAccess

PW = "Synthetic-Pass-4821!"


def provision(*args, password=PW):
    with mock.patch.object(sys, "stdin", io.StringIO(password + "\n")):
        call_command("dashboard_user", *args, "--password-stdin", stdout=io.StringIO())


class DashboardUserTests(TestCase):
    def login(self, username):
        return self.client.post("/api/auth/login", {"username": username, "password": PW},
                                content_type="application/json")

    def test_temporary_user_is_restricted_and_can_view_dashboard(self):
        provision("add", "owner_review", "--temporary", "--days", "7")
        u = User.objects.get(username="owner_review")
        self.assertFalse(u.is_staff)
        self.assertFalse(u.is_superuser)
        self.assertEqual(u.user_permissions.count(), 0)
        self.assertTrue(u.dashboard_access.is_temporary)
        self.assertEqual(self.login("owner_review").status_code, 200)
        self.assertEqual(self.client.get("/api/dashboard/summary").status_code, 200)
        self.assertEqual(self.client.get("/api/learners").status_code, 200)
        # No Django admin access
        r = self.client.get("/admin/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/admin/login/", r["Location"])

    def test_authenticated_user_without_grant_is_refused(self):
        User.objects.create_user("no_grant", password=PW)
        self.assertEqual(self.login("no_grant").status_code, 403)
        self.client.force_login(User.objects.get(username="no_grant"))
        self.assertEqual(self.client.get("/api/learners").status_code, 403)

    def test_expired_temporary_access_is_refused(self):
        provision("add", "expired_user", "--temporary", "--days", "1")
        DashboardAccess.objects.filter(user__username="expired_user").update(
            expires_at=timezone.now() - dt.timedelta(minutes=1))
        self.assertEqual(self.login("expired_user").status_code, 403)

    def test_disable_revokes_existing_session(self):
        provision("add", "temp2", "--temporary")
        self.assertEqual(self.login("temp2").status_code, 200)
        call_command("dashboard_user", "disable", "temp2", stdout=io.StringIO())
        self.assertIn(self.client.get("/api/learners").status_code, (401, 403))
        self.assertEqual(self.login("temp2").status_code, 400)

    def test_approved_email_user_permanent(self):
        provision("add", "approved@example.invalid", "--email", "approved@example.invalid")
        a = DashboardAccess.objects.get(user__username="approved@example.invalid")
        self.assertFalse(a.is_temporary)
        self.assertIsNone(a.expires_at)

    def test_weak_password_rejected(self):
        with self.assertRaises(CommandError):
            provision("add", "weak_user", password="123")

    def test_refuses_to_downgrade_superuser(self):
        User.objects.create_superuser("root_admin", password=PW)
        with self.assertRaises(CommandError):
            provision("add", "root_admin")

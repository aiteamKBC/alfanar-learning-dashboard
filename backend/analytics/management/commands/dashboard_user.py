r"""Provision and manage dashboard-only accounts (never staff, never superuser).

Passwords are never accepted as command-line arguments (they would leak into
shell history / process lists). They are read from a hidden interactive prompt
or, for automation, from stdin.

Examples (PowerShell, from backend\):
    ..\.venv\Scripts\python manage.py dashboard_user add owner_review --temporary --days 14 --set-password
    ..\.venv\Scripts\python manage.py dashboard_user add jane.doe@company.com --email jane.doe@company.com --set-password
    ..\.venv\Scripts\python manage.py dashboard_user set-password owner_review
    ..\.venv\Scripts\python manage.py dashboard_user disable owner_review
    ..\.venv\Scripts\python manage.py dashboard_user list
"""

import datetime as dt
import getpass
import sys

from django.contrib.auth import get_user_model, password_validation
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from analytics.models import DashboardAccess


def _read_password(from_stdin: bool, user) -> str:
    if from_stdin:
        pw = sys.stdin.readline().rstrip("\r\n")
    else:
        if not sys.stdin.isatty():
            raise CommandError("No terminal for a hidden prompt; use --password-stdin.")
        pw = getpass.getpass("New password (hidden): ")
        if pw != getpass.getpass("Repeat password: "):
            raise CommandError("Passwords do not match.")
    try:
        password_validation.validate_password(pw, user)
    except ValidationError as exc:
        raise CommandError("Password rejected: " + " ".join(exc.messages))
    return pw


def _end_sessions(user):
    """Log the user out everywhere."""
    for s in Session.objects.filter(expire_date__gt=timezone.now()):
        if str(s.get_decoded().get("_auth_user_id")) == str(user.pk):
            s.delete()


class Command(BaseCommand):
    help = "Create, update, disable or list dashboard-only user accounts."

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest="action", required=True)
        add = sub.add_parser("add", help="Create or update a dashboard-only user")
        add.add_argument("username")
        add.add_argument("--email", default="")
        add.add_argument("--name", default="", help="Display name")
        add.add_argument("--temporary", action="store_true", help="Mark as temporary review access")
        add.add_argument("--days", type=int, default=14, help="Expiry for temporary access (default 14)")
        add.add_argument("--set-password", action="store_true")
        add.add_argument("--password-stdin", action="store_true")
        add.add_argument("--note", default="")

        sp = sub.add_parser("set-password", help="Set/reset a dashboard user's password")
        sp.add_argument("username")
        sp.add_argument("--password-stdin", action="store_true")

        dis = sub.add_parser("disable", help="Revoke access, deactivate and log out a user")
        dis.add_argument("username")

        sub.add_parser("list", help="List dashboard users (no secrets)")

    def handle(self, *args, action, **o):
        User = get_user_model()
        if action == "list":
            for a in DashboardAccess.objects.select_related("user").order_by("user__username"):
                u = a.user
                state = "active" if (u.is_active and a.is_valid()) else "DISABLED/EXPIRED"
                self.stdout.write(f"{u.username:32} {state:17} temporary={a.is_temporary} "
                                  f"expires={a.expires_at:%Y-%m-%d %H:%M} UTC" if a.expires_at else
                                  f"{u.username:32} {state:17} temporary={a.is_temporary} expires=never")
            return

        if action == "add":
            with transaction.atomic():
                user, created = User.objects.get_or_create(username=o["username"])
                if user.is_superuser or user.is_staff:
                    raise CommandError("Refusing to convert a staff/superuser account into a dashboard-only account.")
                user.email = o["email"] or user.email
                if o["name"]:
                    first, _, last = o["name"].partition(" ")
                    user.first_name, user.last_name = first, last
                user.is_staff = False
                user.is_superuser = False
                user.is_active = True
                if created:
                    user.set_unusable_password()
                if o["set_password"] or o["password_stdin"]:
                    user.set_password(_read_password(o["password_stdin"], user))
                user.save()
                user.groups.clear()
                user.user_permissions.clear()
                access, _ = DashboardAccess.objects.get_or_create(user=user)
                access.is_temporary = o["temporary"]
                access.expires_at = timezone.now() + dt.timedelta(days=o["days"]) if o["temporary"] else None
                access.revoked_at = None
                access.note = o["note"] or access.note
                access.save()
            exp = f", expires {access.expires_at:%Y-%m-%d %H:%M} UTC" if access.expires_at else ""
            pw = "password set" if user.has_usable_password() else "NO PASSWORD YET (run set-password)"
            self.stdout.write(self.style.SUCCESS(
                f"{'Created' if created else 'Updated'} dashboard-only user '{user.username}' "
                f"(staff=False, superuser=False{exp}); {pw}."))
            return

        try:
            user = User.objects.get(username=o["username"])
        except User.DoesNotExist:
            raise CommandError(f"No such user: {o['username']}")

        if action == "set-password":
            if user.is_superuser or user.is_staff:
                raise CommandError("Use 'changepassword' for staff/superuser accounts.")
            user.set_password(_read_password(o["password_stdin"], user))
            user.save(update_fields=["password"])
            _end_sessions(user)
            self.stdout.write(self.style.SUCCESS(f"Password updated for '{user.username}'; existing sessions ended."))
        elif action == "disable":
            user.is_active = False
            user.save(update_fields=["is_active"])
            DashboardAccess.objects.filter(user=user).update(revoked_at=timezone.now())
            _end_sessions(user)
            self.stdout.write(self.style.SUCCESS(f"Disabled '{user.username}' and ended their sessions."))

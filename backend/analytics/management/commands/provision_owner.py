r"""Provision the dashboard owner account from the project-root .env file.

Reads the lowercase `username` and `password` keys directly from the .env FILE.
They are deliberately not read from os.environ: on Windows, environment names are
case-insensitive and the OS always defines USERNAME (the Windows login), which
would silently shadow `username`.

Run explicitly (never on requests, page loads or syncs), from backend\:
    ..\.venv\Scripts\python manage.py provision_owner
    ..\.venv\Scripts\python manage.py provision_owner --days 30
    ..\.venv\Scripts\python manage.py provision_owner --env-file D:\path\to\.env

Idempotent: if the stored hash already matches, the password is left untouched,
so re-running does not log the owner out of existing sessions.
"""

import datetime as dt
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model, password_validation
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from analytics.models import DashboardAccess

USERNAME_KEY = "username"
PASSWORD_KEY = "password"


def read_owner_credentials(env_file: Path) -> tuple[str, str]:
    from dotenv import dotenv_values

    if not env_file.is_file():
        raise CommandError(f"Environment file not found: {env_file}")
    values = dotenv_values(env_file)
    username = (values.get(USERNAME_KEY) or "").strip()
    password = values.get(PASSWORD_KEY) or ""
    missing = [k for k, v in ((USERNAME_KEY, username), (PASSWORD_KEY, password.strip())) if not v]
    if missing:
        raise CommandError(
            f"Missing or empty {' and '.join(repr(m) for m in missing)} in {env_file.name}. "
            "Add both lowercase keys and run the command again."
        )
    return username, password


class Command(BaseCommand):
    help = "Create/update the dashboard owner account from `username`/`password` in the root .env (explicit run only)."

    def add_arguments(self, parser):
        parser.add_argument("--env-file", default=str(Path(settings.PROJECT_ROOT) / ".env"))
        parser.add_argument("--days", type=int, default=14, help="Temporary access period (default 14)")

    def handle(self, *args, env_file, days, **opts):
        username, password = read_owner_credentials(Path(env_file))
        User = get_user_model()

        with transaction.atomic():
            user = User.objects.filter(username=username).first()
            created = user is None
            if created:
                user = User(username=username)
            elif user.is_superuser or user.is_staff:
                raise CommandError(f"'{username}' is a staff/superuser account; refusing to turn it into a dashboard-only account.")

            try:
                password_validation.validate_password(password, user)
            except ValidationError as exc:
                # Validator messages describe the rule, never the value.
                raise CommandError("The configured password was rejected: " + " ".join(exc.messages))

            user.is_active = True
            user.is_staff = False
            user.is_superuser = False
            password_changed = created or not user.check_password(password)
            if password_changed:
                user.set_password(password)  # stores a salted hash only
            user.save()
            user.groups.clear()
            user.user_permissions.clear()

            access, _ = DashboardAccess.objects.get_or_create(user=user)
            access.can_manage_identities = True
            access.is_temporary = True
            access.expires_at = timezone.now() + dt.timedelta(days=days)
            access.revoked_at = None
            access.note = access.note or "Dashboard owner review account (provisioned from .env)"
            access.save()

        db = connection.settings_dict
        db_label = db["NAME"] if db["ENGINE"].endswith("sqlite3") else f"{db['ENGINE'].rsplit('.', 1)[-1]}://{db.get('HOST') or 'localhost'}/{db['NAME']}"
        self.stdout.write(self.style.SUCCESS(
            f"{'Created' if created else 'Updated'} dashboard owner '{username}': active, staff=False, superuser=False, "
            f"dashboard access until {access.expires_at:%Y-%m-%d %H:%M} UTC; "
            f"password {'set from .env' if password_changed else 'unchanged (already matches .env)'}."))
        self.stdout.write(f"Database: {db_label}")

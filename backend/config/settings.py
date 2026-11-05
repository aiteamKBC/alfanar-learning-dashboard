"""Django settings for the Alfanar Learning Dashboard.

All environment-specific values come from environment variables (optionally
loaded from a project-root `.env` file). See `.env.example`.
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

try:
    from dotenv import dotenv_values
except ImportError:  # pragma: no cover
    dotenv_values = None

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/
PROJECT_ROOT = BASE_DIR.parent

# Keys read only by the explicit `provision_owner` command, straight from the file.
# They are never copied into the process environment (so they cannot appear in
# os.environ / request.META of the running server).
_FILE_ONLY_KEYS = {"username", "password"}

if dotenv_values:
    for _key, _value in dotenv_values(PROJECT_ROOT / ".env").items():
        if _key.lower() in _FILE_ONLY_KEYS or _value is None:
            continue
        os.environ.setdefault(_key, _value)  # real environment variables take precedence


def env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [v.strip() for v in os.environ.get(name, default).split(",") if v.strip()]


DEBUG = env_bool("DJANGO_DEBUG", False)

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "dev-only-insecure-key-do-not-use-in-production"
    else:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is false.")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list(
    "DJANGO_CSRF_TRUSTED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "analytics",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Database: SQLite by default; PostgreSQL via DATABASE_URL=postgres://user:pass@host:5432/db
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
if DATABASE_URL:
    import dj_database_url

    DATABASES = {"default": dj_database_url.parse(DATABASE_URL, conn_max_age=60)}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-gb"
TIME_ZONE = os.environ.get("DJANGO_TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Security -------------------------------------------------------------
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 60 * 60 * 8  # 8 hours
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = env_bool("DJANGO_SECURE_COOKIES", not DEBUG)
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
        "analytics.api.permissions.HasDashboardAccess",
    ],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_THROTTLE_RATES": {"login": "10/min"},
    "EXCEPTION_HANDLER": "analytics.api.exceptions.json_exception_handler",
}

# --- Data source configuration --------------------------------------------
ALFANAR_EXCEL_PATH = Path(os.environ.get("ALFANAR_EXCEL_PATH") or "data/Alfanar Endpoint.xlsx")
if not ALFANAR_EXCEL_PATH.is_absolute():
    ALFANAR_EXCEL_PATH = PROJECT_ROOT / ALFANAR_EXCEL_PATH
# --- Microsoft Teams attendance (separate, read-only source) ---------------
def _project_path(value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else PROJECT_ROOT / p


# The former combined export is comparison-only; default imports require all four module files.
ATTENDANCE_DIRECTORY = _project_path(os.environ.get("ATTENDANCE_DIRECTORY") or "data")
# Optional verified aliases (CSV: teams_email,learner_id). Contains emails -> keep under data/ (git-ignored).
TEAMS_ALIASES_PATH = _project_path(os.environ.get("TEAMS_ALIASES_PATH") or "data/teams_email_aliases.csv")
# Email domains of the training provider's staff (trainers/organisers), excluded from learner totals.
TEAMS_STAFF_DOMAINS = env_list("TEAMS_STAFF_DOMAINS", "kentbusinesscollege.com")
# A learner "attended" a lecture with at least this much merged presence.
TEAMS_MIN_ATTENDANCE_MINUTES = int(os.environ.get("TEAMS_MIN_ATTENDANCE_MINUTES", "10"))
# A lecture occurrence counts as "delivered" when at least this many verified learners attended it.
TEAMS_MIN_LEARNERS_FOR_DELIVERED = int(os.environ.get("TEAMS_MIN_LEARNERS_FOR_DELIVERED", "5"))

GOOGLE_SHEETS_ENABLED = env_bool("GOOGLE_SHEETS_ENABLED", False)
# GOOGLE_SHEETS_ID is accepted as an alias.
GOOGLE_SHEETS_SPREADSHEET_ID = (
    os.environ.get("GOOGLE_SHEETS_SPREADSHEET_ID") or os.environ.get("GOOGLE_SHEETS_ID") or ""
).strip()
# Credentials path; relative paths resolve against the project root. The file itself is never committed.
GOOGLE_SERVICE_ACCOUNT_FILE = (os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE") or "").strip()
if GOOGLE_SERVICE_ACCOUNT_FILE and not Path(GOOGLE_SERVICE_ACCOUNT_FILE).is_absolute():
    GOOGLE_SERVICE_ACCOUNT_FILE = str(PROJECT_ROOT / GOOGLE_SERVICE_ACCOUNT_FILE)
if os.environ.get("GOOGLE_SYNC_INTERVAL_MINUTES") and not os.environ.get("SYNC_INTERVAL_SECONDS"):
    SYNC_INTERVAL_SECONDS = int(float(os.environ["GOOGLE_SYNC_INTERVAL_MINUTES"]) * 60)
else:
    SYNC_INTERVAL_SECONDS = int(os.environ.get("SYNC_INTERVAL_SECONDS", "300"))

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"analytics": {"handlers": ["console"], "level": "INFO"}},
}

# HTTPS hardening (enabled together with secure cookies in production)
SECURE_SSL_REDIRECT = SESSION_COOKIE_SECURE
SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SECONDS", "31536000" if SESSION_COOKIE_SECURE else "0"))
# Only trust X-Forwarded-Proto when Django sits behind a proxy (nginx) that always sets it.
if env_bool("DJANGO_BEHIND_PROXY", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    USE_X_FORWARDED_HOST = False

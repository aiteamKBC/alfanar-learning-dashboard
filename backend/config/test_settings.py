"""Isolated, synthetic test stores; never connect to a configured remote PostgreSQL URL."""
from .settings import *  # noqa: F403

DATABASES = {
    'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'},
    'lecture_library': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'},
}

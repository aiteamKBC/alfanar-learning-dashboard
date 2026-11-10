"""Retired: the dashboard database must never be transferred to PostgreSQL."""
from django.core.management import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Disabled. Dashboard data must remain in SQLite; only Lecture Library uses PostgreSQL.'
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument('--source')
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--verify-only', action='store_true')

    def handle(self, *args, **options):
        raise CommandError('Whole-database transfer is disabled. Follow docs/postgresql-storage.md for isolated Lecture Library setup. No data was read or written.')

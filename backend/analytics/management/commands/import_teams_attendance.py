r"""Import Microsoft Teams attendance (separate, read-only source).

    ..\.venv\Scripts\python manage.py import_teams_attendance [--path "..\data\file.xlsx"]

Idempotent and independent of the LMS/Google sync. Re-run after replacing the
Teams export file or after the learner registry changes. Prints aggregate
counts only (no names or emails).
"""

import json

from django.core.management.base import BaseCommand, CommandError

from analytics.attendance.importer import run_attendance_import


class Command(BaseCommand):
    help = "Import verified learner attendance from the Teams attendance workbook (read-only)."

    def add_arguments(self, parser):
        parser.add_argument("--path", help="Override TEAMS_ATTENDANCE_PATH")
        parser.add_argument("--quiet", action="store_true", help="Only print the status line")

    def handle(self, *args, path=None, quiet=False, **opts):
        run = run_attendance_import(path)
        self.stdout.write(f"Teams attendance import #{run.pk} status={run.status} changed={run.changed} "
                          f"coverage={run.coverage_start}..{run.coverage_end}")
        if run.diagnostics and not quiet:
            self.stdout.write(json.dumps(run.diagnostics, indent=2, default=str))
        for w in run.warnings:
            self.stdout.write(self.style.WARNING(f"warning: {w}"))
        for e in run.errors:
            self.stdout.write(self.style.ERROR(f"error: {e}"))
        if run.status != "success":
            raise CommandError("Attendance import failed; previous attendance data was preserved.")

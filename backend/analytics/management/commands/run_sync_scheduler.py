"""Lightweight foreground scheduler: runs a sync every N seconds (default 300).

Suitable for Windows dev (leave running in a terminal) and Ubuntu production
(run under systemd with Restart=on-failure). An exclusive lock file guarantees
only one scheduler process runs per installation; a second instance exits.
"""

import os
import sys
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from analytics.importer.service import SyncInProgress, run_sync


class ProcessLock:
    """Exclusive, non-blocking OS-level lock, released automatically if the process dies."""

    def __init__(self, path: Path):
        self.path = path
        self.fh = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "a+")
        try:
            if sys.platform == "win32":
                import msvcrt
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.fh.close()
            self.fh = None
            return False
        return True

    def release(self):
        if self.fh:
            try:
                if sys.platform == "win32":
                    import msvcrt
                    self.fh.seek(0)
                    msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            self.fh.close()
            self.fh = None


class Command(BaseCommand):
    help = "Run sync_alfanar on a fixed interval until interrupted (single instance)."

    def add_arguments(self, parser):
        parser.add_argument("--source", choices=["excel", "google"], default="google")
        parser.add_argument("--interval", type=int, default=settings.SYNC_INTERVAL_SECONDS)
        parser.add_argument("--once", action="store_true", help="Run a single iteration and exit")
        parser.add_argument("--max-iterations", type=int, default=0, help="Stop after N iterations (testing)")

    def handle(self, *args, source, interval, once, max_iterations, **opts):
        interval = max(60, interval)
        lock = ProcessLock(Path(os.environ.get("SYNC_LOCK_FILE", settings.BASE_DIR / ".sync-scheduler.lock")))
        if not lock.acquire():
            raise CommandError("Another sync scheduler is already running; refusing to start a second one.")
        self.stdout.write(f"Scheduler started: source={source}, every {interval}s (Ctrl+C to stop)")
        iterations = 0
        try:
            while True:
                started = time.monotonic()
                try:
                    run = run_sync(source)
                    detail = f" - {run.errors[0]}" if run.errors and run.status == "failed" else ""
                    self.stdout.write(f"[{run.finished_at:%Y-%m-%d %H:%M:%S}] sync #{run.pk} {run.status}"
                                      f"{' (changed)' if run.changed else ''}{detail}", ending="\n")
                except SyncInProgress as exc:
                    self.stdout.write(f"skipped: {exc}")
                except Exception as exc:  # never let one bad cycle kill the loop
                    self.stderr.write(f"sync cycle error: {type(exc).__name__}: {exc}")
                self.stdout.flush()
                iterations += 1
                if once or (max_iterations and iterations >= max_iterations):
                    return
                time.sleep(max(1, interval - (time.monotonic() - started)))
        except KeyboardInterrupt:
            self.stdout.write("Scheduler stopped.")
        finally:
            lock.release()

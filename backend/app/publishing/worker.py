"""Scheduler: publishes due items. PostgreSQL/SQLite `publications` is the source of truth."""
import logging
import threading
import time

from app.config import settings
from app.db import SessionLocal
from app.publishing import service

log = logging.getLogger("publisher")


def process_due() -> int:
    """Run every due publication; returns how many were handled."""
    n = 0
    with SessionLocal() as s:
        while (pid := service.claim_due(s)) is not None:
            service.run_publication(s, pid)
            n += 1
    return n


def refresh_metrics() -> dict:
    from app.insights.metrics import refresh

    with SessionLocal() as s:
        return refresh(s)


class PublishingWorker:
    def __init__(self):
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        with SessionLocal() as s:
            service.recover_stuck(s)
        self._thread = threading.Thread(target=self._loop, name="publisher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        next_metrics = time.monotonic() + 120  # first results fetch shortly after start-up
        while not self._stop.is_set():
            try:
                process_due()
            except Exception:  # noqa: BLE001
                log.exception("publisher loop error")
            if settings.metrics_refresh_hours > 0 and time.monotonic() >= next_metrics:
                next_metrics = time.monotonic() + settings.metrics_refresh_hours * 3600
                try:
                    log.info("metrics refreshed: %s", refresh_metrics())
                except Exception:  # noqa: BLE001
                    log.exception("metrics refresh error")
            self._stop.wait(settings.publish_poll_interval_s)

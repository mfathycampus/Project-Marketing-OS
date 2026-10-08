"""In-process design worker. PostgreSQL/SQLite `design_jobs` is the source of truth; this just drains it."""
import logging
import threading
import time

from sqlalchemy import select, update

from app.config import settings
from app.db import SessionLocal
from app.designs.factory import get_design_provider
from app.designs.service import DesignService
from app.models import DesignJob

log = logging.getLogger("design-worker")


def recover_stuck_jobs() -> int:
    """Jobs left 'processing' by a crash/restart go back to the queue."""
    with SessionLocal() as s:
        res = s.execute(update(DesignJob).where(DesignJob.status == "processing").values(status="pending"))
        s.commit()
        return res.rowcount or 0


def process_one() -> bool:
    """Claim and run the oldest pending job. Returns False when the queue is empty."""
    with SessionLocal() as s:
        job_id = s.scalar(select(DesignJob.id).where(DesignJob.status == "pending").order_by(DesignJob.created_at).limit(1))
        if job_id is None:
            return False
        # atomic claim: only one worker wins the pending -> processing flip
        claimed = s.execute(
            update(DesignJob).where(DesignJob.id == job_id, DesignJob.status == "pending").values(status="processing")
        ).rowcount
        s.commit()
        if not claimed:
            return True
        try:
            DesignService(s, get_design_provider(s)).run_job(job_id)
        except Exception:  # noqa: BLE001 - failure is recorded on the job by run_job
            log.exception("design job %s failed", job_id)
        return True


class DesignWorker:
    def __init__(self):
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        recover_stuck_jobs()
        self._thread = threading.Thread(target=self._loop, name="design-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                busy = process_one()
            except Exception:  # noqa: BLE001
                log.exception("worker loop error")
                busy = False
            if not busy:
                self._stop.wait(settings.worker_poll_interval_s)

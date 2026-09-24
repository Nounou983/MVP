"""Worker runtime.

Runs either as its own process (``python -m app.services.worker``) next to the
GPU, or inside the API process for single-machine development
(``CIGOGNE_INLINE_WORKER=1``, the default).

Concurrency is deliberately 1 by default: one CUDA device, one diffusion pass.
Raising `CIGOGNE_GPU_CONCURRENCY` above the number of cards you actually have
is how you get out-of-memory crashes instead of a queue.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from ..config import get_settings
from ..db import get_sessionmaker, init_db
from ..storage import get_storage
from . import jobs as job_service
from .executors import Cancelled, JobContext, get_executor

log = logging.getLogger("cigogne.worker")


class WorkerLoop:
    def __init__(self, concurrency: int | None = None, poll: float | None = None):
        settings = get_settings()
        self.concurrency = max(1, concurrency or settings.gpu_concurrency)
        self.poll = poll or settings.worker_poll_seconds
        self.timeout = settings.job_timeout
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._slots = threading.Semaphore(self.concurrency)
        self._pool = ThreadPoolExecutor(max_workers=self.concurrency, thread_name_prefix="cigogne-gpu")
        self._last_maintenance = 0.0

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="cigogne-worker", daemon=True)
        self._thread.start()
        log.info("worker started (concurrency=%s)", self.concurrency)

    def stop(self, wait: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=wait)
        self._pool.shutdown(wait=False, cancel_futures=True)

    # -- main loop ---------------------------------------------------------
    def _run(self) -> None:
        Session = get_sessionmaker()
        while not self._stop.is_set():
            self._maintenance(Session)
            if not self._slots.acquire(timeout=self.poll):
                continue
            job = None
            try:
                with Session() as db:
                    job = job_service.claim_next(db)
            except Exception:                        # pragma: no cover - db hiccup
                log.exception("claim failed")
            if job is None:
                self._slots.release()
                self._stop.wait(self.poll)
                continue
            self._pool.submit(self._run_job, job.id, job.type, dict(job.params or {}), job.user_id)

    def _maintenance(self, Session) -> None:
        now = time.monotonic()
        if now - self._last_maintenance < 60:
            return
        self._last_maintenance = now
        try:
            with Session() as db:
                reaped = job_service.reap_stalled(db)
                removed = job_service.cleanup(db)
            if reaped or removed:
                log.info("maintenance: %s requeued, %s purged", reaped, removed)
        except Exception:                            # pragma: no cover
            log.exception("maintenance failed")

    # -- single job --------------------------------------------------------
    def _run_job(self, job_id: str, job_type: str, params: dict, user_id: str | None) -> None:
        Session = get_sessionmaker()
        started = time.monotonic()
        try:
            def report(progress: float, message: str) -> bool:
                if time.monotonic() - started > self.timeout:
                    raise TimeoutError(f"Dépassement du délai ({self.timeout}s)")
                with Session() as db:
                    return job_service.heartbeat(db, job_id, progress, message)

            ctx = JobContext(job_id=job_id, user_id=user_id, storage=get_storage(), report=report)
            result = get_executor().run(job_type, params, ctx)
            with Session() as db:
                job_service.succeed(db, job_id, result)
            log.info("job %s (%s) done in %.1fs", job_id, job_type, time.monotonic() - started)
        except Cancelled:
            with Session() as db:
                job = db.get(job_service.Job, job_id)
                if job:
                    job.status = "cancelled"
                    job.message = "Annulé"
                    job.finished_at = job_service.utcnow()
                    db.commit()
            log.info("job %s cancelled", job_id)
        except TimeoutError as exc:
            with Session() as db:
                job_service.fail(db, job_id, str(exc), retryable=False)
        except Exception as exc:
            log.exception("job %s failed", job_id)
            with Session() as db:
                job_service.fail(db, job_id, f"{type(exc).__name__}: {exc}")
        finally:
            self._slots.release()


_loop: WorkerLoop | None = None


def start_inline_worker() -> WorkerLoop | None:
    """Called by the API on startup when CIGOGNE_INLINE_WORKER is on."""
    global _loop
    settings = get_settings()
    if not settings.inline_worker:
        return None
    if _loop is None:
        _loop = WorkerLoop()
        _loop.start()
    return _loop


def stop_inline_worker() -> None:
    global _loop
    if _loop is not None:
        _loop.stop()
        _loop = None


def main() -> None:  # pragma: no cover - process entry point
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )
    init_db()
    loop = WorkerLoop()
    loop.start()
    log.info("worker ready — polling for jobs")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("shutting down")
        loop.stop()


if __name__ == "__main__":  # pragma: no cover
    main()

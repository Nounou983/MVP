"""Job queue for GPU work.

The API never calls a model. It writes a row; a worker claims it. That single
indirection is what makes the product multi-user: a second person clicking
"remove" queues behind the first instead of causing two simultaneous CUDA
allocations on the same card.

Claiming is a conditional UPDATE, so several worker processes can share one
database without a broker.
"""
from __future__ import annotations

import logging
import os
import socket
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, delete, select, update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Job, new_id, utcnow

log = logging.getLogger("cigogne.jobs")

JOB_TYPES = {"analyze", "select-mask", "remove", "inpaint"}
TERMINAL = {"succeeded", "failed", "cancelled"}

WORKER_ID = f"{socket.gethostname()}-{os.getpid()}"


def enqueue(
    db: Session,
    *,
    job_type: str,
    params: dict,
    user_id: str | None = None,
    project_id: str | None = None,
    priority: int = 100,
) -> Job:
    if job_type not in JOB_TYPES:
        raise ValueError(f"Type de tâche inconnu: {job_type}")
    settings = get_settings()
    job = Job(
        id=new_id(),
        user_id=user_id,
        project_id=project_id,
        type=job_type,
        status="queued",
        priority=priority,
        params=params,
        max_attempts=settings.job_max_attempts,
        message="En file d'attente",
    )
    db.add(job)
    db.commit()
    return job


def claim_next(db: Session, worker_id: str = WORKER_ID) -> Job | None:
    """Atomically take the highest-priority queued job."""
    candidate = db.execute(
        select(Job)
        .where(Job.status == "queued", Job.cancel_requested.is_(False))
        .order_by(Job.priority.asc(), Job.created_at.asc())
        .limit(1)
    ).scalar_one_or_none()
    if candidate is None:
        return None

    now = utcnow()
    updated = db.execute(
        update(Job)
        .where(and_(Job.id == candidate.id, Job.status == "queued"))
        .values(
            status="running",
            worker_id=worker_id,
            started_at=now,
            heartbeat_at=now,
            attempts=Job.attempts + 1,
            message="Traitement en cours",
        )
    )
    db.commit()
    if updated.rowcount != 1:
        return None       # another worker won the race
    db.refresh(candidate)
    return candidate


def heartbeat(db: Session, job_id: str, progress: float | None = None, message: str | None = None) -> bool:
    """Returns False when the client asked for cancellation."""
    job = db.get(Job, job_id)
    if job is None:
        return False
    job.heartbeat_at = utcnow()
    if progress is not None:
        job.progress = max(0.0, min(1.0, float(progress)))
    if message:
        job.message = message[:255]
    db.commit()
    return not job.cancel_requested


def succeed(db: Session, job_id: str, result: dict) -> None:
    job = db.get(Job, job_id)
    if job is None:
        return
    job.status = "succeeded"
    job.result = result
    job.progress = 1.0
    job.message = "Terminé"
    job.finished_at = utcnow()
    db.commit()


def fail(db: Session, job_id: str, error: str, *, retryable: bool = True) -> None:
    job = db.get(Job, job_id)
    if job is None:
        return
    if retryable and job.attempts < job.max_attempts and not job.cancel_requested:
        job.status = "queued"
        job.message = f"Nouvelle tentative ({job.attempts}/{job.max_attempts})"
        job.error = error[:2000]
        job.worker_id = None
        job.started_at = None
        db.commit()
        log.warning("job %s retry after error: %s", job_id, error)
        return
    job.status = "failed"
    job.error = error[:2000]
    job.message = "Échec"
    job.finished_at = utcnow()
    db.commit()


def cancel(db: Session, job: Job) -> Job:
    if job.status in TERMINAL:
        return job
    job.cancel_requested = True
    if job.status == "queued":
        job.status = "cancelled"
        job.message = "Annulé"
        job.finished_at = utcnow()
    else:
        job.message = "Annulation demandée"
    db.commit()
    return job


def reap_stalled(db: Session, stale_after: int | None = None) -> int:
    """Requeue jobs whose worker died mid-flight."""
    settings = get_settings()
    window = stale_after or max(60, settings.job_timeout)
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=window)
    stalled = db.execute(
        select(Job).where(Job.status == "running", Job.heartbeat_at < cutoff)
    ).scalars().all()
    for job in stalled:
        if job.attempts >= job.max_attempts:
            job.status = "failed"
            job.error = "Le worker n'a plus répondu."
            job.message = "Échec"
            job.finished_at = utcnow()
        else:
            job.status = "queued"
            job.worker_id = None
            job.message = "Reprise après interruption du worker"
    if stalled:
        db.commit()
    return len(stalled)


def cleanup(db: Session, retention_hours: int | None = None) -> int:
    settings = get_settings()
    hours = retention_hours if retention_hours is not None else settings.job_retention_hours
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    result = db.execute(
        delete(Job).where(Job.status.in_(tuple(TERMINAL)), Job.finished_at < cutoff)
    )
    db.commit()
    return int(result.rowcount or 0)


def queue_stats(db: Session) -> dict:
    rows = db.execute(select(Job.status, Job.id)).all()
    counts: dict[str, int] = {}
    for status, _ in rows:
        counts[status] = counts.get(status, 0) + 1
    return {
        "queued": counts.get("queued", 0),
        "running": counts.get("running", 0),
        "succeeded": counts.get("succeeded", 0),
        "failed": counts.get("failed", 0),
        "cancelled": counts.get("cancelled", 0),
    }


def to_payload(job: Job) -> dict:
    return {
        "id": job.id,
        "type": job.type,
        "status": job.status,
        "progress": round(float(job.progress or 0), 3),
        "message": job.message,
        "error": job.error,
        "result": job.result,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }

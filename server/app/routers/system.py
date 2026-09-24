"""Analytics, entitlements, health and admin hooks."""
from __future__ import annotations

import hashlib
import time

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import PLANS, get_settings
from ..db import session_scope
from ..models import AnalyticsEvent, Job, Project, User, new_id
from ..schemas import AnalyticsBatchIn
from ..security import current_user, optional_user, rate_limit, require_admin
from ..services import jobs as job_service
from ..services.entitlements import entitlements_payload

router = APIRouter(prefix="/api", tags=["système"])
read_limit = Depends(rate_limit("read", "rate_limit_read"))
write_limit = Depends(rate_limit("write", "rate_limit_write"))

STARTED_AT = time.time()

#: Only these names are stored. An unknown event is dropped rather than
#: recorded, which keeps the table free of whatever a future build invents.
ALLOWED_EVENTS = {
    "room_upload", "room_sample", "analysis_start", "analysis_complete", "analysis_failed",
    "object_select", "object_remove", "object_remove_failed",
    "furniture_add", "furniture_replace", "furniture_remove", "furniture_color",
    "view_3d", "glb_import", "export", "project_save", "project_open", "share_create",
    "favorite_add", "collection_add", "signup", "login",
}

#: Property keys allowed through. Everything else is discarded before storage,
#: so a stray filename or e-mail cannot end up in analytics by accident.
ALLOWED_PROPS = {
    "product_id", "family", "duration_ms", "item_count", "surface", "mode", "ok",
    "confidence", "source", "count", "plan", "width", "height", "reason",
}


@router.post("/analytics/events", status_code=202, dependencies=[write_limit])
def ingest(
    payload: AnalyticsBatchIn,
    request: Request,
    user: User | None = Depends(optional_user),
    db: Session = Depends(session_scope),
):
    stored = 0
    for event in payload.events:
        if event.name not in ALLOWED_EVENTS:
            continue
        props = {k: v for k, v in (event.props or {}).items() if k in ALLOWED_PROPS}
        session_id = event.session_id[:64]
        if not session_id:
            # Derived, not tracked: a rotating hash so events from one visit
            # group together without storing an address.
            raw = f"{request.client.host if request.client else ''}:{time.time() // 86400}"
            session_id = hashlib.sha256(raw.encode()).hexdigest()[:32]
        db.add(AnalyticsEvent(
            id=new_id(), user_id=user.id if user else None,
            session_id=session_id, name=event.name, props=props,
        ))
        stored += 1
    db.commit()
    return {"stored": stored, "received": len(payload.events)}


@router.get("/account/entitlements", dependencies=[read_limit])
def entitlements(user: User = Depends(current_user), db: Session = Depends(session_scope)):
    return entitlements_payload(db, user)


@router.get("/plans")
def plans():
    return {
        "plans": [
            {"id": key, "label": value["label"], "price_dzd": value["price_dzd"],
             "limits": value["limits"], "features": value["features"]}
            for key, value in PLANS.items()
        ]
    }


@router.get("/health")
def health(db: Session = Depends(session_scope)):
    settings = get_settings()
    try:
        db.execute(select(func.count(User.id)))
        db_ok = True
    except Exception:
        db_ok = False
    return {
        "ok": db_ok,
        "service": "cigogne-api",
        "env": settings.env,
        "uptime_s": round(time.time() - STARTED_AT, 1),
        "database": "ok" if db_ok else "unreachable",
        "storage": settings.storage_backend,
        "queue": job_service.queue_stats(db) if db_ok else {},
        "ai_service": settings.ai_service_url,
        "inline_worker": settings.inline_worker,
    }


@router.get("/admin/stats", dependencies=[read_limit])
def admin_stats(_: User = Depends(require_admin), db: Session = Depends(session_scope)):
    users = db.execute(select(func.count(User.id))).scalar_one()
    projects = db.execute(
        select(func.count(Project.id)).where(Project.deleted_at.is_(None))
    ).scalar_one()
    events = db.execute(select(func.count(AnalyticsEvent.id))).scalar_one()
    by_plan = db.execute(select(User.plan, func.count(User.id)).group_by(User.plan)).all()
    recent_failures = db.execute(
        select(func.count(Job.id)).where(Job.status == "failed")
    ).scalar_one()
    return {
        "users": users,
        "projects": projects,
        "events": events,
        "plans": {plan: count for plan, count in by_plan},
        "queue": job_service.queue_stats(db),
        "failed_jobs": recent_failures,
    }


@router.post("/admin/jobs/cleanup", dependencies=[write_limit])
def admin_cleanup(_: User = Depends(require_admin), db: Session = Depends(session_scope)):
    purged = job_service.cleanup(db)
    reaped = job_service.reap_stalled(db)
    return {"purged": purged, "requeued": reaped}

"""AI gateway + job API.

Two ways in:

* ``/api/jobs`` — the honest asynchronous interface. Enqueue, poll, cancel.
* ``/api/ai/*`` — the same multipart contracts as the model service
  (``/analyze``, ``/select-mask``, ``/inpaint``, ``/remove``), so the existing
  frontend can point at this gateway with a one-line base-URL change and gain
  queueing, quotas and rate limits without any other edit.

The gateway blocks on the queue instead of the GPU, which is the whole point:
two people clicking "remove" at once produce two queued jobs and one busy
card, not two CUDA allocations.
"""
from __future__ import annotations

import base64
import json
import time

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_sessionmaker, session_scope
from ..models import Job, User
from ..schemas import JobIn
from ..security import current_user, optional_user, rate_limit
from ..services import jobs as job_service
from ..services.entitlements import ensure_can_run_ai, record_usage
from ..storage import build_key, get_storage, sniff_image

router = APIRouter(prefix="/api", tags=["ia"])
ai_limit = Depends(rate_limit("ai", "rate_limit_ai"))
read_limit = Depends(rate_limit("read", "rate_limit_read"))

MAX_WAIT = 240.0
POLL = 0.25


# --------------------------------------------------------------------------
# Job API
# --------------------------------------------------------------------------
@router.post("/jobs", status_code=202, dependencies=[ai_limit])
def create_job(payload: JobIn, user: User = Depends(current_user), db: Session = Depends(session_scope)):
    ensure_can_run_ai(db, user)
    storage = get_storage()
    if not storage.exists(payload.image_key):
        raise HTTPException(status_code=404, detail="Image introuvable.")
    if not payload.image_key.startswith(f"{user.id}/"):
        raise HTTPException(status_code=403, detail="Cette image ne vous appartient pas.")

    params = {"image_key": payload.image_key}
    if payload.mask_key:
        if not payload.mask_key.startswith(f"{user.id}/"):
            raise HTTPException(status_code=403, detail="Ce masque ne vous appartient pas.")
        params["mask_key"] = payload.mask_key
    if payload.x is not None:
        params["x"] = payload.x
    if payload.y is not None:
        params["y"] = payload.y
    if payload.label:
        params["label"] = payload.label
    if payload.type in {"select-mask", "remove"} and "mask_key" not in params:
        if payload.x is None or payload.y is None:
            raise HTTPException(status_code=422, detail="Coordonnées x/y requises.")

    job = job_service.enqueue(
        db, job_type=payload.type, params=params, user_id=user.id, project_id=payload.project_id
    )
    record_usage(db, user.id, "ai_jobs", 1)
    return job_service.to_payload(job)


@router.get("/jobs/{job_id}", dependencies=[read_limit])
def get_job(job_id: str, user: User = Depends(current_user), db: Session = Depends(session_scope)):
    job = db.get(Job, job_id)
    if job is None or (job.user_id and job.user_id != user.id):
        raise HTTPException(status_code=404, detail="Tâche introuvable.")
    payload = job_service.to_payload(job)
    payload["urls"] = _result_urls(job)
    return payload


@router.get("/jobs", dependencies=[read_limit])
def list_jobs(
    user: User = Depends(current_user),
    db: Session = Depends(session_scope),
    limit: int = Query(20, ge=1, le=100),
):
    rows = db.execute(
        select(Job).where(Job.user_id == user.id).order_by(Job.created_at.desc()).limit(limit)
    ).scalars().all()
    return {"jobs": [job_service.to_payload(j) for j in rows]}


@router.post("/jobs/{job_id}/cancel", dependencies=[read_limit])
def cancel_job(job_id: str, user: User = Depends(current_user), db: Session = Depends(session_scope)):
    job = db.get(Job, job_id)
    if job is None or (job.user_id and job.user_id != user.id):
        raise HTTPException(status_code=404, detail="Tâche introuvable.")
    return job_service.to_payload(job_service.cancel(db, job))


def _result_urls(job: Job) -> dict:
    """Signed URLs for whatever the job produced."""
    out: dict[str, str] = {}
    storage = get_storage()
    for field in ("image_key", "mask_key", "analysis_key"):
        key = (job.result or {}).get(field)
        if key:
            try:
                out[field.replace("_key", "_url")] = storage.url(key)
            except Exception:
                continue
    return out


# --------------------------------------------------------------------------
# Compatibility gateway
# --------------------------------------------------------------------------
async def _store_upload(upload: UploadFile, user: User | None, kind: str) -> str:
    settings = get_settings()
    data = await upload.read()
    if not data:
        raise HTTPException(status_code=422, detail="Fichier vide.")
    if len(data) > settings.max_image_bytes:
        raise HTTPException(status_code=413, detail="Image trop volumineuse.")
    mime = sniff_image(data)
    if mime is None:
        raise HTTPException(status_code=415, detail="Format d'image non pris en charge.")
    key = build_key(user.id if user else "anon", kind, upload.filename or "upload.png")
    get_storage().save(key, data, mime)
    return key


def _run_sync(job_type: str, params: dict, user: User | None, request: Request) -> dict:
    """Enqueue then wait. Returns the job result dict or raises."""
    Session = get_sessionmaker()
    with Session() as db:
        if user is not None:
            ensure_can_run_ai(db, user)
        job = job_service.enqueue(
            db, job_type=job_type, params=params, user_id=user.id if user else None
        )
        job_id = job.id
        if user is not None:
            record_usage(db, user.id, "ai_jobs", 1)

    deadline = time.monotonic() + MAX_WAIT
    while time.monotonic() < deadline:
        time.sleep(POLL)
        with Session() as db:
            row = db.get(Job, job_id)
            if row is None:
                raise HTTPException(status_code=500, detail="Tâche perdue.")
            if row.status == "succeeded":
                return dict(row.result or {})
            if row.status == "failed":
                raise HTTPException(status_code=502, detail=row.error or "Le traitement IA a échoué.")
            if row.status == "cancelled":
                raise HTTPException(status_code=499, detail="Traitement annulé.")
    # Timed out from the caller's point of view; the job itself keeps its own
    # deadline and will finish or fail on the worker.
    raise HTTPException(
        status_code=504,
        detail=f"Le traitement dépasse {int(MAX_WAIT)} s. Suivez la tâche {job_id} via /api/jobs.",
        headers={"X-Job-Id": job_id},
    )


def _data_url(key: str, mime: str = "image/png") -> str:
    raw = get_storage().load(key)
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")


def _require(result: dict, key: str, action: str) -> str:
    """A queued job can in principle finish "successfully" with a shape the
    caller does not expect — a misconfigured executor, a worker running a
    stale version, a test double. That must surface as a clear 502, never
    as an unhandled KeyError turning into a bare 500."""
    value = result.get(key)
    if not value:
        raise HTTPException(
            status_code=502,
            detail=f"{action} : résultat inattendu du service IA (clé « {key} » absente).",
        )
    return value


@router.post("/ai/analyze", dependencies=[ai_limit])
async def gateway_analyze(
    request: Request,
    file: UploadFile = File(...),
    user: User | None = Depends(optional_user),
):
    key = await _store_upload(file, user, "image")
    result = _run_sync("analyze", {"image_key": key}, user, request)
    analysis_key = result.get("analysis_key")
    if not analysis_key:
        raise HTTPException(status_code=502, detail="Analyse indisponible.")
    payload = json.loads(get_storage().load(analysis_key).decode("utf-8"))
    return JSONResponse(payload)


@router.post("/ai/select-mask", dependencies=[ai_limit])
async def gateway_select_mask(
    request: Request,
    file: UploadFile = File(...),
    x: int = Form(...),
    y: int = Form(...),
    user: User | None = Depends(optional_user),
):
    key = await _store_upload(file, user, "image")
    result = _run_sync("select-mask", {"image_key": key, "x": x, "y": y}, user, request)
    mask_key = _require(result, "mask_key", "Sélection IA")
    return JSONResponse({
        "mask": _data_url(mask_key),
        "label": result.get("label"),
        "area_ratio": result.get("area_ratio"),
        "bbox": result.get("bbox"),
        "method": result.get("method"),
    })


@router.post("/ai/inpaint", dependencies=[ai_limit])
async def gateway_inpaint(
    request: Request,
    file: UploadFile = File(...),
    mask: UploadFile = File(...),
    debug: bool = Form(False),
    user: User | None = Depends(optional_user),
):
    image_key = await _store_upload(file, user, "image")
    mask_key_up = await _store_upload(mask, user, "mask")
    result = _run_sync("inpaint", {"image_key": image_key, "mask_key": mask_key_up}, user, request)
    out_key = _require(result, "image_key", "Reconstruction IA")
    return JSONResponse({
        "image": _data_url(out_key),
        "width": result.get("width"),
        "height": result.get("height"),
        "label": result.get("label", "meuble"),
        "method": "ai-remove-only",
        "quality_gate_passed": result.get("quality_gate_passed"),
        "backend": result.get("backend"),
        "seam": result.get("seam"),
        "furniture_penalty": result.get("furniture_penalty"),
        # Phase 6 additions — older clients ignore unknown keys.
        "confidence": result.get("confidence"),
        "needs_review": result.get("needs_review"),
        "tiled": result.get("tiled"),
        "attempts": result.get("attempts"),
    })


@router.post("/ai/remove", dependencies=[ai_limit])
async def gateway_remove(
    request: Request,
    file: UploadFile = File(...),
    x: int = Form(...),
    y: int = Form(...),
    debug: bool = Form(False),
    user: User | None = Depends(optional_user),
):
    image_key = await _store_upload(file, user, "image")
    result = _run_sync("remove", {"image_key": image_key, "x": x, "y": y}, user, request)
    out_key = _require(result, "image_key", "Retrait IA")
    return JSONResponse({
        "image": _data_url(out_key),
        "width": result.get("width"),
        "height": result.get("height"),
        "label": result.get("label", "meuble"),
        "method": "ai-remove-only",
        "quality_gate_passed": result.get("quality_gate_passed"),
        "backend": result.get("backend"),
        "seam": result.get("seam"),
        "furniture_penalty": result.get("furniture_penalty"),
        "confidence": result.get("confidence"),
        "needs_review": result.get("needs_review"),
    })

"""Upload and download.

Every byte that enters the system comes through here, so this is where the
validation lives: size first (before reading everything into memory), then
magic bytes, then a server-generated key. The client's filename is only ever
kept as a label.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import session_scope
from ..models import Asset, Project, User, new_id
from ..security import current_user, optional_user, rate_limit
from ..services.entitlements import limit_of, record_usage
from ..storage import build_key, get_storage, safe_name, sniff_image, sniff_model, stamp_is_valid

router = APIRouter(prefix="/api/files", tags=["files"])
write_limit = Depends(rate_limit("write", "rate_limit_write"))

CHUNK = 1024 * 512


async def _read_limited(upload: UploadFile, max_bytes: int) -> bytes:
    """Read with a hard ceiling so a 2 GB upload cannot exhaust memory."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Fichier trop volumineux (maximum {max_bytes // (1024 * 1024)} Mo).",
            )
        chunks.append(chunk)
    if total == 0:
        raise HTTPException(status_code=422, detail="Fichier vide.")
    return b"".join(chunks)


def _image_size(data: bytes) -> tuple[int | None, int | None]:
    try:
        import io

        from PIL import Image
        with Image.open(io.BytesIO(data)) as img:
            return img.width, img.height
    except Exception:
        return None, None


@router.post("/images", status_code=201, dependencies=[write_limit])
async def upload_image(
    file: UploadFile = File(...),
    kind: str = Form("image"),
    project_id: str | None = Form(None),
    user: User = Depends(current_user),
    db: Session = Depends(session_scope),
):
    settings = get_settings()
    data = await _read_limited(file, settings.max_image_bytes)
    mime = sniff_image(data)
    if mime is None:
        raise HTTPException(
            status_code=415,
            detail="Format non pris en charge. Utilisez JPG, PNG ou WebP.",
        )
    if kind not in {"image", "render", "mask", "thumbnail"}:
        kind = "image"

    quota_mb = limit_of(user, "storage_mb", 200)
    used = get_storage().usage_bytes(user.id)
    if used + len(data) > quota_mb * 1024 * 1024:
        raise HTTPException(status_code=402, detail="Espace de stockage épuisé pour votre formule.")

    if project_id:
        project = db.get(Project, project_id)
        if project is None or project.user_id != user.id:
            raise HTTPException(status_code=404, detail="Projet introuvable.")

    storage = get_storage()
    key = build_key(user.id, kind, file.filename or "image.png")
    storage.save(key, data, mime)
    width, height = _image_size(data)
    asset = Asset(
        id=new_id(), user_id=user.id, project_id=project_id, kind=kind, storage_key=key,
        content_type=mime, byte_size=len(data), width=width, height=height,
        original_name=safe_name(file.filename or "image"),
    )
    db.add(asset)
    db.commit()
    record_usage(db, user.id, "storage_bytes", len(data))
    return {
        "id": asset.id, "key": key, "url": storage.url(key), "content_type": mime,
        "width": width, "height": height, "byte_size": len(data),
    }


@router.post("/models", status_code=201, dependencies=[write_limit])
async def upload_model(
    file: UploadFile = File(...),
    project_id: str | None = Form(None),
    user: User = Depends(current_user),
    db: Session = Depends(session_scope),
):
    """GLB/GLTF upload. The bytes are never parsed server-side — they are
    stored and handed to the viewer, which loads them in the browser sandbox."""
    settings = get_settings()
    data = await _read_limited(file, settings.max_model_bytes)
    mime = sniff_model(data, file.filename or "")
    if mime is None:
        raise HTTPException(status_code=415, detail="Fichier 3D non reconnu (attendu : .glb ou .gltf).")

    quota_mb = limit_of(user, "storage_mb", 200)
    if get_storage().usage_bytes(user.id) + len(data) > quota_mb * 1024 * 1024:
        raise HTTPException(status_code=402, detail="Espace de stockage épuisé pour votre formule.")

    storage = get_storage()
    key = build_key(user.id, "model", file.filename or "model.glb")
    storage.save(key, data, mime)
    asset = Asset(
        id=new_id(), user_id=user.id, project_id=project_id, kind="model", storage_key=key,
        content_type=mime, byte_size=len(data), original_name=safe_name(file.filename or "model.glb"),
    )
    db.add(asset)
    db.commit()
    record_usage(db, user.id, "storage_bytes", len(data))
    return {"id": asset.id, "key": key, "url": storage.url(key), "byte_size": len(data)}


@router.get("/{key:path}")
def download(
    key: str,
    t: str = Query("", description="Jeton de lien signé"),
    user: User | None = Depends(optional_user),
    db: Session = Depends(session_scope),
):
    """Local-storage delivery.

    Access is granted either by a signed stamp (what `storage.url()` produces,
    usable in an <img src>) or by a bearer token belonging to the owner. With
    the S3 backend this route is unused: URLs are presigned by the bucket.
    """
    asset = db.execute(select(Asset).where(Asset.storage_key == key)).scalar_one_or_none()
    owner_ok = user is not None and asset is not None and asset.user_id == user.id
    stamp_ok = bool(t) and stamp_is_valid(key, t)
    if not (owner_ok or stamp_ok):
        raise HTTPException(status_code=404, detail="Fichier introuvable.")

    storage = get_storage()
    data = storage.load(key)
    media = asset.content_type if asset else "application/octet-stream"
    return Response(
        content=data,
        media_type=media,
        headers={
            "Cache-Control": "private, max-age=300",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline",
        },
    )


@router.delete("/{asset_id}")
def delete_asset(asset_id: str, user: User = Depends(current_user), db: Session = Depends(session_scope)):
    asset = db.get(Asset, asset_id)
    if asset is None or asset.user_id != user.id:
        raise HTTPException(status_code=404, detail="Fichier introuvable.")
    try:
        get_storage().delete(asset.storage_key)
    except Exception:
        pass
    db.delete(asset)
    db.commit()
    return {"ok": True}

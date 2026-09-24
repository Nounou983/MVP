"""Persistent projects — the save/load backbone.

A project row holds the whole scene document: room reference, furniture,
transforms, colours, camera, lighting and the AI edit trail. Closing the tab
and coming back is a GET.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import session_scope
from ..models import Asset, Project, ProjectVersion, Share, User, new_id, utcnow
from ..schemas import ProjectIn, ProjectPatch
from ..security import current_user, rate_limit
from ..services.entitlements import ensure_can_create_project
from ..storage import get_storage

router = APIRouter(prefix="/api/projects", tags=["projects"])
write_limit = Depends(rate_limit("write", "rate_limit_write"))
read_limit = Depends(rate_limit("read", "rate_limit_read"))

MAX_VERSIONS = 20


def _owned(db: Session, project_id: str, user: User) -> Project:
    project = db.get(Project, project_id)
    if project is None or project.deleted_at is not None or project.user_id != user.id:
        # 404 rather than 403: a stranger should not learn that the id exists.
        raise HTTPException(status_code=404, detail="Projet introuvable.")
    return project


def _state_stats(state: dict) -> tuple[int, float]:
    items = state.get("items") or []
    total = 0.0
    for item in items:
        try:
            total += float(item.get("price") or 0)
        except (TypeError, ValueError):
            continue
    return len(items), total


def _check_state_size(state: dict) -> None:
    size = len(json.dumps(state).encode("utf-8"))
    limit = get_settings().max_state_bytes
    if size > limit:
        raise HTTPException(
            status_code=413,
            detail=f"Le projet dépasse la taille autorisée ({size // 1024} Ko > {limit // 1024} Ko).",
        )


def _summary(project: Project, storage=None) -> dict:
    storage = storage or get_storage()
    thumb = None
    if project.thumbnail_key:
        try:
            thumb = storage.url(project.thumbnail_key)
        except Exception:
            thumb = None
    return {
        "id": project.id,
        "name": project.name,
        "item_count": project.item_count,
        "total_price": project.total_price,
        "thumbnail_url": thumb,
        "thumbnail_key": project.thumbnail_key,
        "room_key": project.room_key,
        "created_at": project.created_at.isoformat() if project.created_at else None,
        "updated_at": project.updated_at.isoformat() if project.updated_at else None,
    }


@router.get("", dependencies=[read_limit])
def list_projects(
    user: User = Depends(current_user),
    db: Session = Depends(session_scope),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    rows = db.execute(
        select(Project)
        .where(Project.user_id == user.id, Project.deleted_at.is_(None))
        .order_by(Project.updated_at.desc())
        .limit(limit)
        .offset(offset)
    ).scalars().all()
    return {"projects": [_summary(p) for p in rows]}


@router.post("", status_code=201, dependencies=[write_limit])
def create_project(
    payload: ProjectIn, user: User = Depends(current_user), db: Session = Depends(session_scope)
):
    ensure_can_create_project(db, user)
    _check_state_size(payload.state)
    count, total = _state_stats(payload.state)
    project = Project(
        id=new_id(),
        user_id=user.id,
        name=payload.name.strip()[:200] or "Composition",
        state=payload.state,
        room_key=payload.room_key,
        thumbnail_key=payload.thumbnail_key,
        item_count=count,
        total_price=total,
    )
    db.add(project)
    db.commit()
    return _summary(project)


@router.get("/{project_id}", dependencies=[read_limit])
def get_project(project_id: str, user: User = Depends(current_user), db: Session = Depends(session_scope)):
    project = _owned(db, project_id, user)
    payload = _summary(project)
    payload["state"] = project.state
    payload["schema_version"] = project.schema_version
    return payload


@router.patch("/{project_id}", dependencies=[write_limit])
def update_project(
    project_id: str,
    payload: ProjectPatch,
    user: User = Depends(current_user),
    db: Session = Depends(session_scope),
):
    project = _owned(db, project_id, user)

    if payload.state is not None:
        _check_state_size(payload.state)
        # Keep the previous document so a bad autosave is recoverable.
        revision = len(project.versions) + 1
        db.add(ProjectVersion(id=new_id(), project_id=project.id, revision=revision, state=project.state))
        extra = db.execute(
            select(ProjectVersion)
            .where(ProjectVersion.project_id == project.id)
            .order_by(ProjectVersion.created_at.desc())
            .offset(MAX_VERSIONS)
        ).scalars().all()
        for old in extra:
            db.delete(old)
        project.state = payload.state
        project.item_count, project.total_price = _state_stats(payload.state)

    if payload.name is not None:
        project.name = payload.name.strip()[:200] or project.name
    if payload.room_key is not None:
        project.room_key = payload.room_key
    if payload.thumbnail_key is not None:
        project.thumbnail_key = payload.thumbnail_key

    project.updated_at = utcnow()
    db.commit()
    return _summary(project)


@router.post("/{project_id}/duplicate", status_code=201, dependencies=[write_limit])
def duplicate_project(
    project_id: str, user: User = Depends(current_user), db: Session = Depends(session_scope)
):
    source = _owned(db, project_id, user)
    ensure_can_create_project(db, user)
    copy = Project(
        id=new_id(),
        user_id=user.id,
        name=f"{source.name} (copie)"[:200],
        state=source.state,
        room_key=source.room_key,
        thumbnail_key=source.thumbnail_key,
        item_count=source.item_count,
        total_price=source.total_price,
    )
    db.add(copy)
    db.commit()
    return _summary(copy)


@router.get("/{project_id}/versions", dependencies=[read_limit])
def list_versions(project_id: str, user: User = Depends(current_user), db: Session = Depends(session_scope)):
    project = _owned(db, project_id, user)
    rows = db.execute(
        select(ProjectVersion)
        .where(ProjectVersion.project_id == project.id)
        .order_by(ProjectVersion.created_at.desc())
    ).scalars().all()
    return {
        "versions": [
            {"id": v.id, "revision": v.revision, "created_at": v.created_at.isoformat()}
            for v in rows
        ]
    }


@router.post("/{project_id}/versions/{version_id}/restore", dependencies=[write_limit])
def restore_version(
    project_id: str,
    version_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(session_scope),
):
    project = _owned(db, project_id, user)
    version = db.get(ProjectVersion, version_id)
    if version is None or version.project_id != project.id:
        raise HTTPException(status_code=404, detail="Version introuvable.")
    db.add(ProjectVersion(
        id=new_id(), project_id=project.id, revision=len(project.versions) + 1, state=project.state
    ))
    project.state = version.state
    project.item_count, project.total_price = _state_stats(version.state)
    project.updated_at = utcnow()
    db.commit()
    payload = _summary(project)
    payload["state"] = project.state
    return payload


@router.delete("/{project_id}", dependencies=[write_limit])
def delete_project(
    project_id: str,
    purge: bool = Query(False, description="Supprimer aussi les fichiers stockés"),
    user: User = Depends(current_user),
    db: Session = Depends(session_scope),
):
    project = _owned(db, project_id, user)
    project.deleted_at = utcnow()
    db.execute(select(Share).where(Share.project_id == project.id))
    for share in db.execute(select(Share).where(Share.project_id == project.id)).scalars().all():
        share.revoked = True

    if purge:
        storage = get_storage()
        assets = db.execute(
            select(Asset).where(Asset.project_id == project.id, Asset.user_id == user.id)
        ).scalars().all()
        for asset in assets:
            try:
                storage.delete(asset.storage_key)
            except Exception:
                pass
            db.delete(asset)
    db.commit()
    return {"ok": True, "id": project.id, "purged": purge}

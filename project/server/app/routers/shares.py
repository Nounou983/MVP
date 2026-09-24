"""Shareable links and the read-only client view.

A share token grants access to exactly one project snapshot and nothing else:
no user data, no project list, no write path. Comments are opt-in per link.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import session_scope
from ..models import Project, Share, ShareComment, User, new_id
from ..schemas import CommentIn, ShareIn
from ..security import current_user, rate_limit
from ..services.entitlements import ensure_feature, limit_of
from ..storage import get_storage

router = APIRouter(prefix="/api", tags=["partage"])
write_limit = Depends(rate_limit("write", "rate_limit_write"))
read_limit = Depends(rate_limit("read", "rate_limit_read"))


def _share_payload(share: Share, request: Request | None = None) -> dict:
    base = ""
    if request is not None:
        origin = request.headers.get("origin") or str(request.base_url).rstrip("/")
        base = origin.rstrip("/")
    return {
        "id": share.id,
        "token": share.token,
        "permission": share.permission,
        "url": f"{base}/share.html?t={share.token}" if base else f"/share.html?t={share.token}",
        "expires_at": share.expires_at.isoformat() if share.expires_at else None,
        "revoked": share.revoked,
        "view_count": share.view_count,
        "created_at": share.created_at.isoformat() if share.created_at else None,
    }


def _resolve(db: Session, token: str) -> Share:
    share = db.execute(select(Share).where(Share.token == token)).scalar_one_or_none()
    if share is None or share.revoked:
        raise HTTPException(status_code=404, detail="Ce lien n'est plus valable.")
    if share.expires_at is not None:
        expires = share.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires < datetime.now(timezone.utc):
            raise HTTPException(status_code=410, detail="Ce lien a expiré.")
    return share


@router.post("/projects/{project_id}/shares", status_code=201, dependencies=[write_limit])
def create_share(
    project_id: str,
    payload: ShareIn,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(session_scope),
):
    project = db.get(Project, project_id)
    if project is None or project.user_id != user.id or project.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Projet introuvable.")
    ensure_feature(user, "share", "Le partage de projet")
    if payload.permission == "comment":
        ensure_feature(user, "comments", "Les commentaires client")

    active = db.execute(
        select(Share).where(Share.user_id == user.id, Share.revoked.is_(False))
    ).scalars().all()
    if len(active) >= limit_of(user, "share_links", 1):
        raise HTTPException(
            status_code=402,
            detail=f"Votre formule autorise {int(limit_of(user, 'share_links', 1))} liens actifs.",
        )

    expires = None
    if payload.expires_in_days:
        expires = datetime.now(timezone.utc) + timedelta(days=payload.expires_in_days)

    share = Share(
        id=new_id(), token=secrets.token_urlsafe(24)[:48], project_id=project.id,
        user_id=user.id, permission=payload.permission, expires_at=expires,
    )
    db.add(share)
    db.commit()
    return _share_payload(share, request)


@router.get("/projects/{project_id}/shares", dependencies=[read_limit])
def list_shares(
    project_id: str,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(session_scope),
):
    project = db.get(Project, project_id)
    if project is None or project.user_id != user.id:
        raise HTTPException(status_code=404, detail="Projet introuvable.")
    rows = db.execute(
        select(Share).where(Share.project_id == project.id).order_by(Share.created_at.desc())
    ).scalars().all()
    return {"shares": [_share_payload(s, request) for s in rows]}


@router.delete("/shares/{share_id}", dependencies=[write_limit])
def revoke_share(share_id: str, user: User = Depends(current_user), db: Session = Depends(session_scope)):
    share = db.get(Share, share_id)
    if share is None or share.user_id != user.id:
        raise HTTPException(status_code=404, detail="Lien introuvable.")
    share.revoked = True
    db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------
# Public (no authentication)
# --------------------------------------------------------------------------
@router.get("/shared/{token}", dependencies=[read_limit])
def read_shared(token: str, db: Session = Depends(session_scope)):
    share = _resolve(db, token)
    project = db.get(Project, share.project_id)
    if project is None or project.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Ce projet n'existe plus.")
    share.view_count += 1
    db.commit()

    storage = get_storage()
    room_url = None
    if project.room_key:
        try:
            room_url = storage.url(project.room_key)
        except Exception:
            room_url = None

    owner = db.get(User, project.user_id)
    return {
        "project": {
            "name": project.name,
            "state": project.state,
            "item_count": project.item_count,
            "total_price": project.total_price,
            "room_url": room_url,
            "updated_at": project.updated_at.isoformat() if project.updated_at else None,
        },
        "author": {"display_name": owner.display_name if owner else ""},
        "permission": share.permission,
        "can_comment": share.permission == "comment",
    }


@router.get("/shared/{token}/comments", dependencies=[read_limit])
def list_comments(token: str, db: Session = Depends(session_scope)):
    share = _resolve(db, token)
    rows = db.execute(
        select(ShareComment).where(ShareComment.share_id == share.id).order_by(ShareComment.created_at.asc())
    ).scalars().all()
    return {
        "comments": [
            {
                "id": c.id, "author_name": c.author_name, "body": c.body,
                "anchor": c.anchor, "created_at": c.created_at.isoformat(),
            }
            for c in rows
        ]
    }


@router.post("/shared/{token}/comments", status_code=201, dependencies=[write_limit])
def add_comment(token: str, payload: CommentIn, db: Session = Depends(session_scope)):
    share = _resolve(db, token)
    if share.permission != "comment":
        raise HTTPException(status_code=403, detail="Ce lien est en lecture seule.")
    body = payload.body.strip()
    if not body:
        raise HTTPException(status_code=422, detail="Le commentaire est vide.")
    comment = ShareComment(
        id=new_id(), share_id=share.id,
        author_name=(payload.author_name or "Client").strip()[:120],
        body=body[:4000], anchor=payload.anchor,
    )
    db.add(comment)
    db.commit()
    return {"id": comment.id, "created_at": comment.created_at.isoformat()}

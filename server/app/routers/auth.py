"""Registration, login, tokens and profile."""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import DEFAULT_PLAN
from ..db import session_scope
from ..models import User, new_id, utcnow
from ..schemas import LoginIn, PasswordChangeIn, ProfileIn, RefreshIn, RegisterIn
from ..security import (
    create_token, current_user, decode_token, hash_password, password_problem,
    rate_limit, verify_password,
)
from ..services.entitlements import entitlements_payload

router = APIRouter(prefix="/api/auth", tags=["auth"])

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")
auth_limit = Depends(rate_limit("auth", "rate_limit_auth"))


def _public_user(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "locale": user.locale,
        "plan": user.plan,
        "is_admin": user.is_admin,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


def _session_payload(user: User) -> dict:
    return {
        "user": _public_user(user),
        "access_token": create_token(user, "access"),
        "refresh_token": create_token(user, "refresh"),
        "token_type": "bearer",
    }


@router.post("/register", dependencies=[auth_limit])
def register(payload: RegisterIn, db: Session = Depends(session_scope)):
    email = payload.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="Adresse e-mail invalide.")
    problem = password_problem(payload.password)
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    existing = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if existing is not None:
        # Same status as a bad login so the endpoint cannot be used to enumerate accounts.
        raise HTTPException(status_code=409, detail="Un compte existe déjà pour cette adresse.")

    user = User(
        id=new_id(),
        email=email,
        password_hash=hash_password(payload.password),
        display_name=(payload.display_name or email.split("@")[0])[:120],
        plan=DEFAULT_PLAN,
    )
    db.add(user)
    db.commit()
    return _session_payload(user)


@router.post("/login", dependencies=[auth_limit])
def login(payload: LoginIn, db: Session = Depends(session_scope)):
    email = payload.email.strip().lower()
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Adresse e-mail ou mot de passe incorrect.")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Ce compte est désactivé.")
    user.last_seen_at = utcnow()
    db.commit()
    return _session_payload(user)


@router.post("/refresh", dependencies=[auth_limit])
def refresh(payload: RefreshIn, db: Session = Depends(session_scope)):
    try:
        data = decode_token(payload.refresh_token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    if data.get("kind") != "refresh":
        raise HTTPException(status_code=401, detail="Jeton de rafraîchissement attendu.")
    user = db.get(User, data.get("sub"))
    if user is None or not user.is_active or int(data.get("epoch", 0)) != int(user.token_epoch):
        raise HTTPException(status_code=401, detail="Session expirée, reconnectez-vous.")
    return {"access_token": create_token(user, "access"), "token_type": "bearer"}


@router.get("/me")
def me(user: User = Depends(current_user), db: Session = Depends(session_scope)):
    return {"user": _public_user(user), "entitlements": entitlements_payload(db, user)}


@router.patch("/me")
def update_profile(
    payload: ProfileIn, user: User = Depends(current_user), db: Session = Depends(session_scope)
):
    if payload.display_name is not None:
        user.display_name = payload.display_name.strip()[:120]
    if payload.locale is not None:
        user.locale = payload.locale.strip()[:12] or "fr"
    db.commit()
    return _public_user(user)


@router.post("/password")
def change_password(
    payload: PasswordChangeIn, user: User = Depends(current_user), db: Session = Depends(session_scope)
):
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=401, detail="Mot de passe actuel incorrect.")
    problem = password_problem(payload.new_password)
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    user.password_hash = hash_password(payload.new_password)
    user.token_epoch += 1          # every existing token dies here
    db.commit()
    return _session_payload(user)


@router.post("/logout-all")
def logout_all(user: User = Depends(current_user), db: Session = Depends(session_scope)):
    user.token_epoch += 1
    db.commit()
    return {"ok": True, "revoked": True}

"""Plans, quotas and usage accounting.

Enforcement reads `PLANS` from config; nothing here knows about a payment
provider. When billing is wired, a webhook only has to set `user.plan`.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import DEFAULT_PLAN, PLANS
from ..models import Project, UsageCounter, User, new_id


def plan_of(user: User | None) -> dict:
    name = (user.plan if user else DEFAULT_PLAN) or DEFAULT_PLAN
    return PLANS.get(name, PLANS[DEFAULT_PLAN])


def limit_of(user: User | None, key: str, default: float = 0) -> float:
    return plan_of(user)["limits"].get(key, default)


def feature_enabled(user: User | None, key: str) -> bool:
    return bool(plan_of(user)["features"].get(key, False))


def current_period() -> str:
    now = datetime.now(timezone.utc)
    return f"{now.year:04d}-{now.month:02d}"


def usage_value(db: Session, user_id: str, metric: str, period: str | None = None) -> float:
    period = period or current_period()
    row = db.execute(
        select(UsageCounter).where(
            UsageCounter.user_id == user_id,
            UsageCounter.period == period,
            UsageCounter.metric == metric,
        )
    ).scalar_one_or_none()
    return float(row.value) if row else 0.0


def record_usage(db: Session, user_id: str, metric: str, amount: float = 1.0) -> float:
    period = current_period()
    row = db.execute(
        select(UsageCounter).where(
            UsageCounter.user_id == user_id,
            UsageCounter.period == period,
            UsageCounter.metric == metric,
        )
    ).scalar_one_or_none()
    if row is None:
        row = UsageCounter(id=new_id(), user_id=user_id, period=period, metric=metric, value=0.0)
        db.add(row)
    row.value = float(row.value) + amount
    db.commit()
    return float(row.value)


def ensure_can_create_project(db: Session, user: User) -> None:
    limit = limit_of(user, "projects", 3)
    count = db.execute(
        select(func.count(Project.id)).where(Project.user_id == user.id, Project.deleted_at.is_(None))
    ).scalar_one()
    if count >= limit:
        raise HTTPException(
            status_code=402,
            detail=f"Votre formule {plan_of(user)['label']} autorise {int(limit)} projets. "
                   "Supprimez-en un ou passez à la formule supérieure.",
        )


def ensure_can_run_ai(db: Session, user: User) -> None:
    limit = limit_of(user, "ai_jobs_per_month", 40)
    used = usage_value(db, user.id, "ai_jobs")
    if used >= limit:
        raise HTTPException(
            status_code=402,
            detail=f"Quota IA mensuel atteint ({int(limit)} traitements). "
                   "Il se réinitialise au début du mois prochain.",
        )


def ensure_feature(user: User, key: str, label: str) -> None:
    if not feature_enabled(user, key):
        raise HTTPException(
            status_code=402,
            detail=f"{label} n'est pas inclus dans la formule {plan_of(user)['label']}.",
        )


def entitlements_payload(db: Session, user: User) -> dict:
    plan = plan_of(user)
    period = current_period()
    projects = db.execute(
        select(func.count(Project.id)).where(Project.user_id == user.id, Project.deleted_at.is_(None))
    ).scalar_one()
    return {
        "plan": user.plan,
        "label": plan["label"],
        "limits": plan["limits"],
        "features": plan["features"],
        "period": period,
        "usage": {
            "projects": int(projects),
            "ai_jobs": usage_value(db, user.id, "ai_jobs", period),
            "exports": usage_value(db, user.id, "exports", period),
            "storage_bytes": usage_value(db, user.id, "storage_bytes", period),
        },
    }

"""Database schema.

SQLite by default, PostgreSQL in production through DATABASE_URL. Every row
that belongs to a person carries ``user_id``; there is no global namespace,
which is what makes the isolation checks in the routers cheap and total.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(String(32), primary_key=True, default=new_id)
    email = Column(String(320), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    display_name = Column(String(120), nullable=False, default="")
    locale = Column(String(12), nullable=False, default="fr")
    plan = Column(String(32), nullable=False, default="free")
    is_admin = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True)
    token_epoch = Column(Integer, nullable=False, default=0)  # bump to revoke all tokens
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)

    projects = relationship("Project", back_populates="owner", cascade="all, delete-orphan")


class Project(Base):
    __tablename__ = "projects"

    id = Column(String(32), primary_key=True, default=new_id)
    user_id = Column(String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(200), nullable=False, default="Composition")
    schema_version = Column(Integer, nullable=False, default=1)
    state = Column(JSON, nullable=False, default=dict)      # full scene document
    thumbnail_key = Column(String(400), nullable=True)
    room_key = Column(String(400), nullable=True)           # stored room photo
    item_count = Column(Integer, nullable=False, default=0)
    total_price = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    owner = relationship("User", back_populates="projects")
    versions = relationship("ProjectVersion", back_populates="project", cascade="all, delete-orphan")

    __table_args__ = (Index("ix_projects_user_updated", "user_id", "updated_at"),)


class ProjectVersion(Base):
    """Rolling history so a bad save is recoverable. Capped per project."""
    __tablename__ = "project_versions"

    id = Column(String(32), primary_key=True, default=new_id)
    project_id = Column(String(32), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    revision = Column(Integer, nullable=False, default=1)
    state = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    project = relationship("Project", back_populates="versions")


class Asset(Base):
    """A stored file (room photo, export, GLB). The bytes live in Storage."""
    __tablename__ = "assets"

    id = Column(String(32), primary_key=True, default=new_id)
    user_id = Column(String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id = Column(String(32), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True)
    kind = Column(String(32), nullable=False, default="image")   # image | render | model | mask
    storage_key = Column(String(400), nullable=False)
    content_type = Column(String(120), nullable=False, default="application/octet-stream")
    byte_size = Column(Integer, nullable=False, default=0)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    original_name = Column(String(255), nullable=False, default="")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class Favorite(Base):
    __tablename__ = "favorites"

    id = Column(String(32), primary_key=True, default=new_id)
    user_id = Column(String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (UniqueConstraint("user_id", "product_id", name="uq_favorite_user_product"),)


class Collection(Base):
    __tablename__ = "collections"

    id = Column(String(32), primary_key=True, default=new_id)
    user_id = Column(String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(120), nullable=False)
    note = Column(Text, nullable=False, default="")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    items = relationship("CollectionItem", back_populates="collection", cascade="all, delete-orphan")


class CollectionItem(Base):
    __tablename__ = "collection_items"

    id = Column(String(32), primary_key=True, default=new_id)
    collection_id = Column(String(32), ForeignKey("collections.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id = Column(String(64), nullable=False)
    position = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    collection = relationship("Collection", back_populates="items")

    __table_args__ = (UniqueConstraint("collection_id", "product_id", name="uq_collection_product"),)


class Share(Base):
    __tablename__ = "shares"

    id = Column(String(32), primary_key=True, default=new_id)
    token = Column(String(48), unique=True, nullable=False, index=True)
    project_id = Column(String(32), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    permission = Column(String(16), nullable=False, default="view")   # view | comment
    expires_at = Column(DateTime(timezone=True), nullable=True)
    revoked = Column(Boolean, nullable=False, default=False)
    view_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class ShareComment(Base):
    __tablename__ = "share_comments"

    id = Column(String(32), primary_key=True, default=new_id)
    share_id = Column(String(32), ForeignKey("shares.id", ondelete="CASCADE"), nullable=False, index=True)
    author_name = Column(String(120), nullable=False, default="Client")
    body = Column(Text, nullable=False)
    anchor = Column(JSON, nullable=True)     # optional {x, y} on the photo
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class Job(Base):
    """A unit of GPU work. Queued here, claimed by a worker process."""
    __tablename__ = "jobs"

    id = Column(String(32), primary_key=True, default=new_id)
    user_id = Column(String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    project_id = Column(String(32), nullable=True, index=True)
    type = Column(String(40), nullable=False)                 # analyze | select-mask | remove
    status = Column(String(16), nullable=False, default="queued", index=True)
    # queued | running | succeeded | failed | cancelled
    priority = Column(Integer, nullable=False, default=100)
    params = Column(JSON, nullable=False, default=dict)
    result = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    progress = Column(Float, nullable=False, default=0.0)
    message = Column(String(255), nullable=False, default="")
    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=2)
    cancel_requested = Column(Boolean, nullable=False, default=False)
    worker_id = Column(String(64), nullable=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_jobs_status_priority", "status", "priority", "created_at"),)


class AnalyticsEvent(Base):
    __tablename__ = "analytics_events"

    id = Column(String(32), primary_key=True, default=new_id)
    user_id = Column(String(32), nullable=True, index=True)
    session_id = Column(String(64), nullable=False, default="")
    name = Column(String(64), nullable=False, index=True)
    props = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)


class UsageCounter(Base):
    """Monthly accounting per user, the hook a billing provider reads."""
    __tablename__ = "usage_counters"

    id = Column(String(32), primary_key=True, default=new_id)
    user_id = Column(String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    period = Column(String(7), nullable=False)           # YYYY-MM
    metric = Column(String(40), nullable=False)          # ai_jobs | exports | storage_bytes
    value = Column(Float, nullable=False, default=0.0)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (UniqueConstraint("user_id", "period", "metric", name="uq_usage_user_period_metric"),)


class SchemaVersion(Base):
    __tablename__ = "schema_version"

    id = Column(Integer, primary_key=True, default=1)
    version = Column(Integer, nullable=False, default=1)
    applied_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


CURRENT_SCHEMA_VERSION = 1

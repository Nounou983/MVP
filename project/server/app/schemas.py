"""Request/response models.

Kept in one place so the API documentation at /docs stays readable.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field


class RegisterIn(BaseModel):
    email: str = Field(..., max_length=320)
    password: str = Field(..., max_length=256)
    display_name: str = Field("", max_length=120)


class LoginIn(BaseModel):
    email: str = Field(..., max_length=320)
    password: str = Field(..., max_length=256)


class RefreshIn(BaseModel):
    refresh_token: str


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str = Field(..., max_length=256)


class ProfileIn(BaseModel):
    display_name: str | None = Field(None, max_length=120)
    locale: str | None = Field(None, max_length=12)


class ProjectIn(BaseModel):
    name: str = Field("Composition", max_length=200)
    state: dict[str, Any] = Field(default_factory=dict)
    room_key: str | None = None
    thumbnail_key: str | None = None


class ProjectPatch(BaseModel):
    name: str | None = Field(None, max_length=200)
    state: dict[str, Any] | None = None
    room_key: str | None = None
    thumbnail_key: str | None = None


class CollectionIn(BaseModel):
    name: str = Field(..., max_length=120)
    note: str = Field("", max_length=2000)


class CollectionItemIn(BaseModel):
    product_id: str = Field(..., max_length=64)


class FavoriteIn(BaseModel):
    product_id: str = Field(..., max_length=64)


class ShareIn(BaseModel):
    permission: Literal["view", "comment"] = "view"
    expires_in_days: int | None = Field(None, ge=1, le=365)


class CommentIn(BaseModel):
    author_name: str = Field("Client", max_length=120)
    body: str = Field(..., max_length=4000)
    anchor: dict[str, Any] | None = None


class AnalyticsEventIn(BaseModel):
    name: str = Field(..., max_length=64)
    props: dict[str, Any] = Field(default_factory=dict)
    session_id: str = Field("", max_length=64)


class AnalyticsBatchIn(BaseModel):
    events: list[AnalyticsEventIn] = Field(default_factory=list, max_length=50)


class JobIn(BaseModel):
    type: Literal["analyze", "select-mask", "remove", "inpaint"]
    image_key: str = Field(..., max_length=400)
    mask_key: str | None = Field(None, max_length=400)
    project_id: str | None = Field(None, max_length=32)
    x: int | None = None
    y: int | None = None
    label: str | None = Field(None, max_length=120)

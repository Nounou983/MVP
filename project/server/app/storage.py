"""Storage abstraction.

The application only ever talks to the `Storage` interface. Switching a
deployment from the local disk to S3/R2/MinIO is an environment variable, not
a code change.
"""
from __future__ import annotations

import hashlib
import hmac
import mimetypes
import os
import re
import shutil
import time
import uuid
from abc import ABC, abstractmethod
from pathlib import Path

from fastapi import HTTPException

from .config import get_settings

SAFE_SEGMENT = re.compile(r"[^a-zA-Z0-9._-]+")

# Magic-byte signatures. The browser-supplied content-type is advisory only;
# these bytes are what we actually trust.
IMAGE_SIGNATURES: list[tuple[bytes, str]] = [
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
]
MODEL_SIGNATURES: list[tuple[bytes, str]] = [
    (b"glTF", "model/gltf-binary"),
]


def sniff_image(data: bytes) -> str | None:
    for signature, mime in IMAGE_SIGNATURES:
        if data.startswith(signature):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def sniff_model(data: bytes, filename: str) -> str | None:
    for signature, mime in MODEL_SIGNATURES:
        if data.startswith(signature):
            return mime
    if filename.lower().endswith(".gltf") and data.lstrip()[:1] == b"{":
        return "model/gltf+json"
    return None


def safe_name(name: str, fallback: str = "fichier") -> str:
    base = os.path.basename(name or "").strip()
    base = SAFE_SEGMENT.sub("-", base).strip("-._")
    base = base[:80]
    return base or fallback


def build_key(user_id: str, kind: str, original_name: str) -> str:
    """Never derived from user input alone: uuid first, sanitised name second."""
    suffix = Path(safe_name(original_name)).suffix.lower()[:10]
    stem = uuid.uuid4().hex
    folder = SAFE_SEGMENT.sub("-", kind) or "misc"
    uid = SAFE_SEGMENT.sub("-", user_id or "anon")
    return f"{uid}/{folder}/{stem}{suffix}"


class Storage(ABC):
    @abstractmethod
    def save(self, key: str, data: bytes, content_type: str) -> str: ...

    @abstractmethod
    def load(self, key: str) -> bytes: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def url(self, key: str) -> str: ...

    def usage_bytes(self, prefix: str) -> int:  # pragma: no cover - overridden
        return 0


class LocalStorage(Storage):
    """Files on disk, served back through the authenticated /api/files route."""

    def __init__(self, root: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        candidate = (self.root / key).resolve()
        if not str(candidate).startswith(str(self.root.resolve())):
            raise HTTPException(status_code=400, detail="Chemin de fichier invalide.")
        return candidate

    def save(self, key: str, data: bytes, content_type: str) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def load(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Fichier introuvable.")
        return path.read_bytes()

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.is_file():
            path.unlink()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def url(self, key: str) -> str:
        return f"/api/files/{key}?t={signed_stamp(key)}"

    def usage_bytes(self, prefix: str) -> int:
        base = self._path(prefix)
        if not base.exists():
            return 0
        return sum(f.stat().st_size for f in base.rglob("*") if f.is_file())

    def drop_prefix(self, prefix: str) -> None:
        base = self._path(prefix)
        if base.is_dir():
            shutil.rmtree(base, ignore_errors=True)


class S3Storage(Storage):
    """Production backend. boto3 is imported lazily so the dev box never needs it."""

    def __init__(self, bucket: str, region: str = "", endpoint: str = "", public_base: str = ""):
        try:
            import boto3  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - deployment concern
            raise RuntimeError(
                "CIGOGNE_STORAGE=s3 requires boto3 (pip install boto3)."
            ) from exc
        kwargs: dict = {}
        if region:
            kwargs["region_name"] = region
        if endpoint:
            kwargs["endpoint_url"] = endpoint
        self.client = boto3.client("s3", **kwargs)
        self.bucket = bucket
        self.public_base = public_base.rstrip("/")

    def save(self, key: str, data: bytes, content_type: str) -> str:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        return key

    def load(self, key: str) -> bytes:
        obj = self.client.get_object(Bucket=self.bucket, Key=key)
        return obj["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False

    def url(self, key: str) -> str:
        if self.public_base:
            return f"{self.public_base}/{key}"
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=get_settings().signed_url_ttl,
        )

    def usage_bytes(self, prefix: str) -> int:
        total = 0
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                total += int(obj.get("Size", 0))
        return total


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is not None:
        return _storage
    settings = get_settings()
    if settings.storage_backend == "s3":
        _storage = S3Storage(
            settings.s3_bucket, settings.s3_region, settings.s3_endpoint, settings.s3_public_base
        )
    else:
        _storage = LocalStorage(settings.storage_dir)
    return _storage


def reset_storage_cache() -> None:
    global _storage
    _storage = None


def signed_stamp(key: str) -> str:
    """Short-lived token embedded in local file URLs (cache-busting + tamper check)."""
    settings = get_settings()
    window = int(time.time() // max(60, settings.signed_url_ttl))
    mac = hmac.new(settings.secret_key.encode(), f"{key}:{window}".encode(), hashlib.sha256)
    return mac.hexdigest()[:16]


def stamp_is_valid(key: str, stamp: str) -> bool:
    settings = get_settings()
    now = int(time.time() // max(60, settings.signed_url_ttl))
    for window in (now, now - 1):
        mac = hmac.new(settings.secret_key.encode(), f"{key}:{window}".encode(), hashlib.sha256)
        if hmac.compare_digest(mac.hexdigest()[:16], stamp):
            return True
    return False


def guess_extension(content_type: str) -> str:
    return mimetypes.guess_extension(content_type) or ".bin"

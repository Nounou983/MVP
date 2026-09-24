"""Authentication, authorisation and abuse control.

Implemented on the standard library (pbkdf2 + hmac) rather than pulling in
passlib/pyjwt, so the API image stays small and there is one less supply-chain
surface. The token format is standard JWT (HS256) and can be swapped for an
external identity provider by replacing `decode_token`.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .config import get_settings
from .db import session_scope
from .models import User, utcnow

bearer = HTTPBearer(auto_error=False)


# --------------------------------------------------------------------------
# Passwords
# --------------------------------------------------------------------------
def hash_password(password: str) -> str:
    settings = get_settings()
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, settings.password_iterations)
    return f"pbkdf2_sha256${settings.password_iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, iterations, salt_hex, digest_hex = encoded.split("$")
        if algo != "pbkdf2_sha256":
            return False
        expected = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(expected.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def password_problem(password: str) -> str | None:
    if len(password) < 10:
        return "Le mot de passe doit contenir au moins 10 caractères."
    if password.lower() in {"motdepasse", "password123", "0123456789"}:
        return "Ce mot de passe est trop courant."
    if not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
        return "Le mot de passe doit mêler lettres et chiffres."
    return None


# --------------------------------------------------------------------------
# Tokens (JWT HS256)
# --------------------------------------------------------------------------
def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(raw: str) -> bytes:
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))


def create_token(user: User, kind: str = "access", ttl: int | None = None) -> str:
    settings = get_settings()
    if ttl is None:
        ttl = settings.access_token_ttl if kind == "access" else settings.refresh_token_ttl
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": user.id,
        "kind": kind,
        "plan": user.plan,
        "epoch": user.token_epoch,
        "iat": now,
        "exp": now + ttl,
        "jti": secrets.token_urlsafe(8),
    }
    signing_input = f"{_b64(json.dumps(header, separators=(',', ':')).encode())}." \
                    f"{_b64(json.dumps(payload, separators=(',', ':')).encode())}"
    signature = hmac.new(settings.secret_key.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64(signature)}"


def decode_token(token: str) -> dict:
    settings = get_settings()
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
    except ValueError as exc:
        raise ValueError("Jeton malformé") from exc
    expected = hmac.new(
        settings.secret_key.encode(), f"{header_b64}.{payload_b64}".encode(), hashlib.sha256
    ).digest()
    if not hmac.compare_digest(expected, _unb64(signature_b64)):
        raise ValueError("Signature invalide")
    payload = json.loads(_unb64(payload_b64))
    if int(payload.get("exp", 0)) < int(time.time()):
        raise ValueError("Jeton expiré")
    return payload


# --------------------------------------------------------------------------
# Dependencies
# --------------------------------------------------------------------------
def _user_from_credentials(creds: HTTPAuthorizationCredentials | None, db: Session) -> User | None:
    if creds is None or not creds.credentials:
        return None
    try:
        payload = decode_token(creds.credentials)
    except ValueError:
        return None
    if payload.get("kind") != "access":
        return None
    user = db.get(User, payload.get("sub"))
    if user is None or not user.is_active:
        return None
    if int(payload.get("epoch", 0)) != int(user.token_epoch):
        return None       # password changed or sessions revoked
    return user


def current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(session_scope),
) -> User:
    user = _user_from_credentials(creds, db)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentification requise.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user.last_seen_at = utcnow()
    db.commit()
    return user


def optional_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(session_scope),
) -> User | None:
    return _user_from_credentials(creds, db)


def require_admin(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Réservé à l'administration.")
    return user


# --------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------
class RateLimiter:
    """Sliding window, in-process.

    Good enough for a single API container. For several replicas, swap the
    `_hits` dict for Redis — the call sites do not change.
    """

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, key: str, limit: int, window: float = 60.0) -> tuple[bool, int]:
        now = time.monotonic()
        with self._lock:
            bucket = self._hits[key]
            while bucket and now - bucket[0] > window:
                bucket.popleft()
            if len(bucket) >= limit:
                retry = int(window - (now - bucket[0])) + 1
                return False, retry
            bucket.append(now)
            return True, 0

    def clear(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


def client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(scope: str, limit_attr: str):
    """Dependency factory: `Depends(rate_limit("auth", "rate_limit_auth"))`."""

    def dependency(
        request: Request,
        creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    ) -> None:
        settings = get_settings()
        limit = getattr(settings, limit_attr)
        subject = "anon"
        if creds and creds.credentials:
            try:
                subject = decode_token(creds.credentials).get("sub", "anon")
            except ValueError:
                subject = "anon"
        key = f"{scope}:{subject}:{client_key(request)}"
        ok, retry = limiter.check(key, limit)
        if not ok:
            raise HTTPException(
                status_code=429,
                detail="Trop de requêtes, réessayez dans un instant.",
                headers={"Retry-After": str(retry)},
            )

    return dependency

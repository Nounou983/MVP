"""Backend test suite.

Run from server/:  pytest -q

No GPU and no model weights are needed: CIGOGNE_FAKE_GPU swaps the executor
for one that echoes its parameters, so the queue, the quotas and the isolation
rules are exercised for real while the diffusion pass is not.
"""
from __future__ import annotations

import io
import os
import struct
import sys
import tempfile
import zlib
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_DIR))

os.environ.setdefault("CIGOGNE_SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("CIGOGNE_FAKE_GPU", "1")
os.environ.setdefault("CIGOGNE_INLINE_WORKER", "0")
os.environ.setdefault("CIGOGNE_ENV", "test")

from fastapi.testclient import TestClient  # noqa: E402

from app import config, db as db_module, storage as storage_module  # noqa: E402
from app.services import jobs as job_service  # noqa: E402


def _png(width: int = 8, height: int = 8) -> bytes:
    """Smallest valid PNG, built by hand so the tests need no Pillow."""
    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    raw = b"".join(b"\x00" + b"\xff\x88\x44" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


@pytest.fixture()
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("cigogne")
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp / 'test.db'}"
    os.environ["CIGOGNE_STORAGE_DIR"] = str(tmp / "storage")
    config.reset_settings_cache()
    db_module.reset_engine_cache()
    storage_module.reset_storage_cache()
    from app.api import create_app
    from app.security import limiter
    limiter.clear()
    with TestClient(create_app()) as c:
        yield c


def register(client, email="amine@cigogne.dz", password="motdepasse42"):
    resp = client.post("/api/auth/register",
                       json={"email": email, "password": password, "display_name": "Amine"})
    assert resp.status_code == 200, resp.text
    return resp.json()


def auth(session) -> dict:
    return {"Authorization": f"Bearer {session['access_token']}"}


# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------
def test_register_login_and_me(client):
    session = register(client)
    assert session["user"]["email"] == "amine@cigogne.dz"
    assert session["user"]["plan"] == "free"

    login = client.post("/api/auth/login",
                        json={"email": "amine@cigogne.dz", "password": "motdepasse42"})
    assert login.status_code == 200
    me = client.get("/api/auth/me", headers=auth(login.json()))
    assert me.status_code == 200
    assert me.json()["entitlements"]["limits"]["projects"] == 3


def test_weak_password_is_refused(client):
    resp = client.post("/api/auth/register", json={"email": "x@y.dz", "password": "court"})
    assert resp.status_code == 422


def test_duplicate_email_is_refused(client):
    register(client)
    resp = client.post("/api/auth/register",
                       json={"email": "amine@cigogne.dz", "password": "motdepasse42"})
    assert resp.status_code == 409


def test_wrong_password_and_missing_token(client):
    register(client)
    assert client.post("/api/auth/login",
                       json={"email": "amine@cigogne.dz", "password": "faux-motdepasse1"}).status_code == 401
    assert client.get("/api/projects").status_code == 401
    assert client.get("/api/projects", headers={"Authorization": "Bearer nonsense"}).status_code == 401


def test_password_change_revokes_old_tokens(client):
    session = register(client)
    headers = auth(session)
    assert client.get("/api/auth/me", headers=headers).status_code == 200
    changed = client.post("/api/auth/password", headers=headers,
                          json={"current_password": "motdepasse42", "new_password": "nouveaupass99"})
    assert changed.status_code == 200
    assert client.get("/api/auth/me", headers=headers).status_code == 401
    assert client.get("/api/auth/me", headers=auth(changed.json())).status_code == 200


def test_refresh_token_flow(client):
    session = register(client)
    resp = client.post("/api/auth/refresh", json={"refresh_token": session["refresh_token"]})
    assert resp.status_code == 200
    assert client.get("/api/auth/me",
                      headers={"Authorization": f"Bearer {resp.json()['access_token']}"}).status_code == 200
    # An access token is not a refresh token.
    assert client.post("/api/auth/refresh",
                       json={"refresh_token": session["access_token"]}).status_code == 401


# --------------------------------------------------------------------------
# Projects & isolation
# --------------------------------------------------------------------------
def test_project_roundtrip_and_versions(client):
    headers = auth(register(client))
    state = {"schema": 1, "items": [{"entryId": "sofa-3", "price": 89000, "x": 10, "y": 20}]}
    created = client.post("/api/projects", json={"name": "Salon", "state": state}, headers=headers)
    assert created.status_code == 201
    pid = created.json()["id"]
    assert created.json()["item_count"] == 1
    assert created.json()["total_price"] == 89000

    fetched = client.get(f"/api/projects/{pid}", headers=headers)
    assert fetched.json()["state"]["items"][0]["entryId"] == "sofa-3"

    state["items"].append({"entryId": "lamp", "price": 9900})
    patched = client.patch(f"/api/projects/{pid}", json={"state": state, "name": "Salon v2"},
                           headers=headers)
    assert patched.json()["item_count"] == 2
    assert patched.json()["total_price"] == 98900

    versions = client.get(f"/api/projects/{pid}/versions", headers=headers).json()["versions"]
    assert len(versions) == 1
    restored = client.post(f"/api/projects/{pid}/versions/{versions[0]['id']}/restore", headers=headers)
    assert len(restored.json()["state"]["items"]) == 1


def test_projects_are_isolated_between_users(client):
    alice = auth(register(client, "alice@cigogne.dz"))
    bob = auth(register(client, "bob@cigogne.dz"))
    pid = client.post("/api/projects", json={"name": "Privé", "state": {}}, headers=alice).json()["id"]

    assert client.get(f"/api/projects/{pid}", headers=bob).status_code == 404
    assert client.patch(f"/api/projects/{pid}", json={"name": "vol"}, headers=bob).status_code == 404
    assert client.delete(f"/api/projects/{pid}", headers=bob).status_code == 404
    assert client.get("/api/projects", headers=bob).json()["projects"] == []
    assert len(client.get("/api/projects", headers=alice).json()["projects"]) == 1


def test_project_quota_is_enforced(client):
    headers = auth(register(client))
    for i in range(3):
        assert client.post("/api/projects", json={"name": f"P{i}", "state": {}},
                           headers=headers).status_code == 201
    over = client.post("/api/projects", json={"name": "P4", "state": {}}, headers=headers)
    assert over.status_code == 402
    assert "projets" in over.json()["detail"]


def test_oversized_project_state_is_refused(client):
    headers = auth(register(client))
    huge = {"items": [{"note": "x" * 1000} for _ in range(8000)]}
    assert client.post("/api/projects", json={"name": "gros", "state": huge},
                       headers=headers).status_code == 413


# --------------------------------------------------------------------------
# Uploads
# --------------------------------------------------------------------------
def test_image_upload_and_signed_download(client):
    headers = auth(register(client))
    resp = client.post("/api/files/images", headers=headers,
                       files={"file": ("piece.png", _png(), "image/png")})
    assert resp.status_code == 201, resp.text
    payload = resp.json()
    assert payload["width"] is None or payload["width"] == 8     # Pillow optional
    url = payload["url"]
    assert client.get(url).status_code == 200            # signed stamp works without a token
    assert client.get(f"/api/files/{payload['key']}").status_code == 404   # no stamp, no token


def test_upload_rejects_disguised_file(client):
    headers = auth(register(client))
    resp = client.post("/api/files/images", headers=headers,
                       files={"file": ("evil.png", b"<?php system($_GET[0]); ?>", "image/png")})
    assert resp.status_code == 415


def test_upload_rejects_fake_glb(client):
    headers = auth(register(client))
    assert client.post("/api/files/models", headers=headers,
                       files={"file": ("x.glb", b"not a model", "model/gltf-binary")}).status_code == 415
    real = b"glTF" + b"\x02\x00\x00\x00" + b"\x00" * 64
    assert client.post("/api/files/models", headers=headers,
                       files={"file": ("x.glb", real, "model/gltf-binary")}).status_code == 201


def test_uploaded_file_is_not_readable_by_another_user(client):
    alice = auth(register(client, "alice@cigogne.dz"))
    bob = auth(register(client, "bob@cigogne.dz"))
    key = client.post("/api/files/images", headers=alice,
                      files={"file": ("p.png", _png(), "image/png")}).json()["key"]
    assert client.get(f"/api/files/{key}", headers=bob).status_code == 404


def test_storage_key_ignores_client_filename(client):
    headers = auth(register(client))
    key = client.post("/api/files/images", headers=headers,
                      files={"file": ("../../etc/passwd.png", _png(), "image/png")}).json()["key"]
    assert ".." not in key
    assert key.endswith(".png")


# --------------------------------------------------------------------------
# Jobs / queue
# --------------------------------------------------------------------------
def test_job_lifecycle_with_worker(client):
    from app.services.worker import WorkerLoop
    headers = auth(register(client))
    key = client.post("/api/files/images", headers=headers,
                      files={"file": ("p.png", _png(), "image/png")}).json()["key"]

    created = client.post("/api/jobs", json={"type": "analyze", "image_key": key}, headers=headers)
    assert created.status_code == 202
    job_id = created.json()["id"]
    assert created.json()["status"] == "queued"

    loop = WorkerLoop(concurrency=1, poll=0.05)
    loop.start()
    try:
        import time
        for _ in range(100):
            time.sleep(0.05)
            status = client.get(f"/api/jobs/{job_id}", headers=headers).json()["status"]
            if status in {"succeeded", "failed"}:
                break
    finally:
        loop.stop()
    assert status == "succeeded"
    assert client.get(f"/api/jobs/{job_id}", headers=headers).json()["result"]["echo"] is True


def test_job_cannot_use_another_users_image(client):
    alice = auth(register(client, "alice@cigogne.dz"))
    bob = auth(register(client, "bob@cigogne.dz"))
    key = client.post("/api/files/images", headers=alice,
                      files={"file": ("p.png", _png(), "image/png")}).json()["key"]
    resp = client.post("/api/jobs", json={"type": "analyze", "image_key": key}, headers=bob)
    assert resp.status_code in (403, 404)


def test_job_cancellation(client):
    headers = auth(register(client))
    key = client.post("/api/files/images", headers=headers,
                      files={"file": ("p.png", _png(), "image/png")}).json()["key"]
    job_id = client.post("/api/jobs", json={"type": "analyze", "image_key": key},
                         headers=headers).json()["id"]
    cancelled = client.post(f"/api/jobs/{job_id}/cancel", headers=headers)
    assert cancelled.json()["status"] == "cancelled"


def test_ai_quota_is_enforced(client):
    from app.models import User
    from app.services.entitlements import record_usage
    session = register(client)
    headers = auth(session)
    key = client.post("/api/files/images", headers=headers,
                      files={"file": ("p.png", _png(), "image/png")}).json()["key"]
    with db_module.get_sessionmaker()() as db:
        user = db.query(User).filter(User.email == "amine@cigogne.dz").one()
        record_usage(db, user.id, "ai_jobs", 40)
    resp = client.post("/api/jobs", json={"type": "analyze", "image_key": key}, headers=headers)
    assert resp.status_code == 402


def test_queue_claim_is_single_flight(client):
    """Two workers must not run the same job."""
    register(client)
    with db_module.get_sessionmaker()() as db:
        job = job_service.enqueue(db, job_type="analyze", params={"image_key": "k"})
        first = job_service.claim_next(db, "worker-a")
        second = job_service.claim_next(db, "worker-b")
    assert first is not None and first.id == job.id
    assert second is None


def test_stalled_job_is_requeued(client):
    from datetime import datetime, timedelta, timezone
    from app.models import Job
    register(client)
    Session = db_module.get_sessionmaker()
    with Session() as db:
        job = job_service.enqueue(db, job_type="analyze", params={"image_key": "k"})
        claimed = job_service.claim_next(db)
        row = db.get(Job, claimed.id)
        row.heartbeat_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db.commit()
        assert job_service.reap_stalled(db) == 1
        assert db.get(Job, job.id).status == "queued"


# --------------------------------------------------------------------------
# Catalogue, favourites, collections
# --------------------------------------------------------------------------
def test_product_catalogue_metadata(client):
    payload = client.get("/api/products").json()
    assert payload["count"] == 15
    sofa = next(p for p in payload["products"] if p["id"] == "sofa-3")
    for field in ("sku", "seller", "availability", "product_url", "materials", "variants", "placement"):
        assert field in sofa, field
    assert sofa["dimensions"]["height"] > 0
    assert client.get("/api/products?family=decor").json()["count"] == 5
    assert client.get("/api/products?search=tipaza").json()["count"] == 2


def test_favorites_and_collections(client):
    headers = auth(register(client))
    assert client.post("/api/favorites", json={"product_id": "sofa-3"}, headers=headers).status_code == 201
    assert client.post("/api/favorites", json={"product_id": "sofa-3"},
                       headers=headers).json()["already"] is True
    assert client.post("/api/favorites", json={"product_id": "inexistant"},
                       headers=headers).status_code == 404
    assert len(client.get("/api/favorites", headers=headers).json()["favorites"]) == 1
    assert client.delete("/api/favorites/sofa-3", headers=headers).status_code == 200

    col = client.post("/api/collections", json={"name": "Salon client", "note": ""}, headers=headers)
    assert col.status_code == 201
    cid = col.json()["id"]
    added = client.post(f"/api/collections/{cid}/items", json={"product_id": "lamp"}, headers=headers)
    assert [i["product_id"] for i in added.json()["items"]] == ["lamp"]
    assert client.delete(f"/api/collections/{cid}/items/lamp", headers=headers).json()["items"] == []


def test_collections_are_isolated(client):
    alice = auth(register(client, "alice@cigogne.dz"))
    bob = auth(register(client, "bob@cigogne.dz"))
    cid = client.post("/api/collections", json={"name": "A", "note": ""}, headers=alice).json()["id"]
    assert client.post(f"/api/collections/{cid}/items", json={"product_id": "lamp"},
                       headers=bob).status_code == 404
    assert client.get("/api/collections", headers=bob).json()["collections"] == []


# --------------------------------------------------------------------------
# Sharing
# --------------------------------------------------------------------------
def test_share_link_is_read_only_and_public(client):
    headers = auth(register(client))
    pid = client.post("/api/projects", json={"name": "Salon", "state": {"items": []}},
                      headers=headers).json()["id"]
    share = client.post(f"/api/projects/{pid}/shares", json={"permission": "view"}, headers=headers)
    assert share.status_code == 201
    token = share.json()["token"]

    public = client.get(f"/api/shared/{token}")           # no Authorization header
    assert public.status_code == 200
    assert public.json()["project"]["name"] == "Salon"
    assert public.json()["can_comment"] is False
    assert client.post(f"/api/shared/{token}/comments",
                       json={"body": "joli"}).status_code == 403

    assert client.delete(f"/api/shares/{share.json()['id']}", headers=headers).status_code == 200
    assert client.get(f"/api/shared/{token}").status_code == 404


def test_comment_permission_requires_pro_plan(client):
    headers = auth(register(client))
    pid = client.post("/api/projects", json={"name": "S", "state": {}}, headers=headers).json()["id"]
    resp = client.post(f"/api/projects/{pid}/shares", json={"permission": "comment"}, headers=headers)
    assert resp.status_code == 402         # comments are a paid feature


def test_share_comments_when_enabled(client):
    from app.models import User
    session = register(client)
    headers = auth(session)
    with db_module.get_sessionmaker()() as db:
        user = db.query(User).filter(User.email == "amine@cigogne.dz").one()
        user.plan = "pro"
        db.commit()
    pid = client.post("/api/projects", json={"name": "S", "state": {}}, headers=headers).json()["id"]
    token = client.post(f"/api/projects/{pid}/shares", json={"permission": "comment"},
                        headers=headers).json()["token"]
    assert client.post(f"/api/shared/{token}/comments",
                       json={"author_name": "Client", "body": "Le canapé est trop grand."}).status_code == 201
    comments = client.get(f"/api/shared/{token}/comments").json()["comments"]
    assert comments[0]["body"].startswith("Le canapé")


# --------------------------------------------------------------------------
# Analytics, plans, admin
# --------------------------------------------------------------------------
def test_analytics_drops_unknown_events_and_props(client):
    headers = auth(register(client))
    resp = client.post("/api/analytics/events", headers=headers, json={"events": [
        {"name": "furniture_add", "props": {"product_id": "sofa-3", "email": "leak@x.dz"}},
        {"name": "not_a_real_event", "props": {}},
    ]})
    assert resp.json() == {"stored": 1, "received": 2}
    from app.models import AnalyticsEvent
    with db_module.get_sessionmaker()() as db:
        row = db.query(AnalyticsEvent).one()
        assert row.props == {"product_id": "sofa-3"}       # the address was discarded


def test_admin_routes_are_closed_to_normal_users(client):
    headers = auth(register(client))
    assert client.get("/api/admin/stats", headers=headers).status_code == 403


def test_plans_endpoint(client):
    plans = client.get("/api/plans").json()["plans"]
    assert {p["id"] for p in plans} == {"free", "pro", "business"}


def test_health_reports_queue(client):
    payload = client.get("/api/health").json()
    assert payload["ok"] is True
    assert "queued" in payload["queue"]


def test_security_headers_present(client):
    resp = client.get("/api/health")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"


def test_rate_limit_triggers_on_auth(client):
    for _ in range(10):
        client.post("/api/auth/login", json={"email": "nobody@x.dz", "password": "motdepasse42"})
    resp = client.post("/api/auth/login", json={"email": "nobody@x.dz", "password": "motdepasse42"})
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers


# --------------------------------------------------------------------------
# Production configuration guards
# --------------------------------------------------------------------------
def test_production_requires_a_secret_key(monkeypatch):
    monkeypatch.setenv("CIGOGNE_ENV", "production")
    monkeypatch.delenv("CIGOGNE_SECRET_KEY", raising=False)
    config.reset_settings_cache()
    with pytest.raises(RuntimeError, match="CIGOGNE_SECRET_KEY"):
        config.get_settings()
    config.reset_settings_cache()


def test_production_refuses_wildcard_cors(monkeypatch):
    monkeypatch.setenv("CIGOGNE_ENV", "production")
    monkeypatch.setenv("CIGOGNE_SECRET_KEY", "x" * 40)
    monkeypatch.setenv("CIGOGNE_CORS_ORIGINS", "*")
    config.reset_settings_cache()
    with pytest.raises(RuntimeError, match="CORS"):
        config.get_settings()
    config.reset_settings_cache()


# --------------------------------------------------------------------------
# Passerelle compatible : même contrat que le service de modèles
# --------------------------------------------------------------------------
def test_gateway_analyze_returns_the_legacy_shape(client):
    """Le frontend existant doit pouvoir pointer sur /api/ai sans autre changement."""
    import json as _json
    import threading

    from app.services.executors import Executor, JobContext, set_executor
    from app.services.worker import WorkerLoop
    from app.storage import build_key

    class FakeModelService(Executor):
        """Rend exactement ce que renvoie server/main.py, en plus court."""

        def run(self, job_type, params, ctx: JobContext):
            assert job_type == "analyze"
            payload = {
                "version": 3.0, "width": 8, "height": 8,
                "masks": {"floor": "data:image/png;base64,xx"},
                "floor_top_y": 5, "floor_top_profile": [5, 5, 5],
                "floor_source": "segformer",
                "scene": {"furniture_count": 2, "has_floor": True},
            }
            key = build_key(ctx.user_id or "anon", "analysis", "a.json")
            ctx.storage.save(key, _json.dumps(payload).encode(), "application/json")
            return {"analysis_key": key, "width": 8, "height": 8}

    set_executor(FakeModelService())
    loop = WorkerLoop(concurrency=1, poll=0.05)
    loop.start()
    try:
        headers = auth(register(client))
        resp = client.post("/api/ai/analyze", headers=headers,
                           files={"file": ("piece.png", _png(), "image/png")})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["scene"]["furniture_count"] == 2
        assert body["floor_source"] == "segformer"
        assert "floor_top_profile" in body
    finally:
        loop.stop()
        set_executor(None)


def test_gateway_reports_model_failure_instead_of_hanging(client):
    from app.services.executors import Executor, set_executor
    from app.services.worker import WorkerLoop

    class BrokenService(Executor):
        def run(self, job_type, params, ctx):
            raise RuntimeError("CUDA out of memory")

    set_executor(BrokenService())
    loop = WorkerLoop(concurrency=1, poll=0.05)
    loop.start()
    try:
        headers = auth(register(client))
        resp = client.post("/api/ai/analyze", headers=headers,
                           files={"file": ("piece.png", _png(), "image/png")})
        assert resp.status_code == 502
        assert "CUDA" in resp.json()["detail"]
    finally:
        loop.stop()
        set_executor(None)


def test_gateway_works_without_an_account(client):
    """Non-régression : l'application reste utilisable sans compte, et un
    résultat de tâche mal formé produit une erreur propre, pas un plantage."""
    from app.services.executors import EchoExecutor, set_executor
    from app.services.worker import WorkerLoop

    set_executor(EchoExecutor())
    loop = WorkerLoop(concurrency=1, poll=0.05)
    loop.start()
    try:
        resp = client.post("/api/ai/select-mask",
                           files={"file": ("piece.png", _png(), "image/png")},
                           data={"x": "4", "y": "4"})
        # La requête anonyme est acceptée et traitée (pas de 401). L'exécuteur
        # de test ne produit pas de masque, donc la passerelle doit le
        # signaler proprement (502) plutôt que de lever une KeyError.
        assert resp.status_code == 502, resp.text
        assert "mask_key" in resp.json()["detail"]
    finally:
        loop.stop()
        set_executor(None)


def test_gateway_rejects_malformed_result_on_every_route(client):
    """Les trois passerelles (masque, reconstruction, retrait) doivent
    toutes refuser proprement un résultat de tâche incomplet."""
    from app.services.executors import EchoExecutor, set_executor
    from app.services.worker import WorkerLoop

    set_executor(EchoExecutor())
    loop = WorkerLoop(concurrency=2, poll=0.05)
    loop.start()
    try:
        inpaint = client.post("/api/ai/inpaint",
                              files={"file": ("p.png", _png(), "image/png"),
                                     "mask": ("m.png", _png(), "image/png")})
        assert inpaint.status_code == 502
        assert "image_key" in inpaint.json()["detail"]

        remove = client.post("/api/ai/remove",
                             files={"file": ("p.png", _png(), "image/png")},
                             data={"x": "1", "y": "1"})
        assert remove.status_code == 502
        assert "image_key" in remove.json()["detail"]
    finally:
        loop.stop()
        set_executor(None)

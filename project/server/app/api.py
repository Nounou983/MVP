"""API application factory.

`create_app()` builds the standalone service (no torch, no CUDA — it runs on a
small VM). `attach(app)` mounts the same routers onto an existing FastAPI app,
which is how a single-machine development setup gets everything on port 8000
without running two processes.
"""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import get_settings
from .db import init_db
from .routers import ai, auth, catalog, files, projects, shares, system
from .services.worker import start_inline_worker, stop_inline_worker

log = logging.getLogger("cigogne.api")

ROUTERS = (auth.router, projects.router, files.router, catalog.router,
           shares.router, ai.router, system.router)


def include_routers(app: FastAPI) -> None:
    mounted = {getattr(r, "path", None) for r in app.routes}
    for router in ROUTERS:
        if any(route.path in mounted for route in router.routes):
            continue
        app.include_router(router)


def add_security_headers(app: FastAPI) -> None:
    @app.middleware("http")
    async def _headers(request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("X-Response-Time", f"{(time.perf_counter() - started) * 1000:.1f}ms")
        if get_settings().is_production:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response


def add_error_handler(app: FastAPI) -> None:
    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        # Never leak a stack trace to the client; always leave one in the log.
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "Erreur interne. L'incident a été enregistré."},
        )


def attach(app: FastAPI, *, with_worker: bool = True) -> FastAPI:
    settings = get_settings()
    init_db()
    include_routers(app)
    add_security_headers(app)
    add_error_handler(app)
    if with_worker:
        start_inline_worker()
    log.info("API routers attached (env=%s, storage=%s)", settings.env, settings.storage_backend)
    return app


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_inline_worker()
    yield
    stop_inline_worker()


def create_app() -> FastAPI:
    settings = get_settings()
    init_db()          # idempotent; lifespan repeats it for the worker process
    app = FastAPI(
        title="La Cigogne D'Ailleurs — API",
        version="6.0.0",
        description=(
            "Comptes, projets persistants, catalogue produit, favoris, partage, "
            "file de tâches IA et passerelle compatible avec le service de modèles."
        ),
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "If-None-Match"],
        expose_headers=["ETag", "X-Job-Id"],
        max_age=600,
    )
    include_routers(app)
    add_security_headers(app)
    add_error_handler(app)

    @app.get("/", tags=["système"])
    def root():
        return {
            "ok": True,
            "service": "La Cigogne D'Ailleurs API",
            "version": app.version,
            "docs": "/docs",
            "health": "/api/health",
        }

    return app

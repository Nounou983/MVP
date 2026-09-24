"""Product catalogue, favourites and collections.

Commerce data (SKU, seller, availability, URL) is served as metadata. Nothing
here knows how a sofa is drawn — that stays in the frontend renderer.
"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import session_scope
from ..models import Collection, CollectionItem, Favorite, User, new_id, utcnow
from ..schemas import CollectionIn, CollectionItemIn, FavoriteIn
from ..security import current_user, rate_limit
from ..services.entitlements import limit_of

router = APIRouter(prefix="/api", tags=["catalogue"])
read_limit = Depends(rate_limit("read", "rate_limit_read"))
write_limit = Depends(rate_limit("write", "rate_limit_write"))


@lru_cache(maxsize=1)
def _catalog() -> tuple[dict, str]:
    path = Path(get_settings().products_file)
    if not path.is_file():
        return {"schema_version": 0, "families": [], "products": []}, "empty"
    payload = json.loads(path.read_text(encoding="utf-8"))
    etag = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    return payload, etag


def reload_catalog() -> None:
    _catalog.cache_clear()


def product_ids() -> set[str]:
    payload, _ = _catalog()
    return {p["id"] for p in payload.get("products", [])}


@router.get("/products", dependencies=[read_limit])
def list_products(
    request: Request,
    response: Response,
    family: str | None = Query(None),
    search: str | None = Query(None, max_length=80),
):
    payload, etag = _catalog()
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304)
    products = payload.get("products", [])
    if family and family != "all":
        products = [p for p in products if p.get("family") == family]
    if search:
        needle = search.strip().lower()
        products = [
            p for p in products
            if needle in p.get("name", "").lower()
            or needle in p.get("blurb", "").lower()
            or needle in p.get("sku", "").lower()
            or any(needle in tag for tag in p.get("tags", []))
        ]
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "public, max-age=300"
    return {
        "schema_version": payload.get("schema_version"),
        "currency": payload.get("currency", "DZD"),
        "families": payload.get("families", []),
        "seller": payload.get("seller"),
        "products": products,
        "count": len(products),
    }


@router.get("/products/{product_id}", dependencies=[read_limit])
def get_product(product_id: str):
    payload, _ = _catalog()
    for product in payload.get("products", []):
        if product["id"] == product_id:
            return product
    raise HTTPException(status_code=404, detail="Produit introuvable.")


# --------------------------------------------------------------------------
# Favourites
# --------------------------------------------------------------------------
@router.get("/favorites", dependencies=[read_limit])
def list_favorites(user: User = Depends(current_user), db: Session = Depends(session_scope)):
    rows = db.execute(
        select(Favorite).where(Favorite.user_id == user.id).order_by(Favorite.created_at.desc())
    ).scalars().all()
    return {"favorites": [{"product_id": f.product_id, "created_at": f.created_at.isoformat()} for f in rows]}


@router.post("/favorites", status_code=201, dependencies=[write_limit])
def add_favorite(
    payload: FavoriteIn, user: User = Depends(current_user), db: Session = Depends(session_scope)
):
    if payload.product_id not in product_ids():
        raise HTTPException(status_code=404, detail="Produit introuvable.")
    existing = db.execute(
        select(Favorite).where(Favorite.user_id == user.id, Favorite.product_id == payload.product_id)
    ).scalar_one_or_none()
    if existing:
        return {"product_id": existing.product_id, "already": True}
    db.add(Favorite(id=new_id(), user_id=user.id, product_id=payload.product_id))
    db.commit()
    return {"product_id": payload.product_id, "already": False}


@router.delete("/favorites/{product_id}", dependencies=[write_limit])
def remove_favorite(
    product_id: str, user: User = Depends(current_user), db: Session = Depends(session_scope)
):
    row = db.execute(
        select(Favorite).where(Favorite.user_id == user.id, Favorite.product_id == product_id)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Ce produit n'est pas dans vos favoris.")
    db.delete(row)
    db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------
# Collections
# --------------------------------------------------------------------------
def _collection_payload(collection: Collection) -> dict:
    return {
        "id": collection.id,
        "name": collection.name,
        "note": collection.note,
        "items": [
            {"product_id": item.product_id, "position": item.position}
            for item in sorted(collection.items, key=lambda i: i.position)
        ],
        "created_at": collection.created_at.isoformat() if collection.created_at else None,
        "updated_at": collection.updated_at.isoformat() if collection.updated_at else None,
    }


def _owned_collection(db: Session, collection_id: str, user: User) -> Collection:
    collection = db.get(Collection, collection_id)
    if collection is None or collection.user_id != user.id:
        raise HTTPException(status_code=404, detail="Collection introuvable.")
    return collection


@router.get("/collections", dependencies=[read_limit])
def list_collections(user: User = Depends(current_user), db: Session = Depends(session_scope)):
    rows = db.execute(
        select(Collection).where(Collection.user_id == user.id).order_by(Collection.updated_at.desc())
    ).scalars().all()
    return {"collections": [_collection_payload(c) for c in rows]}


@router.post("/collections", status_code=201, dependencies=[write_limit])
def create_collection(
    payload: CollectionIn, user: User = Depends(current_user), db: Session = Depends(session_scope)
):
    limit = limit_of(user, "collections", 2)
    count = len(db.execute(select(Collection).where(Collection.user_id == user.id)).scalars().all())
    if count >= limit:
        raise HTTPException(
            status_code=402, detail=f"Votre formule autorise {int(limit)} collections."
        )
    collection = Collection(
        id=new_id(), user_id=user.id, name=payload.name.strip()[:120] or "Sélection", note=payload.note
    )
    db.add(collection)
    db.commit()
    return _collection_payload(collection)


@router.patch("/collections/{collection_id}", dependencies=[write_limit])
def rename_collection(
    collection_id: str,
    payload: CollectionIn,
    user: User = Depends(current_user),
    db: Session = Depends(session_scope),
):
    collection = _owned_collection(db, collection_id, user)
    collection.name = payload.name.strip()[:120] or collection.name
    collection.note = payload.note
    collection.updated_at = utcnow()
    db.commit()
    return _collection_payload(collection)


@router.post("/collections/{collection_id}/items", status_code=201, dependencies=[write_limit])
def add_to_collection(
    collection_id: str,
    payload: CollectionItemIn,
    user: User = Depends(current_user),
    db: Session = Depends(session_scope),
):
    collection = _owned_collection(db, collection_id, user)
    if payload.product_id not in product_ids():
        raise HTTPException(status_code=404, detail="Produit introuvable.")
    if any(item.product_id == payload.product_id for item in collection.items):
        return _collection_payload(collection)
    db.add(CollectionItem(
        id=new_id(), collection_id=collection.id, product_id=payload.product_id,
        position=len(collection.items),
    ))
    collection.updated_at = utcnow()
    db.commit()
    db.refresh(collection)
    return _collection_payload(collection)


@router.delete("/collections/{collection_id}/items/{product_id}", dependencies=[write_limit])
def remove_from_collection(
    collection_id: str,
    product_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(session_scope),
):
    collection = _owned_collection(db, collection_id, user)
    for item in list(collection.items):
        if item.product_id == product_id:
            db.delete(item)
            collection.updated_at = utcnow()
            db.commit()
            db.refresh(collection)
            return _collection_payload(collection)
    raise HTTPException(status_code=404, detail="Produit absent de cette collection.")


@router.delete("/collections/{collection_id}", dependencies=[write_limit])
def delete_collection(
    collection_id: str, user: User = Depends(current_user), db: Session = Depends(session_scope)
):
    collection = _owned_collection(db, collection_id, user)
    db.delete(collection)
    db.commit()
    return {"ok": True}

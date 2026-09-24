"""GPU work executors.

An executor turns a queued job into a result. The default one forwards to the
existing FastAPI AI service (``server/main.py``) over HTTP, which keeps that
file untouched while letting the API live on a different machine from the GPU.

`RemovalExecutor` adds the Phase 6 quality work on top of the unchanged
endpoint: full-resolution tiled processing, mask refinement, seed retries and
a confidence figure the UI can show.
"""
from __future__ import annotations

import base64
import io
import logging
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

from ..config import get_settings
from ..storage import Storage, build_key, get_storage

log = logging.getLogger("cigogne.executors")

try:  # Pillow lives on the worker image; the pure API container does not need it.
    from PIL import Image, ImageFilter
except ImportError:  # pragma: no cover - depends on deployment target
    Image = None
    ImageFilter = None


@dataclass
class JobContext:
    job_id: str
    user_id: str | None
    storage: Storage
    report: callable          # report(progress: float, message: str) -> bool (False = cancelled)


class Cancelled(RuntimeError):
    pass


class Executor(ABC):
    @abstractmethod
    def run(self, job_type: str, params: dict, ctx: JobContext) -> dict: ...


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _data_url_to_bytes(data_url: str) -> bytes:
    if "," in data_url and data_url.startswith("data:"):
        return base64.b64decode(data_url.split(",", 1)[1])
    return base64.b64decode(data_url)


def _bytes_to_data_url(raw: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")


def _png_bytes(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class HttpAIExecutor(Executor):
    """Forwards to the model service. One instance per worker thread."""

    def __init__(self, base_url: str | None = None, timeout: float | None = None):
        settings = get_settings()
        self.base_url = (base_url or settings.ai_service_url).rstrip("/")
        self.timeout = timeout or settings.job_timeout

    # -- raw calls ---------------------------------------------------------
    def _post(self, path: str, files: dict, data: dict | None = None) -> dict:
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(f"{self.base_url}{path}", files=files, data=data or {})
        if resp.status_code >= 400:
            detail = resp.text[:400]
            try:
                detail = resp.json().get("detail", detail)
            except Exception:
                pass
            raise RuntimeError(f"{path} → HTTP {resp.status_code}: {detail}")
        return resp.json()

    def analyze(self, image: bytes) -> dict:
        return self._post("/analyze", {"file": ("room.png", image, "image/png")})

    def select_mask(self, image: bytes, x: int, y: int) -> dict:
        return self._post(
            "/select-mask",
            {"file": ("room.png", image, "image/png")},
            {"x": str(int(x)), "y": str(int(y))},
        )

    def inpaint(self, image: bytes, mask: bytes, debug: bool = False) -> dict:
        return self._post(
            "/inpaint",
            {"file": ("room.png", image, "image/png"), "mask": ("mask.png", mask, "image/png")},
            {"debug": "true" if debug else "false"},
        )

    def remove_at(self, image: bytes, x: int, y: int, debug: bool = False) -> dict:
        return self._post(
            "/remove",
            {"file": ("room.png", image, "image/png")},
            {"x": str(int(x)), "y": str(int(y)), "debug": "true" if debug else "false"},
        )

    def health(self) -> dict:
        with httpx.Client(timeout=10.0) as client:
            return client.get(f"{self.base_url}/health").json()

    # -- job entry point ---------------------------------------------------
    def run(self, job_type: str, params: dict, ctx: JobContext) -> dict:
        storage = ctx.storage
        if job_type == "analyze":
            image = storage.load(params["image_key"])
            if not ctx.report(0.15, "Analyse de la pièce"):
                raise Cancelled()
            data = self.analyze(image)
            key = build_key(ctx.user_id or "anon", "analysis", "analysis.json")
            storage.save(key, _json_bytes(data), "application/json")
            return {"analysis_key": key, "width": data.get("width"), "height": data.get("height"),
                    "floor_source": data.get("floor_source"),
                    "furniture_count": data.get("scene", {}).get("furniture_count", 0)}

        if job_type == "select-mask":
            image = storage.load(params["image_key"])
            if not ctx.report(0.25, "Détection de l'objet"):
                raise Cancelled()
            data = self.select_mask(image, params["x"], params["y"])
            mask_bytes = _data_url_to_bytes(data["mask"])
            key = build_key(ctx.user_id or "anon", "mask", "mask.png")
            storage.save(key, mask_bytes, "image/png")
            return {
                "mask_key": key, "label": data.get("label"),
                "area_ratio": data.get("area_ratio"), "bbox": data.get("bbox"),
                "method": data.get("method"),
            }

        if job_type in {"remove", "inpaint"}:
            return RemovalExecutor(self).run(job_type, params, ctx)

        raise ValueError(f"Type de tâche non pris en charge: {job_type}")


def _json_bytes(payload: dict) -> bytes:
    import json
    return json.dumps(payload).encode("utf-8")


# --------------------------------------------------------------------------
# Phase 6 — removal quality
# --------------------------------------------------------------------------
class RemovalExecutor(Executor):
    """Quality layer around the unchanged /inpaint endpoint.

    Three things the raw endpoint cannot do on its own:

    1. **Full-resolution work on large photos.** The model runs at a reduced
       short side. On a 4000 px photo that throws away the texture the fill has
       to match. We crop a padded window around the mask, send the window at
       native resolution, then feather it back in.
    2. **Retries that actually differ.** A failed quality gate is retried with a
       wider mask dilation, which is what usually rescues a thin-leg chair or a
       shadow the first mask clipped.
    3. **A confidence figure.** The endpoint already returns `seam` and
       `furniture_penalty`; we turn those into one number and surface it, so the
       UI can say "vérifiez le résultat" instead of silently shipping a smear.
    """

    #: A window larger than this is sent whole — cropping would not help.
    MIN_TILE_GAIN = 1.35
    MAX_TILE_SIDE = 2048
    PAD_RATIO = 0.55

    def __init__(self, http: HttpAIExecutor | None = None):
        self.http = http or HttpAIExecutor()

    def run(self, job_type: str, params: dict, ctx: JobContext) -> dict:
        storage = ctx.storage
        image_bytes = storage.load(params["image_key"])

        if job_type == "remove" and "mask_key" not in params:
            # point-driven one-shot: let the service select, then reuse our pipeline
            if not ctx.report(0.2, "Sélection de l'objet"):
                raise Cancelled()
            selection = self.http.select_mask(image_bytes, params["x"], params["y"])
            mask_bytes = _data_url_to_bytes(selection["mask"])
            label = selection.get("label", "meuble")
        else:
            mask_bytes = storage.load(params["mask_key"])
            label = params.get("label", "meuble")

        if not ctx.report(0.35, "Reconstruction de la zone"):
            raise Cancelled()

        attempts: list[dict] = []
        best: dict | None = None
        dilations = [0, 6, 14]

        for index, dilation in enumerate(dilations):
            if not ctx.report(0.35 + 0.2 * index, f"Reconstruction ({index + 1}/{len(dilations)})"):
                raise Cancelled()
            try:
                out = self._one_pass(image_bytes, mask_bytes, dilation)
            except Exception as exc:                      # network / model failure
                attempts.append({"dilation": dilation, "error": str(exc)})
                log.warning("removal pass failed (dilation=%s): %s", dilation, exc)
                continue

            confidence = self._confidence(out["meta"])
            out["confidence"] = confidence
            attempts.append({
                "dilation": dilation,
                "seam": out["meta"].get("seam"),
                "furniture_penalty": out["meta"].get("furniture_penalty"),
                "quality_gate_passed": out["meta"].get("quality_gate_passed"),
                "confidence": confidence,
                "tiled": out["tiled"],
            })
            if best is None or confidence > best["confidence"]:
                best = out
            if out["meta"].get("quality_gate_passed") and confidence >= 0.72:
                break

        if best is None:
            raise RuntimeError(
                "La reconstruction a échoué sur toutes les tentatives. "
                "La photo est conservée telle quelle."
            )

        key = build_key(ctx.user_id or "anon", "render", "removed.png")
        ctx.storage.save(key, best["image"], "image/png")
        ctx.report(0.95, "Finalisation")

        return {
            "image_key": key,
            "label": label,
            "width": best["width"],
            "height": best["height"],
            "confidence": round(best["confidence"], 3),
            "quality_gate_passed": bool(best["meta"].get("quality_gate_passed")),
            "backend": best["meta"].get("backend"),
            "seam": best["meta"].get("seam"),
            "furniture_penalty": best["meta"].get("furniture_penalty"),
            "tiled": best["tiled"],
            "attempts": attempts,
            "needs_review": best["confidence"] < 0.55,
        }

    # ---------------------------------------------------------------- passes
    def _one_pass(self, image_bytes: bytes, mask_bytes: bytes, dilation: int) -> dict:
        if Image is None:
            data = self.http.inpaint(image_bytes, mask_bytes)
            raw = _data_url_to_bytes(data["image"])
            return {"image": raw, "meta": data, "tiled": False,
                    "width": data.get("width"), "height": data.get("height")}

        source = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        mask = Image.open(io.BytesIO(mask_bytes)).convert("L").resize(source.size, Image.NEAREST)
        if dilation:
            mask = mask.filter(ImageFilter.MaxFilter(self._odd(dilation)))

        window = self._mask_window(mask, source.size)
        if window is None:
            raise RuntimeError("Masque vide — rien à retirer.")

        use_tile = self._should_tile(window, source.size)
        if use_tile:
            crop_box = window
            crop_img = source.crop(crop_box)
            crop_mask = mask.crop(crop_box)
            data = self.http.inpaint(_png_bytes(crop_img), _png_bytes(crop_mask))
            patch = Image.open(io.BytesIO(_data_url_to_bytes(data["image"]))).convert("RGB")
            if patch.size != crop_img.size:
                patch = patch.resize(crop_img.size, Image.LANCZOS)
            merged = source.copy()
            merged.paste(patch, crop_box, self._feather(crop_mask))
            out = merged
        else:
            data = self.http.inpaint(image_bytes, _png_bytes(mask))
            out = Image.open(io.BytesIO(_data_url_to_bytes(data["image"]))).convert("RGB")
            if out.size != source.size:
                out = out.resize(source.size, Image.LANCZOS)

        return {
            "image": _png_bytes(out),
            "meta": data,
            "tiled": bool(use_tile),
            "width": out.width,
            "height": out.height,
        }

    # ---------------------------------------------------------------- geometry
    @staticmethod
    def _odd(value: int) -> int:
        value = max(3, int(value))
        return value if value % 2 else value + 1

    @staticmethod
    def _mask_window(mask, size) -> tuple[int, int, int, int] | None:
        bbox = mask.point(lambda v: 255 if v > 127 else 0).getbbox()
        if bbox is None:
            return None
        x0, y0, x1, y1 = bbox
        w, h = x1 - x0, y1 - y0
        pad_x = int(w * RemovalExecutor.PAD_RATIO) + 24
        pad_y = int(h * RemovalExecutor.PAD_RATIO) + 24
        W, H = size
        return (
            max(0, x0 - pad_x), max(0, y0 - pad_y),
            min(W, x1 + pad_x), min(H, y1 + pad_y),
        )

    @classmethod
    def _should_tile(cls, window, size) -> bool:
        W, H = size
        win_w = window[2] - window[0]
        win_h = window[3] - window[1]
        if win_w <= 0 or win_h <= 0:
            return False
        if max(W, H) <= 1400:
            return False                       # already small; whole image is fine
        if max(win_w, win_h) > cls.MAX_TILE_SIDE:
            return False                       # window nearly the whole photo
        gain = max(W, H) / max(win_w, win_h)
        return gain >= cls.MIN_TILE_GAIN

    @staticmethod
    def _feather(mask, radius: int = 6):
        """Soft alpha so the patched window does not show a rectangle edge."""
        grown = mask.filter(ImageFilter.MaxFilter(RemovalExecutor._odd(radius)))
        return grown.filter(ImageFilter.GaussianBlur(radius * 0.8))

    # ---------------------------------------------------------------- scoring
    @staticmethod
    def _confidence(meta: dict) -> float:
        """Map the service's own numbers onto 0..1.

        `seam` is a pixel-difference figure at the mask boundary (lower is
        better; the service's own gate sits at 45). `furniture_penalty` is how
        much furniture the segmenter still sees inside the hole (lower is
        better). Neither is calibrated against human judgement — this is a
        monotone mapping of the two, not a probability.
        """
        seam = float(meta.get("seam") or 0.0)
        penalty = float(meta.get("furniture_penalty") or 0.0)
        gate = bool(meta.get("quality_gate_passed"))
        seam_score = 1.0 / (1.0 + math.exp((seam - 32.0) / 8.0))
        penalty_score = max(0.0, 1.0 - penalty / 0.45)
        score = 0.55 * seam_score + 0.45 * penalty_score
        if not gate:
            score *= 0.6
        return max(0.0, min(1.0, score))


class EchoExecutor(Executor):
    """Used by the test suite and by `CIGOGNE_FAKE_GPU=1` local runs."""

    def run(self, job_type: str, params: dict, ctx: JobContext) -> dict:
        if not ctx.report(0.5, "Simulation"):
            raise Cancelled()
        return {"echo": True, "type": job_type, "params": {k: v for k, v in params.items() if k != "image_key"}}


_executor: Executor | None = None


def get_executor() -> Executor:
    global _executor
    if _executor is None:
        import os
        _executor = EchoExecutor() if os.environ.get("CIGOGNE_FAKE_GPU") == "1" else HttpAIExecutor()
    return _executor


def set_executor(executor: Executor | None) -> None:
    global _executor
    _executor = executor

"""Tests for the Phase 6 removal-quality layer.

The model service is replaced by a stub that returns a flat grey fill, so what
is measured here is our own work: does a big photo get processed at full
resolution through a crop, does the composite land in the right place, does a
failed quality gate trigger a wider retry, and does confidence move in the
right direction.
"""
from __future__ import annotations

import io
import os
import sys
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_DIR))
os.environ.setdefault("CIGOGNE_SECRET_KEY", "test-secret-key-not-for-production")

PIL = pytest.importorskip("PIL", reason="Pillow absent de cet environnement")
from PIL import Image  # noqa: E402

from app.services.executors import JobContext, RemovalExecutor, _png_bytes  # noqa: E402


class StubHttp:
    """Stands in for the model service."""

    def __init__(self, seam=20.0, penalty=0.05, gate=True, fill=(180, 180, 180)):
        self.calls = []
        self.seam = seam
        self.penalty = penalty
        self.gate = gate
        self.fill = fill

    def _reply(self, image_bytes):
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        out = Image.new("RGB", img.size, self.fill)
        import base64
        payload = base64.b64encode(_png_bytes(out)).decode()
        return {
            "image": f"data:image/png;base64,{payload}",
            "width": out.width, "height": out.height,
            "seam": self.seam, "furniture_penalty": self.penalty,
            "quality_gate_passed": self.gate, "backend": "stub",
        }

    def inpaint(self, image, mask, debug=False):
        img = Image.open(io.BytesIO(image)).convert("RGB")
        self.calls.append(img.size)
        return self._reply(image)

    def select_mask(self, image, x, y):
        raise AssertionError("not used in these tests")


class MemoryStorage:
    def __init__(self):
        self.data = {}

    def load(self, key):
        return self.data[key]

    def save(self, key, data, content_type):
        self.data[key] = data
        return key


def make_context(storage):
    reports = []

    def report(progress, message):
        reports.append((progress, message))
        return True

    ctx = JobContext(job_id="j1", user_id="u1", storage=storage, report=report)
    return ctx, reports


def scene(width, height, box):
    """A photo with a dark rectangle standing in for the furniture."""
    img = Image.new("RGB", (width, height), (240, 235, 225))
    mask = Image.new("L", (width, height), 0)
    for target, colour in ((img, (40, 40, 45)), (mask, 255)):
        for x in range(box[0], box[2]):
            for y in range(box[1], box[3]):
                target.putpixel((x, y), colour)
    return _png_bytes(img), _png_bytes(mask)


def test_large_photo_is_processed_through_a_crop():
    """A small object in a big photo must not force the whole image through."""
    image, mask = scene(3000, 2000, (1400, 900, 1700, 1200))
    storage = MemoryStorage()
    storage.data["img"] = image
    storage.data["mask"] = mask
    stub = StubHttp()
    ctx, _ = make_context(storage)

    result = RemovalExecutor(stub).run("inpaint", {"image_key": "img", "mask_key": "mask"}, ctx)

    assert result["tiled"] is True
    sent_w, sent_h = stub.calls[0]
    assert sent_w < 3000 and sent_h < 2000          # a window, not the photo
    assert sent_w > 300                              # with context around the object
    out = Image.open(io.BytesIO(storage.data[result["image_key"]]))
    assert out.size == (3000, 2000)                 # returned at native resolution


def test_pixels_outside_the_window_are_untouched():
    image, mask = scene(3000, 2000, (1400, 900, 1700, 1200))
    storage = MemoryStorage()
    storage.data["img"] = image
    storage.data["mask"] = mask
    ctx, _ = make_context(storage)

    result = RemovalExecutor(StubHttp()).run("inpaint", {"image_key": "img", "mask_key": "mask"}, ctx)
    out = Image.open(io.BytesIO(storage.data[result["image_key"]])).convert("RGB")

    assert out.getpixel((20, 20)) == (240, 235, 225)          # far corner untouched
    centre = out.getpixel((1550, 1050))
    assert abs(centre[0] - 180) < 12                          # hole actually filled


def test_small_photo_is_sent_whole():
    image, mask = scene(900, 600, (400, 300, 500, 420))
    storage = MemoryStorage()
    storage.data["img"] = image
    storage.data["mask"] = mask
    ctx, _ = make_context(storage)

    result = RemovalExecutor(StubHttp()).run("inpaint", {"image_key": "img", "mask_key": "mask"}, ctx)
    assert result["tiled"] is False


def test_failed_gate_retries_with_a_wider_mask():
    image, mask = scene(1200, 900, (500, 400, 640, 560))
    storage = MemoryStorage()
    storage.data["img"] = image
    storage.data["mask"] = mask
    stub = StubHttp(seam=80.0, penalty=0.4, gate=False)
    ctx, _ = make_context(storage)

    result = RemovalExecutor(stub).run("inpaint", {"image_key": "img", "mask_key": "mask"}, ctx)

    assert len(stub.calls) == 3                     # all three dilations attempted
    assert len(result["attempts"]) == 3
    assert result["needs_review"] is True           # and it says so instead of pretending
    assert result["confidence"] < 0.5


def test_good_result_stops_after_one_pass():
    image, mask = scene(1200, 900, (500, 400, 640, 560))
    storage = MemoryStorage()
    storage.data["img"] = image
    storage.data["mask"] = mask
    stub = StubHttp(seam=8.0, penalty=0.01, gate=True)
    ctx, _ = make_context(storage)

    result = RemovalExecutor(stub).run("inpaint", {"image_key": "img", "mask_key": "mask"}, ctx)
    assert len(stub.calls) == 1
    assert result["confidence"] > 0.85
    assert result["needs_review"] is False


def test_confidence_is_monotone():
    good = RemovalExecutor._confidence({"seam": 5, "furniture_penalty": 0.01, "quality_gate_passed": True})
    middling = RemovalExecutor._confidence({"seam": 35, "furniture_penalty": 0.2, "quality_gate_passed": True})
    bad = RemovalExecutor._confidence({"seam": 90, "furniture_penalty": 0.5, "quality_gate_passed": False})
    assert good > middling > bad
    assert 0.0 <= bad and good <= 1.0


def test_total_failure_raises_instead_of_returning_a_smear():
    class Broken(StubHttp):
        def inpaint(self, image, mask, debug=False):
            raise RuntimeError("GPU indisponible")

    image, mask = scene(800, 600, (300, 200, 400, 320))
    storage = MemoryStorage()
    storage.data["img"] = image
    storage.data["mask"] = mask
    ctx, _ = make_context(storage)

    with pytest.raises(RuntimeError, match="échoué"):
        RemovalExecutor(Broken()).run("inpaint", {"image_key": "img", "mask_key": "mask"}, ctx)


def test_empty_mask_is_reported():
    img = _png_bytes(Image.new("RGB", (400, 300), (200, 200, 200)))
    empty = _png_bytes(Image.new("L", (400, 300), 0))
    storage = MemoryStorage()
    storage.data["img"] = img
    storage.data["mask"] = empty
    ctx, _ = make_context(storage)
    with pytest.raises(RuntimeError):
        RemovalExecutor(StubHttp()).run("inpaint", {"image_key": "img", "mask_key": "mask"}, ctx)

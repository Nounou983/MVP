"""Deterministic unit tests for Phase 9 surface-aware reconstruction helpers."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
import types

# Keep helper tests CPU-only and independent of optional model packages.
if "transformers" not in sys.modules:
    _t = types.ModuleType("transformers")
    _t.pipeline = lambda *a, **k: None
    sys.modules["transformers"] = _t

SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_DIR))
os.environ.setdefault("CIGOGNE_SECRET_KEY", "test-secret-key-not-for-production")

from main import _nearest_surface_map, _add_contact_shadow_mask, _texture_residual_transfer  # noqa: E402


def test_surface_map_keeps_lower_hole_on_rug():
    H, W = 120, 180
    hole = np.zeros((H, W), bool)
    hole[55:105, 45:135] = True
    floor = np.zeros_like(hole); floor[55:, :] = True
    wall = np.zeros_like(hole); wall[:55, :] = True
    rug = np.zeros_like(hole); rug[70:115, 20:160] = True
    labels = _nearest_surface_map(hole, {"floor": floor, "wall": wall, "rug": rug})
    assert np.mean(labels[72:100, 55:125] == 3) > 0.80
    assert np.mean(labels[0:20, 0:W] == 0) == 1.0


def test_contact_shadow_addition_is_narrow():
    H, W = 100, 140
    img = np.full((H, W, 3), 210, np.uint8)
    # Dark horizontal band immediately below the object footprint.
    img[68:73, 35:105] = 150
    image = Image.fromarray(img, "RGB")
    mask = np.zeros((H, W), bool); mask[30:70, 35:105] = True
    surfaces = np.ones_like(mask, np.uint8)
    expanded = _add_contact_shadow_mask(image, mask, surfaces)
    extra = expanded & ~mask
    assert extra.sum() > 0
    assert extra.sum() < mask.sum() * 0.12 + 80


def test_texture_transfer_preserves_dimensions():
    H, W = 80, 120
    yy, xx = np.indices((H, W))
    arr = np.stack([(120 + xx % 30), (100 + yy % 25), (90 + (xx + yy) % 20)], axis=-1).astype(np.uint8)
    src = Image.fromarray(arr, "RGB")
    target = np.zeros((H, W), bool); target[30:60, 40:80] = True
    surface = np.ones((H, W), np.uint8)
    out = _texture_residual_transfer(src, src, target, surface)
    assert out.size == src.size
    assert np.asarray(out).dtype == np.uint8

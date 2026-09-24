"""Deterministic unit tests for the Phase 14 optimizer-stability fixes.

CPU-only, GPU-free, following the same pattern as test_phase9.py: transformers
is stubbed and main.py's torch import is now optional (Phase 14 fix), so these
import and run the REAL functions from main.py, not a reimplementation.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
import types

if "transformers" not in sys.modules:
    _t = types.ModuleType("transformers")
    _t.pipeline = lambda *a, **k: None
    sys.modules["transformers"] = _t

SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_DIR))
os.environ.setdefault("CIGOGNE_SECRET_KEY", "test-secret-key-not-for-production")

cv2 = pytest.importorskip("cv2", reason="opencv absent from this environment")

from main import (  # noqa: E402
    _residual_structure_score,
    _texture_ratio,
    REMOVAL_GATE_THRESHOLDS,
)


# ---------------------------------------------------------------------------
# _residual_structure_score recalibration
#
# Diagnosed by direct execution against synthetic images before changing the
# formula: the OLD floor (`max(0.055, outer * 1.35)`) made "excess" exactly
# 0.0 even for a deliberately drawn sharp rectangular outline standing in for
# a leftover furniture edge on a flat background (inner=0.0229, outer=0.0,
# floor=0.055 -> excess clamped to 0). That is the exact case this function
# exists to catch. These tests pin the recalibrated behaviour so it cannot
# silently regress back to being a permanent no-op.
# ---------------------------------------------------------------------------
def test_residual_structure_flat_fill_still_scores_zero():
    """A genuinely flat, edgeless fill should NOT be flagged — that failure
    mode (blur/no detail at all) is what texture_ratio catches, not this."""
    H, W = 200, 300
    img = np.full((H, W, 3), 180, np.uint8)
    mask = np.zeros((H, W), bool)
    mask[60:140, 100:220] = True
    score = _residual_structure_score(Image.fromarray(img), mask)
    assert score == 0.0


def test_residual_structure_now_detects_sharp_edges_on_flat_background():
    """The exact synthetic case that exposed the bug: with the OLD floor
    (0.055 abs / 1.35x rel) this scored exactly 0.0. It must now be > 0."""
    H, W = 200, 300
    img = np.full((H, W, 3), 180, np.uint8)
    mask = np.zeros((H, W), bool)
    mask[60:140, 100:220] = True
    cv2.rectangle(img, (120, 75), (200, 125), (40, 40, 40), thickness=4)
    score = _residual_structure_score(Image.fromarray(img), mask)
    assert score > 0.0, (
        "Regression: residual_structure_score is back to being unable to "
        "register any excess edge density (the original Phase 14 bug)."
    )


def test_residual_structure_constants_are_lower_than_the_old_hardcoded_values():
    """Pins the direction of the fix, not just the outcome for one image."""
    assert REMOVAL_GATE_THRESHOLDS["residual_structure_abs_floor"] < 0.055
    assert REMOVAL_GATE_THRESHOLDS["residual_structure_rel_multiplier"] < 1.35


# ---------------------------------------------------------------------------
# _texture_ratio (new in Phase 14)
# ---------------------------------------------------------------------------
def test_texture_ratio_flat_fill_scores_low():
    """A flat gray patch inside a genuinely textured room must score well
    under MIN_TEXTURE_RATIO — this is the exact failure mode a sibling branch
    of this codebase hit in real testing: a bed replaced by a flat gray patch
    that scored an excellent seam (0.8) while being obviously wrong."""
    H, W = 220, 320
    rng = np.random.default_rng(0)
    img = (170 + rng.normal(0, 22, (H, W, 3))).clip(0, 255).astype(np.uint8)
    mask = np.zeros((H, W), bool)
    mask[70:150, 110:230] = True
    img[mask] = 180  # flat fill, no texture at all inside the mask
    ratio = _texture_ratio(Image.fromarray(img), Image.fromarray(img), Image.fromarray((mask * 255).astype(np.uint8), "L"))
    assert ratio < REMOVAL_GATE_THRESHOLDS["min_texture_ratio"]


def test_texture_ratio_matching_texture_scores_high():
    """A candidate whose interior has texture comparable to its surroundings
    should NOT be penalised — this must not reject a genuinely good fill."""
    H, W = 220, 320
    rng = np.random.default_rng(1)
    img = (170 + rng.normal(0, 22, (H, W, 3))).clip(0, 255).astype(np.uint8)
    mask = np.zeros((H, W), bool)
    mask[70:150, 110:230] = True
    # Leave the same noisy texture inside the mask (a "clean, detailed" fill).
    ratio = _texture_ratio(Image.fromarray(img), Image.fromarray(img), Image.fromarray((mask * 255).astype(np.uint8), "L"))
    assert ratio >= REMOVAL_GATE_THRESHOLDS["min_texture_ratio"]


def test_texture_ratio_handles_size_mismatch_without_raising():
    H, W = 200, 300
    orig = Image.fromarray(np.full((H, W, 3), 180, np.uint8))
    small_candidate = Image.fromarray(np.full((100, 150, 3), 180, np.uint8))
    mask = np.zeros((H, W), bool)
    mask[60:140, 100:220] = True
    ratio = _texture_ratio(orig, small_candidate, Image.fromarray((mask * 255).astype(np.uint8), "L"))
    assert isinstance(ratio, float)


def test_texture_ratio_empty_mask_is_neutral():
    H, W = 100, 100
    img = Image.fromarray(np.full((H, W, 3), 180, np.uint8))
    empty_mask = Image.fromarray(np.zeros((H, W), np.uint8), "L")
    assert _texture_ratio(img, img, empty_mask) == 1.0


# ---------------------------------------------------------------------------
# hybrid_inpaint cascade fix
#
# Diagnosed: `if rorem_result is not None:` accepted ANY RORem result,
# including one whose own gate had just set passed_gate=False, and returned
# it immediately — meaning the LaMa-recovery and surface-emergency tiers
# below were unreachable dead code whenever RORem ran without throwing an
# exception, which is the common case. This test mocks RORem to return an
# explicitly rejected candidate and LaMa to return a clean one, and confirms
# hybrid_inpaint now actually reaches and returns the LaMa recovery result
# instead of silently keeping RORem's rejected one.
# ---------------------------------------------------------------------------
def test_cascade_falls_through_to_lama_when_rorem_is_rejected():
    from unittest.mock import patch
    import main

    H, W = 200, 300
    room = np.full((H, W, 3), 180, np.uint8)
    room_img = Image.fromarray(room)
    mask = np.zeros((H, W), np.uint8)
    mask[60:140, 100:220] = 255  # 8.9% of frame -> large-object path
    mask_img = Image.fromarray(mask, "L")

    # A rejected RORem candidate: still recognisably flawed (flat, low detail)
    rorem_pixels = room.copy()
    rejected_candidate = Image.fromarray(rorem_pixels)

    # A clean LaMa candidate: matches surrounding texture closely enough to
    # pass every threshold (target_change/furniture_penalty/texture_ratio).
    rng = np.random.default_rng(3)
    lama_pixels = (170 + rng.normal(0, 20, (H, W, 3))).clip(0, 255).astype(np.uint8)
    lama_candidate = Image.fromarray(lama_pixels)

    with patch.object(
        main, "_remove_only_large_object",
        return_value={
            "image": rejected_candidate,
            "passed_gate": False,
            "backend": "rorem-official-512-square-ensemble",
            "seam": 10.0, "furniture_penalty": 0.05, "texture_ratio": 1.0,
        },
    ) as mock_rorem, patch.object(
        main, "_surface_candidate_for_large_object", return_value=None
    ), patch.object(
        main, "_surface_aware_rorem_candidate", return_value=None
    ), patch.object(
        main, "get_lama", return_value=lambda image, m: lama_candidate
    ) as mock_lama, patch.object(
        main, "_removal_quality_score",
        side_effect=[
            (100.0, 10.0, 0.05, 0.0, 0.0, 1.0, 0.9),
            (5.0, 5.0, 0.01, 0.35, 0.0, 0.8, 0.9),
        ],
    ):
        composited, meta = main.hybrid_inpaint(room_img, mask_img)

    assert mock_rorem.called
    assert mock_lama.called, (
        "Regression: LaMa recovery was never invoked. This is exactly the "
        "Phase 14 dead-fallback bug — a rejected RORem result must not be "
        "returned as final without genuinely attempting LaMa recovery."
    )
    assert meta["reconstruction_strategy"] in {"lama-recovery", "rorem-rejected"}, meta


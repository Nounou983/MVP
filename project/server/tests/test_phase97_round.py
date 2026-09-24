"""Phase 9.7 targeted regression tests for the controlled mask/removal round."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import types
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image

if "transformers" not in sys.modules:
    _t = types.ModuleType("transformers")
    _t.pipeline = lambda *a, **k: None
    sys.modules["transformers"] = _t

SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_DIR))
os.environ.setdefault("CIGOGNE_SECRET_KEY", "test-secret-key-not-for-production")

import main  # noqa: E402


def _synthetic_room():
    h, w = 180, 240
    yy, xx = np.indices((h, w))
    base = (170 + 15 * np.sin(xx / 20) + 8 * np.sin(yy / 14)).clip(0, 255).astype(np.uint8)
    original = np.stack([base, np.clip(base - 8, 0, 255), np.clip(base - 15, 0, 255)], axis=-1)
    mask = np.zeros((h, w), dtype=bool)
    mask[55:125, 80:160] = True
    return Image.fromarray(original, "RGB"), mask


def test_composite_preserves_every_pixel_outside_confirmed_binary_mask():
    original = np.full((120, 180, 3), 91, np.uint8)
    generated = np.full((120, 180, 3), 233, np.uint8)
    mask = np.zeros((120, 180), np.uint8)
    mask[40:80, 65:115] = 255

    out = np.asarray(main._composite_inside_mask(
        Image.fromarray(original), Image.fromarray(generated), Image.fromarray(mask, "L")
    ))
    outside = mask == 0
    inside = mask > 0
    assert int(np.abs(out.astype(int) - original.astype(int))[outside].max()) == 0
    # The confirmed mask is not an alpha/transparency map: inside it the
    # generated candidate must be copied at full opacity, not blended with
    # the removed furniture.
    assert np.array_equal(out[inside], generated[inside])


def test_surface_consistency_rejects_dark_glossy_mass_but_accepts_close_surface():
    original, mask = _synthetic_room()
    orig = np.asarray(original).copy()

    bad = orig.copy()
    bad[mask] = (35, 38, 45)
    bad[60:65, 90:150] = (80, 80, 85)

    close = orig.copy()
    close[mask] = np.clip(orig[mask].astype(int) + 3, 0, 255).astype(np.uint8)

    bad_score = main._surface_consistency_score(original, Image.fromarray(bad), mask)
    close_score = main._surface_consistency_score(original, Image.fromarray(close), mask)

    assert bad_score < main.REMOVAL_GATE_THRESHOLDS["min_surface_consistency"]
    assert close_score >= main.REMOVAL_GATE_THRESHOLDS["min_surface_consistency"]


def test_gate_no_longer_uses_target_change_as_a_blanket_minimum():
    # Replays the report's low-change synthetic case with a detailed/consistent
    # reconstruction. 0.163 is intentionally below the former 0.34 floor.
    metrics = (5.24, 5.24, 0.0, 0.163, 0.0, 0.80, 0.95)
    assert main._candidate_passes_removal_gate(metrics) is True


def test_old_glossy_blob_metrics_are_rejected_by_new_surface_guard():
    # These are the exact aggregate values recorded by the Phase 9.7 report
    # before the new surface-consistency guard: the old gate returned TRUE.
    original, mask = _synthetic_room()
    orig = np.asarray(original).copy()
    bad = orig.copy()
    bad[mask] = (35, 38, 45)
    bad[60:65, 90:150] = (80, 80, 85)
    surface = main._surface_consistency_score(original, Image.fromarray(bad), mask)

    old_metrics = (5.45, 0.351, 0.0, 0.377, 0.027, 2.084, surface)
    assert main._candidate_passes_removal_gate(old_metrics) is False
    assert surface < main.REMOVAL_GATE_THRESHOLDS["min_surface_consistency"]




def test_real_case_furniture_penalty_04660_is_rejected_by_authoritative_gate():
    # Case B RTX runtime: seam/texture are excellent enough to expose that the
    # direct RORem path is controlled by a conjunctive gate, not OR compensation.
    # The old 0.60 ceiling incorrectly accepted fp=0.4660; the tightened 0.45
    # ceiling must reject this real observed value.
    metrics = (81.95, 4.94, 0.4660, 0.266, 0.172, 1.59, 0.885)
    assert main.REMOVAL_GATE_THRESHOLDS["max_furniture_penalty"] == 0.45
    assert main._candidate_passes_removal_gate(metrics) is False


def test_direct_gate_is_conjunctive_no_or_compensation():
    # Each individual threshold is necessary. Excellent seam/texture/surface
    # scores cannot compensate for an over-limit furniture penalty.
    t = main.REMOVAL_GATE_THRESHOLDS
    metrics = (0.0, t["max_seam"] - 1.0, t["max_furniture_penalty"] + 0.001,
               t["noop_target_change"] + 0.1, 0.0,
               t["min_texture_ratio"] + 1.0, t["min_surface_consistency"] + 0.2)
    assert main._candidate_passes_removal_gate(metrics) is False


def test_real_case_furniture_penalty_06774_is_rejected_by_authoritative_gate():
    # Replays the exact Phase 9.7 RTX runtime value that incorrectly passed
    # when the authoritative ceiling was 0.70. The tightened 0.60 ceiling
    # must reject it in normal gate evaluation.
    metrics = (81.03, 16.68, 0.6774, 0.212, 0.0, 0.58, 0.933)
    assert main.REMOVAL_GATE_THRESHOLDS["max_furniture_penalty"] == 0.45
    assert main._candidate_passes_removal_gate(metrics) is False

def test_no_passing_candidate_fails_loudly_in_normal_mode_and_is_explicit_in_debug():
    h, w = 200, 300
    room = Image.new("RGB", (w, h), (180, 180, 180))
    mask = np.zeros((h, w), np.uint8)
    mask[50:140, 90:210] = 255  # large-object path
    mask_img = Image.fromarray(mask, "L")

    with patch.object(main, "_remove_only_large_object", return_value={
        "image": room, "passed_gate": False, "backend": "stub-rorem"
    }), patch.object(main, "_surface_candidate_for_large_object", return_value=None), patch.object(
        main, "_surface_aware_rorem_candidate", return_value=None
    ), patch.object(main, "_lama_remove_candidate", return_value=(room, (0, 0))):
        with pytest.raises(main.HTTPException) as exc:
            main.hybrid_inpaint(room, mask_img, allow_below_gate=False)
        assert exc.value.status_code == 422

        result, meta = main.hybrid_inpaint(room, mask_img, allow_below_gate=True)
        assert isinstance(result, Image.Image)
        assert meta["passed_gate"] is False
        assert meta["unconfirmed"] is True
        assert meta["quality_warning"] is True




def test_real_case_furniture_penalty_debug_mode_is_explicitly_unconfirmed():
    h, w = 200, 300
    room = Image.new("RGB", (w, h), (180, 180, 180))
    mask = np.zeros((h, w), np.uint8)
    mask[50:140, 90:210] = 255
    mask_img = Image.fromarray(mask, "L")
    candidate = Image.new("RGB", (w, h), (150, 150, 150))
    metrics = (81.03, 16.68, 0.6774, 0.212, 0.0, 0.58, 0.933)

    with patch.object(main, "_remove_only_large_object", return_value={
        "image": candidate, "passed_gate": False, "backend": "stub-rorem"
    }), patch.object(main, "_surface_candidate_for_large_object", return_value=None), patch.object(
        main, "_surface_aware_rorem_candidate", return_value=None
    ), patch.object(main, "_lama_remove_candidate", return_value=(candidate, (0, 0))), patch.object(
        main, "_removal_quality_score", return_value=metrics
    ):
        with pytest.raises(main.HTTPException) as exc:
            main.hybrid_inpaint(room, mask_img, allow_below_gate=False)
        assert exc.value.status_code == 422

        result, meta = main.hybrid_inpaint(room, mask_img, allow_below_gate=True)
        assert isinstance(result, Image.Image)
        assert meta["passed_gate"] is False
        assert meta["unconfirmed"] is True
        assert meta["quality_warning"] is True

def test_small_object_uses_same_gate_semantics():
    h, w = 200, 300
    room = Image.new("RGB", (w, h), (180, 180, 180))
    mask = np.zeros((h, w), np.uint8)
    mask[20:35, 20:35] = 255  # 0.375% -> small-object path
    mask_img = Image.fromarray(mask, "L")

    with patch.object(main, "get_lama", return_value=lambda image, m: image):
        with pytest.raises(main.HTTPException) as exc:
            main.hybrid_inpaint(room, mask_img, allow_below_gate=False)
        assert exc.value.status_code == 422

        result, meta = main.hybrid_inpaint(room, mask_img, allow_below_gate=True)
        assert isinstance(result, Image.Image)
        assert meta["passed_gate"] is False
        assert meta["unconfirmed"] is True


def test_inpaint_status_exposes_authoritative_thresholds_and_version():
    response = asyncio.run(main.inpaint_status())
    payload = json.loads(response.body)
    assert payload["version"] == main.APP_VERSION == "9.7.0"
    assert payload["phase"] == "9.7"
    assert payload["quality_gate"] == main.REMOVAL_GATE_THRESHOLDS
    assert payload["failure_policy"]["normal"] == "fail_if_no_candidate_passes"
    assert payload["failure_policy"]["debug"] == "return_best_unconfirmed_candidate"

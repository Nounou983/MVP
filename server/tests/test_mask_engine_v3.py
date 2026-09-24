"""Deterministic CPU-only tests for Mask Engine V3 geometry and scoring."""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

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

cv2 = pytest.importorskip("cv2", reason="opencv absent")

from main import (  # noqa: E402
    _adaptive_prompt_boxes,
    _candidate_mask_score,
    _mask_confidence,
    _retain_physical_components,
)


def test_empty_semantic_seed_gets_real_multiscale_boxes():
    seed = np.zeros((400, 600), bool)
    boxes = _adaptive_prompt_boxes(seed, 300, 200, (600, 400))
    assert len(boxes) >= 2
    assert all(0 <= x0 < x1 < 600 and 0 <= y0 < y1 < 400 for x0,y0,x1,y1 in boxes)
    # The old one-pixel prior is impossible here: all boxes have meaningful area.
    assert all((x1-x0+1)*(y1-y0+1) > 5000 for x0,y0,x1,y1 in boxes)


def test_candidate_score_does_not_require_semantic_seed():
    mask = np.zeros((200, 300), bool)
    mask[60:140, 90:210] = True
    score, metrics = _candidate_mask_score(
        mask, 0.88, 150, 100,
        [(150.0,100.0)], [(10.0,10.0)],
        np.zeros_like(mask),
        (50,40,250,170),
        mask.shape,
    )
    assert score > 1.5
    assert metrics["click_hit"] == 1.0
    assert metrics["seed_iou"] == 0.0


def test_retain_physical_components_keeps_nearby_leg_but_not_distant_object():
    mask = np.zeros((300, 500), bool)
    # Main furniture body.
    mask[80:180, 150:350] = True
    # A disconnected narrow leg close to the body.
    mask[180:235, 190:205] = True
    # A distant second object.
    mask[80:150, 420:480] = True

    out = _retain_physical_components(
        mask, 250, 120, np.zeros_like(mask),
        (120, 50, 380, 260),
    )
    assert out[120, 250]
    assert out[205, 195], "nearby physical leg should survive"
    assert not out[110, 450], "distant room object must not be swallowed"


def test_confidence_is_not_a_fixed_range_constant():
    small = np.zeros((200, 300), bool)
    small[80:120, 130:170] = True
    large = np.ones((200, 300), bool)
    c1 = _mask_confidence(small, 150, 100, np.zeros_like(small))
    c2 = _mask_confidence(large, 150, 100, np.zeros_like(large))
    assert 0.0 <= c1 <= 1.0
    assert 0.0 <= c2 <= 1.0
    assert c1 != c2


def test_semantic_seed_is_soft_evidence_not_a_hard_clip():
    seed = np.zeros((200, 300), bool)
    seed[70:130, 100:200] = True
    mask = seed.copy()
    mask[130:155, 130:170] = True  # valid extension beyond semantic mask
    score, metrics = _candidate_mask_score(
        mask, 0.90, 150, 100,
        [(150.0,100.0)], [],
        seed, (80,50,220,170), mask.shape,
    )
    assert score > 1.5
    assert metrics["seed_iou"] < 1.0
    assert mask[145,150], "candidate extension must remain available to scoring"

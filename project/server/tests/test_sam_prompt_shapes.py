from __future__ import annotations

import os
import sys
import types
from pathlib import Path

from PIL import Image

if "transformers" not in sys.modules:
    _t = types.ModuleType("transformers")
    _t.pipeline = lambda *a, **k: None
    sys.modules["transformers"] = _t

SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_DIR))
os.environ.setdefault("CIGOGNE_SECRET_KEY", "test-secret-key-not-for-production")

from main import _run_sam_prompt  # noqa: E402


class CaptureProcessor:
    def __init__(self):
        self.kwargs = None

    def __call__(self, **kwargs):
        self.kwargs = kwargs
        raise RuntimeError("capture-only")


def test_sam_multiple_points_use_object_point_nesting():
    processor = CaptureProcessor()
    result = _run_sam_prompt(
        object(), processor, Image.new("RGB", (100, 80)),
        [(10, 20), (30, 40), (50, 60)], [1, 1, 0], None, "cpu"
    )
    assert result == []
    assert processor.kwargs["input_points"] == [[[[10.0, 20.0], [30.0, 40.0], [50.0, 60.0]]]]
    assert processor.kwargs["input_labels"] == [[[1, 1, 0]]]
    assert "input_boxes" not in processor.kwargs


def test_sam_box_prompt_is_independent_from_points():
    processor = CaptureProcessor()
    result = _run_sam_prompt(
        object(), processor, Image.new("RGB", (100, 80)),
        [], [], (5, 6, 70, 60), "cpu"
    )
    assert result == []
    assert processor.kwargs["input_boxes"] == [[[5.0, 6.0, 70.0, 60.0]]]
    assert "input_points" not in processor.kwargs

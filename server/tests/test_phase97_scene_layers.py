import os, sys, types
import numpy as np
from PIL import Image
if "transformers" not in sys.modules:
    t=types.ModuleType("transformers"); t.pipeline=lambda *a,**k: None; sys.modules["transformers"]=t
SERVER_DIR=__import__("pathlib").Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_DIR))
os.environ.setdefault("CIGOGNE_SECRET_KEY", "test-secret-key-not-for-production")
import main


def test_scene_layer_map_partitions_confirmed_mask(monkeypatch):
    h, w = 100, 140
    mask = np.zeros((h, w), dtype=bool)
    mask[20:80, 30:110] = True
    wall = np.zeros_like(mask); wall[0:48, :] = True
    floor = np.zeros_like(mask); floor[48:, :] = True
    rug = np.zeros_like(mask); rug[65:92, 20:120] = True

    monkeypatch.setattr(main, "_surface_masks", lambda image: {"wall": wall, "floor": floor, "rug": rug})
    smap, counts = main._scene_layer_map(Image.new("RGB", (w, h)), mask)

    assert int((smap == 2).sum()) > 0
    assert int((smap == 1).sum()) > 0
    assert int((smap == 3).sum()) > 0
    assert sum(counts.values()) == int(mask.sum())
    assert np.all(smap[~mask] == 0)


def test_surface_partition_never_expands_mask(monkeypatch):
    h, w = 80, 100
    mask = np.zeros((h, w), dtype=bool)
    mask[20:60, 30:70] = True
    surface = np.zeros_like(mask, dtype=np.uint8)
    surface[15:65, 25:75] = 1
    part = main._surface_partition_mask(mask, surface, 1)
    assert np.all(~part | mask)
    assert part.sum() > 0

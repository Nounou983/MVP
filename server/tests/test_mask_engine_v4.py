from __future__ import annotations
import os, sys, types
from pathlib import Path
import numpy as np
import pytest
if 'transformers' not in sys.modules:
    t=types.ModuleType('transformers'); t.pipeline=lambda *a,**k: None; sys.modules['transformers']=t
SERVER_DIR=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(SERVER_DIR))
os.environ.setdefault('CIGOGNE_SECRET_KEY','test-secret-key-not-for-production')
pytest.importorskip('cv2')
from main import _mask_engine_v4_boxes, _v4_candidate_score, _v4_boundary_probe_points, _merge_sam_physical_parts, _physical_family_prior

def test_v4_boxes_do_not_depend_only_on_semantic_fragment():
    seed=np.zeros((768,1408),bool); seed[350:430,580:900]=True
    boxes=_mask_engine_v4_boxes(seed,700,390,(1408,768))
    assert len(boxes)>=5
    # At least one box must extend substantially beyond the semantic fragment.
    assert max((b[2]-b[0]+1) for b in boxes) > 500

def test_v4_click_candidate_is_valid_without_semantic_seed():
    m=np.zeros((300,500),bool); m[90:210,180:340]=True
    score,metrics=_v4_candidate_score(m,0.72,250,150,[(250.,150.)],[],np.zeros_like(m),(100,50,400,250),m.shape)
    assert score>0
    assert metrics['seed_iou']==0

def test_v4_rejects_candidate_not_containing_click():
    m=np.zeros((300,500),bool); m[90:210,180:340]=True
    score,_=_v4_candidate_score(m,0.99,50,50,[(50.,50.)],[],np.zeros_like(m),(100,50,400,250),m.shape)
    assert score < -1e8

def test_v4_boundary_probes_are_outside_and_bounded():
    m=np.zeros((300,500),bool); m[80:180,150:350]=True
    pts=_v4_boundary_probe_points(m,250,120,12)
    assert 1 <= len(pts) <= 12
    assert all(0<=x<500 and 0<=y<300 for x,y in pts)


def test_v4_penalizes_semantic_neighbor_contamination():
    seed=np.zeros((300,500),bool); seed[100:180,180:300]=True
    exclusion=np.zeros_like(seed); exclusion[90:210,310:380]=True
    clean=seed.copy()
    contaminated=seed.copy(); contaminated[120:170,310:360]=True

    clean_score, clean_metrics=_v4_candidate_score(
        clean,0.80,220,130,[(220.,130.)],[],seed,(150,80,400,220),seed.shape,exclusion
    )
    bad_score, bad_metrics=_v4_candidate_score(
        contaminated,0.80,220,130,[(220.,130.)],[],seed,(150,80,400,220),seed.shape,exclusion
    )
    assert bad_metrics["neighbor_overlap"] > clean_metrics["neighbor_overlap"]
    assert bad_score < clean_score


def test_v4_does_not_delete_disconnected_recovered_part():
    primary=np.zeros((200,300),bool); primary[70:120,100:180]=True
    leg=np.zeros_like(primary); leg[125:155,130:145]=True
    score=0.92
    candidates=[(leg,score,137,140)]
    merged,added=_merge_sam_physical_parts(primary,candidates,120,90)
    assert added == 1
    assert merged[140,137]


def test_physical_family_prior_uses_tuple_bboxes_without_crashing():
    seed=np.zeros((100,140),bool); seed[30:50,50:80]=True
    related=np.zeros_like(seed); related[50:70,52:78]=True
    seg=[
        {"label":"bedclothes", "mask": related.astype(np.uint8)*255, "score": 0.9},
    ]
    prior, meta=_physical_family_prior(seg, (140,100), (140,100), 60, 40, "bed", seed)
    assert prior.any()
    assert meta["family"] == ["bed", "bedclothes", "pillow"]
    assert meta["components_added"] == 1

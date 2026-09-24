from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import numpy as np
try:
    import torch
except Exception:  # pragma: no cover - CPU-only test/dev envs, or a broken/incompatible torch+CUDA install
    torch = None
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from PIL import Image
from transformers import pipeline

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

MAX_ANALYSIS_SIDE = 1280
APP_VERSION = "9.7.0"
DEPTH_MODEL = "depth-anything/Depth-Anything-V2-Small-hf"
SEG_MODEL = "nvidia/segformer-b3-finetuned-ade-512-512"

# Single source of truth for every removal quality-gate threshold. Both the
# enforcement path and /inpaint-status consume this mapping so status cannot
# drift from the actual gate again. Values are intentionally named by the
# condition they enforce rather than by historical phase names.
REMOVAL_GATE_THRESHOLDS = {
    "max_seam": 55.0,
    "max_furniture_penalty": 0.45,
    "max_residual_structure": 0.45,
    "min_texture_ratio": 0.35,
    "min_surface_consistency": 0.60,
    "residual_structure_abs_floor": 0.020,
    "residual_structure_rel_multiplier": 1.15,
    # target_change is no longer a blanket minimum. It is only used to detect
    # a near-no-op that still looks like furniture or still contains structure.
    "noop_target_change": 0.02,
    # Surface-partition recovery gates are kept separate from the final gate
    # because they operate on individual wall/floor/rug partitions.
    "surface_partition_min_texture_wall_floor": 0.22,
    "surface_partition_min_texture_other": 0.08,
    "surface_partition_min_change": 0.28,
    "surface_partition_max_residual": 0.55,
    "surface_partition_max_seam": 65.0,
    "surface_partition_max_furniture_penalty": 0.72,
    "surface_lama_min_change": 0.28,
    "surface_lama_max_residual": 0.60,
    "surface_lama_max_seam": 70.0,
    "surface_lama_max_furniture_penalty": 0.78,
}

def _phase97_capture_path(filename: str) -> Optional[Path]:
    """Return an opt-in diagnostic capture path for an RTX trace."""
    root = _os.environ.get("CIGOGNE_PHASE97_CAPTURE_DIR", "").strip()
    if not root:
        return None
    path = Path(root)
    path.mkdir(parents=True, exist_ok=True)
    return path / filename


def _phase97_capture_image(filename: str, image: Optional[Image.Image]) -> None:
    if image is None:
        return
    path = _phase97_capture_path(filename)
    if path is None:
        return
    try:
        image.convert("RGB").save(path)
    except Exception as exc:
        print(f"Phase97 diagnostic capture failed for {filename}: {exc}")


def _phase97_capture_mask(mask: Image.Image) -> None:
    path = _phase97_capture_path("mask_submitted.png")
    if path is None:
        return
    try:
        mask.convert("L").save(path)
    except Exception as exc:
        print(f"Phase97 diagnostic mask capture failed: {exc}")

# Remove-only quality-gate documentation. The authoritative thresholds live
# exclusively in REMOVAL_GATE_THRESHOLDS above. Do not add gate literals here.
#
# RORem seed/ensemble parameters are intentionally unchanged in this round.
# Phase 14: recalibrated so _residual_structure_score can actually register a
# nonzero value (see that function's docstring for the empirical evidence).
# Lower = more sensitive. These are a reasoned starting point, not a
# calibrated measurement — tune from the "[residual_structure] inner=...
# outer=... floor=..." lines this build now prints for every candidate.
# A flat/blurry "cop-out" fill is rejected by the texture and surface-consistency
# checks in the authoritative gate mapping above.
# Phase 5.2.16: generative replacement engines intentionally disabled in Remove AI.
# The product requirement is REMOVE, never replacement furniture.

# ADE20K labels used by the visualizer. Keep aliases broad because model
# label strings can vary slightly across Transformers versions.
ADE_STRUCT = {"floor", "wall", "windowpane", "door"}
ADE_FURNITURE = {
    "bed", "bedclothes", "chair", "sofa", "table", "desk", "cabinet",
    "chest of drawers", "counter", "bench", "shelf", "shelves", "ottoman",
    "armchair", "seat", "stool", "bookcase", "wardrobe", "coffee table",
    "dining table", "pillow", "lamp", "television", "monitor", "plant",
    "basket", "vase", "bottle", "bowl", "cup", "pot", "potted plant",
}
# Semantic labels that are useful as *negative evidence* around a clicked
# furniture object. They never define the positive object extent; they only
# penalize a SAM candidate that spills into an independently segmented
# neighbour/background surface.
ADE_NEIGHBOR_OBJECTS = ADE_FURNITURE | {
    "rug", "carpet", "floor", "wall", "windowpane", "door",
    "curtain", "blind", "painting", "picture", "mirror",
    "sconce", "chandelier", "ceiling", "countertop",
}

app = FastAPI(title="La Cigogne D'Ailleurs AI", version=APP_VERSION)

# Phase 6: origins come from the environment. The default is unchanged for a
# laptop ("*"), but a production deployment must list its frontends — in that
# setup this service normally isn't public at all, it sits behind the API and
# only the worker talks to it.
import os as _os  # noqa: E402  (kept local to this block on purpose)

_cors_env = _os.environ.get("CIGOGNE_AI_CORS_ORIGINS", "").strip()
_cors_origins = [o.strip() for o in _cors_env.split(",") if o.strip()] or ["*"]
if _os.environ.get("CIGOGNE_ENV", "development").lower() in {"production", "prod", "staging"} \
        and _cors_origins == ["*"]:
    raise RuntimeError(
        "CIGOGNE_AI_CORS_ORIGINS must list explicit origins when CIGOGNE_ENV=production."
    )
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_origins != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

depth_pipe = None
seg_pipe = None
lama = None
rorem_pipe = None
rorem_failed = False
smarteraser_pipe = None
smarteraser_failed = False
diffusion_pipe = None
diffusion_failed = False
powerpaint_pipe = None
powerpaint_failed = False
sam_model = None
sam_processor = None
sam_failed = False


def get_device():
    return 0 if torch.cuda.is_available() else -1


def get_pipes():
    global depth_pipe, seg_pipe
    if depth_pipe is None or seg_pipe is None:
        device = get_device()
        device_name = "CUDA" if device >= 0 else "CPU"
        print(f"Loading AI models (device={device_name})...")
        depth_pipe = pipeline("depth-estimation", model=DEPTH_MODEL, device=device)
        seg_pipe = pipeline("image-segmentation", model=SEG_MODEL, device=device)
        print("AI models ready.")
    return depth_pipe, seg_pipe



def get_sam():
    """Load point-prompted SAM lazily.

    SAM is intentionally lazy because the normal room-analysis path does not
    need it. If SAM cannot be loaded, the caller falls back to an OpenCV
    GrabCut refinement instead of returning a 500 to the browser.
    """
    global sam_model, sam_processor, sam_failed
    if sam_model is not None and sam_processor is not None:
        return sam_model, sam_processor
    if sam_failed:
        return None, None
    try:
        from transformers import SamModel, SamProcessor
        device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"Loading SAM point-refiner (device={device})...")
        sam_processor = SamProcessor.from_pretrained("facebook/sam-vit-base")
        sam_model = SamModel.from_pretrained("facebook/sam-vit-base")
        sam_model = sam_model.to(device)
        sam_model.eval()
        print("SAM point-refiner ready.")
        return sam_model, sam_processor
    except Exception as exc:
        sam_failed = True
        print(f"WARNING: SAM unavailable, using GrabCut fallback: {exc}")
        return None, None


def _connected_component_at_point(seed: np.ndarray, x: int, y: int) -> np.ndarray:
    """Return only the semantic component containing the user click."""
    if not seed.any():
        return seed.astype(bool)
    if cv2 is None:
        return seed.astype(bool)
    u8 = (seed.astype(np.uint8) * 255)
    # Close tiny holes but never grow the semantic region aggressively.
    u8 = cv2.morphologyEx(u8, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8), iterations=1)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(u8, 8)
    if n <= 1:
        return seed.astype(bool)
    yy = int(np.clip(y, 0, seed.shape[0] - 1)); xx = int(np.clip(x, 0, seed.shape[1] - 1))
    lab = int(labels[yy, xx])
    if lab > 0:
        return labels == lab
    # If the semantic prediction misses the exact click by a few pixels,
    # choose the nearest non-empty component rather than the largest one.
    ys, xs = np.where(labels > 0)
    if xs.size:
        k = int(np.argmin((xs - xx) ** 2 + (ys - yy) ** 2))
        return labels == labels[ys[k], xs[k]]
    return seed.astype(bool)


def _semantic_box(seed: np.ndarray, x: int, y: int, image_size: Tuple[int, int]):
    H, W = seed.shape
    component = _connected_component_at_point(seed, x, y)
    ys, xs = np.where(component)
    if xs.size < 20:
        # Small local fallback around the click.
        half = int(max(48, min(W, H) * 0.10))
        x0, x1 = max(0, x-half), min(W-1, x+half)
        y0, y1 = max(0, y-half), min(H-1, y+half)
        return component, (x0, y0, x1, y1)
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    # Expand enough to include furniture edges/legs while keeping the prompt
    # local. This is deliberately much tighter than a whole-scene semantic mask.
    bw, bh = x1-x0+1, y1-y0+1
    pad_x = int(np.clip(round(bw * 0.14), 8, 120))
    pad_y = int(np.clip(round(bh * 0.14), 8, 120))
    return component, (max(0, x0-pad_x), max(0, y0-pad_y), min(W-1, x1+pad_x), min(H-1, y1+pad_y))


def _sample_positive_points(component: np.ndarray, x: int, y: int, max_points: int = 7):
    """Create a compact set of positive prompts inside the semantic component."""
    H, W = component.shape
    ys, xs = np.where(component)
    pts = [(float(x), float(y))]
    if xs.size == 0:
        return pts
    # Prefer interior pixels (distance transform) so prompts do not sit on edges.
    if cv2 is not None:
        dist = cv2.distanceTransform((component.astype(np.uint8)), cv2.DIST_L2, 5)
        flat = np.argsort(dist.ravel())[::-1]
        seen = {(int(x), int(y))}
        for idx in flat[:max(300, max_points*50)]:
            yy, xx = np.unravel_index(int(idx), dist.shape)
            if dist[yy, xx] < 3:
                break
            key = (int(xx), int(yy))
            if key in seen:
                continue
            if ((xx-x)**2 + (yy-y)**2) > max(W,H)**2 * 0.08:
                continue
            seen.add(key); pts.append((float(xx), float(yy)))
            if len(pts) >= max_points:
                break
    if len(pts) < 3:
        # Deterministic quantile samples across the semantic component.
        for q in (0.25, 0.5, 0.75):
            pts.append((float(np.quantile(xs, q)), float(np.quantile(ys, q))))
            if len(pts) >= max_points:
                break
    # Deduplicate after rounding.
    out=[]; seen=set()
    for px, py in pts:
        k=(int(round(px)), int(round(py)))
        if k not in seen:
            seen.add(k); out.append([float(k[0]), float(k[1])])
    return out[:max_points]


def _grabcut_refine(image: Image.Image, x: int, y: int, seed: np.ndarray, box=None):
    """Local GrabCut fallback constrained by the semantic object component."""
    if cv2 is None:
        return Image.fromarray((seed.astype(np.uint8) * 255), mode="L")
    rgb = np.asarray(image.convert("RGB"))
    H, W = seed.shape
    component, sb = _semantic_box(seed, x, y, image.size)
    if box is None:
        box = sb
    bx0, by0, bx1, by1 = map(int, box)
    crop = rgb[by0:by1+1, bx0:bx1+1]
    local_seed = component[by0:by1+1, bx0:bx1+1]
    if local_seed.sum() < 20:
        # Semantic miss: make a compact local prior so GrabCut still has a
        # chance to recover the clicked object from real image boundaries.
        rx = max(28, min(120, int(W * 0.055)))
        ry = max(28, min(120, int(H * 0.055)))
        yy, xx = np.ogrid[:H, :W]
        component = (((xx-x)**2/(rx*rx) + (yy-y)**2/(ry*ry)) <= 1.0).astype(bool)
        sb = (max(0, x-rx*2), max(0, y-ry*2), min(W-1, x+rx*2), min(H-1, y+ry*2))
        bx0, by0, bx1, by1 = map(int, sb)
        crop = rgb[by0:by1+1, bx0:bx1+1]
        local_seed = component[by0:by1+1, bx0:bx1+1]

    gc = np.full(local_seed.shape, cv2.GC_PR_BGD, np.uint8)
    gc[local_seed] = cv2.GC_PR_FGD
    core = cv2.erode((local_seed.astype(np.uint8) * 255), np.ones((5,5), np.uint8), iterations=1)
    gc[core > 0] = cv2.GC_FGD
    # A narrow frame is definite background; do not mark the whole crop bg.
    frame = np.zeros(local_seed.shape, np.uint8)
    frame[:3,:] = frame[-3:,:] = frame[:,:3] = frame[:,-3:] = 1
    gc[frame > 0] = cv2.GC_BGD
    py = int(np.clip(y-by0, 0, gc.shape[0]-1)); px = int(np.clip(x-bx0, 0, gc.shape[1]-1))
    gc[py,px] = cv2.GC_FGD
    bgd=np.zeros((1,65),np.float64); fgd=np.zeros((1,65),np.float64)
    try:
        cv2.grabCut(crop, gc, None, bgd, fgd, 5, cv2.GC_INIT_WITH_MASK)
        out=((gc==cv2.GC_FGD)|(gc==cv2.GC_PR_FGD)).astype(np.uint8)
    except Exception:
        out=local_seed.astype(np.uint8)
    full=np.zeros((H,W),np.uint8); full[by0:by1+1,bx0:bx1+1]=out
    # Never return disconnected scene-wide islands.
    n, labels, stats, _ = cv2.connectedComponentsWithStats(full, 8)
    if n > 1:
        lab=int(labels[int(np.clip(y,0,H-1)),int(np.clip(x,0,W-1))])
        if lab > 0:
            full=(labels==lab).astype(np.uint8)
    return Image.fromarray(full*255, mode="L")



def _mask_bbox(mask: np.ndarray):
    ys, xs = np.where(mask)
    if xs.size == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def _box_iou(a, b):
    if not a or not b:
        return 0.0
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    if ix1 < ix0 or iy1 < iy0:
        return 0.0
    inter = float((ix1 - ix0 + 1) * (iy1 - iy0 + 1))
    aa = float(max(0, ax1-ax0+1) * max(0, ay1-ay0+1))
    bb = float(max(0, bx1-bx0+1) * max(0, by1-by0+1))
    return inter / max(1.0, aa + bb - inter)


def _adaptive_prompt_boxes(seed: np.ndarray, x: int, y: int, image_size: Tuple[int, int]):
    """Build several local SAM boxes without treating the semantic mask as truth.

    When SegFormer misses the object, the old implementation manufactured an
    empty seed and then scored SAM masks against a one-pixel prior.  V3 instead
    uses image-local boxes at two scales.  When a semantic seed exists, its
    envelope is still useful, but only as soft context.
    """
    W, H = image_size
    boxes = []
    component = _connected_component_at_point(seed, x, y)
    bb = _mask_bbox(component)

    if bb:
        x0, y0, x1, y1 = bb
        bw, bh = x1-x0+1, y1-y0+1
        for scale in (0.18, 0.30):
            px = int(np.clip(round(bw * scale), 18, max(24, int(W*0.10))))
            py = int(np.clip(round(bh * scale), 18, max(24, int(H*0.10))))
            boxes.append((
                max(0, x0-px), max(0, y0-py),
                min(W-1, x1+px), min(H-1, y1+py)
            ))
    else:
        # Semantic miss: use compact, medium, and broad click-centered boxes.
        # The broad box is deliberately capped so a click cannot select a
        # second room object hundreds of pixels away.
        base = max(48, int(min(W, H) * 0.10))
        for mult in (1.0, 1.65, 2.35):
            half = int(np.clip(round(base * mult), 48, min(420, max(W, H)//3)))
            boxes.append((
                max(0, x-half), max(0, y-half),
                min(W-1, x+half), min(H-1, y+half)
            ))

    out = []
    seen = set()
    for box in boxes:
        b = tuple(int(v) for v in box)
        if b[2] <= b[0] or b[3] <= b[1]:
            continue
        if b not in seen:
            seen.add(b)
            out.append(b)
    return out[:3]


def _prompt_points_v3(component: np.ndarray, x: int, y: int, box):
    """Create conservative positive/negative points for a SAM prompt.

    Positive points come from the semantic interior when available. Negative
    points are sampled from the outside of the semantic component / prompt box,
    rather than from a fixed-radius ring that can accidentally land on the
    clicked object.
    """
    H, W = component.shape
    x = int(np.clip(x, 0, W-1)); y = int(np.clip(y, 0, H-1))
    positives = [(float(x), float(y))]
    if component.any() and cv2 is not None:
        dist = cv2.distanceTransform(component.astype(np.uint8), cv2.DIST_L2, 5)
        ys, xs = np.where(component)
        # Select spatially separated deep-interior points, not merely the
        # highest distance-transform pixels clustered in one cushion.
        if xs.size:
            quantiles = [(0.30,0.30),(0.70,0.30),(0.30,0.70),(0.70,0.70),(0.50,0.50)]
            for qx, qy in quantiles:
                tx = int(np.clip(round(np.quantile(xs, qx)), 0, W-1))
                ty = int(np.clip(round(np.quantile(ys, qy)), 0, H-1))
                if component[ty, tx] and dist[ty, tx] >= 2.5:
                    if all((tx-px)**2 + (ty-py)**2 > 18**2 for px,py in positives):
                        positives.append((float(tx), float(ty)))
                if len(positives) >= 5:
                    break

    x0, y0, x1, y1 = [int(v) for v in box]
    # Outside points are placed beyond the semantic envelope where possible.
    raw_neg = [
        (x0-10, y0-10), (x1+10, y0-10),
        (x0-10, y1+10), (x1+10, y1+10),
        ((x0+x1)//2, y0-14), ((x0+x1)//2, y1+14),
        (x0-14, (y0+y1)//2), (x1+14, (y0+y1)//2),
    ]
    negatives = []
    for px, py in raw_neg:
        px = int(np.clip(px, 0, W-1)); py = int(np.clip(py, 0, H-1))
        # Never label a semantic interior pixel as background.
        if component[py, px]:
            continue
        if all((px-a)**2 + (py-b)**2 > 14**2 for a,b in negatives):
            negatives.append((float(px), float(py)))
        if len(negatives) >= 6:
            break
    return positives[:5], negatives[:6]


def _run_sam_prompt(model, processor, image: Image.Image, points, labels,
                    box, device):
    """Run one SAM prompt with Transformers 5-compatible prompt nesting.

    IMPORTANT: ``SamProcessor`` expects point prompts as
    ``[image][object][point][xy]`` and labels as ``[image][object][point]``.
    The previous implementation passed ``[image][point][xy]``.  That shape
    happens to look plausible for a single click, but it becomes ambiguous with
    multiple points and, with the fast processor, can produce errors such as
    ``tensor a (12) must match tensor b (6)``.  We also keep box prompts and
    point prompts separate: SAM can accept both, but separate candidates are
    more stable across Transformers 5.x processor implementations and make the
    candidate scorer able to compare the two prompt types fairly.
    """
    try:
        pts = [(float(px), float(py)) for px, py in (points or [])]
        labs = [int(v) for v in (labels or [])]
        if len(pts) != len(labs):
            raise ValueError(f"SAM prompt point/label mismatch: {len(pts)} != {len(labs)}")
        if not pts and box is None:
            raise ValueError("SAM prompt requires at least a point or a box")

        kwargs = {
            "images": image.convert("RGB"),
            "return_tensors": "pt",
        }
        if pts:
            # One image -> one object -> N points.
            kwargs["input_points"] = [[[[px, py] for px, py in pts]]]
            kwargs["input_labels"] = [[[v for v in labs]]]
        if box is not None:
            x0, y0, x1, y1 = [float(v) for v in box]
            if x1 <= x0 or y1 <= y0:
                raise ValueError(f"Invalid SAM box: {box}")
            # One image -> one box. Do not mix a box with points in the same
            # processor call; point-only and box-only candidates are evaluated
            # independently by the caller.
            if pts:
                raise ValueError("SAM box/point prompts must be evaluated separately")
            kwargs["input_boxes"] = [[[x0, y0, x1, y1]]]

        inputs = processor(**kwargs)
        if hasattr(inputs, "to"):
            inputs = inputs.to(device)
        else:
            inputs = {k: (v.to(device) if hasattr(v, "to") else v)
                      for k, v in inputs.items()}

        with torch.inference_mode():
            # Explicit multimask output gives us SAM's alternatives; the
            # geometry scorer chooses the object-consistent one.
            outputs = model(**inputs, multimask_output=True)

        original_sizes = inputs["original_sizes"].detach().cpu()
        reshaped_sizes = inputs["reshaped_input_sizes"].detach().cpu()
        pred_masks = outputs.pred_masks.detach().cpu()
        masks = processor.image_processor.post_process_masks(
            pred_masks, original_sizes, reshaped_sizes
        )

        raw_scores = outputs.iou_scores.detach().cpu().numpy()
        # Depending on the Transformers minor version, SAM outputs can be
        # [B, M] or [B, 1, M]. Normalize to one score per returned mask.
        scores = np.asarray(raw_scores).reshape(-1)
        first = masks[0] if isinstance(masks, (list, tuple)) else masks
        if hasattr(first, "detach"):
            first = first.detach().cpu()
        first = np.asarray(first)
        if first.ndim == 4:
            # [objects, masks, H, W] -> this call contains one object.
            first = first[0]
        elif first.ndim == 3:
            # [masks, H, W]
            pass
        elif first.ndim == 2:
            first = first[None, ...]
        else:
            raise RuntimeError(f"Unexpected SAM post-process shape: {first.shape}")

        out = []
        for i in range(int(first.shape[0])):
            m = np.asarray(first[i]).astype(bool)
            if m.ndim != 2 or not m.any():
                continue
            score = float(scores[i]) if i < len(scores) else 0.0
            out.append((m, score))
        return out
    except Exception as exc:
        print(f"SAM prompt failed: {exc}")
        return []


def _retain_physical_components(mask: np.ndarray, x: int, y: int,
                                seed: np.ndarray, prompt_box):
    """Keep the clicked SAM component plus nearby small physical pieces.

    This intentionally preserves disconnected legs/feet/rails when SAM
    separates them from the body, while rejecting distant room objects.
    Holes inside the retained components are never filled.
    """
    if cv2 is None or not mask.any():
        return mask.astype(bool)
    H,W = mask.shape
    u8 = (mask.astype(np.uint8)*255)
    n, labels, stats, cent = cv2.connectedComponentsWithStats(u8, 8)
    if n <= 1:
        return mask.astype(bool)
    cx, cy = int(np.clip(x,0,W-1)), int(np.clip(y,0,H-1))
    core_lab = int(labels[cy,cx])
    if core_lab <= 0:
        # Choose nearest component if SAM's thresholded mask moved the click by
        # a few pixels.
        ys,xs=np.where(labels>0)
        if xs.size == 0:
            return np.zeros_like(mask, bool)
        k=int(np.argmin((xs-cx)**2+(ys-cy)**2))
        core_lab=int(labels[ys[k],xs[k]])
    core = labels == core_lab
    core_area = max(1,int(core.sum()))
    core_bb = _mask_bbox(core)
    px0,py0,px1,py1 = prompt_box
    prompt_diag = max(1.0, ((px1-px0+1)**2 + (py1-py0+1)**2)**0.5)
    result = core.copy()
    for lab in range(1,n):
        if lab == core_lab:
            continue
        part = labels == lab
        area=int(part.sum())
        if area < 6 or area > max(0.55*core_area, 5000):
            continue
        bb=_mask_bbox(part)
        if not bb or not core_bb:
            continue
        # Bounding-box gap (0 if overlapping/touching).
        gx=max(0, max(core_bb[0],bb[0]) - min(core_bb[2],bb[2]))
        gy=max(0, max(core_bb[1],bb[1]) - min(core_bb[3],bb[3]))
        gap=(gx*gx+gy*gy)**0.5
        part_cx=float(np.mean(np.where(part)[1])); part_cy=float(np.mean(np.where(part)[0]))
        core_cx=float(np.mean(np.where(core)[1])); core_cy=float(np.mean(np.where(core)[0]))
        dist=((part_cx-core_cx)**2+(part_cy-core_cy)**2)**0.5
        # Small parts may sit below/alongside a sofa or table, but not halfway
        # across the room.
        if gap > max(24.0, min(80.0, prompt_diag*0.08)):
            continue
        if dist > max(100.0, min(240.0, prompt_diag*0.42)):
            continue
        # Semantic support is strong evidence for a missed physical part.
        support=False
        if seed.any():
            seed_d = cv2.dilate(seed.astype(np.uint8),
                                cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(31,31)),1)>0
            support=bool((seed_d & part).any())
        if support or gap <= 14:
            result |= part
    return result


def _candidate_mask_score(mask, sam_score, x, y, positives, negatives,
                          seed, prompt_box, image_shape):
    """Score a SAM mask using independent evidence; no hard semantic clipping."""
    H,W=image_shape
    area=float(mask.sum()); image_area=float(H*W)
    if area <= 0:
        return -1e9, {}
    yy=int(np.clip(y,0,H-1)); xx=int(np.clip(x,0,W-1))
    click_hit=1.0 if mask[yy,xx] else 0.0
    pos_hit=(sum(bool(mask[int(np.clip(round(py),0,H-1)), int(np.clip(round(px),0,W-1))])
                 for px,py in positives)/max(1,len(positives)))
    neg_hit=(sum(bool(mask[int(np.clip(round(py),0,H-1)), int(np.clip(round(px),0,W-1))])
                 for px,py in negatives)/max(1,len(negatives))) if negatives else 0.0
    bb=_mask_bbox(mask)
    box_area=float(max(1,(prompt_box[2]-prompt_box[0]+1)*(prompt_box[3]-prompt_box[1]+1)))
    area_in_box=float(mask[prompt_box[1]:prompt_box[3]+1,prompt_box[0]:prompt_box[2]+1].sum())/area
    seed_iou=0.0; seed_precision=0.0
    if seed.any():
        inter=float((mask & seed).sum())
        seed_iou=inter/max(1.0,float((mask|seed).sum()))
        seed_precision=inter/max(1.0,area)
    # Avoid scene-wide masks while still allowing real sofas/beds to exceed a
    # coarse semantic component.
    area_ratio=area/image_area
    size_penalty=0.0
    if area_ratio > 0.40:
        size_penalty += (area_ratio-0.40)*8.0
    if seed.any():
        seed_area=max(1.0,float(seed.sum()))
        ratio=area/seed_area
        if ratio > 2.4:
            size_penalty += min(3.0,(ratio-2.4)*0.8)
        elif ratio < 0.20:
            size_penalty += min(1.2,(0.20-ratio)*2.0)
    # A mask that is mostly inside the prompt box is more trustworthy than a
    # huge spill outside it, but legs are allowed to extend beyond the box.
    box_penalty=max(0.0, 0.72-area_in_box)*1.1
    score=(3.2*click_hit + 1.4*pos_hit + 1.5*(1-neg_hit)
           + 1.25*float(np.clip(sam_score,0,1))
           + 1.2*seed_iou + 0.55*seed_precision
           + 0.35*area_in_box - size_penalty - box_penalty)
    metrics={
        "sam_score":float(sam_score),"click_hit":click_hit,
        "positive_hit":pos_hit,"negative_hit":neg_hit,
        "seed_iou":seed_iou,"seed_precision":seed_precision,
        "area_ratio":area_ratio,"area_in_prompt_box":area_in_box,
        "score":float(score),
    }
    return float(score),metrics


def _sam_box_candidates(model, processor, image: Image.Image, box, click, device):
    """Compatibility wrapper: one local box prompt, returning scored tuples."""
    positives=[(float(click[0]),float(click[1]))]
    return [
        (m,score,int(round(click[0])),int(round(click[1])))
        for m,score in _run_sam_prompt(
            model,processor,image,positives,[1],box,device
        )
        if score >= 0.35
    ]


def _sam_single_point_candidates(model, processor, image: Image.Image, points, device):
    """Run independent positive-point prompts for thin/disconnected recovery."""
    out=[]
    for px,py in points:
        for m,score in _run_sam_prompt(
            model,processor,image,[(float(px),float(py))],[1],None,device
        ):
            if score >= 0.38 and m[int(np.clip(round(py),0,m.shape[0]-1)),
                                   int(np.clip(round(px),0,m.shape[1]-1))]:
                out.append((m,score,int(round(px)),int(round(py))))
    return out


def _merge_sam_physical_parts(primary: np.ndarray, candidates, x: int, y: int,
                                exclusion: np.ndarray | None = None):
    """Merge geometrically plausible disconnected furniture sub-parts.

    A recovered leg/arm is allowed to be disconnected from the body, but it
    cannot be accepted if it is predominantly a known neighbour/background
    component.
    """
    if cv2 is None or not primary.any():
        return primary.astype(bool), 0
    base=primary.astype(bool)
    base_area=max(1,int(base.sum()))
    accepted=0
    for m,score,px,py in sorted(candidates,key=lambda c:c[1],reverse=True):
        if m.shape != base.shape or score < 0.45:
            continue
        n,labels,stats,_=cv2.connectedComponentsWithStats((m.astype(np.uint8)*255),8)
        lab=int(labels[int(np.clip(py,0,m.shape[0]-1)),int(np.clip(px,0,m.shape[1]-1))])
        if lab<=0:
            continue
        part=labels==lab
        area=int(part.sum())
        if area < 8 or area > max(int(base_area*0.65), 12000):
            continue

        if exclusion is not None and exclusion.shape == part.shape:
            contam=float((part & exclusion).sum())/max(1.0,float(area))
            if contam > 0.18:
                continue

        overlap=int((part&base).sum())
        if overlap/max(1,min(area,base_area)) >= 0.025:
            base |= part; accepted += 1; base_area=max(base_area,int(base.sum())); continue

        dil=cv2.dilate(base.astype(np.uint8),
                       cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(25,25)),1)>0
        if not (dil&part).any():
            continue
        ys,xs=np.where(part)
        if xs.size==0:
            continue
        bcx,bcy=float(np.mean(np.where(base)[1])),float(np.mean(np.where(base)[0]))
        pcx,pcy=float(np.mean(xs)),float(np.mean(ys))
        dist=((pcx-bcx)**2+(pcy-bcy)**2)**0.5
        max_dist=max(60.0,min(220.0,0.45*max(m.shape)))
        if dist > max_dist or score < 0.58:
            continue
        base |= part
        base_area=max(base_area,int(base.sum()))
        accepted += 1
    return base,accepted


def _repair_points_from_mask(mask: np.ndarray, x: int, y: int, max_points=8):
    """Generate probes around the physical envelope, especially its lower edge."""
    H,W=mask.shape
    bb=_mask_bbox(mask)
    if not bb:
        return []
    x0,y0,x1,y1=bb
    bw,bh=x1-x0+1,y1-y0+1
    px=int(np.clip(round(bw*0.18),18,140))
    py=int(np.clip(round(bh*0.22),18,140))
    x0=max(0,x0-px); x1=min(W-1,x1+px)
    y0=max(0,y0-py); y1=min(H-1,y1+py)
    raw=[
        (x0+0.10*(x1-x0), y0+0.90*(y1-y0)),
        (x0+0.28*(x1-x0), y0+0.97*(y1-y0)),
        (x0+0.50*(x1-x0), y0+0.98*(y1-y0)),
        (x0+0.72*(x1-x0), y0+0.97*(y1-y0)),
        (x0+0.90*(x1-x0), y0+0.90*(y1-y0)),
        (x0+0.04*(x1-x0), y0+0.50*(y1-y0)),
        (x0+0.96*(x1-x0), y0+0.50*(y1-y0)),
        (x0+0.50*(x1-x0), y0+0.05*(y1-y0)),
    ]
    pts=[]
    for pxv,pyv in raw:
        pxx=int(np.clip(round(pxv),0,W-1)); pyy=int(np.clip(round(pyv),0,H-1))
        if mask[pyy,pxx]:
            continue
        if all((pxx-a)**2+(pyy-b)**2>20**2 for a,b in pts):
            pts.append((pxx,pyy))
    return pts[:max_points]


def _mask_confidence(mask: np.ndarray, x: int, y: int, seed: np.ndarray):
    """Return an evidence-based 0..1 confidence, not an arbitrary constant."""
    H,W=mask.shape
    area=float(mask.sum()); ratio=area/max(1.0,float(H*W))
    click=1.0 if 0<=y<H and 0<=x<W and mask[y,x] else 0.0
    if area <= 0:
        return 0.0
    area_score=1.0 if 0.001 <= ratio <= 0.30 else (
        max(0.0, 1.0-(0.001-ratio)/0.001) if ratio<0.001 else
        max(0.0, 1.0-(ratio-0.30)/0.15))
    seed_score=0.55
    if seed.any():
        inter=float((mask&seed).sum())
        seed_score=0.65*(inter/max(1.0,float(seed.sum()))) + 0.35*(inter/max(1.0,area))
        seed_score=float(np.clip(seed_score,0,1))
    component_count=1
    if cv2 is not None:
        component_count=max(1,cv2.connectedComponentsWithStats((mask.astype(np.uint8)*255),8)[0]-1)
    component_score=1.0 if component_count<=5 else max(0.0,1.0-(component_count-5)*0.08)
    confidence=float(np.clip(0.36*click+0.28*seed_score+0.24*area_score+0.12*component_score,0,1))
    return round(confidence,4)


def _mask_engine_v4_boxes(seed: np.ndarray, x: int, y: int, image_size: Tuple[int, int]):
    """Generate overlapping SAM boxes for V4.

    The semantic mask is only a hint.  V4 always includes click-centred boxes
    so a partially detected object cannot shrink the search region around the
    exact fragment SegFormer happened to see.
    """
    W, H = image_size
    boxes = []
    comp = _connected_component_at_point(seed, x, y)
    bb = _mask_bbox(comp)
    if bb:
        x0, y0, x1, y1 = bb
        bw, bh = x1-x0+1, y1-y0+1
        for scale in (0.20, 0.45, 0.85):
            px = max(24, int(round(bw * scale)))
            py = max(24, int(round(bh * scale)))
            boxes.append((max(0,x0-px), max(0,y0-py), min(W-1,x1+px), min(H-1,y1+py)))
    # These are deliberately independent of SegFormer.  The two wider boxes
    # are what prevent a headboard/seat/table-top semantic fragment from
    # becoming the permanent search envelope.
    short = min(W, H)
    for fx, fy in ((0.16,0.22),(0.28,0.34),(0.42,0.46)):
        hw = max(56, int(round(W*fx)))
        hh = max(56, int(round(H*fy)))
        boxes.append((max(0,x-hw), max(0,y-hh), min(W-1,x+hw), min(H-1,y+hh)))
    out=[]; seen=set()
    for b in boxes:
        b=tuple(int(v) for v in b)
        if b[2]-b[0] < 40 or b[3]-b[1] < 40 or b in seen:
            continue
        seen.add(b); out.append(b)
    # Keep the search bounded. The final candidate ranking is still click-first.
    return out[:7]


def _v4_candidate_score(mask, sam_score, x, y, positives, negatives, seed,
                         box, image_shape, exclusion=None):
    """Score a SAM candidate with click-first *and neighbour-protection* evidence.

    SegFormer is not allowed to clip the positive object.  Its independent
    components are used only as negative evidence: a candidate that absorbs a
    plant, basket, floor, wall, rug, or a second furniture component is
    penalized even when SAM's internal IoU score is high.
    """
    H,W=image_shape
    area=float(mask.sum())
    if area <= 0 or not mask[int(y),int(x)]:
        return -1e9, {}
    ratio=area/max(1.0,H*W)
    if ratio > 0.68 or ratio < 0.00005:
        return -1e9, {}
    pos=sum(bool(mask[int(np.clip(round(py),0,H-1)),int(np.clip(round(px),0,W-1))]) for px,py in positives)/max(1,len(positives))
    neg=sum(bool(mask[int(np.clip(round(py),0,H-1)),int(np.clip(round(px),0,W-1))]) for px,py in negatives)/max(1,len(negatives)) if negatives else 0.0
    seed_iou=seed_recall=seed_precision=0.0
    if seed.any():
        inter=float((mask&seed).sum())
        seed_iou=inter/max(1.0,float((mask|seed).sum()))
        seed_recall=inter/max(1.0,float(seed.sum()))
        seed_precision=inter/max(1.0,area)

    exclusion_overlap=0.0
    exclusion_outside=0.0
    if exclusion is not None:
        ex=np.asarray(exclusion,dtype=bool)
        if ex.shape == mask.shape:
            overlap=float((mask & ex).sum())
            exclusion_overlap=overlap/max(1.0,area)
            # Only count spill outside the semantic positive seed as
            # contamination. This prevents imperfect SegFormer boundaries
            # from punishing a legitimate furniture extension.
            outside=mask & ex
            if seed.any():
                seed_d=seed
                if cv2 is not None:
                    seed_d=cv2.dilate(
                        seed.astype(np.uint8),
                        cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(31,31)),1
                    ).astype(bool)
                outside=outside & ~seed_d
            exclusion_outside=float(outside.sum())/max(1.0,area)

    x0,y0,x1,y1=map(int,box)
    inside=float(mask[y0:y1+1,x0:x1+1].sum())/max(1.0,area)
    area_prior=1.0 if 0.002 <= ratio <= 0.30 else max(0.0,1.0-abs(ratio-0.30)/0.38)

    # Coverage is deliberately stronger than raw SAM IoU: a partial seat/arm
    # mask must not beat a complete furniture silhouette merely because its
    # boundary is locally cleaner.
    score=(5.0
           + 2.0*float(np.clip(sam_score,0,1))
           + 2.4*pos
           + 1.6*seed_recall
           + 0.8*seed_iou
           + 0.4*seed_precision
           + 0.7*inside
           + 0.8*area_prior
           - 2.8*neg
           - 6.0*exclusion_outside
           - 2.0*exclusion_overlap)
    return float(score), {
        'sam_score':float(sam_score),'positive_hit':float(pos),'negative_hit':float(neg),
        'seed_iou':float(seed_iou),'seed_recall':float(seed_recall),
        'seed_precision':float(seed_precision),
        'neighbor_overlap':float(exclusion_overlap),
        'neighbor_outside_overlap':float(exclusion_outside),
        'area_ratio':float(ratio),'box_coverage':float(inside),'score':float(score)
    }


def _v4_boundary_probe_points(mask: np.ndarray, x: int, y: int, max_points=12):
    """Find likely omitted legs/arms/rails around the selected envelope."""
    H,W=mask.shape; bb=_mask_bbox(mask)
    if not bb: return []
    x0,y0,x1,y1=bb; bw=max(1,x1-x0+1); bh=max(1,y1-y0+1)
    pts=[]
    # Probe just inside/outside the physical envelope. Outside probes are useful
    # because a disconnected leg often begins below the body rather than inside it.
    samples=[
        (.08,.90),(.22,.98),(.38,.99),(.50,.99),(.62,.99),(.78,.98),(.92,.90),
        (.03,.50),(.97,.50),(.08,.12),(.92,.12),(.50,.03)
    ]
    for fx,fy in samples:
        px=int(np.clip(round(x0+fx*(bw-1)),0,W-1)); py=int(np.clip(round(y0+fy*(bh-1)),0,H-1))
        # Offset alternate probes outward by a small, resolution-aware amount.
        ox=int(np.clip(px + (6 if fx>.5 else -6 if fx<.5 else 0),0,W-1))
        oy=int(np.clip(py + (8 if fy>.5 else -8 if fy<.5 else 0),0,H-1))
        for qx,qy in ((px,py),(ox,oy)):
            if not (mask[qy,qx]) and all((qx-a)**2+(qy-b)**2 > max(12,bw*0.025)**2 for a,b in pts):
                pts.append((qx,qy))
            if len(pts)>=max_points: return pts
    return pts


def refine_mask_with_sam(image: Image.Image, x: int, y: int, seed: np.ndarray,
                         exclusion: np.ndarray | None = None,
                         exclusion_details=None, return_meta: bool = False):
    """Mask Engine V4.2: click-first SAM + physical-family prior + semantic neighbour protection.

    The key change from V4 is candidate selection. Previously a broad SAM
    candidate could win because it had a good internal SAM score and click hit,
    even when it absorbed neighbouring furniture/background. V4.1 evaluates all
    multimask/box candidates against independent semantic negative evidence.
    """
    model, processor = get_sam()
    H,W=np.asarray(image).shape[:2]
    x=int(np.clip(x,0,W-1)); y=int(np.clip(y,0,H-1))
    component=_connected_component_at_point(seed,x,y)
    boxes=_mask_engine_v4_boxes(component,x,y,image.size)
    meta={"engine":"Mask Engine V4.2","fallback":False,"candidate_count":0,
          "selected_method":None,"neighbor_overlap":0.0,
          "neighbor_outside_overlap":0.0,"confidence":0.0}
    if model is None or processor is None:
        meta.update(engine="GrabCut",fallback=True,selected_method="grabcut")
        print('WARNING: Mask Engine V4.2: SAM unavailable; using GrabCut fallback')
        out=_grabcut_refine(image,x,y,component,boxes[0])
        return (out,meta) if return_meta else out
    try:
        device=next(model.parameters()).device
        candidates=[]
        pp, nn = _prompt_points_v3(component,x,y,boxes[0])
        point_prompts=[
            ([(float(x),float(y))],[1],[], 'click'),
            (pp,[1]*len(pp),nn, 'semantic-points'),
        ]
        for pts,labs,negs,name in point_prompts:
            for m,sam_score in _run_sam_prompt(model,processor,image,pts,labs,None,device):
                score,metrics=_v4_candidate_score(
                    m,sam_score,x,y,pts,negs,component,boxes[0],(H,W),exclusion
                )
                if score > -1e8:
                    candidates.append((score,m,metrics,name))

        for i,b in enumerate(boxes):
            for m,sam_score in _run_sam_prompt(model,processor,image,[],[],b,device):
                score,metrics=_v4_candidate_score(
                    m,sam_score,x,y,[(float(x),float(y))],[],component,b,(H,W),exclusion
                )
                if score > -1e8:
                    candidates.append((score,m,metrics,f'box-{i+1}'))

        if not candidates:
            raise RuntimeError('SAM returned no valid click-containing candidate')

        candidates.sort(key=lambda z:z[0],reverse=True)
        best_score,best,best_metrics,best_name=candidates[0]

        # Do not let a tiny score advantage override a materially cleaner
        # boundary. Within this margin, choose the candidate with less
        # neighbour contamination and greater semantic coverage.
        close=[c for c in candidates if c[0] >= best_score-1.25]
        best=min(
            close,
            key=lambda c: (
                c[2].get("neighbor_outside_overlap",0.0)*7.0
                + c[2].get("neighbor_overlap",0.0)*2.0
                - c[2].get("seed_recall",0.0)*1.8
                - c[2].get("positive_hit",0.0)*0.5
                - c[2].get("area_ratio",0.0)*0.08
            )
        )
        best_score,best,best_metrics,best_name=best

        # If a cleaner candidate has substantially better semantic coverage,
        # prefer it even when its raw SAM score is slightly lower.
        for cand_score,cand,metrics,name in candidates:
            if metrics.get("neighbor_outside_overlap",0.0) > 0.22:
                continue
            if component.any() and metrics.get("seed_recall",0.0) > best_metrics.get("seed_recall",0.0)+0.12:
                if cand_score >= best_score-1.8:
                    best_score,best,best_metrics,best_name=cand_score,cand,metrics,name

        best=_retain_physical_components(best,x,y,component,boxes[0])

        # Recover disconnected physical parts using independent positive-point
        # SAM calls. These are merged only when they touch/near-touch the
        # selected object. Importantly, we no longer run the old final
        # `_retain_physical_components` pass after merging: that pass could
        # immediately delete a legitimately recovered disconnected leg.
        probes=_v4_boundary_probe_points(best,x,y,16)
        repair_candidates=_sam_single_point_candidates(
            model,processor,image,probes,device
        ) if probes else []
        best,added=_merge_sam_physical_parts(
            best,repair_candidates,x,y,
            exclusion=exclusion
        )
        if added:
            print(f'Mask Engine V4.2: accepted {added} independent physical-part candidate(s)')

        if int(best.sum()) < 20 or not best[y,x]:
            raise RuntimeError('V4.2 post-processing produced an invalid mask')

        conf=_mask_confidence(best,x,y,component)
        # Final QA is measured after physical-part recovery.
        final_neighbor_overlap=0.0
        final_neighbor_outside=0.0
        if exclusion is not None and exclusion.shape == best.shape:
            final_neighbor_overlap=float((best & exclusion).sum())/max(1.0,float(best.sum()))
            ex=best & exclusion
            if component.any():
                comp_d=component
                if cv2 is not None:
                    comp_d=cv2.dilate(
                        component.astype(np.uint8),
                        cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(31,31)),1
                    ).astype(bool)
                ex=ex & ~comp_d
            final_neighbor_outside=float(ex.sum())/max(1.0,float(best.sum()))

        meta.update(
            candidate_count=len(candidates),
            selected_method=best_name,
            neighbor_overlap=round(final_neighbor_overlap,4),
            neighbor_outside_overlap=round(final_neighbor_outside,4),
            confidence=conf,
            area_ratio=float(best.mean()),
            candidate_metrics=best_metrics,
            physical_parts_added=added,
        )
        print(
            f"Mask Engine V4.2: method={best_name}, candidates={len(candidates)}, "
            f"area_ratio={best.mean():.4f}, confidence={conf:.3f}, "
            f"neighbor_overlap={final_neighbor_overlap:.3f}, "
            f"neighbor_outside={final_neighbor_outside:.3f}, score={best_score:.3f}"
        )
        return (Image.fromarray((best.astype(np.uint8)*255),mode='L'),meta) if return_meta else Image.fromarray((best.astype(np.uint8)*255),mode='L')
    except Exception as exc:
        meta.update(engine="GrabCut",fallback=True,selected_method="grabcut",
                    fallback_reason=str(exc))
        print(f'WARNING: Mask Engine V4.2 failed, using GrabCut fallback: {exc}')
        out=_grabcut_refine(image,x,y,component,boxes[0])
        return (out,meta) if return_meta else out


def get_smarteraser_inpainter():
    """SmartEraser is unavailable through the generic diffusers API in this build.

    The released checkpoint requires SmartEraser's custom
    StableDiffusionInpaintRegionPipeline from the official repository.
    We explicitly skip it rather than loading weights and failing later.
    """
    global smarteraser_failed
    smarteraser_failed = True
    print("SmartEraser skipped: official custom pipeline is not installed; using RORem.")
    return None


def get_rorem_inpainter():
    """Load LetsThink/RORem without requesting a nonexistent weight variant."""
    global rorem_pipe, rorem_failed
    if rorem_pipe is not None:
        return rorem_pipe
    if rorem_failed:
        return None
    try:
        import torch as _torch
        from diffusers import AutoPipelineForInpainting
        if not _torch.cuda.is_available():
            print("RORem skipped: CUDA is required for client-demo quality.")
            return None

        print("Loading RORem object-removal model (LetsThink/RORem) with native checkpoint weights...")
        last_error = None
        pipe = None

        # The current LetsThink/RORem model card exposes F16 safetensors, but
        # not a separately named `fp16` variant. Requesting variant='fp16'
        # makes Diffusers fail before inference starts.
        for dtype_name, dtype in (("float16", _torch.float16), ("bfloat16", _torch.bfloat16)):
            try:
                print(f"Trying RORem dtype={dtype_name} (no variant override)...")
                pipe = AutoPipelineForInpainting.from_pretrained(
                    "LetsThink/RORem",
                    dtype=dtype,
                    low_cpu_mem_usage=True,
                    use_safetensors=True,
                )
                print(f"RORem checkpoint loaded with dtype={dtype_name}.")
                break
            except Exception as exc:
                last_error = exc
                print(f"RORem dtype={dtype_name} failed: {exc}")
                pipe = None

        if pipe is None:
            raise RuntimeError(f"RORem checkpoint could not be loaded: {last_error}")

        pipe.enable_model_cpu_offload()
        try:
            pipe.enable_vae_slicing()
        except Exception:
            pass
        try:
            pipe.enable_attention_slicing()
        except Exception:
            pass
        rorem_pipe = pipe
        print("RORem object-removal model READY (native checkpoint weights).")
        return rorem_pipe
    except Exception as exc:
        rorem_failed = True
        print(f"WARNING: RORem unavailable: {exc}")
        return None

def _resize_by_short_side(image: Image.Image, mask: Image.Image, short_side: int = 512):
    """Resize image and mask like the official RORem inference code."""
    w, h = image.size
    scale = float(short_side) / max(1, min(w, h))
    nw = max(64, int(round(w * scale)))
    nh = max(64, int(round(h * scale)))
    nw = max(64, (nw // 8) * 8)
    nh = max(64, (nh // 8) * 8)
    return (image.resize((nw, nh), Image.Resampling.BICUBIC),
            mask.resize((nw, nh), Image.Resampling.NEAREST))


def _dilate_binary_mask(mask: Image.Image, pixels: int = 8) -> Image.Image:
    """Small halo for object/contact-shadow edges."""
    if cv2 is None or pixels <= 0:
        return mask.convert("L")
    arr = np.asarray(mask.convert("L"), np.uint8)
    k = max(3, int(pixels) * 2 + 1)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    out = cv2.dilate((arr > 127).astype(np.uint8), kernel, 1) * 255
    return Image.fromarray(out.astype(np.uint8), "L")


def _square_object_crop(image: Image.Image, object_mask: np.ndarray, context_ratio: float = 0.24):
    """Build an object-centred square crop for RORem's native 512x512 regime.

    The previous implementation kept the whole room width for a large bed.
    That made the bed occupy only a small part of the 512px latent and forced
    the base RORem checkpoint to solve a nearly 1024x512 scene. The official
    RORem release states that the base checkpoint is optimal at 512x512.
    A square crop keeps the selected object large while retaining enough wall,
    rug and floor context to reconstruct the room instead of another object.
    """
    H, W = object_mask.shape
    ys, xs = np.where(object_mask)
    if xs.size == 0:
        return image.convert("RGB"), object_mask.copy(), (0, 0)
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    bw, bh = x1 - x0, y1 - y0
    base = max(bw, bh)
    side = int(round(base * (1.0 + 2.0 * context_ratio)))
    side = max(base + 64, side)
    side = min(side, W, H)
    side = max(256, side)

    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0
    cx0 = int(round(cx - side / 2.0))
    cy0 = int(round(cy - side / 2.0))
    cx0 = max(0, min(cx0, W - side))
    cy0 = max(0, min(cy0, H - side))
    cx1, cy1 = cx0 + side, cy0 + side
    return image.crop((cx0, cy0, cx1, cy1)), object_mask[cy0:cy1, cx0:cx1], (cx0, cy0)


def _rorem_remove_candidate(
    pipe, image: Image.Image, object_mask: np.ndarray, seed: int,
    context_ratio: float = 0.20, dilation_px: int | None = None,
    steps: int = 50,
):
    """Run one conservative RORem removal candidate.

    Important: the SAM mask is the *only* final reconstruction region.
    Dilation is used only inside the diffusion model to erase antialiased edges
    and contact shadows. This prevents the removal engine from expanding the
    user's selection.

    RORem's documented limitations include sensitivity to mask dilation, crop
    context, resolution and seed, so Phase 9.3 evaluates several tight
    configurations instead of trusting one 512px pass.
    """
    import torch as _torch
    crop, crop_mask, origin = _square_object_crop(image, object_mask, context_ratio=context_ratio)
    base_mask = Image.fromarray((crop_mask.astype(np.uint8) * 255), "L")

    if dilation_px is None:
        mask_area_ratio = float(crop_mask.mean()) if crop_mask.size else 0.0
        if mask_area_ratio >= 0.20:
            dilation_px = 30
        elif mask_area_ratio >= 0.10:
            dilation_px = 26
        elif mask_area_ratio >= 0.04:
            dilation_px = 22
        else:
            dilation_px = 18
    work_mask = _dilate_binary_mask(base_mask, pixels=int(dilation_px))
    work = crop.convert("RGB").resize((512, 512), Image.Resampling.LANCZOS)
    work_mask = work_mask.resize((512, 512), Image.Resampling.NEAREST)

    # RORem's own inference guidance recommends a content-irrelevant prompt +
    # classifier-free guidance. The previous Phase 9.3 build used guidance_scale=1.0,
    # which effectively disabled CFG and was a major quality regression on room photos.
    prompt = ""
    # The RORem project guidance used by Phase 9.4 is a content-irrelevant
    # prompt + classifier-free guidance. The old build added a long negative
    # prompt containing quality words such as "blurry" and "low detail".
    # Those terms are not a reliable room-surface prior and can over-constrain
    # an object-removal model. Keep the prompt neutral and let candidate gates
    # judge the result instead.
    negative = ""
    generator = _torch.Generator(device="cpu").manual_seed(int(seed))
    try:
        with _torch.inference_mode():
            out = pipe(
                prompt=prompt,
                negative_prompt=negative,
                height=512,
                width=512,
                image=work,
                mask_image=work_mask,
                guidance_scale=7.5,
                num_inference_steps=int(steps),
                strength=0.999,
                generator=generator,
            ).images[0].convert("RGB")
    except Exception:
        if _torch.cuda.is_available():
            _torch.cuda.empty_cache()
        raise

    restored = out.resize(crop.size, Image.Resampling.LANCZOS)
    placed = _place_crop(image, restored, origin)
    halo_full = np.zeros_like(object_mask, bool)
    hm = np.asarray(work_mask.resize(crop.size, Image.Resampling.NEAREST), np.uint8) > 127
    oy, ox = origin[1], origin[0]
    hh, ww = crop.size[1], crop.size[0]
    halo_full[oy:oy+hh, ox:ox+ww] = hm
    # Score on the dilated internal candidate, but the caller's final composite
    # remains constrained to the confirmed SAM mask.
    candidate = _composite_inside_mask(
        image, placed, Image.fromarray((halo_full.astype(np.uint8) * 255), "L")
    )
    return candidate, halo_full


def _lama_remove_candidate(image: Image.Image, object_mask: np.ndarray, mask_img: Image.Image):
    """Run LaMa on a local square crop instead of the entire room.

    Large-room LaMa passes dilute a large furniture hole across the whole frame.
    A local crop gives the model more pixels per metre of wall/floor/rug while
    preserving the original image outside the confirmed mask.
    """
    crop, crop_mask, origin = _square_object_crop(image, object_mask, context_ratio=0.30)
    # Keep LaMa's working mask conservative: enough halo to remove edge residue,
    # but not so much that the donor context is swallowed.
    local_mask = _dilate_binary_mask(Image.fromarray((crop_mask.astype(np.uint8) * 255), "L"), pixels=18)
    result = get_lama()(crop.convert("RGB"), local_mask)
    placed = _place_crop(image, result.resize(crop.size, Image.Resampling.LANCZOS), origin)
    # Never use the LaMa halo as the final user-visible mask.
    return _composite_inside_mask(image, placed, mask_img), origin


def _target_change_score(original: Image.Image, candidate: Image.Image, mask: np.ndarray) -> float:
    """Measure how much the candidate actually changed the selected object.

    Returns 0..1. This deliberately evaluates ONLY the original confirmed
    object mask, not the dilated context halo used internally by RORem.
    A removal candidate that barely changes the bed must never pass merely
    because the surrounding seam looks good.
    """
    src = np.asarray(original.convert("RGB"), dtype=np.float32)
    out = np.asarray(candidate.convert("RGB").resize(original.size, Image.Resampling.LANCZOS), dtype=np.float32)
    m = np.asarray(mask, dtype=bool)
    if m.shape != src.shape[:2]:
        m = np.asarray(Image.fromarray((m.astype(np.uint8) * 255), "L").resize(original.size, Image.Resampling.NEAREST), dtype=np.uint8) > 127
    if not m.any():
        return 0.0
    delta = np.abs(src - out).mean(axis=2) / 255.0
    mean_change = float(np.mean(delta[m]))
    changed_fraction = float(np.mean(delta[m] > (12.0 / 255.0)))
    # Mean color change catches large residual ghosts; changed-pixel fraction
    # catches the case where only a few details were altered.
    return float(np.clip(0.70 * mean_change + 0.30 * changed_fraction, 0.0, 1.0))


def _rorem_quality_candidate(original: Image.Image, candidate: Image.Image, mask: np.ndarray):
    """Return a score that heavily penalizes surviving furniture."""
    score, seam, fp = _candidate_quality(original, candidate, Image.fromarray((mask.astype(np.uint8) * 255), "L"))
    # RORem is a removal model; for this product, visible furniture inside the
    # selected hole is much worse than a small boundary-color mismatch.
    total = float(seam + 420.0 * fp)
    return total, seam, fp


def get_powerpaint_inpainter():
    """Deprecated in Phase 5.2.16: replacement/generative backends are not used by Remove AI."""
    return None


def _powerpaint_remove(*args, **kwargs):
    raise RuntimeError("PowerPaint is disabled for the remove-only workflow")

def get_diffusion_inpainter():
    """Load the actual diffusion inpainting backend, or return None with a clear diagnostic."""
    global diffusion_pipe, diffusion_failed
    if diffusion_pipe is not None:
        return diffusion_pipe
    if diffusion_failed:
        return None
    try:
        import torch as _torch
        from diffusers import StableDiffusionInpaintPipeline
        device = "cuda" if _torch.cuda.is_available() else "cpu"
        dtype = _torch.float16 if device == "cuda" else _torch.float32
        print(f"Loading REQUIRED generative inpainting model (device={device})...")
        pipe = StableDiffusionInpaintPipeline.from_pretrained(
            "stable-diffusion-v1-5/stable-diffusion-inpainting",
            torch_dtype=dtype,
            safety_checker=None,
            requires_safety_checker=False,
        )
        if device == "cuda":
            pipe.enable_model_cpu_offload()
            try:
                pipe.enable_attention_slicing()
            except Exception:
                pass
            try:
                pipe.enable_vae_slicing()
            except Exception:
                pass
        else:
            pipe.to(device)
        diffusion_pipe = pipe
        print("Generative inpainting model READY.")
        return diffusion_pipe
    except Exception as exc:
        diffusion_failed = True
        print(f"ERROR: generative inpainting unavailable: {exc}")
        print("Install/update requirements with: pip install -r server\\requirements.txt")
        return None


def _seam_score(original_crop: Image.Image, candidate: Image.Image, mask_crop: Image.Image) -> float:
    """Robust boundary continuity score. Lower is better.

    This deliberately never uses a 1e6 sentinel for a normal thin/irregular
    mask. It samples visible pixels just outside the hole and compares them to
    generated pixels immediately inside the hole. A very large value is used
    only for an actual shape/size error.
    """
    if cv2 is None:
        return 0.0
    a = np.asarray(mask_crop.convert("L"), np.uint8) > 127
    src = np.asarray(original_crop.convert("RGB"), np.uint8)
    gen = np.asarray(candidate.convert("RGB"), np.uint8)
    if src.shape != gen.shape or src.shape[:2] != a.shape:
        return 1e6
    if not a.any():
        return 1e6

    # One-pixel contour inside and a short visible band outside.
    er = cv2.erode(a.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(3,3)), 1).astype(bool)
    inner = a & ~er
    dil = cv2.dilate(a.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(9,9)), 1).astype(bool)
    outer = dil & ~a
    if not inner.any() or not outer.any():
        # Boundary-touching masks can have no exterior context in the crop.
        # Use a valid low-weight interior texture statistic instead of 1e6.
        inner = a
        if not outer.any():
            return float(np.std(cv2.cvtColor(gen, cv2.COLOR_RGB2GRAY)[inner])) * 0.15

    src_g = cv2.cvtColor(src, cv2.COLOR_RGB2GRAY).astype(np.float32)
    gen_g = cv2.cvtColor(gen, cv2.COLOR_RGB2GRAY).astype(np.float32)
    src_blur = cv2.GaussianBlur(src_g,(0,0),2.0)
    gen_blur = cv2.GaussianBlur(gen_g,(0,0),2.0)

    # Compare robust medians and local gradients.
    lum_gap = abs(float(np.median(gen_g[inner])) - float(np.median(src_g[outer])))
    tex_gap = abs(float(np.std(gen_blur[inner])) - float(np.std(src_blur[outer])))

    sx=cv2.Sobel(src_g,cv2.CV_32F,1,0,ksize=3); sy=cv2.Sobel(src_g,cv2.CV_32F,0,1,ksize=3)
    gx=cv2.Sobel(gen_g,cv2.CV_32F,1,0,ksize=3); gy=cv2.Sobel(gen_g,cv2.CV_32F,0,1,ksize=3)
    sg=cv2.magnitude(sx,sy); gg=cv2.magnitude(gx,gy)
    grad_gap=abs(float(np.median(gg[inner]))-float(np.median(sg[outer])))

    # Excess edge energy is a strong signal for hallucinated furniture.
    deep = er & a
    if not deep.any(): deep=a
    edge_excess=max(0.0,float(np.mean(gg[deep]))-max(1.8*float(np.mean(sg[outer])),float(np.mean(sg[outer]))+5.0))
    return float(lum_gap + 0.45*tex_gap + 0.30*grad_gap + 0.70*edge_excess)

def _square_working_crop(crop: Image.Image, mask: np.ndarray, target: int = 512):
    """Return square 512x512 image/mask plus geometry needed to undo padding."""
    cw, ch = crop.size
    side = max(cw, ch)
    pad_left = (side - cw) // 2
    pad_right = side - cw - pad_left
    pad_top = (side - ch) // 2
    pad_bottom = side - ch - pad_top
    crop_np = np.asarray(crop.convert("RGB"))
    if cv2 is not None:
        square_np = cv2.copyMakeBorder(
            crop_np, pad_top, pad_bottom, pad_left, pad_right,
            borderType=cv2.BORDER_REFLECT_101
        )
    else:
        square = Image.new("RGB", (side, side))
        square.paste(crop, (pad_left, pad_top))
        square_np = np.asarray(square)
    square_mask = np.zeros((side, side), dtype=np.uint8)
    square_mask[pad_top:pad_top+ch, pad_left:pad_left+cw] = mask
    return (
        Image.fromarray(square_np).resize((target, target), Image.Resampling.LANCZOS),
        Image.fromarray(square_mask).resize((target, target), Image.Resampling.NEAREST),
        (side, pad_left, pad_top, cw, ch),
    )


def _restore_square(result_sq: Image.Image, geometry):
    side, pad_left, pad_top, cw, ch = geometry
    arr = np.asarray(result_sq.convert("RGB"))
    if cv2 is not None:
        arr = cv2.resize(arr, (side, side), interpolation=cv2.INTER_LANCZOS4)
    else:
        arr = np.asarray(Image.fromarray(arr).resize((side, side), Image.Resampling.LANCZOS))
    arr = arr[pad_top:pad_top+ch, pad_left:pad_left+cw]
    return Image.fromarray(arr.astype(np.uint8), "RGB")


def _furniture_hallucination_score(candidate: Image.Image, target_mask: np.ndarray) -> float:
    """Estimate how much furniture the generated hole contains.

    SegFormer is already loaded for surface classification, so reuse it as a
    cheap semantic guardrail. The score is the fraction of the confirmed
    reconstruction mask that SegFormer labels as furniture. Lower is better.
    It is intentionally a penalty, not a hard classifier: a few boundary
    pixels can be mislabeled in a photograph.
    """
    try:
        _, sp = get_pipes()
        arr = np.asarray(target_mask, bool)
        if not arr.any():
            return 1.0
        results = sp(candidate.convert("RGB"))
        H, W = arr.shape
        furniture = np.zeros((H, W), bool)
        for r in results:
            label = str(r.get("label", "")).lower().strip()
            if label not in ADE_FURNITURE:
                continue
            # SegFormer can assign low-confidence furniture labels to large
            # texture regions (rug/wall/floor). For quality gating, only count
            # reasonably confident semantic detections.
            confidence = float(r.get("score", 1.0) or 1.0)
            if confidence < 0.65:
                continue
            rm = r.get("mask")
            if rm is None:
                continue
            m = mask_array(rm, (W, H))
            furniture |= np.asarray(m, bool)
        return float((furniture & arr).sum() / max(1, arr.sum()))
    except Exception as exc:
        # Never make reconstruction fail because the optional semantic
        # validation failed. A neutral score simply disables this penalty.
        print(f"WARNING: furniture hallucination check unavailable: {exc}")
        return 0.0


def _diffusion_surface_pass(pipe, crop: Image.Image, target_mask: np.ndarray,
                            prompt: str, negative: str, seed: int):
    """One tightly constrained diffusion pass for one room surface."""
    import torch as _torch
    work, work_mask, geometry = _square_working_crop(crop, target_mask, 512)
    generator = _torch.Generator(device="cpu").manual_seed(seed)
    with _torch.inference_mode():
        out = pipe(
            prompt=prompt,
            negative_prompt=negative,
            image=work,
            mask_image=work_mask,
            num_inference_steps=32,
            guidance_scale=5.5,
            strength=1.0,
            generator=generator,
        ).images[0].convert("RGB")
    if out.size != work.size:
        out = out.resize(work.size, Image.Resampling.LANCZOS)
    seam = _seam_score(work, out, work_mask)
    hallucination = _furniture_hallucination_score(out, np.asarray(work_mask.convert("L")) > 127)
    # A visible piece of furniture inside a surface reconstruction is much
    # worse than a small color/texture mismatch. Make it dominate candidate
    # selection while still allowing minor segmentation noise.
    score = float(seam + 180.0 * hallucination)
    return _restore_square(out, geometry), score, seam, hallucination


def _compose_candidate(original: Image.Image, generated: Image.Image, mask_img: Image.Image) -> Image.Image:
    """Composite a generated full-image candidate strictly inside the mask."""
    src=np.asarray(original.convert("RGB"),np.float32)
    gen=np.asarray(generated.convert("RGB").resize(original.size,Image.Resampling.LANCZOS),np.float32)
    m=np.asarray(mask_img.convert("L").resize(original.size,Image.Resampling.NEAREST),np.uint8)>127
    alpha=m.astype(np.float32)
    if cv2 is not None:
        # Only a very small feather; never leak into neighbouring furniture.
        alpha=cv2.GaussianBlur(alpha,(0,0),0.65)
        alpha*=m
    out=src*(1-alpha[...,None])+gen*alpha[...,None]
    return Image.fromarray(np.clip(out,0,255).astype(np.uint8))


def _candidate_quality(original: Image.Image, candidate: Image.Image, mask_img: Image.Image):
    """Return quality tuple used for real candidate selection."""
    m=np.asarray(mask_img.convert("L"),np.uint8)>127
    ys,xs=np.where(m)
    if xs.size==0: return 1e6,1e6,1.0
    x0,x1=max(0,int(xs.min())-32),min(original.width,int(xs.max())+33)
    y0,y1=max(0,int(ys.min())-32),min(original.height,int(ys.max())+33)
    oc=original.crop((x0,y0,x1,y1)); cc=candidate.crop((x0,y0,x1,y1)); mc=mask_img.crop((x0,y0,x1,y1))
    seam=_seam_score(oc,cc,mc)
    # SegFormer needs actual visible wall/floor to tell a large reconstructed
    # region apart from real furniture. The tight seam crop above is mostly
    # hole with only a ~32px visible margin — for a large object that is an
    # out-of-distribution input for a scene segmentation model and measurably
    # biases it toward guessing "furniture" even on a clean fill (observed in
    # testing: two different backends and three different seeds all scored
    # furniture_penalty in the same 0.77-0.80 band on one large-hole removal,
    # which independent hallucinations would not do). Classify from a much
    # wider context crop instead; only pixels inside the confirmed mask are
    # ever counted, so this cannot start counting a real neighbouring object.
    wide_candidate, wide_mask_arr, _ = _local_object_crop(candidate, m, pad_ratio=1.4)
    furniture=_furniture_hallucination_score(wide_candidate, wide_mask_arr)
    return float(seam+260.0*furniture), float(seam), float(furniture)


def _local_object_crop(image: Image.Image, object_mask: np.ndarray, pad_ratio: float = 0.85):
    """Return a generous context crop around the object, preserving the full hole."""
    H, W = object_mask.shape
    ys, xs = np.where(object_mask)
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    bw, bh = x1 - x0, y1 - y0
    px = max(64, int(bw * pad_ratio))
    py = max(64, int(bh * pad_ratio))
    cx0, cy0 = max(0, x0 - px), max(0, y0 - py)
    cx1, cy1 = min(W, x1 + px), min(H, y1 + py)
    return image.crop((cx0, cy0, cx1, cy1)), object_mask[cy0:cy1, cx0:cx1], (cx0, cy0)


def _place_crop(base: Image.Image, crop: Image.Image, origin):
    out = base.copy()
    out.paste(crop.convert("RGB"), origin)
    return out


def _semantic_furniture_shield(image: Image.Image, target_mask: np.ndarray) -> np.ndarray:
    """Find visible furniture that should NOT be used as donor context.

    Remove AI must reconstruct the hidden room, not copy neighboring furniture
    into the hole. We therefore make a temporary *context shield* around
    furniture outside the selected object. The shield is used only as input to
    LaMa; the original pixels are restored byte-for-byte in the final image.
    """
    if cv2 is None:
        return np.zeros_like(target_mask, bool)
    try:
        _, sp = get_pipes()
        H, W = target_mask.shape
        shield = np.zeros((H, W), bool)
        for r in sp(image.convert("RGB")):
            label = str(r.get("label", "")).lower().strip()
            if label not in ADE_FURNITURE:
                continue
            score = float(r.get("score", 1.0) or 1.0)
            if score < 0.50:
                continue
            rm = r.get("mask")
            if rm is None:
                continue
            m = mask_array(rm, (W, H))
            # Only shield visible furniture outside the selected object.
            m = np.asarray(m, bool) & ~target_mask
            if int(m.sum()) >= 180:
                shield |= m
        # Give the shield a small safety halo so LaMa does not pull furniture
        # edges into the reconstruction. Never change the user's actual mask.
        if shield.any():
            shield = cv2.dilate(
                shield.astype(np.uint8),
                cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)),
                iterations=1,
            ) > 0
            shield &= ~target_mask
        return shield
    except Exception as exc:
        print(f"WARNING: furniture context shield unavailable: {exc}")
        return np.zeros_like(target_mask, bool)


def _build_remove_only_context(image: Image.Image, target_mask: np.ndarray) -> Image.Image:
    """Create a temporary furniture-neutral context for object removal.

    This is NOT the output image. It exists only to stop LaMa from seeing a
    neighboring sofa/table/bed and hallucinating that furniture into the hole.
    The final composite always comes from the original image outside target.
    """
    shield = _semantic_furniture_shield(image, target_mask)
    if not shield.any():
        return image.convert("RGB")
    context = _multiscale_cv_inpaint(image.convert("RGB"), shield, radius=3.0)
    print(f"Remove-only context shield: {int(shield.sum())}px of neighboring furniture hidden from donor context")
    return context


def _surface_donor_context(image: Image.Image, object_mask: np.ndarray,
                           surface_map: np.ndarray, surface_id: int) -> Image.Image:
    """Make a donor image dominated by the surface being reconstructed.

    The selected object is already absent from ``image`` when this helper is
    called.  We also neutralize clearly different surfaces inside the local
    object neighborhood.  This is a donor-context trick only: the final image
    is always composited over the untouched original outside the target mask.
    """
    if cv2 is None:
        return image.convert("RGB")
    src = np.asarray(image.convert("RGB"), np.uint8)
    shield = _semantic_furniture_shield(image, object_mask)
    keep = (surface_map == surface_id) & ~object_mask & ~shield
    # Start from a heavily blurred copy so non-target surfaces cannot provide
    # sharp furniture/edge donors.  Same-surface pixels remain untouched.
    blurred = cv2.GaussianBlur(src, (0, 0), 11.0)
    out = src.copy()
    out[~keep] = blurred[~keep]
    return Image.fromarray(out, "RGB")

def _planar_wall_fill(image: Image.Image, target: np.ndarray) -> Image.Image:
    """Deterministic wall reconstruction using visible same-row wall pixels.

    Walls in room photos are usually locally planar and slowly varying in
    color.  For this surface we should not ask an inpainting network to invent
    texture.  Interpolating between real wall pixels preserves the actual
    paint tone and avoids the soft brown/grey blob produced by large-hole
    LaMa/OpenCV passes.
    """
    if cv2 is None or not target.any():
        return image.convert("RGB")
    src=np.asarray(image.convert("RGB"),np.float32)
    out=src.copy(); H,W=target.shape
    wall_target=target.copy()
    # Lightly close tiny gaps so each row has a stable boundary.
    for y in range(H):
        xs=np.where(wall_target[y])[0]
        if xs.size<2: continue
        x0,x1=int(xs.min()),int(xs.max())
        # Real wall samples immediately outside the hole, plus a small robust
        # horizontal window to reduce lamp/object contamination.
        left=max(0,x0-18); right=min(W-1,x1+18)
        L=np.where(~wall_target[y,left:x0])[0]
        R=np.where(~wall_target[y,x1+1:right+1])[0]
        if L.size and R.size:
            lx=left+int(L[-1]); rx=x1+1+int(R[0])
            if rx>lx:
                lc=np.median(src[max(0,y-2):min(H,y+3),max(0,lx-3):min(W,lx+4)],axis=(0,1))
                rc=np.median(src[max(0,y-2):min(H,y+3),max(0,rx-3):min(W,rx+4)],axis=(0,1))
                span=rx-lx
                for x in range(x0,x1+1):
                    if wall_target[y,x]:
                        t=(x-lx)/span
                        # Preserve a gentle illumination gradient instead of
                        # producing a flat single-color rectangle.
                        out[y,x]=lc*(1-t)+rc*t
        elif L.size:
            lx=left+int(L[-1]); out[y,x0:x1+1]=src[y,lx]
        elif R.size:
            rx=x1+1+int(R[0]); out[y,x0:x1+1]=src[y,rx]
    # Small-radius NS pass only at the contour to blend one-pixel seams.
    contour=cv2.dilate(wall_target.astype(np.uint8),np.ones((3,3),np.uint8),1).astype(bool) & ~wall_target
    if contour.any():
        repaired=cv2.inpaint(np.clip(out,0,255).astype(np.uint8),(contour.astype(np.uint8)*255),2.0,cv2.INPAINT_NS)
        # Do not alter the interior interpolation with this pass.
        out[contour]=repaired[contour]
    return Image.fromarray(np.clip(out,0,255).astype(np.uint8),'RGB')

def _smart_quality(original: Image.Image, candidate: Image.Image, mask: np.ndarray):
    full=Image.fromarray((mask.astype(np.uint8)*255),'L')
    score,seam,fp=_candidate_quality(original,candidate,full)
    change=_target_change_score(original,candidate,mask)
    # Reward actual erasure while penalising furniture/ghosts. The change term
    # is capped so a hallucinated replacement cannot win merely by changing more pixels.
    total=float(seam + 260.0*fp + 160.0*max(0.0, 0.18-change))
    return total,seam,fp,change


def _residual_structure_score(candidate: Image.Image, object_mask: np.ndarray) -> float:
    """Estimate whether a furniture-like structural pattern survived inside the hole.

    SegFormer catches semantic furniture, but it can miss a generated sofa/bed
    when the hallucination has low confidence. This second signal is deliberately
    lightweight: compare edge density inside the confirmed removal mask with a
    nearby context ring. A reconstructed wall/floor/rug should not suddenly have
    a much higher concentration of strong edges than its surroundings.

    Returns 0..1, where lower is better. It is a ranking signal, not a standalone
    detector, so textured rugs and wood grain do not get rejected by themselves.

    Phase 14 optimizer-stability fix: the previous floor (`max(0.055, outer *
    1.35)`) made "excess" mathematically unreachable for almost any real input.
    Verified with synthetic test images before changing this: a flat, edgeless
    fill correctly scored 0 (expected), but so did a deliberately drawn sharp
    rectangular outline standing in for a leftover furniture edge — the exact
    case this function exists to catch. The absolute floor and relative
    multiplier are both lowered so genuine excess edge density can register;
    print output below exposes inner/outer/floor so this can be tuned further
    against real RORem/LaMa output once GPU testing is available.
    """
    try:
        if cv2 is None:
            return 0.0
        m = np.asarray(object_mask, bool)
        if not m.any():
            return 0.0
        gray = np.asarray(candidate.convert("L"), np.uint8)
        if gray.shape != m.shape:
            gray = np.asarray(candidate.convert("L").resize((m.shape[1], m.shape[0]), Image.Resampling.BILINEAR), np.uint8)
        edges = cv2.Canny(gray, 60, 140) > 0
        inner = float(edges[m].mean())
        ring = cv2.dilate(m.astype(np.uint8), np.ones((21, 21), np.uint8), iterations=1).astype(bool) & ~m
        if not ring.any():
            return 0.0
        outer = float(edges[ring].mean())
        floor = max(REMOVAL_GATE_THRESHOLDS["residual_structure_abs_floor"], outer * REMOVAL_GATE_THRESHOLDS["residual_structure_rel_multiplier"])
        excess = max(0.0, inner - floor)
        print(f"    [residual_structure] inner={inner:.4f} outer={outer:.4f} floor={floor:.4f} excess={excess:.4f}")
        return float(np.clip(excess / 0.20, 0.0, 1.0))
    except Exception as exc:
        print(f"WARNING: residual structure check unavailable: {exc}")
        return 0.0


def _texture_ratio(original: Image.Image, candidate: Image.Image, mask_img: Image.Image) -> float:
    """Compare the generated region's local texture detail against the real,
    untouched texture immediately surrounding it (a ring just outside the
    mask). 1.0+ means as detailed as the real surroundings or more; near 0.0
    means a flat/blurry fill — a known failure mode where a reconstruction
    engine gives up on a large hole and returns a smooth, low-frequency patch.
    That failure scores a near-perfect seam by definition (nothing sharp
    means nothing discontinuous) and can also score a low furniture_penalty
    (nothing furniture-shaped either) while still being obviously wrong to a
    person, since real floor/rug/wall has local detail a lazy fill does not
    reproduce. Uses Laplacian variance, a standard blur-detection measure.
    """
    if cv2 is None:
        return 1.0
    try:
        m = np.asarray(mask_img.convert("L"), np.uint8) > 127
        if m.sum() < 50:
            return 1.0
        candidate_rgb = candidate.convert("RGB")
        if candidate_rgb.size != original.size:
            candidate_rgb = candidate_rgb.resize(original.size, Image.Resampling.LANCZOS)
        gray_gen = cv2.cvtColor(np.asarray(candidate_rgb), cv2.COLOR_RGB2GRAY)
        gray_orig = cv2.cvtColor(np.asarray(original.convert("RGB")), cv2.COLOR_RGB2GRAY)
        lap_gen = cv2.Laplacian(gray_gen, cv2.CV_64F)
        lap_orig = cv2.Laplacian(gray_orig, cv2.CV_64F)
        inside_var = float(lap_gen[m].var())
        ring = cv2.dilate(m.astype(np.uint8), np.ones((25, 25), np.uint8)).astype(bool) & ~m
        if ring.sum() < 50:
            return 1.0
        outside_var = float(lap_orig[ring].var())
        if outside_var < 1e-6:
            return 1.0
        ratio = float(inside_var / outside_var)
        print(f"    [texture_ratio] inside_var={inside_var:.1f} outside_var={outside_var:.1f} ratio={ratio:.3f}")
        return ratio
    except Exception as exc:
        print(f"WARNING: texture ratio check unavailable: {exc}")
        return 1.0


def _surface_consistency_score(original: Image.Image, candidate: Image.Image, object_mask: np.ndarray) -> float:
    """Measure low-frequency color/luminance agreement with visible context.

    The previous texture signal used Laplacian variance only. A dark glossy or
    otherwise smooth hallucination can have *more* high-frequency variance than
    the surrounding surface because of a few specular/edge transitions, while
    still being the wrong material and brightness. This signal deliberately
    ignores high-frequency detail: it compares blurred LAB statistics inside
    the reconstructed hole with a nearby untouched ring from the original photo.

    Returns 0..1, where 1 means the reconstructed region has a color/luminance
    distribution compatible with the immediately visible surrounding surface.
    It is a gate guardrail, not a claim of perceptual correctness.
    """
    if cv2 is None:
        return 1.0
    try:
        m = np.asarray(object_mask, bool)
        if not m.any():
            return 1.0
        orig = np.asarray(original.convert("RGB"), np.uint8)
        cand = np.asarray(candidate.convert("RGB"), np.uint8)
        if cand.shape[:2] != m.shape:
            cand = np.asarray(candidate.convert("RGB").resize((m.shape[1], m.shape[0]), Image.Resampling.BILINEAR), np.uint8)
        lab_orig = cv2.cvtColor(orig, cv2.COLOR_RGB2LAB).astype(np.float32)
        lab_cand = cv2.cvtColor(cand, cv2.COLOR_RGB2LAB).astype(np.float32)
        # Low-frequency structure is the relevant signal here; suppress the
        # exact texture-frequency failure mode already covered by Laplacian.
        lab_orig = cv2.GaussianBlur(lab_orig, (0, 0), 3.0)
        lab_cand = cv2.GaussianBlur(lab_cand, (0, 0), 3.0)
        ring = cv2.dilate(m.astype(np.uint8), np.ones((31, 31), np.uint8), iterations=1).astype(bool) & ~m
        if ring.sum() < 50:
            return 1.0

        similarities = []
        for channel in range(3):
            inside = lab_cand[:, :, channel][m]
            context = lab_orig[:, :, channel][ring]
            inside_median = float(np.median(inside))
            context_median = float(np.median(context))
            q25, q75 = np.percentile(context, [25, 75])
            scale = max(5.0, float(q75 - q25))
            similarities.append(float(np.exp(-abs(inside_median - context_median) / (1.5 * scale))))

        # L carries most of the material/illumination information; chroma is
        # useful for catching a gray/green/brown material swap without making
        # color matching overly dominant.
        score = float(0.55 * similarities[0] + 0.25 * similarities[1] + 0.20 * similarities[2])
        print(
            f"    [surface_consistency] L={similarities[0]:.3f} a={similarities[1]:.3f} "
            f"b={similarities[2]:.3f} score={score:.3f}"
        )
        return float(np.clip(score, 0.0, 1.0))
    except Exception as exc:
        print(f"WARNING: surface consistency check unavailable: {exc}")
        return 1.0


def _removal_quality_score(original: Image.Image, candidate: Image.Image, object_mask: np.ndarray):
    """Score an object-removal candidate for the actual product objective."""
    full_mask = Image.fromarray((object_mask.astype(np.uint8) * 255), "L")
    _, seam, fp = _candidate_quality(original, candidate, full_mask)
    change = _target_change_score(original, candidate, object_mask)
    residual = _residual_structure_score(candidate, object_mask)
    texture_ratio = _texture_ratio(original, candidate, full_mask)
    surface_consistency = _surface_consistency_score(original, candidate, object_mask)
    texture_threshold = REMOVAL_GATE_THRESHOLDS["min_texture_ratio"]
    texture_penalty = max(0.0, texture_threshold - min(texture_ratio, texture_threshold)) / texture_threshold
    surface_penalty = max(0.0, REMOVAL_GATE_THRESHOLDS["min_surface_consistency"] - surface_consistency)
    # Target change remains a ranking signal, but is no longer a blanket minimum:
    # a correct reconstruction can legitimately be close to the source pixels.
    # It only contributes a penalty when the candidate looks like a no-op.
    noop_change = REMOVAL_GATE_THRESHOLDS["noop_target_change"]
    change_penalty = max(0.0, noop_change - change) / max(noop_change, 1e-6)
    total = float(
        seam
        + 95.0 * fp
        + 190.0 * residual
        + 80.0 * change_penalty
        + 180.0 * texture_penalty
        + 140.0 * surface_penalty
    )
    return total, seam, fp, change, residual, texture_ratio, surface_consistency


def _candidate_passes_removal_gate(metrics):
    total, seam, fp, change, residual, texture_ratio, surface_consistency = metrics
    t = REMOVAL_GATE_THRESHOLDS
    # A low target-change value is not itself a failure anymore. It becomes a
    # failure only when the result is also semantically/structurally unchanged.
    # Target change is now only a no-op detector. A 0.163 change can be a
    # perfectly valid reconstruction; a ~0.0 change means the selected pixels
    # were effectively left untouched and must not be reported as a removal.
    return bool(
        change >= t["noop_target_change"]
        and seam <= t["max_seam"]
        and fp <= t["max_furniture_penalty"]
        and residual <= t["max_residual_structure"]
        and texture_ratio >= t["min_texture_ratio"]
        and surface_consistency >= t["min_surface_consistency"]
    )


def _remove_only_large_object(image: Image.Image, object_mask: np.ndarray) -> dict | None:
    """Phase 9.3 large-object removal ensemble.

    Three tight RORem configurations are evaluated. The winning candidate is
    chosen from target-change, residual structure, seam continuity and texture
    instead of allowing a low SegFormer furniture score to hide a partial
    deletion. A local LaMa recovery is handled by ``hybrid_inpaint`` if none
    of the RORem candidates clears the visual gate.
    """
    if not object_mask.any():
        return None
    src = image.convert("RGB")
    pipe = get_rorem_inpainter()
    if pipe is None:
        print("REMOVE-ONLY large-object removal rejected: RORem unavailable")
        return None

    # Tight/medium/wide contexts. Smaller dilation protects nearby floor/rug/wall
    # while the wider variant catches thin legs and antialiased silhouettes.
    mask_ratio = float(object_mask.mean())
    if mask_ratio >= 0.055:
        # Large beds/sofas are a different regime: the previous 0.14-0.28
        # contexts left too little visible room surface around a large hole.
        # Add wider-context candidates while retaining the proven tight ones.
        configs = (
            (2026, 0.14, 0, 40),
            (7319, 0.20, 6, 40),
            (11037, 0.28, 12, 40),
            (17041, 0.40, 4, 40),
            (24019, 0.52, 6, 40),
        )
    else:
        configs = (
            (2026, 0.14, 0, 40),
            (7319, 0.20, 6, 40),
            (11037, 0.28, 12, 40),
        )
    candidates = []
    for seed, context_ratio, dilation_px, steps in configs:
        try:
            cand, effective_mask = _rorem_remove_candidate(
                pipe, src, object_mask, seed, context_ratio=context_ratio,
                dilation_px=dilation_px, steps=steps
            )
            _phase97_capture_image(f"rorem_raw_seed{seed}.png", cand)
            metrics = _removal_quality_score(src, cand, object_mask)
            print(
                f"Phase9.3 RORem seed={seed} ctx={context_ratio:.2f} dil={dilation_px}: "
                f"score={metrics[0]:.2f}, seam={metrics[1]:.2f}, "
                f"furniture_penalty={metrics[2]:.4f}, target_change={metrics[3]:.3f}, "
                f"residual_structure={metrics[4]:.3f}, texture_ratio={metrics[5]:.2f}, surface_consistency={metrics[6]:.3f}"
            )
            candidates.append((*metrics, cand, seed, effective_mask))
        except Exception as exc:
            print(f"Phase9.3 RORem seed={seed} failed: {exc}")

    if not candidates:
        return None

    # Prefer candidates that clear the actual removal gate. Among passing
    # candidates, use the visual score. Otherwise choose the strongest candidate
    # by removal evidence rather than automatically returning the least-bad ghost.
    passing = [c for c in candidates if _candidate_passes_removal_gate(c[:7])]
    if passing:
        best = min(passing, key=lambda c: (c[0], -c[3], c[4], c[1]))
        passed = True
    else:
        # Lexicographic fallback: actual target change first, then residual
        # structure, then score. This fixes the observed ``change=0.255`` result
        # winning over a materially more complete removal.
        # No candidate is visually acceptable. Choose the least-bad candidate
        # using a hard floor on texture/detail before target-change tie-breaking.
        # This prevents a high-change but visibly smeared result from winning
        # simply because it changed more pixels.
        def _failed_candidate_key(c):
            total, seam, fp, change, residual, texture, surface_consistency = c[:7]
            texture_deficit = max(0.0, 0.28 - texture)
            surface_deficit = max(0.0, REMOVAL_GATE_THRESHOLDS["min_surface_consistency"] - surface_consistency)
            return (texture_deficit * 260.0 + surface_deficit * 220.0 + residual * 150.0 + 80.0 * fp
                    + seam + max(0.0, 0.36 - change) * 180.0,
                    -change, -texture)
        best = min(candidates, key=_failed_candidate_key)
        passed = False

    total, seam, fp, change, residual, texture_ratio, surface_consistency, cand, seed, effective_mask = best
    print(
        f"Phase9.3 RORem selected seed={seed}: score={total:.2f}, seam={seam:.2f}, "
        f"furniture_penalty={fp:.4f}, target_change={change:.3f}, "
        f"residual_structure={residual:.3f}, texture_ratio={texture_ratio:.2f}, "
        f"passed_gate={passed}"
    )
    return {
        "image": cand,
        "passed_gate": passed,
        "backend": "rorem-phase9.3-ensemble",
        "seam": seam,
        "furniture_penalty": fp,
        "texture_ratio": texture_ratio,
        "surface_consistency": surface_consistency,
        "target_change": change,
        "residual_structure": residual,
        "score": total,
    }

def get_lama():
    global lama
    if lama is None:
        print("Loading LaMa inpainting model...")
        from simple_lama_inpainting import SimpleLama
        lama = SimpleLama()
        print("LaMa ready.")
    return lama


def to_data_url(img: Image.Image, max_side: int | None = None) -> str:
    out = img.convert("RGBA")
    if max_side:
        scale = min(1.0, max_side / max(out.size))
        if scale < 1:
            out = out.resize((max(1, int(out.width * scale)), max(1, int(out.height * scale))), Image.Resampling.BILINEAR)
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def read_image(file: UploadFile) -> Image.Image:
    try:
        raw = file.file.read()
        image = Image.open(io.BytesIO(raw)).convert("RGB")
        if image.width < 64 or image.height < 64:
            raise ValueError("image too small")
        return image
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Image invalide: {exc}") from exc


def resize_for_analysis(image: Image.Image) -> Image.Image:
    scale = min(1.0, MAX_ANALYSIS_SIDE / max(image.size))
    if scale >= 1:
        return image
    return image.resize(
        (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
        Image.Resampling.LANCZOS,
    )


def mask_array(mask, size: Tuple[int, int], threshold: int = 128) -> np.ndarray:
    if isinstance(mask, Image.Image):
        gray = mask.convert("L").resize(size, Image.Resampling.NEAREST)
        arr = np.asarray(gray)
    else:
        arr = np.asarray(mask)
        if arr.ndim > 2:
            arr = arr.squeeze()
        if arr.shape[::-1] != size:
            gray = Image.fromarray(arr.astype(np.uint8)).resize(size, Image.Resampling.NEAREST)
            arr = np.asarray(gray)
    return arr > threshold


def percentile_pair(values: np.ndarray, low_q=0.10, high_q=0.90):
    values = values[np.isfinite(values)]
    values = values[values > 0]
    if values.size < 10:
        return 0.0, 1.0
    lo, hi = np.quantile(values, [low_q, high_q])
    if hi <= lo + 1e-6:
        hi = lo + 1.0
    return float(lo), float(hi)


def clean_floor_mask(mask: np.ndarray) -> np.ndarray:
    """Close small holes and retain the useful lower connected floor area."""
    if cv2 is None or not mask.any():
        return mask
    u8 = (mask.astype(np.uint8) * 255)
    kernel = np.ones((7, 7), np.uint8)
    u8 = cv2.morphologyEx(u8, cv2.MORPH_CLOSE, kernel, iterations=2)
    u8 = cv2.morphologyEx(u8, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8), iterations=1)

    # Prefer components touching the lower image boundary; furniture masks
    # should not accidentally become the floor.
    n, labels, stats, _ = cv2.connectedComponentsWithStats(u8, 8)
    if n > 1:
        bottom_labels = set(labels[-1, :].tolist()) - {0}
        if bottom_labels:
            keep = np.isin(labels, list(bottom_labels))
            u8 = np.where(keep, 255, 0).astype(np.uint8)
    return u8 > 128


def smooth_profile(values: np.ndarray) -> np.ndarray:
    values = values.astype(np.float32)
    valid = np.flatnonzero(values > 0)
    if valid.size == 0:
        return values
    if valid.size == 1:
        values[:] = values[valid[0]]
        return values
    values = np.interp(np.arange(len(values)), valid, values[valid]).astype(np.float32)
    if cv2 is not None:
        return cv2.GaussianBlur(values.reshape(1, -1), (0, 0), 6).reshape(-1).astype(np.float32)
    kernel = np.ones(15, dtype=np.float32) / 15.0
    return np.convolve(values, kernel, mode="same").astype(np.float32)


def floor_profile(depth: np.ndarray, floor_mask: np.ndarray) -> Tuple[np.ndarray, int, np.ndarray]:
    H, W = depth.shape
    profile = np.zeros(H, dtype=np.float32)
    for y in range(H):
        row = depth[y][floor_mask[y]]
        if row.size:
            profile[y] = float(np.median(row))
    profile = smooth_profile(profile)

    tops = np.full(W, np.nan, dtype=np.float32)
    for x in range(W):
        ys = np.flatnonzero(floor_mask[:, x])
        if ys.size:
            tops[x] = ys[0]
    valid = np.flatnonzero(np.isfinite(tops))
    if valid.size:
        tops = np.interp(np.arange(W), valid, tops[valid]).astype(np.float32)
        if cv2 is not None:
            tops = cv2.GaussianBlur(tops.reshape(1, -1), (0, 0), max(2, W / 160)).reshape(-1).astype(np.float32)
        floor_top = int(np.median(tops[valid]))
    else:
        floor_top = int(H * 0.60)
        tops[:] = floor_top
    return profile, floor_top, tops


def aggregate_masks(seg_results, original_size: Tuple[int, int], analysis_size: Tuple[int, int]):
    W, H = original_size
    aw, ah = analysis_size
    masks: Dict[str, np.ndarray] = {}
    regions: List[dict] = []

    for result in seg_results:
        label = str(result.get("label", "")).lower().strip()
        if label not in ADE_STRUCT and label not in ADE_FURNITURE:
            continue
        arr_small = mask_array(result["mask"], (aw, ah))
        if not arr_small.any():
            continue
        arr = np.asarray(
            Image.fromarray((arr_small * 255).astype(np.uint8)).resize((W, H), Image.Resampling.NEAREST)
        ) > 128
        key = "furniture" if label in ADE_FURNITURE else label
        masks[key] = masks[key] | arr if key in masks else arr
        if label in ADE_FURNITURE:
            ys, xs = np.where(arr)
            if len(xs):
                regions.append({
                    "label": label,
                    "x": int(xs.min()), "y": int(ys.min()),
                    "width": int(xs.max() - xs.min() + 1),
                    "height": int(ys.max() - ys.min() + 1),
                    "area": int(arr.sum()),
                })

    if "floor" in masks:
        masks["floor"] = clean_floor_mask(masks["floor"])
        floor_source = "segformer"
    else:
        fallback = np.zeros((H, W), dtype=bool)
        fallback[int(H * 0.62):, :] = True
        masks["floor"] = fallback
        floor_source = "fallback"
    return masks, regions, floor_source


@app.get("/")
def root():
    return {"ok": True, "service": "La Cigogne D'Ailleurs AI", "version": app.version, "docs": "/docs", "health": "/health"}


@app.get("/health")
def health():
    return {
        "ok": True,
        "version": app.version,
        "device": "cuda" if torch.cuda.is_available() else "cpu",
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "models_loaded": depth_pipe is not None and seg_pipe is not None,
        "lama_loaded": lama is not None,
    }


@app.post("/analyze")
async def analyze(file: UploadFile = File(...)):
    image = read_image(file)
    W, H = image.size
    analysis_image = resize_for_analysis(image)
    aw, ah = analysis_image.size
    dp, sp = get_pipes()

    depth_result = dp(analysis_image)
    depth_pil = depth_result["depth"].convert("L").resize((aw, ah), Image.Resampling.BILINEAR)
    depth = np.asarray(depth_pil, dtype=np.float32) / 255.0

    seg_results = sp(analysis_image)
    masks, regions, floor_source = aggregate_masks(seg_results, (W, H), (aw, ah))
    floor_original = masks["floor"]
    floor_for_depth = np.asarray(
        Image.fromarray((floor_original * 255).astype(np.uint8)).resize((aw, ah), Image.Resampling.NEAREST)
    ) > 128

    profile_small, floor_top_small, floor_top_profile_small = floor_profile(depth, floor_for_depth)
    profile = np.interp(np.linspace(0, ah - 1, H), np.arange(ah), profile_small).astype(np.float32)
    floor_top_profile = np.interp(
        np.linspace(0, aw - 1, W), np.arange(aw), floor_top_profile_small
    ).astype(np.float32)

    floor_values = depth[floor_for_depth]
    depth_low, depth_high = percentile_pair(floor_values)
    # Depth Anything V2 is relative depth. Infer its polarity from the
    # expected indoor perspective trend on the detected floor: pixels lower
    # in the image should normally represent the nearer part of the floor.
    y_idx = np.arange(ah, dtype=np.float32)
    valid_rows = (y_idx >= floor_top_small) & (profile_small > 0)
    if int(valid_rows.sum()) >= 8:
        corr = np.corrcoef(y_idx[valid_rows], profile_small[valid_rows])[0, 1]
        near_is_high = bool(np.isfinite(corr) and corr >= 0)
    else:
        near_is_high = True

    floor_top_y = int(round(floor_top_small / max(1, ah - 1) * max(1, H - 1)))
    depth_vis = Image.fromarray(np.clip(depth * 255, 0, 255).astype(np.uint8), mode="L")
    out_masks = {name: to_data_url(Image.fromarray((arr * 255).astype(np.uint8)), max_side=1600) for name, arr in masks.items()}

    # Downsample the floor boundary for a lightweight frontend debug overlay.
    sample_n = min(240, W)
    sample_x = np.linspace(0, W - 1, sample_n).astype(int)
    top_profile_out = floor_top_profile[sample_x].round(1).tolist()

    return JSONResponse({
        "version": 3.0,
        "width": W, "height": H,
        "analysis_width": aw, "analysis_height": ah,
        "depth": to_data_url(depth_vis, max_side=1600),
        "masks": out_masks,
        "floor_depth": profile.tolist(),
        "reference_depth": float(depth_high),
        "depth_floor_low": float(depth_low),
        "depth_floor_high": float(depth_high),
        "depth_near_is_high": bool(near_is_high),
        "floor_top_y": floor_top_y,
        "floor_top_profile": top_profile_out,
        "floor_source": floor_source,
        "scene": {
            "furniture_count": len(regions),
            "furniture": regions,
            "has_floor": "floor" in masks,
            "has_walls": "wall" in masks,
            "has_windows": "windowpane" in masks,
            "has_doors": "door" in masks,
        },
    })



def _bbox_from_mask(mask: np.ndarray):
    ys, xs = np.where(mask)
    if xs.size == 0:
        return None
    return {"x": int(xs.min()), "y": int(ys.min()), "width": int(xs.max()-xs.min()+1), "height": int(ys.max()-ys.min()+1)}


def _detect_related_furniture(image: Image.Image, x: int, y: int, primary_mask: np.ndarray, primary_label: str):
    """Return nearby semantic furniture components as explicit multi-selection candidates.

    SegFormer is used for discovery; the clicked object remains the only mask that is
    refined by SAM. Related candidates are suggestions so the frontend can keep several
    objects selected in one removal operation instead of losing earlier selections.
    """
    try:
        _, sp = get_pipes()
        W, H = image.size
        analysis_image = resize_for_analysis(image)
        aw, ah = analysis_image.size
        sx, sy = aw / W, ah / H
        ax, ay = int(round(x*sx)), int(round(y*sy))
        primary = _bbox_from_mask(primary_mask)
        if not primary:
            return []
        pcx = primary["x"] + primary["width"] / 2
        pcy = primary["y"] + primary["height"] / 2
        pdiag = max(1.0, (primary["width"]**2 + primary["height"]**2) ** 0.5)
        # Conservative component families. These are suggestions, not a license
        # to merge the whole room.
        families = {
            "bed": {"bed", "bedclothes", "pillow", "bench", "cabinet", "chest of drawers", "lamp", "table"},
            "bedclothes": {"bed", "bedclothes", "pillow", "bench"},
            "pillow": {"bed", "bedclothes", "pillow"},
            "sofa": {"sofa", "seat", "ottoman", "coffee table", "table", "pillow"},
            "chair": {"chair", "table", "stool", "bench"},
            "armchair": {"armchair", "chair", "table", "stool", "bench"},
            "table": {"table", "chair", "bench", "stool", "lamp"},
            "coffee table": {"coffee table", "table", "ottoman", "sofa"},
            "bench": {"bench", "bed", "bedclothes", "pillow"},
        }
        allowed = families.get(primary_label, {primary_label})
        candidates = []
        for result in sp(analysis_image):
            label = str(result.get("label", "")).lower().strip()
            if label not in allowed:
                continue
            score = float(result.get("score", 1.0) or 1.0)
            if score < 0.45:
                continue
            arr_small = mask_array(result.get("mask"), (aw, ah)) if result.get("mask") is not None else None
            if arr_small is None or int(arr_small.sum()) < max(40, int(aw*ah*0.00015)):
                continue
            full = np.asarray(Image.fromarray((arr_small*255).astype(np.uint8)).resize((W,H), Image.Resampling.NEAREST)) > 127
            full = _connected_component_at_point(full, int(round(np.clip(np.mean(np.where(full)[1]) if full.any() else x, 0, W-1))), int(round(np.clip(np.mean(np.where(full)[0]) if full.any() else y, 0, H-1))))
            bbox = _bbox_from_mask(full)
            if not bbox:
                continue
            cx = bbox["x"] + bbox["width"]/2
            cy = bbox["y"] + bbox["height"]/2
            dist = ((cx-pcx)**2 + (cy-pcy)**2) ** 0.5
            # Auto-grouping is only for physically linked/attached pieces.
            # Never auto-select another full-size piece of furniture merely
            # because it is nearby in the room.
            max_dist = max(70.0, min(120.0, pdiag*0.55))
            if label != primary_label and dist > max_dist:
                continue
            area = int(full.sum())
            primary_area = max(1, int(primary_mask.sum()))
            ratio = area / primary_area
            if label != primary_label and (ratio > 0.45 or ratio < 0.004):
                continue
            # Require physical contact/near-contact with the primary mask.
            near = cv2.dilate(primary_mask.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(25,25)),1).astype(bool) if cv2 is not None else primary_mask
            contact = float((near & full).sum()) / max(1, area)
            if label != primary_label and contact < 0.015:
                continue
            # Exclude a duplicate of the primary component.
            overlap = float((full & primary_mask).sum()) / max(1, min(area, primary_area))
            if overlap > 0.82:
                continue
            candidates.append({
                "label": label,
                "mask": Image.fromarray((full.astype(np.uint8)*255), "L"),
                "bbox": bbox,
                "score": score,
                "distance": dist,
            })
        candidates.sort(key=lambda c: (c["distance"], -c["score"]))
        return candidates[:7]
    except Exception as exc:
        print(f"WARNING: related furniture discovery unavailable: {exc}")
        return []


_PHYSICAL_FAMILIES = {
    "bed": {"bed", "bedclothes", "pillow"},
    "bedclothes": {"bed", "bedclothes", "pillow"},
    "pillow": {"bed", "bedclothes", "pillow"},
    "sofa": {"sofa", "seat", "ottoman", "pillow"},
    "seat": {"sofa", "seat", "ottoman", "armchair", "chair", "pillow"},
    "armchair": {"armchair", "chair", "seat", "ottoman"},
    "chair": {"chair", "armchair", "seat", "stool"},
    "stool": {"stool", "chair", "seat"},
    "table": {"table", "dining table", "desk"},
    "dining table": {"table", "dining table", "desk"},
    "desk": {"desk", "table", "dining table"},
    "cabinet": {"cabinet", "chest of drawers", "wardrobe", "bookcase"},
    "chest of drawers": {"cabinet", "chest of drawers", "wardrobe"},
    "wardrobe": {"wardrobe", "cabinet", "bookcase"},
}


def _physical_family_prior(seg_results, analysis_size, original_size,
                           x, y, selected_label, clicked_seed):
    """Build a *soft* physical-object prior from nearby ADE components.

    SegFormer often splits one real piece of furniture into semantic parts
    (bed -> bedclothes -> pillow, sofa -> seat -> pillow). V4.1 intentionally
    used only the clicked component as the positive prior. That protects
    neighbours, but it can also make SAM solve the wrong problem: segment the
    duvet instead of the complete bed. V4.2 unions only components belonging to
    a conservative physical family and only when they are spatially attached
    to the clicked component. The result is still only evidence for SAM; SAM
    remains the final silhouette authority.
    """
    W, H = original_size
    aw, ah = analysis_size
    family = _PHYSICAL_FAMILIES.get(selected_label, {selected_label})
    prior = clicked_seed.astype(bool).copy()
    if not prior.any():
        return prior, {"family": sorted(family), "components_added": 0}

    base_bb = _mask_bbox(prior)
    if not base_bb:
        return prior, {"family": sorted(family), "components_added": 0}
    base_cx = (base_bb[0] + base_bb[2]) / 2.0
    base_cy = (base_bb[1] + base_bb[3]) / 2.0
    base_diag = max(1.0, ((base_bb[2] - base_bb[0] + 1)**2 + (base_bb[3] - base_bb[1] + 1)**2) ** 0.5)

    added = 0
    components = []
    for result in seg_results:
        label = str(result.get("label", "")).lower().strip()
        if label not in family:
            continue
        raw = result.get("mask")
        if raw is None:
            continue
        small = mask_array(raw, (aw, ah))
        if not small.any():
            continue
        full = np.asarray(
            Image.fromarray((small.astype(np.uint8) * 255)).resize(
                (W, H), Image.Resampling.NEAREST
            )
        ) > 127
        # Split same-label masks so a second chair/bed elsewhere cannot become
        # part of the selected physical object.
        if cv2 is not None:
            n, labels, stats, _ = cv2.connectedComponentsWithStats(
                (full.astype(np.uint8) * 255), 8
            )
            parts = [labels == i for i in range(1, n)]
        else:
            parts = [full]
        for part in parts:
            area = int(part.sum())
            if area < max(20, int(W * H * 0.00004)):
                continue
            bb = _mask_bbox(part)
            if not bb:
                continue
            cx = (bb[0] + bb[2]) / 2.0
            cy = (bb[1] + bb[3]) / 2.0
            dist = ((cx - base_cx)**2 + (cy - base_cy)**2) ** 0.5
            # Physical parts can be separated by legs/gaps, but a second piece
            # of furniture should not be absorbed merely because it is nearby.
            max_dist = max(90.0, min(360.0, base_diag * 0.95))
            if dist > max_dist:
                continue
            near = cv2.dilate(
                prior.astype(np.uint8),
                cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31)), 1
            ).astype(bool) if cv2 is not None else prior
            contact = float((near & part).sum()) / max(1.0, area)
            overlap = float((part & prior).sum()) / max(1.0, min(area, int(prior.sum())))
            if contact < 0.015 and overlap < 0.05:
                continue
            # Prevent a family component from swallowing a component whose
            # area is wildly larger than the clicked physical envelope.
            if area > max(int(prior.sum()) * 2.8, int(W * H * 0.08)):
                continue
            if not (part & prior).any():
                added += 1
            prior |= part
            components.append({"label": label, "area": area, "distance": round(dist, 1),
                               "contact": round(contact, 3)})

    return prior, {"family": sorted(family), "components_added": added,
                    "components": components[:20]}


def _semantic_neighbor_exclusion(seg_results, analysis_size, original_size,
                                  x, y, selected_seed, selected_label):
    """Build negative semantic evidence without turning SegFormer into a mask.

    The selected semantic component is removed from the exclusion map. Every
    other nearby object/surface becomes negative evidence for SAM candidate
    ranking. This is specifically intended to stop the failure visible in the
    living-room benchmark where a chair candidate swallowed the plant, basket,
    and floor.
    """
    W,H=original_size
    aw,ah=analysis_size
    exclusion=np.zeros((H,W),dtype=bool)
    details=[]
    seed_d=selected_seed.astype(bool)
    if cv2 is not None and seed_d.any():
        seed_d=cv2.dilate(
            seed_d.astype(np.uint8),
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(25,25)),1
        ).astype(bool)

    for result in seg_results:
        label=str(result.get("label","")).lower().strip()
        if label not in ADE_NEIGHBOR_OBJECTS:
            continue
        raw=result.get("mask")
        if raw is None:
            continue
        small=mask_array(raw,(aw,ah))
        if not small.any():
            continue
        full=np.asarray(
            Image.fromarray((small.astype(np.uint8)*255)).resize(
                (W,H),Image.Resampling.NEAREST
            )
        )>127
        if not full.any():
            continue

        # For the clicked semantic label, remove the clicked component only;
        # same-label components elsewhere remain negative evidence.
        if label == selected_label:
            clicked=_connected_component_at_point(full,x,y)
            if clicked.any():
                full=full & ~clicked
        # Never let the selected positive seed itself count as contamination.
        full=full & ~seed_d
        if not full.any():
            continue

        # Ignore tiny segmentation noise.
        if int(full.sum()) < max(24,int(W*H*0.00005)):
            continue
        exclusion |= full
        ys,xs=np.where(full)
        details.append({
            "label":label,
            "area":int(full.sum()),
            "bbox":(int(xs.min()),int(ys.min()),int(xs.max()),int(ys.max()))
        })

    return exclusion,details


async def _segment_furniture_at_point(image: Image.Image, x: int, y: int):
    """Select one physical object: semantic class for label + SAM silhouette."""
    W, H = image.size
    x = int(np.clip(x, 0, W - 1)); y = int(np.clip(y, 0, H - 1))
    _, sp = get_pipes()
    analysis_image = resize_for_analysis(image)
    aw, ah = analysis_image.size
    sx = aw / W; sy = ah / H
    ax = int(round(x * sx)); ay = int(round(y * sy))

    seg_results = sp(analysis_image)
    candidates = []
    for result in seg_results:
        label = str(result.get("label", "")).lower().strip()
        if label not in ADE_FURNITURE:
            continue
        arr_small = mask_array(result["mask"], (aw, ah))
        area = int(arr_small.sum())
        if area < max(40, int(aw * ah * 0.0002)):
            continue
        y0, y1 = max(0, ay - 10), min(ah, ay + 11)
        x0, x1 = max(0, ax - 10), min(aw, ax + 11)
        if arr_small[y0:y1, x0:x1].any():
            candidates.append((area, label, arr_small))

    if not candidates:
        selected_label = "objet"
        seed = np.zeros((H, W), dtype=bool)
    else:
        # Prefer the largest clicked semantic component. The old `min(area)`
        # rule selected tiny fragments (e.g. a cushion/plant part) and then
        # fed that fragment to the SAM box generator, shrinking the search
        # context before SAM had a chance to see the whole object.
        _, selected_label, selected_small = max(candidates, key=lambda item: item[0])
        seed = np.asarray(
            Image.fromarray((selected_small * 255).astype(np.uint8)).resize(
                (W, H), Image.Resampling.NEAREST
            )
        ) > 128

    seed = _connected_component_at_point(seed, x, y)
    family_seed, family_meta = _physical_family_prior(
        seg_results, (aw, ah), (W, H), x, y, selected_label, seed
    )
    exclusion, exclusion_details = _semantic_neighbor_exclusion(
        seg_results, (aw,ah), (W,H), x, y, family_seed, selected_label
    )

    # SAM owns the actual silhouette. SegFormer is context plus negative
    # evidence, never a hard positive boundary. V4.2 uses the conservative
    # physical-family prior so a click on a duvet/cushion can still recover the
    # complete bed/sofa instead of permanently shrinking SAM to that fragment.
    sam_mask = refine_mask_with_sam(
        image, x, y, family_seed, exclusion=exclusion,
        exclusion_details=exclusion_details
    )
    if isinstance(sam_mask, tuple):
        final_mask = sam_mask[0]
    else:
        final_mask = sam_mask

    if final_mask is not None:
        return image, selected_label, final_mask, {
            "semantic_label": selected_label,
            "neighbor_count": len(exclusion_details),
            "neighbor_labels": sorted({d["label"] for d in exclusion_details}),
            "neighbor_area": int(exclusion.sum()),
            "physical_family": family_meta,
            "semantic_seed_area_ratio": float(seed.mean()),
            "family_seed_area_ratio": float(family_seed.mean()),
        }
    if cv2 is not None:
        u8 = cv2.morphologyEx(
            (seed * 255).astype(np.uint8),
            cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8), iterations=1
        )
        final_mask = Image.fromarray(u8).convert("L")
    else:
        final_mask = Image.fromarray((seed * 255).astype(np.uint8)).convert("L")
    return image, selected_label, final_mask, {
        "semantic_label": selected_label,
        "neighbor_count": len(exclusion_details),
        "neighbor_labels": sorted({d["label"] for d in exclusion_details}),
        "neighbor_area": int(exclusion.sum()),
    }


def prepare_inpaint_mask(mask_img: Image.Image, image_size: Tuple[int, int]) -> Image.Image:
    """Normalize a binary selection and dilate it by a small 1–4 px edge margin."""
    if mask_img.size != image_size:
        # NEAREST, not BILINEAR: this is a binary object silhouette, not a
        # photo. Smooth interpolation manufactures grey edge pixels that then
        # get re-thresholded, rounding off real detail (e.g. bed legs) instead
        # of just anti-aliasing — and the same source mask already goes
        # through this resize twice (once on the frontend round-trip, once
        # here), so the effect compounds.
        mask_img=mask_img.resize(image_size,Image.Resampling.NEAREST)
    arr=np.asarray(mask_img,dtype=np.uint8)
    if cv2 is None:
        return Image.fromarray(arr).convert("L")
    binary=(arr>127).astype(np.uint8)*255
    scale=max(image_size)/1600.0
    radius=int(np.clip(round(1.6*scale),1,4))
    kernel=cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(radius*2+1,radius*2+1))
    binary=cv2.morphologyEx(binary,cv2.MORPH_CLOSE,kernel,iterations=1)
    binary=cv2.dilate(binary,kernel,iterations=1)
    return Image.fromarray(binary).convert("L")


def _surface_masks(image: Image.Image):
    """Get coarse wall/floor context only; never use it as the object mask."""
    try:
        _, sp=get_pipes()
        analysis=resize_for_analysis(image)
        aw,ah=analysis.size; W,H=image.size
        results=sp(analysis)
        surfaces={}
        wanted={"floor","wall","rug","carpet"}
        for r in results:
            label=str(r.get("label","")).lower().strip()
            if label not in wanted:
                continue
            a=mask_array(r["mask"],(aw,ah))
            up=np.asarray(Image.fromarray((a*255).astype(np.uint8)).resize((W,H),Image.Resampling.NEAREST))>128
            surfaces[label]=surfaces.get(label,False)|up
        return surfaces
    except Exception as exc:
        print(f"WARNING: surface context unavailable: {exc}")
        return {}


def _nearest_surface_map(mask: np.ndarray, surfaces: dict) -> np.ndarray:
    """Route the selected hole to the *actual* visible surface.

    Euclidean nearest-surface assignment is wrong for perspective rooms: a
    rug can be physically closer to a wall pixel than the wall itself.  We
    first estimate the wall/floor transition from the visible segmentation,
    then use the rug segmentation only below that transition.
    IDs: 1=floor, 2=wall, 3=rug/carpet.
    """
    H, W = mask.shape
    floor=np.asarray(surfaces.get("floor", np.zeros_like(mask)),bool)
    wall=np.asarray(surfaces.get("wall", np.zeros_like(mask)),bool)
    rug=np.asarray(surfaces.get("rug", np.zeros_like(mask)),bool) | np.asarray(surfaces.get("carpet", np.zeros_like(mask)),bool)
    labels=np.zeros((H,W),np.uint8)
    hole=mask.astype(bool)

    # Estimate the visible floor/wall boundary column-by-column.  Robust
    # quantiles stop isolated segmentation pixels from moving the boundary.
    boundary=np.full(W, int(H*0.58), dtype=np.float32)
    valid=[]
    for x in range(W):
        wy=np.where(wall[:,x])[0]
        fy=np.where(floor[:,x])[0]
        if wy.size and fy.size:
            valid.append((x, float(np.percentile(wy,90))))
        elif wy.size:
            valid.append((x, float(np.percentile(wy,90))))
    if valid:
        vx=np.array([v[0] for v in valid],np.float32); vy=np.array([v[1] for v in valid],np.float32)
        boundary=np.interp(np.arange(W,dtype=np.float32),vx,vy,left=float(vy[0]),right=float(vy[-1]))
        if cv2 is not None:
            boundary=cv2.GaussianBlur(boundary.reshape(1,-1),(0,0),max(3.0,W/180.0)).ravel()

    yy=np.indices((H,W))[0]
    # A small transition band is assigned using surface proximity.
    wall_zone=hole & (yy <= (boundary[None,:]-6))
    lower_zone=hole & (yy >= (boundary[None,:]+6))
    transition=hole & ~(wall_zone|lower_zone)
    labels[wall_zone]=2

    # Below the wall line: rug wins only when the pixel is genuinely near a
    # visible rug region. Otherwise it is floor. This prevents the rug from
    # being projected upward across the entire bed footprint.
    if cv2 is not None:
        def dist_to(m):
            if not m.any(): return np.full((H,W),1e6,np.float32)
            return cv2.distanceTransform((~m).astype(np.uint8),cv2.DIST_L2,5)
        dr=dist_to(rug); df=dist_to(floor); dw=dist_to(wall)
        rug_pick=lower_zone & rug.any() & (dr <= np.minimum(df*1.35, 170.0))
        labels[lower_zone]=1
        labels[rug_pick]=3
        # Transition pixels use the nearest plausible surface, but wall is
        # strongly preferred above the estimated boundary.
        dstack=np.stack([dw,df,dr],axis=0)
        idx=np.argmin(dstack,axis=0)
        transition_labels=np.where(idx==0,2,np.where(idx==2,3,1)).astype(np.uint8)
        labels[transition]=transition_labels[transition]
    else:
        labels[lower_zone]=1; labels[transition]=np.where(yy[transition]<H*0.58,2,1)

    labels[hole & (labels==0)] = np.where(yy[hole & (labels==0)] < H*0.58,2,1)
    return labels

def _add_contact_shadow_mask(image: Image.Image, object_mask: np.ndarray, surface_map: np.ndarray) -> np.ndarray:
    """Add only the likely contact shadow around the lower object edge.

    This deliberately avoids expanding the whole object mask. A narrow floor
    shadow band is detected from local luminance and is reconstructed together
    with the object so dark 'ghost furniture' does not remain.
    """
    if cv2 is None or not object_mask.any():
        return object_mask.copy()
    H, W = object_mask.shape
    ys, xs = np.where(object_mask)
    if xs.size < 20:
        return object_mask.copy()
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    band_h = int(np.clip((y1 - y0 + 1) * 0.10, 8, 36))
    dil = cv2.dilate(object_mask.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11)), 1).astype(bool)
    ring = dil & (~object_mask) & np.isin(surface_map, (1, 3))
    # Only the lower part of the object's footprint can be contact shadow.
    lower = np.zeros_like(ring)
    lower[max(0, y1 - band_h):min(H, y1 + band_h + 1), max(0, x0 - 16):min(W, x1 + 17)] = True
    ring &= lower
    if not ring.any():
        return object_mask.copy()

    rgb = np.asarray(image.convert("RGB"), np.float32)
    gray = cv2.cvtColor(rgb.astype(np.uint8), cv2.COLOR_RGB2GRAY).astype(np.float32)
    # Compare each candidate to a local horizontal neighborhood. Shadows are
    # usually substantially darker than the surrounding carpet/floor.
    blur = cv2.GaussianBlur(gray, (0, 0), 7)
    darkness = blur - gray
    shadow = ring & (darkness > 9.0)
    # Keep this conservative: at most a small fraction of the original object
    # area is allowed to become shadow mask.
    max_extra = max(80, int(object_mask.sum() * 0.10))
    if int(shadow.sum()) > max_extra:
        vals = darkness[shadow]
        threshold = float(np.quantile(vals, 1.0 - max_extra / max(1, len(vals))))
        shadow = shadow & (darkness >= threshold)
    return object_mask | shadow


def _multiscale_cv_inpaint(image: Image.Image, mask: np.ndarray, radius: float = 3.0) -> Image.Image:
    """Stable multi-scale classical inpainting.

    Large furniture holes are difficult for single-pass Telea/NS because the
    algorithm has to propagate pixels across a very large missing region. We
    first solve the low-frequency structure at reduced resolution, then refine
    at the original resolution. This avoids the long vertical smear produced
    by direct row-copy texture synthesis.
    """
    src = np.asarray(image.convert("RGB"), np.uint8)
    if cv2 is None or not mask.any():
        return image
    H, W = src.shape[:2]
    # Work at up to 768px on the long side for the coarse structural pass.
    scale = min(1.0, 768.0 / max(H, W))
    cw, ch = max(32, int(round(W * scale))), max(32, int(round(H * scale)))
    small = cv2.resize(src, (cw, ch), interpolation=cv2.INTER_AREA)
    smask = cv2.resize((mask.astype(np.uint8) * 255), (cw, ch), interpolation=cv2.INTER_NEAREST)
    # A modest radius at coarse scale gives broad background continuity.
    coarse = cv2.inpaint(small, smask, max(2.0, min(6.0, radius * 0.75)), cv2.INPAINT_NS)
    coarse_up = cv2.resize(coarse, (W, H), interpolation=cv2.INTER_CUBIC)

    # Use the coarse result only inside the hole, then make a local full-res
    # inpaint pass to restore edges and small texture transitions.
    work = src.copy()
    work[mask] = coarse_up[mask]
    fine = cv2.inpaint(work, (mask.astype(np.uint8) * 255), max(2.0, min(7.0, radius)), cv2.INPAINT_TELEA)

    # Prefer coarse reconstruction in the middle of a large hole and fine
    # reconstruction near the contour.
    dist = cv2.distanceTransform((mask.astype(np.uint8) * 255), cv2.DIST_L2, 5)
    alpha = np.clip(dist / 28.0, 0.0, 1.0)[..., None]
    result = src.astype(np.float32)
    mixed = fine.astype(np.float32) * (1.0 - alpha) + coarse_up.astype(np.float32) * alpha
    result[mask] = mixed[mask]
    return Image.fromarray(np.clip(result, 0, 255).astype(np.uint8))


def _texture_residual_transfer(base: Image.Image, original: Image.Image,
                               target: np.ndarray, surface_map: np.ndarray) -> Image.Image:
    """Transfer only high-frequency texture from visible same-surface pixels.

    We deliberately do NOT copy RGB pixels directly. The low-frequency image
    comes from geometric/multiscale inpainting; only fine carpet/wood grain is
    borrowed from nearby visible pixels. A varying 2-D donor offset prevents
    the vertical bands seen in the previous implementation.
    """
    if cv2 is None or not target.any():
        return base
    src = np.asarray(original.convert("RGB"), np.float32)
    out = np.asarray(base.convert("RGB"), np.float32).copy()
    H, W = target.shape
    # Use the actual target surface as the donor class. Previous versions
    # always sampled floor pixels, which is wrong when a bed sits over a rug:
    # the hole then receives wood/floor texture instead of the rug pattern.
    target_surface = surface_map[target]
    target_surface = target_surface[target_surface > 0]
    if target_surface.size:
        vals, counts = np.unique(target_surface, return_counts=True)
        sid = int(vals[np.argmax(counts)])
    else:
        sid = 1
    visible = (~target) & (surface_map == sid)
    if int(visible.sum()) < 500 and sid == 3:
        # Rug segmentation can be sparse. A floor donor is only a fallback.
        visible = (~target) & (surface_map == 1)
    if int(visible.sum()) < 500:
        return base

    # Prefer a broad visible floor/carpet donor below the object, with fallback
    # to the full visible surface. We use residual texture, not absolute color.
    ys, xs = np.where(target)
    y0, y1 = int(ys.min()), int(ys.max())
    x0, x1 = int(xs.min()), int(xs.max())
    candidates = np.where(visible & (np.indices((H, W))[0] >= max(y0, int(H * 0.42))))
    if len(candidates[0]) < 500:
        candidates = np.where(visible)
    if len(candidates[0]) < 500:
        return base

    cy = int(np.median(candidates[0])); cx = int(np.median(candidates[1]))
    # A donor texture window should remain local to the room surface.
    half_h = int(np.clip((y1 - y0 + 1) * 0.55, 24, 180))
    half_w = int(np.clip((x1 - x0 + 1) * 0.55, 32, 240))
    sy0, sy1 = max(0, cy - half_h), min(H, cy + half_h + 1)
    sx0, sx1 = max(0, cx - half_w), min(W, cx + half_w + 1)
    donor = src[sy0:sy1, sx0:sx1]
    if donor.shape[0] < 8 or donor.shape[1] < 8:
        return base

    # High-pass residual. Keep it weak; this is texture, not geometry/color.
    donor_blur = cv2.GaussianBlur(donor, (0, 0), 2.2)
    residual = donor - donor_blur
    # Estimate residual amplitude from visible donor pixels.
    residual = np.clip(residual, -22.0, 22.0)

    yy, xx = np.indices((H, W), dtype=np.float32)
    # Smoothly varying 2-D offsets; no fixed x/y row mapping.
    ox = (np.sin(yy * 0.021 + xx * 0.004) * 0.22 +
          np.sin(yy * 0.007 - xx * 0.013) * 0.13) * max(8, donor.shape[1] * 0.25)
    oy = (np.sin(xx * 0.017 - yy * 0.005) * 0.18 +
          np.sin(xx * 0.006 + yy * 0.011) * 0.10) * max(6, donor.shape[0] * 0.20)
    map_x = np.mod(xx - x0 + ox, donor.shape[1] - 1).astype(np.float32)
    map_y = np.mod(yy - y0 + oy, donor.shape[0] - 1).astype(np.float32)
    tex = np.empty_like(src)
    for c in range(3):
        tex[..., c] = cv2.remap(residual[..., c], map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)

    # Normalize texture amplitude to the local visible surface statistics.
    local_std = float(np.std(residual))
    if not np.isfinite(local_std) or local_std < 0.5:
        return base
    gain = float(np.clip(0.55 / max(0.55, local_std), 0.35, 0.85))
    texture = tex * gain

    # Feather texture so the boundary remains controlled.
    feather = cv2.GaussianBlur(target.astype(np.float32), (0, 0), 2.0)
    a = np.clip(feather * 0.72, 0.0, 0.72)[..., None]
    out = out + texture * a
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))


def _surface_guided_reconstruct(image: Image.Image, hard_mask: np.ndarray, surface_map: np.ndarray) -> Image.Image:
    """Reconstruct a large furniture hole as photographed room surfaces.

    This is intentionally non-generative: it cannot invent a new bed/table.
    Wall and floor/rug regions are reconstructed independently, using a
    coarse inpaint pass followed by a narrow full-resolution pass. For large
    holes this is a safer first choice than Stable Diffusion, whose latent
    prior can hallucinate replacement furniture even with a strong negative
    prompt.
    """
    if cv2 is None or not hard_mask.any():
        return image.convert("RGB")
    src=image.convert("RGB")
    result=src
    # Do the wall first, then floor/rug. Each pass sees the original visible
    # context and only receives pixels from the corresponding surface class.
    for sid in (2,3,1):
        target=hard_mask & (surface_map==sid)
        if not target.any():
            continue
        # Large smooth walls benefit from a broader coarse pass; floor/rug
        # needs a little more local texture retention.
        radius=6.0 if sid==2 else 5.0
        result=_multiscale_cv_inpaint(result,target,radius=radius)
        if sid in (1,3):
            result=_texture_residual_transfer(result,src,target,surface_map)
    unresolved=hard_mask & ~((surface_map==1)|(surface_map==2)|(surface_map==3))
    if unresolved.any():
        result=_multiscale_cv_inpaint(result,unresolved,radius=4.0)
    # Strictly restore the original outside the selected object.
    return _composite_inside_mask(src,result,Image.fromarray((hard_mask.astype(np.uint8)*255),"L"))

def _surface_texture_fill(image: Image.Image, hard_mask: np.ndarray, surface_map: np.ndarray) -> Image.Image:
    """Reconstruct room surfaces without direct RGB stretching.

    - Wall: multi-scale structural inpainting.
    - Floor/rug: multi-scale structure + weak high-frequency texture transfer.
    - All pixels outside the confirmed reconstruction region stay unchanged.
    """
    if cv2 is None or not hard_mask.any():
        return image
    src = image.convert("RGB")
    result = src

    # Reconstruct each surface independently. This prevents a floor texture
    # donor from bleeding into the wall or vice versa.
    for sid in (2, 1):
        target = hard_mask & (surface_map == sid)
        if not target.any():
            continue
        radius = 3.5 if sid == 2 else 4.5
        structural = _multiscale_cv_inpaint(result, target, radius=radius)
        if sid == 1:
            structural = _texture_residual_transfer(structural, src, target, surface_map)
        result = structural

    # If surface classification missed a small fraction of the mask, repair it
    # conservatively with the same multiscale method rather than leaving a hole.
    unresolved = hard_mask & ~((surface_map == 1) | (surface_map == 2))
    if unresolved.any():
        result = _multiscale_cv_inpaint(result, unresolved, radius=3.0)
    return result

def _composite_inside_mask(original: Image.Image, generated: Image.Image, mask: Image.Image) -> Image.Image:
    """Composite generated pixels only inside the confirmed binary mask.

    The feather may soften the confirmed contour, but it is explicitly clipped
    back to the binary mask afterwards. Therefore every pixel whose confirmed
    mask value is zero is copied from the source byte-for-byte.
    """
    src=np.asarray(original.convert("RGB"))
    if generated.size != original.size:
        generated = generated.convert("RGB").resize(original.size, Image.Resampling.BICUBIC)
    gen=np.asarray(generated.convert("RGB"))
    mask = mask.convert("L")
    if mask.size != original.size:
        mask = mask.resize(original.size, Image.Resampling.NEAREST)
    binary=np.asarray(mask, dtype=np.uint8) > 127
    # A confirmed removal mask is an ownership boundary, not a transparency
    # map: every pixel inside it must come fully from the generated candidate.
    # Boundary harmonization happens upstream in _phase9_edge_harmonize();
    # applying a Gaussian alpha here would make edge pixels a blend of the
    # removed furniture and the reconstruction, producing the observed ghosted
    # / translucent appearance.
    a=binary.astype(np.float32)[...,None]
    out=(src*(1-a)+gen*a).clip(0,255).astype(np.uint8)
    out[~binary] = src[~binary]
    return Image.fromarray(out)


def _phase9_surface_reconstruction(image: Image.Image, object_mask: np.ndarray):
    """Build a deterministic, surface-aware reconstruction candidate.

    Phase 9 treats a furniture hole as a *room-surface reconstruction* problem,
    not a generic image-generation problem. The pipeline estimates wall/floor/
    rug regions, removes the selected object's contact shadow, reconstructs each
    surface independently, and finally restores the untouched original pixels.

    Returns (candidate, reconstruction_mask, diagnostics). It is intentionally
    conservative: if surface inference is weak, the caller can keep the RORem
    candidate instead of forcing this result.
    """
    surfaces = _surface_masks(image)
    surface_map = _nearest_surface_map(object_mask, surfaces)
    reconstruction_mask = _add_contact_shadow_mask(image, object_mask, surface_map)

    # Reclassify only the newly added shadow pixels; this keeps the surface map
    # tied to the actual hole instead of changing the user's selection globally.
    candidate = _surface_guided_reconstruct(image, reconstruction_mask, surface_map)
    candidate = _surface_texture_fill(candidate, reconstruction_mask, surface_map)

    # A tiny contour-only blend reduces a hard boundary without blurring the
    # interior reconstruction. The original image remains byte-for-byte intact
    # outside reconstruction_mask.
    if cv2 is not None:
        src = np.asarray(image.convert("RGB"), np.float32)
        gen = np.asarray(candidate.convert("RGB"), np.float32)
        m = reconstruction_mask.astype(np.uint8)
        contour = cv2.morphologyEx(m, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 0
        if contour.any():
            # Match local mean/luminance at the contour only. This avoids a
            # visible exposure step where the reconstructed surface meets the
            # real photographed surface.
            gray_src = cv2.cvtColor(src.astype(np.uint8), cv2.COLOR_RGB2GRAY).astype(np.float32)
            gray_gen = cv2.cvtColor(gen.astype(np.uint8), cv2.COLOR_RGB2GRAY).astype(np.float32)
            delta = cv2.GaussianBlur(gray_src - gray_gen, (0, 0), 5.0)
            for c in range(3):
                gen[..., c] += np.clip(delta * 0.55, -18.0, 18.0)
            candidate = Image.fromarray(np.clip(gen, 0, 255).astype(np.uint8), "RGB")

    # Composite strictly inside the reconstruction mask so contact-shadow
    # cleanup can never alter furniture or decor outside the intended footprint.
    candidate = _composite_inside_mask(image, candidate, Image.fromarray((reconstruction_mask * 255).astype(np.uint8), "L"))
    labeled = {"wall": int((surface_map == 2).sum()), "floor": int((surface_map == 1).sum()), "rug": int((surface_map == 3).sum()), "unknown": int((surface_map == 0).sum())}
    return candidate, reconstruction_mask, labeled


def _phase9_score_candidate(src: Image.Image, candidate: Image.Image, object_mask: np.ndarray):
    """Score a Phase 9 candidate against the original room."""
    total, seam, fp = _candidate_quality(src, candidate, Image.fromarray((object_mask * 255).astype(np.uint8), "L"))
    residual = _residual_structure_score(candidate, object_mask)
    change = _target_change_score(src, candidate, object_mask)
    score = float(total + 95.0 * residual + 90.0 * max(0.0, 0.24 - change))
    return score, seam, fp, residual, change


def _phase9_edge_harmonize(original: Image.Image, candidate: Image.Image, mask: np.ndarray) -> Image.Image:
    """Low-risk finishing only: harmonize the boundary, never invent surfaces.

    Phase 9.1 deliberately does NOT replace the proven RORem reconstruction with
    the deterministic surface generator. The previous Phase 9 implementation
    could select a blurry surface-transfer candidate and visibly downgrade a
    good removal. This pass only adjusts a narrow boundary band.
    """
    if cv2 is None:
        return candidate
    try:
        src=np.asarray(original.convert("RGB"), np.float32)
        gen=np.asarray(candidate.convert("RGB"), np.float32)
        m=(np.asarray(mask, bool)).astype(np.uint8)
        if not m.any(): return candidate
        ring=cv2.dilate(m, np.ones((7,7),np.uint8), iterations=1).astype(bool) & ~m.astype(bool)
        band=cv2.dilate(m, np.ones((5,5),np.uint8), iterations=1).astype(bool) & ~cv2.erode(m,np.ones((5,5),np.uint8),iterations=1).astype(bool)
        if not band.any(): return candidate
        gray_src=cv2.cvtColor(src.astype(np.uint8),cv2.COLOR_RGB2GRAY).astype(np.float32)
        gray_gen=cv2.cvtColor(gen.astype(np.uint8),cv2.COLOR_RGB2GRAY).astype(np.float32)
        # Estimate only a small local luminance offset at the seam.
        diff=(gray_src-gray_gen)
        local=cv2.GaussianBlur(diff,(0,0),3.0)
        adj=np.clip(local*0.25,-10.0,10.0)
        out=gen.copy()
        for c in range(3): out[...,c][band] += adj[band]
        # Feather only a 2px boundary; interior pixels stay exactly as produced by the model.
        feather=cv2.GaussianBlur(m.astype(np.float32),(0,0),1.2)
        alpha=np.clip(feather,0,1)
        for c in range(3):
            out[...,c]=gen[...,c]*(1-0.10*alpha)+out[...,c]*(0.10*alpha)
        return Image.fromarray(np.clip(out,0,255).astype(np.uint8),'RGB')
    except Exception as exc:
        print(f"Phase9.1 edge harmonization skipped: {exc}")
        return candidate



def _scene_layer_map(image: Image.Image, object_mask: np.ndarray):
    """Build a conservative scene-layer map for the selected furniture hole.

    Phase 9.7 turns surface understanding into an active reconstruction control:
    the wall, floor and rug portions of a single furniture mask are reconstructed
    independently instead of forcing one model pass to solve the whole object.

    IDs: 0=unknown, 1=floor, 2=wall, 3=rug/carpet.
    The function deliberately never expands the user's object mask.
    """
    surfaces = _surface_masks(image)
    surface_map = _nearest_surface_map(object_mask, surfaces)
    if cv2 is not None:
        # Remove tiny classification islands inside the confirmed object.  We
        # only operate on the label map, never on the original image or mask.
        cleaned = surface_map.copy()
        for sid in (1, 2, 3):
            part = (surface_map == sid) & object_mask
            if not part.any():
                continue
            n, labels, stats, _ = cv2.connectedComponentsWithStats(part.astype(np.uint8), 8)
            keep = np.zeros_like(part)
            min_area = max(32, int(object_mask.sum() * 0.002))
            for i in range(1, n):
                if int(stats[i, cv2.CC_STAT_AREA]) >= min_area:
                    keep |= labels == i
            # Preserve small regions at the object boundary; they can be thin
            # floor/wall slivers and should not become unknown simply because
            # the segmentation model is coarse.
            boundary = part & ~cv2.erode(part.astype(np.uint8), np.ones((3, 3), np.uint8), 1).astype(bool)
            cleaned[(part & ~keep) & ~boundary] = 0
        surface_map = cleaned
    counts = {
        "wall": int(((surface_map == 2) & object_mask).sum()),
        "floor": int(((surface_map == 1) & object_mask).sum()),
        "rug": int(((surface_map == 3) & object_mask).sum()),
        "unknown": int(((surface_map == 0) & object_mask).sum()),
    }
    return surface_map, counts


def _surface_partition_mask(object_mask: np.ndarray, surface_map: np.ndarray, sid: int) -> np.ndarray:
    """Return one surface-specific portion of the confirmed furniture mask."""
    part = object_mask & (surface_map == sid)
    if not part.any():
        return part
    if cv2 is None:
        return part
    # Close tiny holes but do not grow outside the original selection.
    u8 = (part.astype(np.uint8) * 255)
    u8 = cv2.morphologyEx(u8, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8), 1)
    return (u8 > 127) & object_mask


def _surface_aware_rorem_candidate(src: Image.Image, object_mask: np.ndarray):
    """Reconstruct a large object as several surface-specific removal problems.

    This is the key Phase 9.7 architecture change. A bed covering wall + rug is
    not one homogeneous hole. RORem is run on each sufficiently large surface
    partition with a context tuned to that surface, then the results are merged
    only inside the original confirmed mask.
    """
    if not object_mask.any():
        return None
    pipe = get_rorem_inpainter()
    if pipe is None:
        return None
    surface_map, counts = _scene_layer_map(src, object_mask)
    total_pixels = max(1, int(object_mask.sum()))
    parts = []
    # Wall first: it needs broad contextual continuity but little texture.
    for sid, name, ctx, dil, seed in (
        (2, "wall", 0.42, 12, 17041),
        (3, "rug", 0.38, 14, 24019),
        (1, "floor", 0.46, 12, 31117),
        (0, "unknown", 0.30, 16, 40009),
    ):
        part = _surface_partition_mask(object_mask, surface_map, sid)
        ratio = float(part.sum()) / total_pixels
        if ratio < 0.035:
            continue
        try:
            cand, _ = _rorem_remove_candidate(
                pipe, src, part, seed, context_ratio=ctx,
                dilation_px=dil, steps=40
            )
            # Evaluate only the partition, so a good wall reconstruction is not
            # rejected because the floor portion belongs to another candidate.
            metrics = _removal_quality_score(src, cand, part)
            parts.append((sid, name, part, cand, metrics))
            print(
                f"Phase9.7 surface={name} ratio={ratio:.3f}: "
                f"score={metrics[0]:.2f} seam={metrics[1]:.2f} "
                f"fp={metrics[2]:.3f} change={metrics[3]:.3f} "
                f"residual={metrics[4]:.3f} texture={metrics[5]:.2f} surface={metrics[6]:.3f}"
            )
        except Exception as exc:
            print(f"Phase9.7 surface={name} failed: {exc}")

    if not parts:
        return None

    result = src.copy()
    accepted = []
    for sid, name, part, cand, metrics in parts:
        # A partition is accepted when it is genuinely changed and not a strong
        # furniture-shaped residual. Texture is checked separately for floor/rug.
        _, seam, fp, change, residual, texture, surface_consistency = metrics
        t = REMOVAL_GATE_THRESHOLDS
        min_texture = t["surface_partition_min_texture_wall_floor"] if sid in (1, 3) else t["surface_partition_min_texture_other"]
        good = (change >= t["surface_partition_min_change"] and
                residual <= t["surface_partition_max_residual"] and
                seam <= t["surface_partition_max_seam"] and
                fp <= t["surface_partition_max_furniture_penalty"] and
                texture >= min_texture)
        if not good:
            # LaMa is a deterministic local recovery for the failed partition.
            try:
                lama_img, _ = _lama_remove_candidate(src, part, Image.fromarray((part * 255).astype(np.uint8), "L"))
                lm = _removal_quality_score(src, lama_img, part)
                if lm[3] >= change and lm[4] <= residual + 0.08:
                    cand, metrics = lama_img, lm
                    _, seam, fp, change, residual, texture, surface_consistency = lm
                    good = (change >= t["surface_lama_min_change"] and
                            residual <= t["surface_lama_max_residual"] and
                            seam <= t["surface_lama_max_seam"] and
                            fp <= t["surface_lama_max_furniture_penalty"] and
                            texture >= min_texture)
                    print(f"Phase9.7 surface={name} LaMa recovery: seam={seam:.2f} fp={fp:.3f} change={change:.3f} residual={residual:.3f} texture={texture:.2f}")
            except Exception as exc:
                print(f"Phase9.7 surface={name} LaMa recovery failed: {exc}")
        if good:
            accepted.append((sid, name, part, cand, metrics))

    # Do not return a partial reconstruction unless every substantial surface
    # portion was solved. Otherwise the composite would create a new seam at the
    # boundary between a reconstructed surface and the old furniture pixels.
    substantial = [p for p in parts if (p[2].sum() / total_pixels) >= 0.035]
    if len(accepted) < len(substantial):
        print(f"Phase9.7 surface ensemble rejected: accepted={len(accepted)} substantial={len(substantial)}")
        return None

    for sid, name, part, cand, metrics in accepted:
        result = _composite_inside_mask(result, cand, Image.fromarray((part * 255).astype(np.uint8), "L"))

    # One very narrow contour harmonization across the union. Interior pixels
    # remain exactly those generated by the surface-specific candidates.
    result = _phase9_edge_harmonize(src, result, object_mask)
    full_metrics = _removal_quality_score(src, result, object_mask)
    print(
        f"Phase9.7 surface ensemble selected: score={full_metrics[0]:.2f} "
        f"seam={full_metrics[1]:.2f} fp={full_metrics[2]:.3f} "
        f"change={full_metrics[3]:.3f} residual={full_metrics[4]:.3f} "
        f"texture={full_metrics[5]:.2f} surface={full_metrics[6]:.3f} surfaces={counts}"
    )
    return {
        "image": result,
        "metrics": full_metrics,
        "surface_map": surface_map,
        "surface_counts": counts,
        "accepted_surfaces": [name for _, name, *_ in accepted],
        "passed_gate": _candidate_passes_removal_gate(full_metrics),
    }

def _surface_candidate_for_large_object(src: Image.Image, object_mask: np.ndarray):
    """Build and score the deterministic surface reconstruction as a real candidate.

    Phase 9.3 only reached this engine after both neural removal paths failed.
    That made the most geometry-faithful path effectively dead in normal runs.
    For room furniture removal, wall/floor/rug reconstruction is often better
    represented by visible pixels from the same surface than by a generative prior.
    """
    try:
        candidate, reconstruction_mask, surfaces = _phase9_surface_reconstruction(src, object_mask)
        # Score the user-confirmed furniture mask, not the optional contact-shadow halo.
        metrics = _removal_quality_score(src, candidate, object_mask)
        total, seam, fp, change, residual, texture_ratio, surface_consistency = metrics
        print(
            f"Phase9.4 surface candidate: score={total:.2f}, seam={seam:.2f}, "
            f"furniture_penalty={fp:.4f}, target_change={change:.3f}, "
            f"residual_structure={residual:.3f}, texture_ratio={texture_ratio:.2f}, "
            f"surfaces={surfaces}"
        )
        return {
            "image": candidate,
            "mask": reconstruction_mask,
            "metrics": metrics,
            "passed_gate": _candidate_passes_removal_gate(metrics),
            "backend": "surface-first-phase9.4",
            "surfaces": surfaces,
        }
    except Exception as exc:
        print(f"Phase9.4 surface candidate unavailable: {exc}")
        return None


def hybrid_inpaint(image: Image.Image, mask_img: Image.Image, allow_below_gate: bool = False):
    """Run the REMOVE-ONLY reconstruction pipeline with an honest final gate.

    A candidate is returned normally only when it passes the authoritative gate.
    ``allow_below_gate=True`` is an explicit debug escape hatch that returns the
    best rejected candidate marked ``unconfirmed`` for inspection; normal calls
    fail loudly instead of presenting a failed reconstruction as successful.
    """
    hard_mask_img = prepare_inpaint_mask(mask_img, image.size)
    _phase97_capture_mask(hard_mask_img)
    _phase97_capture_image("original.png", image.convert("RGB"))
    object_mask = np.asarray(hard_mask_img.convert("L")) > 127
    if not object_mask.any():
        raise HTTPException(status_code=422, detail="Masque vide")
    ys, xs = np.where(object_mask)
    area_ratio = float(object_mask.mean())
    print(
        f"[Phase 9.1] mask={100*area_ratio:.2f}% "
        f"bbox=({int(xs.min())},{int(ys.min())})-({int(xs.max())},{int(ys.max())}) size={image.size}"
    )

    def _unconfirmed_or_raise(src, choices, reason="Aucun candidat n'a satisfait le quality gate"):
        if not choices:
            raise HTTPException(status_code=503, detail=reason)
        winner = min(choices, key=_fallback_key)
        name, win_img, win_metrics, win_meta = winner
        meta = dict(win_meta)
        meta.update({
            "passed_gate": False,
            "unconfirmed": True,
            "quality_warning": True,
            "reconstruction_strategy": f"{meta.get('reconstruction_strategy', 'reconstruction')}-unconfirmed",
        })
        print(
            f"Phase9.7 UNCONFIRMED candidate={name}: score={win_metrics[0]:.2f}, "
            f"seam={win_metrics[1]:.2f} fp={win_metrics[2]:.4f} "
            f"change={win_metrics[3]:.3f} residual={win_metrics[4]:.3f} "
            f"texture_ratio={win_metrics[5]:.2f} surface_consistency={win_metrics[6]:.3f}"
        )
        diagnostic_final = _composite_inside_mask(src, win_img, hard_mask_img)
        _phase97_capture_image("composited_final.png", diagnostic_final)
        if not allow_below_gate:
            raise HTTPException(status_code=422, detail=reason + "; résultat non retourné en mode normal")
        return diagnostic_final, meta

    def _append_choice(choices, name, img, metrics, meta):
        if img is None or metrics is None:
            return
        choices.append((name, img, metrics, meta))

    def _fallback_key(item):
        _, _, mt, _ = item
        total, sm_seam, sm_fp, sm_change, sm_residual, sm_texture, sm_surface = mt
        texture_deficit = max(0.0, 0.55 - sm_texture)
        surface_deficit = max(0.0, REMOVAL_GATE_THRESHOLDS["min_surface_consistency"] - sm_surface)
        noop_deficit = max(0.0, REMOVAL_GATE_THRESHOLDS["noop_target_change"] - sm_change)
        return (
            texture_deficit * 260.0
            + surface_deficit * 220.0
            + noop_deficit * 120.0
            + sm_residual * 120.0
            + sm_seam
            + 80.0 * sm_fp,
            -sm_surface,
            -sm_texture,
            -sm_change,
            sm_seam,
        )

    if area_ratio >= 0.015:
        src = image.convert("RGB")
        choices = []

        rorem_result = None
        try:
            rorem_result = _remove_only_large_object(src, object_mask)
        except Exception as exc:
            print(f"RORem exception: {exc}")

        if rorem_result is not None:
            cand = _phase9_edge_harmonize(src, rorem_result["image"], object_mask)
            rorem_metrics = _removal_quality_score(src, cand, object_mask)
            print(
                f"Phase9.3 RORem final recheck: seam={rorem_metrics[1]:.2f} "
                f"fp={rorem_metrics[2]:.4f} change={rorem_metrics[3]:.3f} "
                f"residual={rorem_metrics[4]:.3f} texture_ratio={rorem_metrics[5]:.2f} "
                f"surface_consistency={rorem_metrics[6]:.3f}"
            )
            if _candidate_passes_removal_gate(rorem_metrics):
                final_img = _composite_inside_mask(src, cand, hard_mask_img)
                _phase97_capture_image("rorem_raw_selected.png", rorem_result.get("image", cand))
                _phase97_capture_image("composited_final.png", final_img)
                return final_img, {
                    "passed_gate": True,
                    "backend": rorem_result.get("backend", "rorem-official-512-square-ensemble"),
                    "seam": rorem_metrics[1], "furniture_penalty": rorem_metrics[2], "phase9": True,
                    "reconstruction_strategy": "rorem-primary", "residual_structure": rorem_metrics[4],
                    "target_change": rorem_metrics[3], "texture_ratio": rorem_metrics[5],
                    "surface_consistency": rorem_metrics[6],
                }
            rorem_meta = {
                "passed_gate": False,
                "backend": rorem_result.get("backend", "rorem-official-512-square-ensemble"),
                "seam": rorem_metrics[1], "furniture_penalty": rorem_metrics[2], "phase9": True,
                "reconstruction_strategy": "rorem-rejected", "residual_structure": rorem_metrics[4],
                "target_change": rorem_metrics[3], "quality_warning": True,
                "texture_ratio": rorem_metrics[5], "surface_consistency": rorem_metrics[6],
                "selection_score": rorem_metrics[0],
            }
            _append_choice(choices, "rorem", cand, rorem_metrics, rorem_meta)
            print(
                f"Phase9.3 RORem rejected its gate -> recovery tiers: "
                f"seam={rorem_metrics[1]:.2f} fp={rorem_metrics[2]:.4f} "
                f"change={rorem_metrics[3]:.3f} residual={rorem_metrics[4]:.3f} "
                f"texture_ratio={rorem_metrics[5]:.2f} surface_consistency={rorem_metrics[6]:.3f}"
            )

        surface_result = _surface_candidate_for_large_object(src, object_mask)
        surface_ensemble = _surface_aware_rorem_candidate(src, object_mask)

        if surface_ensemble is not None:
            em = surface_ensemble["metrics"]
            emeta = {
                "passed_gate": bool(surface_ensemble["passed_gate"]),
                "backend": "surface-aware-rorem-ensemble",
                "seam": em[1], "furniture_penalty": em[2], "phase9": True,
                "reconstruction_strategy": "surface-aware-ensemble" if surface_ensemble["passed_gate"] else "surface-aware-ensemble-rejected",
                "residual_structure": em[4], "target_change": em[3], "texture_ratio": em[5],
                "surface_consistency": em[6], "surface_map": surface_ensemble.get("surface_counts"),
                "quality_warning": not surface_ensemble["passed_gate"],
            }
            if surface_ensemble["passed_gate"]:
                print("Phase9.7 surface-aware ensemble PASSED removal gate -> selected")
                _phase97_capture_image("surface_raw.png", surface_ensemble["image"])
                final_img = _composite_inside_mask(src, surface_ensemble["image"], hard_mask_img)
                _phase97_capture_image("composited_final.png", final_img)
                return final_img, emeta
            _append_choice(choices, "surface-aware", surface_ensemble["image"], em, emeta)

        if surface_result is not None:
            sm = surface_result["metrics"]
            smeta = {
                "passed_gate": bool(surface_result["passed_gate"]),
                "backend": surface_result["backend"], "seam": sm[1], "furniture_penalty": sm[2], "phase9": True,
                "reconstruction_strategy": "surface-first" if surface_result["passed_gate"] else "surface-first-rejected",
                "residual_structure": sm[4], "target_change": sm[3], "texture_ratio": sm[5],
                "surface_consistency": sm[6], "surface_map": surface_result["surfaces"],
                "quality_warning": not surface_result["passed_gate"],
            }
            if surface_result["passed_gate"]:
                print("Phase9.4 surface candidate PASSED removal gate -> selected")
                _phase97_capture_image("surface_raw.png", surface_result["image"])
                final_img = _composite_inside_mask(src, surface_result["image"], hard_mask_img)
                _phase97_capture_image("composited_final.png", final_img)
                return final_img, smeta
            _append_choice(choices, "surface", surface_result["image"], sm, smeta)

        try:
            lama_img, _ = _lama_remove_candidate(src, object_mask, hard_mask_img)
            _phase97_capture_image("lama_raw.png", lama_img)
            lama_img = _phase9_edge_harmonize(src, lama_img, object_mask)
            lama_metrics = _removal_quality_score(src, lama_img, object_mask)
            lama_gate_passed = _candidate_passes_removal_gate(lama_metrics)
            print(
                f"Phase9.1 LaMa recovery: seam={lama_metrics[1]:.2f} fp={lama_metrics[2]:.4f} "
                f"change={lama_metrics[3]:.3f} residual={lama_metrics[4]:.3f} "
                f"texture_ratio={lama_metrics[5]:.2f} surface_consistency={lama_metrics[6]:.3f} "
                f"passed_gate={lama_gate_passed}"
            )
            lama_meta = {
                "passed_gate": lama_gate_passed,
                "backend": "lama-large-object-recovery",
                "seam": lama_metrics[1], "furniture_penalty": lama_metrics[2], "phase9": True,
                "reconstruction_strategy": "lama-recovery", "residual_structure": lama_metrics[4],
                "target_change": lama_metrics[3], "texture_ratio": lama_metrics[5],
                "surface_consistency": lama_metrics[6], "quality_warning": not lama_gate_passed,
            }
            if lama_gate_passed:
                final_img = _composite_inside_mask(src, lama_img, hard_mask_img)
                _phase97_capture_image("composited_final.png", final_img)
                return final_img, lama_meta
            _append_choice(choices, "lama", lama_img, lama_metrics, lama_meta)
        except Exception as exc:
            print(f"LaMa recovery failed: {exc}")

        return _unconfirmed_or_raise(src, choices)

    # Small-object path: it must report the same gate semantics as large objects.
    try:
        lama_img = get_lama()(image, hard_mask_img)
        _phase97_capture_image("lama_raw.png", lama_img)
        lama_metrics = _removal_quality_score(image.convert("RGB"), lama_img, object_mask)
        passed = _candidate_passes_removal_gate(lama_metrics)
        print(
            f"Phase9.7 small-object LaMa: seam={lama_metrics[1]:.2f} fp={lama_metrics[2]:.4f} "
            f"change={lama_metrics[3]:.3f} residual={lama_metrics[4]:.3f} "
            f"texture_ratio={lama_metrics[5]:.2f} surface_consistency={lama_metrics[6]:.3f} "
            f"passed_gate={passed}"
        )
        meta = {
            "passed_gate": passed,
            "backend": "lama-small-object",
            "seam": lama_metrics[1], "furniture_penalty": lama_metrics[2], "phase9": True,
            "reconstruction_strategy": "lama-small-object",
            "residual_structure": lama_metrics[4], "target_change": lama_metrics[3],
            "texture_ratio": lama_metrics[5], "surface_consistency": lama_metrics[6],
            "quality_warning": not passed,
        }
        if passed:
            final_img = _composite_inside_mask(image, lama_img, hard_mask_img)
            _phase97_capture_image("composited_final.png", final_img)
            return final_img, meta
        return _unconfirmed_or_raise(image, [("lama-small", lama_img, lama_metrics, meta)])
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Inpainting petit objet impossible: {exc}") from exc


@app.get("/inpaint-status")
async def inpaint_status():
    """Expose the REMOVE-ONLY engine status."""
    lama_available = False
    try:
        import simple_lama_inpainting  # noqa: F401
        lama_available = True
    except Exception:
        pass
    t = REMOVAL_GATE_THRESHOLDS
    return JSONResponse({
        "phase": APP_VERSION.rsplit(".", 1)[0],
        "version": APP_VERSION,
        "mode": "REMOVE-ONLY",
        "large_object_strategy": "Mask Engine V4.2 physical-family selection + RORem adaptive-context ensemble + local LaMa recovery + surface fallback; visual removal gate",
        "lama_package_available": lama_available,
        "generative_replacement_enabled": False,
        "surface_texture_fallback": "planar wall interpolation + local LaMa for rug/floor",
        "quality_gate": dict(t),
        "failure_policy": {
            "normal": "fail_if_no_candidate_passes",
            "debug": "return_best_unconfirmed_candidate",
            "debug_parameter": "allow_below_gate",
        },
    })


@app.post("/select-mask")
async def select_mask(file: UploadFile = File(...), x: int = Form(...), y: int = Form(...)):
    """Phase 4 smart-selection endpoint used by the frontend preview step."""
    image = read_image(file)
    segmented = await _segment_furniture_at_point(image, x, y)
    _, label, mask, mask_meta = segmented
    arr=np.asarray(mask.convert("L"))>127
    ys,xs=np.where(arr)
    ratio=float(arr.mean())
    if xs.size < 80:
        raise HTTPException(status_code=422, detail="Masque trop petit — cliquez davantage au centre du meuble.")
    if ratio > 0.42:
        raise HTTPException(status_code=422, detail="Masque non fiable — le modèle a sélectionné une zone trop grande. Cliquez au centre du meuble.")
    bbox={"x":int(xs.min()),"y":int(ys.min()),"width":int(xs.max()-xs.min()+1),"height":int(ys.max()-ys.min()+1)}
    # No max_side cap here: unlike the /analyze debug overlays, this exact
    # mask is what the frontend redraws to a canvas and posts back for the
    # real /inpaint call (see eraser.js). Downscaling it here was throwing
    # away SAM's full-resolution boundary before reconstruction ever saw it,
    # for a single-channel silhouette that compresses to a tiny PNG anyway.
    # Evidence-based V3 confidence. This is a descriptive confidence score,
    # not a calibrated probability; unlike the old fixed 0.72–0.94 range it
    # changes with the actual returned mask geometry.
    confidence = _mask_confidence(arr, x, y, np.zeros_like(arr, dtype=bool))
    related = []
    for item in _detect_related_furniture(image, x, y, arr, label):
        related.append({
            "mask": to_data_url(item["mask"]),
            "label": item["label"],
            "bbox": item["bbox"],
            "score": round(float(item["score"]), 3),
            "area_ratio": round(float(np.asarray(item["mask"].convert("L" )).mean()/255.0), 5),
            "method": "SegFormer component",
        })
    return JSONResponse({
        "mask": to_data_url(mask), "label": label, "area_ratio": ratio, "bbox": bbox,
        "confidence": confidence,
        "method": mask_meta.get("engine", "Mask Engine V4.2"),
        "mask_diagnostics": mask_meta,
        "related": related,
        "selection_version": 4.2,
    })


@app.post("/inpaint")
async def inpaint(
    file: UploadFile = File(...),
    mask: UploadFile = File(...),
    debug: bool = Form(False),
    selection_keys: str = Form(""),
):
    """Remove a user-confirmed furniture mask with LaMa."""
    image = read_image(file)
    try:
        raw = await mask.read()
        mask_img = Image.open(io.BytesIO(raw)).convert("L")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Masque invalide: {exc}") from exc
    try:
        submitted_keys = json.loads(selection_keys) if selection_keys else []
    except Exception:
        submitted_keys = []
    mask_arr = np.asarray(mask_img, dtype=np.uint8) > 127
    if cv2 is not None:
        component_count, _, _, _ = cv2.connectedComponentsWithStats(mask_arr.astype(np.uint8), 8)
        component_count = max(0, int(component_count - 1))
    else:
        component_count = None
    print(
        f"Phase9.7 selection boundary: keys={submitted_keys!r} "
        f"union_mask_components={component_count} area_ratio={float(mask_arr.mean()):.4f}"
    )
    result, meta = hybrid_inpaint(image, mask_img, allow_below_gate=debug)
    if not isinstance(result, Image.Image):
        result = Image.fromarray(np.asarray(result).astype(np.uint8))
    return JSONResponse({
        "image": to_data_url(result),
        "width": result.width,
        "height": result.height,
        "label": "meuble",
        "method": "ai-remove-only",
        "quality_gate_passed": meta["passed_gate"],
        "unconfirmed": bool(meta.get("unconfirmed", False)),
        "quality_warning": bool(meta.get("quality_warning", False)),
        "backend": meta["backend"],
        "seam": meta["seam"],
        "furniture_penalty": meta["furniture_penalty"],
        "phase9": meta.get("phase9", False),
        "residual_structure": meta.get("residual_structure"),
        "target_change": meta.get("target_change"),
        "texture_ratio": meta.get("texture_ratio"),
        "surface_consistency": meta.get("surface_consistency"),
        "reconstruction_mask_area": meta.get("reconstruction_mask_area"),
    })


@app.post("/remove")
async def remove(file: UploadFile = File(...), x: int = Form(...), y: int = Form(...), debug: bool = Form(False)):
    """One-click backward-compatible remove endpoint using precise selection."""
    image = read_image(file)
    _, label, mask, _mask_meta = await _segment_furniture_at_point(image, x, y)
    result, meta = hybrid_inpaint(image, mask, allow_below_gate=debug)
    return JSONResponse({
        "image": to_data_url(result),
        "width": result.width,
        "height": result.height,
        "label": label,
        "method": "ai-remove-only",
        "quality_gate_passed": meta["passed_gate"],
        "unconfirmed": bool(meta.get("unconfirmed", False)),
        "quality_warning": bool(meta.get("quality_warning", False)),
        "backend": meta["backend"],
        "seam": meta["seam"],
        "furniture_penalty": meta["furniture_penalty"],
        "phase9": meta.get("phase9", False),
        "residual_structure": meta.get("residual_structure"),
        "target_change": meta.get("target_change"),
        "texture_ratio": meta.get("texture_ratio"),
        "surface_consistency": meta.get("surface_consistency"),
        "reconstruction_mask_area": meta.get("reconstruction_mask_area"),
    })


# ---------------------------------------------------------------------------
# Phase 6 — optional single-process mode.
#
# Set CIGOGNE_SINGLE_PROCESS=1 to mount the accounts/projects/catalogue/queue
# API onto this same service, so a demo machine runs one command instead of
# three. In production the two are separate processes: this one needs a GPU,
# the other does not.
# ---------------------------------------------------------------------------
if _os.environ.get("CIGOGNE_SINGLE_PROCESS") == "1":     # pragma: no cover
    try:
        from app.api import attach as _attach_api
        _attach_api(app)
        print("[Phase 6] API modules mounted on the model service (single-process mode).")
    except Exception as _exc:
        print(f"[Phase 6] API modules not mounted: {_exc}")

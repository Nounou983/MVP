#!/usr/bin/env python3
"""Reusable Phase 9.7 artifact-boundary diagnostic.

The tool is intentionally artifact-bound: it never fabricates neural-model
outputs.  A manifest can describe any number of runtime cases.  For each case
it saves/validates the exact mask, raw candidate(s), final composite and alpha
map, computes mask geometry, exact compositing invariants, and (when the
project scoring functions are importable) per-connected-component quality
scores.

Example:
  python server/tools/phase97_artifact_trace.py \
      --manifest diagnostics/phase97_cases.json \
      --output-dir diagnostics/phase97_round3_runtime

A manifest case looks like:
{
  "id": "case_b",
  "image": "runtime/original.png",
  "mask": "runtime/mask.png",
  "rorem_raw_selected": "runtime/rorem_selected.png",
  "lama_raw": null,
  "surface_raw": null,
  "final": "runtime/final.png"
}

For missing runtime artifacts the script emits a clearly labelled placeholder
and records status=not_captured.  Missing artifacts are never interpreted as
model output.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw


def connected_components(binary: np.ndarray) -> int:
    try:
        import cv2
        n, _, _, _ = cv2.connectedComponentsWithStats(binary.astype(np.uint8), 8)
        return max(0, int(n - 1))
    except Exception:
        h, w = binary.shape
        seen = np.zeros_like(binary, dtype=bool)
        count = 0
        for y, x in zip(*np.where(binary & ~seen)):
            if seen[y, x]:
                continue
            count += 1
            stack = [(int(y), int(x))]
            seen[y, x] = True
            while stack:
                cy, cx = stack.pop()
                for dy, dx in ((1,0),(-1,0),(0,1),(0,-1)):
                    ny, nx = cy + dy, cx + dx
                    if 0 <= ny < h and 0 <= nx < w and binary[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
        return count


def write_placeholder(path: Path, title: str, subtitle: str) -> None:
    im = Image.new("RGB", (1200, 500), (35, 35, 35))
    d = ImageDraw.Draw(im)
    d.text((50, 150), title, fill=(255, 255, 255))
    d.text((50, 225), subtitle, fill=(230, 185, 80))
    im.save(path)


def mask_stats(mask: Image.Image) -> dict:
    arr = np.asarray(mask.convert("L"), dtype=np.uint8)
    binary = arr > 127
    ys, xs = np.where(binary)
    return {
        "width": int(arr.shape[1]),
        "height": int(arr.shape[0]),
        "area_ratio": float(binary.mean()),
        "area_pixels": int(binary.sum()),
        "bbox_xyxy_exclusive": (
            [int(xs.min()), int(ys.min()), int(xs.max()+1), int(ys.max()+1)]
            if xs.size else None
        ),
        "connected_components": connected_components(binary),
    }


def save_mask_artifact(src: Path, dest: Path) -> tuple[Image.Image, str]:
    if src and src.exists():
        mask = Image.open(src).convert("L")
        # Runtime contract is binary. Preserve the submitted pixels exactly;
        # validation below reports any non-binary values instead of repairing it.
        mask.save(dest)
        return mask, "exact_runtime_mask"
    raise FileNotFoundError(str(src) if src else "mask not supplied")


def compare_original_raw_final(
    original: Image.Image, raw: Image.Image, final: Image.Image, mask: Image.Image
) -> dict:
    size = original.size
    src = np.asarray(original.convert("RGB").resize(size), dtype=np.int16)
    gen = np.asarray(raw.convert("RGB").resize(size), dtype=np.int16)
    out = np.asarray(final.convert("RGB").resize(size), dtype=np.int16)
    m = np.asarray(mask.convert("L").resize(size, Image.Resampling.NEAREST)) > 127
    outside = np.abs(out - src)
    inside_raw = np.abs(out - gen)
    return {
        "outside_mask_max_rgb_difference": int(outside[~m].max()) if (~m).any() else 0,
        "inside_mask_max_difference_vs_raw": int(inside_raw[m].max()) if m.any() else 0,
        "inside_mask_exact_match_to_raw": bool(np.array_equal(out[m], gen[m])),
        "outside_mask_exact_match_to_original": bool(np.array_equal(out[~m], src[~m])),
    }


def component_stats(mask_arr: np.ndarray) -> list[dict]:
    import cv2
    n, labels, stats, centroids = cv2.connectedComponentsWithStats(mask_arr.astype(np.uint8), 8)
    out = []
    for label in range(1, n):
        x, y, w, h, area = [int(v) for v in stats[label]]
        out.append({
            "component": label,
            "area_pixels": area,
            "area_ratio": float(area / mask_arr.size),
            "bbox_xywh": [x, y, w, h],
            "centroid_xy": [float(centroids[label][0]), float(centroids[label][1])],
        })
    return out


def score_components(project_root: Path, original: Image.Image, raw: Image.Image,
                     mask: Image.Image) -> dict:
    """Score each disjoint mask component with the same production scorer.

    This is diagnostic-only. It imports the production scoring functions but
    does not run the inpainting pipeline.
    """
    server_dir = project_root / "server"
    sys.path.insert(0, str(server_dir))
    try:
        import main as app_main
    except Exception as exc:
        return {"status": "unavailable", "reason": f"could not import server.main: {exc}"}

    import cv2
    arr = np.asarray(mask.convert("L")) > 127
    n, labels, _, _ = cv2.connectedComponentsWithStats(arr.astype(np.uint8), 8)
    results = []
    for label in range(1, n):
        comp = labels == label
        if int(comp.sum()) < 20:
            continue
        try:
            metrics = app_main._removal_quality_score(original, raw, comp)
            results.append({
                "component": label,
                "pixels": int(comp.sum()),
                "metrics": {
                    "total": float(metrics[0]),
                    "seam": float(metrics[1]),
                    "furniture_penalty": float(metrics[2]),
                    "target_change": float(metrics[3]),
                    "residual_structure": float(metrics[4]),
                    "texture_ratio": float(metrics[5]),
                    "surface_consistency": float(metrics[6]),
                },
                "passed_gate": bool(app_main._candidate_passes_removal_gate(metrics)),
            })
        except Exception as exc:
            results.append({"component": label, "status": "error", "error": str(exc)})
    return {
        "status": "measured",
        "thresholds": dict(app_main.REMOVAL_GATE_THRESHOLDS),
        "components": results,
    }


def process_case(case: dict, output_root: Path, project_root: Path) -> dict:
    case_id = case["id"]
    out = output_root / case_id
    out.mkdir(parents=True, exist_ok=True)
    summary = {"case_id": case_id, "artifacts": {}, "tests": {}}

    def resolve(value):
        if not value:
            return None
        p = Path(value)
        return p if p.is_absolute() else (project_root / p)

    # Required named artifacts.
    artifact_map = {
        "mask_submitted.png": ("mask", "exact binary mask sent to /inpaint"),
        "rorem_raw_selected.png": ("rorem_raw_selected", "raw selected RORem output"),
        "lama_raw.png": ("lama_raw", "raw LaMa output"),
        "surface_raw.png": ("surface_raw", "raw surface-aware output"),
        "composited_final.png": ("final", "final composite returned/diagnostic"),
        "original.png": ("image", "exact source image"),
    }

    mask = None
    for dest_name, (key, desc) in artifact_map.items():
        src = resolve(case.get(key))
        dest = out / dest_name
        if src and src.exists():
            shutil.copy2(src, dest)
            summary["artifacts"][dest_name] = {"status": "captured", "source": str(src)}
            if key == "mask":
                mask = Image.open(dest).convert("L")
        else:
            if key == "mask":
                summary["artifacts"][dest_name] = {"status": "not_captured", "reason": "exact runtime mask not supplied"}
            else:
                write_placeholder(dest, dest_name, "NOT CAPTURED — requires exact RTX runtime artifact")
                summary["artifacts"][dest_name] = {"status": "not_captured", "reason": desc}

    if mask is not None:
        arr = np.asarray(mask, dtype=np.uint8)
        binary = arr > 127
        non_binary = int(np.count_nonzero((arr != 0) & (arr != 255)))
        stats = mask_stats(mask)
        summary["mask_metrics"] = {
            **stats,
            "non_binary_pixels": non_binary,
            "binary_contract": non_binary == 0,
            "components": component_stats(binary),
        }
        # Alpha map is the exact binary ownership map used by the diagnostic
        # composite. This is not inferred from the final RGB image.
        alpha = (binary.astype(np.uint8) * 255)
        Image.fromarray(alpha, "L").save(out / "alpha_map.png")
        summary["alpha_metrics"] = {
            "inside_alpha_min": int(alpha[binary].min()) if binary.any() else None,
            "inside_alpha_max": int(alpha[binary].max()) if binary.any() else None,
            "outside_alpha_min": int(alpha[~binary].min()) if (~binary).any() else None,
            "outside_alpha_max": int(alpha[~binary].max()) if (~binary).any() else None,
            "inside_all_255": bool(np.all(alpha[binary] == 255)) if binary.any() else True,
            "outside_all_0": bool(np.all(alpha[~binary] == 0)) if (~binary).any() else True,
        }
    else:
        write_placeholder(out / "alpha_map.png", "alpha_map.png", "NOT CAPTURED — requires exact runtime mask")
        summary["alpha_metrics"] = {"status": "not_measurable"}

    original = Image.open(out / "original.png").convert("RGB") if (out / "original.png").exists() and summary["artifacts"]["original.png"]["status"] == "captured" else None
    raw = Image.open(out / "rorem_raw_selected.png").convert("RGB") if summary["artifacts"]["rorem_raw_selected.png"]["status"] == "captured" else None
    final = Image.open(out / "composited_final.png").convert("RGB") if summary["artifacts"]["composited_final.png"]["status"] == "captured" else None

    if original is not None and raw is not None and final is not None and mask is not None:
        try:
            summary["compositing_invariants"] = compare_original_raw_final(original, raw, final, mask)
        except Exception as exc:
            summary["compositing_invariants"] = {"status": "error", "error": str(exc)}
        try:
            summary["per_component_scores"] = score_components(project_root, original, raw, mask)
        except Exception as exc:
            summary["per_component_scores"] = {"status": "error", "error": str(exc)}
    else:
        summary["compositing_invariants"] = {
            "status": "not_measurable",
            "reason": "requires exact original + mask + selected raw candidate + final composite",
        }
        summary["per_component_scores"] = {
            "status": "not_measurable",
            "reason": "requires exact original + exact mask + raw candidate",
        }

    (out / "diagnostic_report.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    args = ap.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    project_root = Path(__file__).resolve().parents[2]
    output_root = args.output_dir
    output_root.mkdir(parents=True, exist_ok=True)

    results = [process_case(case, output_root, project_root) for case in manifest["cases"]]
    (output_root / "trace_summary.json").write_text(
        json.dumps({"cases": results}, indent=2), encoding="utf-8"
    )
    print(json.dumps({
        "cases": [r["case_id"] for r in results],
        "output_dir": str(output_root),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

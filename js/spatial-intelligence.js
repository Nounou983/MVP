/* =========================================================
   La Cigogne D'Ailleurs — Spatial Intelligence
   Phase 11 — depth-aware, semantic furniture placement.

   This is deliberately model-light: it consumes the room analysis already
   produced by the project (floor mask, relative depth, RoomModel geometry)
   and turns it into a spatial placement layer. No new heavyweight model is
   required, so the feature works with the existing local stack.

   Responsibilities:
   - project furniture anchors onto the estimated floor;
   - use camera geometry for perspective-correct apparent scale;
   - choose semantic positions (sofa/bed against wall, table in front,
     lamps at sides, rug under seating, chairs near tables);
   - reject candidates that leave the floor or collide with another item;
   - preserve manual edits: the smart solver is used for new items and
     only when explicitly requested for an existing scene.
   ========================================================= */

(() => {
  "use strict";

  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const TAU = Math.PI * 2;

  function normBase(entry) {
    return String(entry?.base || "").toLowerCase();
  }

  function supportPoint(item, sizeOf) {
    const { w, h } = sizeOf(item);
    const c = Math.cos(item.rot || 0), s = Math.sin(item.rot || 0);
    // The lowest point in image space is the visual floor contact.
    const pts = [[-w/2,h/2],[w/2,h/2],[-w/2,-h/2],[w/2,-h/2]];
    let best = { x: item.x, y: item.y };
    for (const [lx, ly] of pts) {
      const p = { x: item.x + lx*c - ly*s, y: item.y + lx*s + ly*c };
      if (p.y > best.y) best = p;
    }
    return best;
  }

  function translateToSupport(item, target, sizeOf) {
    const p = supportPoint(item, sizeOf);
    item.x += target.x - p.x;
    item.y += target.y - p.y;
  }

  function sampleFloor(x, y, ctx) {
    const img = ctx.floorMaskImg, px = ctx.floorMaskPx, fit = ctx.fit;
    if (!img || !px || !fit || !fit.w || !fit.h) return true;
    const W = img.naturalWidth, H = img.naturalHeight;
    const ix = Math.round(((x - fit.x) / fit.w) * (W - 1));
    const iy = Math.round(((y - fit.y) / fit.h) * (H - 1));
    if (ix < 0 || iy < 0 || ix >= W || iy < 0 || iy >= H) return false;
    return px[(iy * W + ix) * 4] > 128;
  }

  function floorRatio(item, ctx) {
    if (!ctx.floorMaskImg || !ctx.floorMaskPx) return 1;
    const size = ctx.sizeOf(item);
    const samples = [
      [0, size.h * .42], [-size.w*.35, size.h*.34], [size.w*.35, size.h*.34],
      [-size.w*.42, size.h*.10], [size.w*.42, size.h*.10],
    ];
    const c = Math.cos(item.rot || 0), s = Math.sin(item.rot || 0);
    let ok = 0;
    for (const [lx, ly] of samples) {
      const x = item.x + lx*c - ly*s;
      const y = item.y + lx*s + ly*c;
      if (sampleFloor(x, y, ctx)) ok++;
    }
    return ok / samples.length;
  }

  function distanceScore(a, b) {
    return Math.hypot(a.x - b.x, a.y - b.y);
  }

  function collides(item, ctx) {
    const P = window.Placement;
    if (!P) return false;
    const shape = P.corners(item.x, item.y, ctx.sizeOf(item).w, ctx.sizeOf(item).h, item.rot || 0);
    for (const other of (ctx.items || [])) {
      if (!other || other.uid === item.uid) continue;
      const oe = ctx.entryOf?.(other);
      if (item.catId === "rug" || other.catId === "rug" ||
          item._spatialIgnoreCollision || oe?.placement?.under_furniture) continue;
      const os = ctx.sizeOf(other);
      if (P.overlaps(shape, P.corners(other.x, other.y, os.w, os.h, other.rot || 0))) return true;
    }
    return false;
  }

  function openingPenalty(point, ctx) {
    const openings = ctx.roomModel?.openings || [];
    if (!openings.length || !ctx.fit) return 0;
    let penalty = 0;
    for (const o of openings) {
      const b = o.box;
      if (!b) continue;
      const x = ctx.fit.x + (b.x + b.width * .5) * ctx.fit.w;
      const y = ctx.fit.y + (b.y + b.height) * ctx.fit.h;
      const dx = Math.abs(point.x - x) / Math.max(1, ctx.fit.w * b.width);
      const dy = Math.abs(point.y - y) / Math.max(1, ctx.fit.h * .25);
      if (dx < 1.2 && dy < 1.5) penalty += 1.5;
    }
    return penalty;
  }

  function depthAt(point, ctx) {
    const a = ctx.analysis, fit = ctx.fit;
    if (!a || !fit || !Array.isArray(a.floor_depth) || !a.floor_depth.length) return null;
    const row = clamp(Math.round(((point.y - fit.y) / Math.max(1, fit.h)) * (a.height - 1)), 0, a.height - 1);
    const d = Number(a.floor_depth[row]);
    return Number.isFinite(d) ? d : null;
  }

  function perspectiveFactor(item, ctx) {
    const model = ctx.roomModel || window.CigogneRoom?.model;
    const fit = ctx.fit;
    if (!model || !fit || !window.RoomModel?.canvasToFloor || !window.RoomModel?.floorToCanvas) return null;

    // Do not call ctx.sizeOf here: App.itemSize itself asks this function
    // for the perspective factor. Use the uncalibrated catalogue footprint
    // only to locate the floor contact point, then calibrate its projected width.
    const ppm = window.App?.PPM || 60;
    const rawW = Math.max(1, item.w * (item.scale || 1) * ppm);
    const rawH = Math.max(1, item.d * (item.scale || 1) * ppm);
    const rawSizeOf = () => ({ w: rawW, h: rawH });
    const support = supportPoint(item, rawSizeOf);
    const floor = window.RoomModel.canvasToFloor(support, model, fit);
    if (!floor || !Number.isFinite(floor.z) || floor.z <= 0.25) return null;

    const half = Math.max(0.05, (item.w * (item.scale || 1)) / 2);
    const a = window.RoomModel.floorToCanvas({ x: floor.x - half, z: floor.z }, model, fit);
    const b = window.RoomModel.floorToCanvas({ x: floor.x + half, z: floor.z }, model, fit);
    if (!a || !b) return null;

    const projected = Math.abs(b.x - a.x);
    const nominal = Math.max(1, item.w * (item.scale || 1) * (window.App?.PPM || 60));
    const factor = projected / nominal;
    return clamp(factor, 0.48, 1.65);
  }

  function wallTarget(entry, item, ctx) {
    if (!ctx.fit) return null;
    // Never call Placement.suggestDrop() here: that function delegates back to
    // SpatialIntelligence.suggest(), which would recurse indefinitely.
    // Prefer the measured wall/floor junction when available, otherwise use a
    // conservative upper-floor prior that still works before room analysis.
    const x = ctx.fit.x + ctx.fit.w * 0.5;
    let y = ctx.fit.y + ctx.fit.h * 0.46;
    const a = ctx.analysis;
    const profile = a?.floor_top_profile;
    if (Array.isArray(profile) && profile.length >= 2) {
      const mid = Number(profile[Math.floor(profile.length / 2)]);
      if (Number.isFinite(mid) && Number.isFinite(a.height) && a.height > 1) {
        y = ctx.fit.y + (mid / (a.height - 1)) * ctx.fit.h + ctx.fit.h * 0.12;
      }
    } else if (Number.isFinite(a?.floor_top_y) && Number.isFinite(a?.height) && a.height > 1) {
      y = ctx.fit.y + (a.floor_top_y / (a.height - 1)) * ctx.fit.h + ctx.fit.h * 0.12;
    }
    return { x, y: Math.min(ctx.fit.y + ctx.fit.h * .72, y), rot: 0, priority: 3 };
  }

  function findAnchor(base, ctx, preferred) {
    const items = ctx.items || [];
    if (preferred) return preferred;
    const wanted = base === "table" ? ["sofa","armchair","chair","bed"] :
      base === "lamp" ? ["sofa","bed","armchair","chair","table"] :
      base === "chair" ? ["table","sofa"] :
      base === "rug" ? ["sofa","bed","armchair"] :
      base === "plant" ? ["sofa","armchair"] : [];
    for (const w of wanted) {
      const hit = [...items].reverse().find(i => normBase(ctx.entryOf?.(i)) === w);
      if (hit) return hit;
    }
    return null;
  }

  function semanticTarget(entry, item, ctx, relation = null) {
    if (!ctx.fit) return null;
    const fit = ctx.fit;
    const base = normBase(entry);
    const anchor = findAnchor(base, ctx, relation?.anchor);
    const size = ctx.sizeOf(item);

    // Wall-backed pieces: use the detected wall/floor junction.
    if (entry?.placement?.against_wall || ["sofa","bed","tvstand"].includes(base)) {
      const t = wallTarget(entry, item, ctx);
      if (t) return t;
    }

    if (anchor) {
      const as = ctx.sizeOf(anchor);
      const ac = ctx.entryOf?.(anchor);
      const aw = as.w * .5, ah = as.h * .5;
      const gap = Math.max(28, Math.min(120, Math.max(size.w, size.h) * .12));

      if (base === "table" || relation?.kind === "front") {
        return { x: anchor.x, y: anchor.y + ah + size.h*.45 + gap, rot: 0, priority: 4 };
      }
      if (base === "lamp" || relation?.kind === "side") {
        const side = relation?.side || ((item.z || 0) % 2 ? 1 : -1);
        return { x: anchor.x + side * (aw + size.w*.52 + gap), y: anchor.y + ah*.1, rot: 0, priority: 4 };
      }
      if (base === "chair") {
        return { x: anchor.x, y: anchor.y + ah + size.h*.45 + gap, rot: 0, priority: 3 };
      }
      if (base === "rug") {
        return { x: anchor.x, y: anchor.y + Math.min(18, ah*.08), rot: 0, priority: 4 };
      }
      if (base === "plant") {
        const side = (anchor.x < fit.x + fit.w*.5) ? 1 : -1;
        return { x: anchor.x + side*(aw + size.w*.55 + gap), y: anchor.y + ah*.15, rot: 0, priority: 2 };
      }
    }

    return {
      x: fit.x + fit.w*.5,
      y: fit.y + fit.h*.70,
      rot: 0,
      priority: 1,
    };
  }

  function candidatePoints(target, item, ctx) {
    const s = ctx.sizeOf(item);
    const sx = Math.max(28, Math.min(220, s.w * .72));
    const sy = Math.max(22, Math.min(170, s.h * .55));
    // Dense local search prevents repeated assistant commands from stacking
    // exactly on top of one another when no floor/depth analysis is available.
    const offsets = [[0,0]];
    for (let ring = 1; ring <= 3; ring++) {
      const x = sx * ring, y = sy * ring;
      offsets.push([-x,0],[x,0],[0,-y],[0,y],[-x,-y],[x,-y],[-x,y],[x,y]);
    }
    return offsets.map(([dx,dy]) => ({ x: target.x+dx, y: target.y+dy }));
  }

  function scoreCandidate(point, target, item, ctx) {
    const test = { ...item, x: point.x, y: point.y };
    const fit = ctx.fit;
    if (!fit) return -Infinity;

    const s = ctx.sizeOf(test);
    const margin = 4;
    if (point.x < fit.x + s.w/2 + margin || point.x > fit.x + fit.w - s.w/2 - margin) return -Infinity;
    if (point.y < fit.y + s.h/2 + margin || point.y > fit.y + fit.h - s.h/2 - margin) return -Infinity;

    const floor = floorRatio(test, ctx);
    const collision = collides(test, ctx);
    const opening = openingPenalty(point, ctx);
    const dist = distanceScore(point, target);

    // Floor contact and non-overlap dominate; semantic proximity follows.
    let score = floor * 120 - (collision ? 260 : 0) - opening * 35 - dist * .16;

    // Prefer plausible floor depth over arbitrary canvas placement.
    const d = depthAt(point, ctx);
    if (d != null) {
      const dTarget = depthAt(target, ctx);
      if (dTarget != null) score -= Math.abs(d - dTarget) * 12;
    }
    return score;
  }

  function placeNewItem(item, ctx, options = {}) {
    if (!item || !ctx?.fit) return { changed:false, confidence:0 };
    const entry = ctx.entryOf?.(item) || {};
    const target = semanticTarget(entry, item, ctx, options.relation);
    if (!target) return { changed:false, confidence:0 };

    const candidates = candidatePoints(target, item, ctx);
    let best = null, bestScore = -Infinity;
    for (const p of candidates) {
      const score = scoreCandidate(p, target, item, ctx);
      if (score > bestScore) { bestScore = score; best = p; }
    }
    if (!best || !Number.isFinite(bestScore)) return { changed:false, confidence:0.1 };

    item.x = best.x;
    item.y = best.y;
    item.rot = target.rot || 0;
    item._spatialAuto = true;
    item._spatialVersion = 1;

    // Re-ground after the candidate search.
    if (ctx.constrainToFloor) ctx.constrainToFloor(item, true);

    const pf = perspectiveFactor(item, ctx);
    return {
      changed:true,
      confidence: clamp((bestScore + 100) / 220, 0, 1),
      perspectiveFactor: pf,
      target,
    };
  }

  function optimizeItem(item, ctx) {
    if (!item || !ctx?.fit) return null;
    const entry = ctx.entryOf?.(item) || {};
    const target = semanticTarget(entry, item, ctx);
    if (!target) return null;

    // IMPORTANT: "Optimiser la composition" is a refinement command, not a
    // reset-to-default-placement command. A user may already have positioned a
    // sofa correctly, while the generic semantic target (for example the
    // centre of a detected wall) is intentionally different. The old .34
    // interpolation therefore caused visible 1–2 m jumps.
    //
    // First score the CURRENT position. We only move the item when the solver
    // can find a materially better, valid position. Small numerical differences
    // are ignored so an already-good composition remains visually stable.
    const current = { x: item.x, y: item.y };
    const currentScore = scoreCandidate(current, current, item, ctx);
    if (!Number.isFinite(currentScore)) return null;

    const currentFloor = floorRatio(item, ctx);
    const currentCollision = collides(item, ctx);
    const currentOpening = openingPenalty(current, ctx);

    // Only use the semantic target as a gentle correction vector. Never chase a
    // generic target aggressively: the maximum correction is 3% of the room
    // image in each axis (or 48 px, whichever is smaller). This makes optimizer
    // safe for sofas/beds that are already placed correctly.
    const maxMove = Math.max(12, Math.min(48, Math.max(ctx.fit.w, ctx.fit.h) * 0.03));
    const dx = clamp((target.x - current.x) * 0.10, -maxMove, maxMove);
    const dy = clamp((target.y - current.y) * 0.10, -maxMove, maxMove);
    const wanted = { x: current.x + dx, y: current.y + dy };

    const candidates = candidatePoints(wanted, item, ctx)
      .map(p => ({
        x: clamp(p.x, ctx.fit.x + 1, ctx.fit.x + ctx.fit.w - 1),
        y: clamp(p.y, ctx.fit.y + 1, ctx.fit.y + ctx.fit.h - 1),
      }));

    // Always include the conservative correction point itself.
    candidates.unshift(wanted);

    let best = null, bestScore = currentScore;
    for (const p of candidates) {
      const move = Math.hypot(p.x - current.x, p.y - current.y);
      if (move > maxMove + 0.5) continue;
      const score = scoreCandidate(p, current, item, ctx);
      if (score > bestScore) { bestScore = score; best = p; }
    }

    // Do not move an already valid item merely because a semantic prior has a
    // different preferred location. The optimizer needs a meaningful gain.
    const improvement = bestScore - currentScore;
    const needsRepair = currentFloor < 0.80 || currentCollision || currentOpening > 0;
    const minImprovement = needsRepair ? 2.0 : 18.0;
    if (!best || improvement < minImprovement) {
      return { changed:false, score:currentScore };
    }

    item.x = best.x;
    item.y = best.y;
    item._spatialVersion = 2;
    if (ctx.constrainToFloor) ctx.constrainToFloor(item, true);
    return { changed:true, score:bestScore, improvement };
  }

  function analyzeScene(ctx) {
    const items = ctx.items || [];
    const rows = items.map(item => {
      const pf = perspectiveFactor(item, ctx);
      const support = supportPoint(item, ctx.sizeOf);
      const floor = window.RoomModel?.canvasToFloor?.(support, ctx.roomModel || window.CigogneRoom?.model, ctx.fit);
      return {
        uid:item.uid, base:item.catId, support,
        floor, perspectiveFactor:pf,
        floorCoverage:floorRatio(item,ctx),
      };
    });
    return { version:1, items:rows };
  }

  function suggest(entry, ctx, options = {}) {
    if (!entry || !ctx?.fit) return null;
    const base = normBase(entry);
    const size = {
      w: Math.max(1, Number(entry.w || entry.dimensions?.width || .8)) * (window.App?.PPM || 60),
      h: Math.max(1, Number(entry.d || entry.dimensions?.depth || .8)) * (window.App?.PPM || 60),
    };
    const pseudo = { ...options, w: Number(entry.w || .8), d: Number(entry.d || .8), scale: 1,
      x: ctx.fit.x + ctx.fit.w*.5, y: ctx.fit.y + ctx.fit.h*.70, rot: Number(options.rot || 0),
      catId: base, uid: "__spatial_suggestion__" };
    const target = semanticTarget(entry, pseudo, ctx, options.relation || null);
    if (!target) return null;
    const candidates = candidatePoints(target, pseudo, ctx);
    let best = null, bestScore = -Infinity;
    for (const point of candidates) {
      const test = { ...pseudo, x:point.x, y:point.y };
      const score = scoreCandidate(point, target, test, ctx);
      if (score > bestScore) { bestScore = score; best = point; }
    }
    if (!best || !Number.isFinite(bestScore)) return target;
    return { x: best.x, y: best.y, rot: target.rot || 0, spatial: true, confidence: clamp((bestScore + 100)/220,0,1) };
  }

  window.SpatialIntelligence = {
    version: 1,
    suggest,
    perspectiveFactor,
    placeNewItem,
    optimizeItem,
    optimizeScene: (ctx) => {
      let changed = 0;
      for (const item of (ctx.items || [])) if (optimizeItem(item, ctx)?.changed) changed++;
      return changed;
    },
    analyzeScene,
  };
})();

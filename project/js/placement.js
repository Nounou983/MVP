/* =========================================================
   La Cigogne D'Ailleurs — Placement assisté
   Ce module ne dessine rien et ne touche à aucun état global :
   on lui donne un meuble et un contexte, il rend une position
   corrigée plus la liste des repères à afficher. Tout est
   testable sans navigateur.

   Quatre aides, dans cet ordre de priorité :
     1. mur     — le meuble adossable se colle à la ligne mur/sol ;
     2. sol     — le point d'appui reste sur le sol détecté ;
     3. voisins — alignement des centres et des bords ;
     4. collision + dégagement — signalés, jamais forcés.

   Le choix de signaler plutôt que d'empêcher est délibéré : une
   photo n'est pas un plan, et refuser un déplacement parce qu'un
   modèle de profondeur estime un chevauchement serait pénible.
   ========================================================= */

(() => {
  "use strict";

  const SNAP_ALIGN = 7;        // px : alignement sur un voisin
  const SNAP_WALL = 26;        // px : aimantation au mur
  const GUIDE_LIFETIME = 900;  // ms

  /* --------------------------- géométrie --------------------------- */

  function corners(cx, cy, w, h, rot) {
    const cos = Math.cos(rot), sin = Math.sin(rot);
    const hw = w / 2, hh = h / 2;
    return [[-hw, -hh], [hw, -hh], [hw, hh], [-hw, hh]].map(([lx, ly]) => ({
      x: cx + lx * cos - ly * sin,
      y: cy + lx * sin + ly * cos,
    }));
  }

  function project(points, axis) {
    let min = Infinity, max = -Infinity;
    for (const p of points) {
      const value = p.x * axis.x + p.y * axis.y;
      if (value < min) min = value;
      if (value > max) max = value;
    }
    return { min, max };
  }

  /** Séparation d'axes : deux rectangles pivotés se chevauchent-ils ? */
  function overlaps(a, b) {
    const axes = [];
    for (const shape of [a, b]) {
      for (let i = 0; i < 4; i++) {
        const p1 = shape[i], p2 = shape[(i + 1) % 4];
        const edge = { x: p2.x - p1.x, y: p2.y - p1.y };
        const len = Math.hypot(edge.x, edge.y) || 1;
        axes.push({ x: -edge.y / len, y: edge.x / len });
      }
    }
    for (const axis of axes) {
      const pa = project(a, axis), pb = project(b, axis);
      if (pa.max < pb.min || pb.max < pa.min) return false;
    }
    return true;
  }

  function bounds(points) {
    const xs = points.map(p => p.x), ys = points.map(p => p.y);
    return { left: Math.min(...xs), right: Math.max(...xs), top: Math.min(...ys), bottom: Math.max(...ys) };
  }

  /* ------------------------- ligne mur/sol ------------------------- */

  /**
   * Hauteur (en px canvas) de la jonction mur/sol sous une abscisse.
   * `floor_top_profile` est échantillonné sur la largeur de l'image par
   * le backend ; on interpole linéairement entre deux échantillons.
   */
  function wallLineAt(canvasX, ctx) {
    const analysis = ctx.analysis;
    const fit = ctx.fit;
    if (!analysis || !fit) return null;
    const profile = analysis.floor_top_profile;
    if (!Array.isArray(profile) || profile.length < 2) {
      return Number.isFinite(analysis.floor_top_y)
        ? fit.y + (analysis.floor_top_y / Math.max(1, analysis.height - 1)) * fit.h
        : null;
    }
    const t = (canvasX - fit.x) / Math.max(1, fit.w);
    if (t < -0.05 || t > 1.05) return null;
    const pos = Math.max(0, Math.min(1, t)) * (profile.length - 1);
    const i = Math.floor(pos);
    const frac = pos - i;
    const a = Number(profile[i]);
    const b = Number(profile[Math.min(profile.length - 1, i + 1)]);
    if (!Number.isFinite(a) || !Number.isFinite(b)) return null;
    const imageY = a + (b - a) * frac;
    return fit.y + (imageY / Math.max(1, analysis.height - 1)) * fit.h;
  }

  /* ---------------------------- résultat --------------------------- */

  function emptyResult(item) {
    return {
      x: item.x, y: item.y, rot: item.rot,
      guides: [], collisions: [], clearance: null,
      snapped: null, wallLine: null,
    };
  }

  /**
   * @param {object} item     meuble en cours de manipulation
   * @param {object} ctx      { items, fit, analysis, sizeOf, entryOf, isFloor, enabled }
   * @returns {object}        position corrigée + repères
   */
  function resolve(item, ctx) {
    const result = emptyResult(item);
    if (!item || !ctx || ctx.enabled === false) return result;

    const size = ctx.sizeOf(item);
    const entry = ctx.entryOf ? ctx.entryOf(item) : null;
    const rules = entry?.placement || {};
    const metersToPx = size.w / Math.max(0.05, item.w * item.scale);

    let x = item.x;
    let y = item.y;
    let rot = item.rot;

    /* 1. mur ------------------------------------------------------- */
    if (rules.against_wall && ctx.analysis) {
      const line = wallLineAt(x, ctx);
      result.wallLine = line;
      if (line != null) {
        // Le bord arrière du meuble, à la rotation courante.
        const back = bounds(corners(x, y, size.w, size.h, rot)).top;
        const gap = back - line;
        if (Math.abs(gap) <= SNAP_WALL) {
          y -= gap;
          // Adossé : le meuble regarde la pièce. Une photo ne dit pas
          // dans quel sens est le mur latéral, donc on ne redresse que
          // vers l'avant, et seulement si l'angle est déjà proche.
          if (Math.abs(((rot % (Math.PI * 2)) + Math.PI * 2) % (Math.PI * 2)) < 0.42) rot = 0;
          result.snapped = "wall";
          result.guides.push({ type: "wall", y: line, from: ctx.fit.x, to: ctx.fit.x + ctx.fit.w });
        }
      }
    }

    /* 2. voisins --------------------------------------------------- */
    const others = (ctx.items || []).filter(other => other.uid !== item.uid);
    let bestX = null, bestY = null;
    for (const other of others) {
      const otherSize = ctx.sizeOf(other);
      const candidatesX = [
        { value: other.x, kind: "center" },
        { value: other.x - otherSize.w / 2 + size.w / 2, kind: "edge" },
        { value: other.x + otherSize.w / 2 - size.w / 2, kind: "edge" },
      ];
      for (const candidate of candidatesX) {
        const delta = Math.abs(candidate.value - x);
        if (delta <= SNAP_ALIGN && (!bestX || delta < bestX.delta)) {
          bestX = { ...candidate, delta, other };
        }
      }
      const candidatesY = [{ value: other.y, kind: "center" }];
      for (const candidate of candidatesY) {
        const delta = Math.abs(candidate.value - y);
        if (delta <= SNAP_ALIGN && (!bestY || delta < bestY.delta)) {
          bestY = { ...candidate, delta, other };
        }
      }
    }
    if (bestX) {
      x = bestX.value;
      result.guides.push({ type: "align-v", x, from: Math.min(y, bestX.other.y), to: Math.max(y, bestX.other.y) });
      result.snapped = result.snapped || "align";
    }
    if (bestY && result.snapped !== "wall") {
      y = bestY.value;
      result.guides.push({ type: "align-h", y, from: Math.min(x, bestY.other.x), to: Math.max(x, bestY.other.x) });
      result.snapped = result.snapped || "align";
    }

    /* 3. collisions ------------------------------------------------ */
    const shape = corners(x, y, size.w, size.h, rot);
    const isRug = rules.under_furniture || item.catId === "rug";
    for (const other of others) {
      const otherEntry = ctx.entryOf ? ctx.entryOf(other) : null;
      if (isRug || otherEntry?.placement?.under_furniture || other.catId === "rug") continue;
      const otherSize = ctx.sizeOf(other);
      if (overlaps(shape, corners(other.x, other.y, otherSize.w, otherSize.h, other.rot))) {
        result.collisions.push(other.uid);
      }
    }

    /* 4. dégagement ------------------------------------------------ */
    const needed = Number(rules.clearance_front || 0);
    if (needed > 0 && !isRug) {
      const frontDepth = needed * metersToPx;
      const front = corners(
        x + Math.sin(rot) * (size.h / 2 + frontDepth / 2),
        y + Math.cos(rot) * (size.h / 2 + frontDepth / 2),
        size.w * 0.86, frontDepth, rot,
      );
      const blockers = others.filter(other => {
        const otherEntry = ctx.entryOf ? ctx.entryOf(other) : null;
        if (otherEntry?.placement?.under_furniture || other.catId === "rug") return false;
        const otherSize = ctx.sizeOf(other);
        return overlaps(front, corners(other.x, other.y, otherSize.w, otherSize.h, other.rot));
      });
      if (blockers.length) {
        result.clearance = { needed, blocked: blockers.map(b => b.uid), label: `${needed.toFixed(2)} m` };
      }
    }

    result.x = x;
    result.y = y;
    result.rot = rot;
    return result;
  }

  /** Suggestion de pose pour un meuble qu'on vient d'ajouter. */
  function suggestDrop(entry, ctx) {
    const fit = ctx.fit;
    if (!fit) return null;
    const rules = entry?.placement || {};

    // Phase 10.4: when an analyzed room is available, use the floor mask,
    // relative depth and detected furniture as a spatial prior. This is only
    // a suggestion; explicit x/y from a user drag always wins.
    if (ctx.spatialIntelligence !== false && window.SpatialIntelligence?.suggest && ctx.analysis) {
      const spatial = window.SpatialIntelligence.suggest(entry, ctx, {
        rot: 0,
        preferWall: Boolean(rules.against_wall),
      });
      if (spatial) return spatial;
    }

    const x = fit.x + fit.w * 0.5;
    if (rules.against_wall && ctx.analysis) {
      const line = wallLineAt(x, ctx);
      if (line != null) {
        // Un peu en avant de la ligne de mur : le meuble a de la profondeur.
        return { x, y: Math.min(fit.y + fit.h * 0.92, line + fit.h * 0.16), rot: 0 };
      }
    }
    return { x, y: fit.y + fit.h * 0.72, rot: 0 };
  }

  window.Placement = {
    resolve, suggestDrop, wallLineAt, overlaps, corners, bounds,
    SNAP_ALIGN, SNAP_WALL, GUIDE_LIFETIME,
  };
})();

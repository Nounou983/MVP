/* =========================================================
   La Cigogne D'Ailleurs — Reconstruction de la pièce
   Passe du « proxy approximatif » à une géométrie estimée :
   sol, murs, plafond, ouvertures, caméra, dimensions — et un seul
   repère partagé par la vue photo et la vue 3D.

   Ce qui est mesuré, ce qui est supposé
   -------------------------------------
   Mesuré depuis la photo : la ligne de jonction mur/sol (profil
   renvoyé par le backend), l'étendue du sol, les zones de mur, de
   porte et de fenêtre, la carte de profondeur relative.

   Supposé, faute d'information dans une image unique : la hauteur
   de l'appareil (1,55 m, hauteur de prise de vue courante), le
   champ de vision (52°, équivalent ~28 mm), et la hauteur sous
   plafond quand le plafond n'est pas visible (2,70 m).

   Conséquence assumée : les dimensions sont des estimations
   cohérentes entre elles, pas un relevé. `confidence` dit à quel
   point s'y fier, et en dessous de LOW_CONFIDENCE l'application
   retombe sur le proxy d'origine plutôt que d'afficher un faux
   plan juste.
   ========================================================= */

(() => {
  "use strict";

  const DEG = Math.PI / 180;
  const DEFAULTS = {
    fov: 52,             // degrés, vertical
    cameraHeight: 1.55,  // m
    ceiling: 2.70,       // m
  };
  const LOW_CONFIDENCE = 0.35;

  /* ----------------------------- caméra ---------------------------- */

  /**
   * Inclinaison de l'appareil déduite de la position de la ligne
   * mur/sol dans le cadre : plus elle est haute, plus l'appareil
   * plonge vers le sol.
   */
  function estimateCamera(analysis, opts = {}) {
    const fov = (opts.fov || DEFAULTS.fov);
    const height = (opts.cameraHeight || DEFAULTS.cameraHeight);
    const H = analysis?.height || 1000;

    let horizonY = null;
    const profile = analysis?.floor_top_profile;
    if (Array.isArray(profile) && profile.length) {
      const valid = profile.map(Number).filter(Number.isFinite);
      if (valid.length) {
        const sorted = [...valid].sort((a, b) => a - b);
        horizonY = sorted[Math.floor(sorted.length / 2)];
      }
    }
    if (horizonY == null && Number.isFinite(analysis?.floor_top_y)) horizonY = analysis.floor_top_y;
    const junctionY = horizonY;
    if (horizonY == null) horizonY = H * 0.42;

    // L'horizon (hauteur des yeux) est toujours AU-DESSUS de la jonction
    // mur/sol : le sol s'évanouit à l'horizon, la base du mur est à
    // distance finie. Confondre les deux — l'erreur de la première
    // version — renvoyait un mur à l'infini et donc aucune dimension.
    const MARGIN = 0.06;
    horizonY = Math.min(horizonY - H * MARGIN, H * 0.5);

    const fovRad = fov * DEG;
    const pitch = Math.max(-0.2, Math.min(0.95, (0.5 - horizonY / H) * fovRad));

    // Distance du mur du fond, mesurée depuis l'angle sous l'horizon.
    const tanY = Math.tan(fovRad / 2);
    let depth = null;
    if (Number.isFinite(junctionY)) {
      const drop = ((junctionY - horizonY) / H) * 2 * tanY;   // tangente de l'angle
      if (drop > 1e-3) depth = height / drop;
    }
    const plausible = Number.isFinite(depth) && depth >= 1.8 && depth <= 12;
    if (!Number.isFinite(depth)) depth = 4.5;                 // valeur de repli
    depth = Math.max(1.6, Math.min(16, depth));

    return { fov, fovRad, pitch, height, depth, horizonY, junctionY, plausibleDepth: plausible, unit: "m" };
  }

  /* ------------------- projection canvas <-> sol ------------------- */

  /**
   * Point du sol visé par un pixel du canvas.
   * Repère : origine au pied de l'appareil, X vers la droite,
   * Z vers le fond de la pièce, Y vers le haut.
   */
  function canvasToFloor(point, model, fit) {
    if (!model || !fit || !fit.w || !fit.h) return null;
    const { camera } = model;
    const ndcX = ((point.x - fit.x) / fit.w) * 2 - 1;
    const ndcY = 1 - ((point.y - fit.y) / fit.h) * 2;

    const tanY = Math.tan(camera.fovRad / 2);
    const aspect = fit.w / fit.h;
    // Direction dans le repère caméra, puis rotation par le pitch.
    const dir = { x: ndcX * tanY * aspect, y: ndcY * tanY, z: -1 };
    const cos = Math.cos(-camera.pitch), sin = Math.sin(-camera.pitch);
    const world = {
      x: dir.x,
      y: dir.y * cos - dir.z * sin,
      z: dir.y * sin + dir.z * cos,
    };
    if (world.y >= -1e-4) return null;              // au-dessus de l'horizon
    const t = camera.height / -world.y;
    return { x: world.x * t, z: -world.z * t, distance: t };
  }

  function floorToCanvas(pointOnFloor, model, fit) {
    if (!model || !fit) return null;
    const { camera } = model;
    const relative = { x: pointOnFloor.x, y: -camera.height, z: -pointOnFloor.z };
    const cos = Math.cos(camera.pitch), sin = Math.sin(camera.pitch);
    const cam = {
      x: relative.x,
      y: relative.y * cos - relative.z * sin,
      z: relative.y * sin + relative.z * cos,
    };
    if (cam.z >= -1e-4) return null;                 // derrière l'appareil
    const tanY = Math.tan(camera.fovRad / 2);
    const aspect = fit.w / fit.h;
    const ndcX = (cam.x / -cam.z) / (tanY * aspect);
    const ndcY = (cam.y / -cam.z) / tanY;
    return {
      x: fit.x + ((ndcX + 1) / 2) * fit.w,
      y: fit.y + ((1 - ndcY) / 2) * fit.h,
    };
  }

  /* ------------------------------ sol ------------------------------ */

  function floorPolygon(analysis) {
    const profile = (analysis?.floor_top_profile || []).map(Number);
    const W = analysis?.width || 0;
    const H = analysis?.height || 0;
    if (!profile.length || !W || !H) return null;
    const points = profile.map((y, i) => ({
      x: (i / Math.max(1, profile.length - 1)) * (W - 1),
      y: Math.max(0, Math.min(H - 1, Number.isFinite(y) ? y : H)),
    }));
    return [...points, { x: W - 1, y: H - 1 }, { x: 0, y: H - 1 }];
  }

  /* ----------------------------- murs ------------------------------ */

  /**
   * Trois plans, déduits de la ligne mur/sol :
   *  - fond   : là où la ligne est la plus haute (le plus loin) ;
   *  - côtés  : là où elle redescend vers les bords du cadre.
   * Une pièce en L ou un mur oblique sortent de ce modèle ; c'est
   * exactement ce que mesure `confidence`.
   */
  function estimateWalls(analysis, model) {
    const profile = (analysis?.floor_top_profile || []).map(Number).filter(Number.isFinite);
    if (profile.length < 8) return [];
    const W = analysis.width || 1;
    const H = analysis.height || 1;
    const fit = { x: 0, y: 0, w: W, h: H };

    const n = profile.length;
    const sampleAt = i => ({
      x: (i / (n - 1)) * (W - 1),
      y: Math.max(0, Math.min(H - 1, profile[i])),
    });

    const junction = [];
    for (let i = 0; i < n; i += Math.max(1, Math.floor(n / 48))) {
      const p = sampleAt(i);
      const floor = canvasToFloor(p, model, fit);
      if (floor && floor.distance > 0.3 && floor.distance < 40) junction.push(floor);
    }
    if (junction.length < 4) return [];

    const depths = junction.map(p => p.z);
    const backDepth = depths.reduce((a, b) => a + b, 0) / depths.length;
    const left = Math.min(...junction.map(p => p.x));
    const right = Math.max(...junction.map(p => p.x));
    const height = model.ceiling;

    return [
      { id: "back", normal: { x: 0, z: -1 }, distance: backDepth, width: right - left, height,
        quad: [{ x: left, z: backDepth }, { x: right, z: backDepth }] },
      { id: "left", normal: { x: 1, z: 0 }, distance: Math.abs(left), width: backDepth, height,
        quad: [{ x: left, z: 0 }, { x: left, z: backDepth }] },
      { id: "right", normal: { x: -1, z: 0 }, distance: Math.abs(right), width: backDepth, height,
        quad: [{ x: right, z: 0 }, { x: right, z: backDepth }] },
    ];
  }

  /* -------------------------- ouvertures --------------------------- */

  /** Composantes connexes d'un masque binaire, en boîtes englobantes. */
  function maskRegions(pixels, width, height, { minArea = 0.002, maxRegions = 8 } = {}) {
    const total = width * height;
    const seen = new Uint8Array(total);
    const regions = [];
    const stack = [];

    for (let start = 0; start < total; start++) {
      if (seen[start] || pixels[start * 4] <= 128) continue;
      stack.length = 0;
      stack.push(start);
      seen[start] = 1;
      let area = 0, minX = width, maxX = 0, minY = height, maxY = 0;

      while (stack.length) {
        const index = stack.pop();
        const x = index % width;
        const y = (index - x) / width;
        area++;
        if (x < minX) minX = x;
        if (x > maxX) maxX = x;
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
        const neighbours = [
          x > 0 ? index - 1 : -1,
          x < width - 1 ? index + 1 : -1,
          y > 0 ? index - width : -1,
          y < height - 1 ? index + width : -1,
        ];
        for (const nb of neighbours) {
          if (nb < 0 || seen[nb] || pixels[nb * 4] <= 128) continue;
          seen[nb] = 1;
          stack.push(nb);
        }
      }
      if (area / total >= minArea) {
        regions.push({
          area, ratio: area / total,
          x: minX, y: minY, width: maxX - minX + 1, height: maxY - minY + 1,
        });
      }
    }
    return regions.sort((a, b) => b.area - a.area).slice(0, maxRegions);
  }

  function readMask(image, maxSide = 320) {
    if (!image || !image.naturalWidth) return null;
    const scale = Math.min(1, maxSide / Math.max(image.naturalWidth, image.naturalHeight));
    const w = Math.max(1, Math.round(image.naturalWidth * scale));
    const h = Math.max(1, Math.round(image.naturalHeight * scale));
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    if (!ctx) return null;
    try {
      ctx.drawImage(image, 0, 0, w, h);
      return { pixels: ctx.getImageData(0, 0, w, h).data, width: w, height: h };
    } catch (err) {
      console.warn("[pièce] masque illisible", err);
      return null;
    }
  }

  function loadImage(src) {
    return new Promise(resolve => {
      if (!src) return resolve(null);
      const img = new Image();
      img.crossOrigin = "anonymous";
      img.onload = () => resolve(img);
      img.onerror = () => resolve(null);
      img.src = src;
    });
  }

  async function detectOpenings(analysis) {
    const out = [];
    const masks = analysis?.masks || {};
    const jobs = [
      ["windowpane", "window"],
      ["door", "door"],
    ].filter(([key]) => masks[key]);

    for (const [key, type] of jobs) {
      const image = await loadImage(masks[key]);
      const data = readMask(image);
      if (!data) continue;
      for (const region of maskRegions(data.pixels, data.width, data.height, { minArea: 0.004, maxRegions: 5 })) {
        out.push({
          type,
          // Normalisé 0..1 sur l'image : indépendant de l'affichage.
          box: {
            x: region.x / data.width,
            y: region.y / data.height,
            width: region.width / data.width,
            height: region.height / data.height,
          },
          ratio: region.ratio,
        });
      }
    }
    return out;
  }

  /* ---------------------------- plafond ---------------------------- */

  function estimateCeiling(analysis) {
    const scene = analysis?.scene || {};
    if (!scene.has_walls) return { height: DEFAULTS.ceiling, source: "assumed" };
    // Le plafond n'est presque jamais segmenté : on garde la valeur
    // courante des logements, et on le dit.
    return { height: DEFAULTS.ceiling, source: "assumed" };
  }

  /* --------------------------- confiance --------------------------- */

  function confidenceOf(analysis, walls) {
    let score = 0;
    const profile = (analysis?.floor_top_profile || []).map(Number).filter(Number.isFinite);
    if (profile.length >= 16) score += 0.2;

    if (profile.length >= 8) {
      const H = analysis.height || 1;
      const mean = profile.reduce((a, b) => a + b, 0) / profile.length;

      // Régularité : une vraie jonction mur/sol est continue. Un profil
      // qui saute d'un échantillon à l'autre vient d'une segmentation
      // ratée, pas d'une pièce biscornue — et il doit faire chuter la
      // note, pas seulement ne rien rapporter.
      let jumps = 0;
      for (let i = 1; i < profile.length; i++) jumps += Math.abs(profile[i] - profile[i - 1]);
      const roughness = (jumps / (profile.length - 1)) / H;
      score += roughness < 0.02 ? 0.3 : Math.max(-0.3, 0.3 - roughness * 6);

      const variance = profile.reduce((a, b) => a + (b - mean) ** 2, 0) / profile.length;
      const spread = Math.sqrt(variance) / H;
      score += spread < 0.18 ? 0.15 : Math.max(-0.2, 0.15 - spread);

      if (mean > H * 0.08 && mean < H * 0.95) score += 0.15;
    }
    if (analysis?.scene?.has_floor) score += 0.1;
    if (analysis?.scene?.has_walls) score += 0.05;
    if (walls.length >= 3) score += 0.05;
    if (analysis?.floor_source && analysis.floor_source !== "fallback") score += 0.05;
    return Math.max(0, Math.min(1, score));
  }

  function depthPenalty(model) {
    // Une profondeur hors plage crédible ne disqualifie pas l'analyse,
    // mais elle interdit d'annoncer des dimensions comme si elles
    // étaient mesurées.
    return model.camera.plausibleDepth ? 1 : 0.45;
  }

  /* ----------------------------- build ----------------------------- */

  function buildSync(analysis, opts = {}) {
    if (!analysis) return null;
    const camera = estimateCamera(analysis, opts);
    const model = {
      version: 1,
      camera,
      ceiling: estimateCeiling(analysis).height,
      ceilingSource: estimateCeiling(analysis).source,
      floor: { polygon: floorPolygon(analysis), topProfile: analysis.floor_top_profile || [] },
      walls: [],
      openings: [],
      dimensions: null,
      confidence: 0,
      reliable: false,
      assumptions: {
        cameraHeight: camera.height,
        fov: camera.fov,
        ceiling: DEFAULTS.ceiling,
        note: "Hauteur d'appareil, champ de vision et hauteur sous plafond sont des valeurs courantes, pas des mesures.",
      },
      imageSize: { width: analysis.width, height: analysis.height },
    };
    model.walls = estimateWalls(analysis, model);

    const back = model.walls.find(w => w.id === "back");
    if (back) {
      model.dimensions = {
        width: Math.max(1.2, Math.round(back.width * 10) / 10),
        depth: Math.max(1.2, Math.round(back.distance * 10) / 10),
        height: model.ceiling,
        unit: "m",
      };
    }
    model.confidence = confidenceOf(analysis, model.walls) * depthPenalty(model);
    model.reliable = model.confidence >= LOW_CONFIDENCE
      && Boolean(model.dimensions)
      && model.camera.plausibleDepth;
    return model;
  }

  async function build(analysis, opts = {}) {
    const model = buildSync(analysis, opts);
    if (!model) return null;
    try {
      model.openings = await detectOpenings(analysis);
    } catch (err) {
      console.warn("[pièce] détection des ouvertures impossible", err);
      model.openings = [];
    }
    return model;
  }

  function describe(model) {
    if (!model) return { title: "Pièce non analysée", detail: "" };
    if (!model.reliable) {
      return {
        title: "Reconstruction approximative",
        detail: "La géométrie est trop incertaine sur cette photo : le volume de secours est utilisé.",
        confidence: model.confidence,
      };
    }
    const d = model.dimensions;
    return {
      title: `≈ ${d.width.toFixed(1)} × ${d.depth.toFixed(1)} m`,
      detail: `Hauteur estimée ${d.height.toFixed(2)} m · appareil à ${model.camera.height.toFixed(2)} m`,
      confidence: model.confidence,
    };
  }

  window.RoomModel = {
    build, buildSync, describe,
    estimateCamera, canvasToFloor, floorToCanvas,
    floorPolygon, estimateWalls, detectOpenings, maskRegions,
    LOW_CONFIDENCE, DEFAULTS,
  };
})();

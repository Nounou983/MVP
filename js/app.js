/* =========================================================
   La Cigogne D'Ailleurs — Éditeur de pièce (canvas 2D)
   Placement, perspective, ancrage au sol, historique.
   L'API publique (window.App / window.AppActions) reste
   compatible avec ai.js, eraser.js, phase3.js et phase3e.js.
   ========================================================= */

(() => {
  "use strict";

  const canvas = document.getElementById("stage");
  const ctx = canvas.getContext("2d");
  const statusEl = document.getElementById("status");
  const emptyState = document.getElementById("emptyState");
  const inspectorBody = document.getElementById("inspectorBody");

  const PPM = 60;                 // pixels par mètre à perspective neutre
  const HISTORY_LIMIT = 60;

  const state = {
    roomImage: null,
    roomFileName: "",
    roomObjectUrl: null,
    items: [],
    selectedId: null,
    hoverId: null,
    zCounter: 1,
    drag: null,
    analysis: null,
    floorMaskImg: null,
    floorMaskPx: null,
    floorMaskW: 0,
    floorMaskH: 0,
    depthImg: null,
    showMask: false,
    showDepth: false,
    showFurnitureZones: false,
    eraserOn: false,
    guides: [],
    collisions: [],
    clearance: null,
    snapped: null,
    cssWidth: 0,
    cssHeight: 0,
  };

  /* ---------------------------------------------------------------
     Bus d'événements minimal — l'interface écoute, le moteur émet.
     --------------------------------------------------------------- */
  const listeners = new Map();
  function on(name, fn) {
    if (!listeners.has(name)) listeners.set(name, new Set());
    listeners.get(name).add(fn);
    return () => listeners.get(name).delete(fn);
  }
  function emit(name, detail) {
    listeners.get(name)?.forEach(fn => { try { fn(detail); } catch (err) { console.error(err); } });
  }

  function setStatus(msg) {
    statusEl.textContent = msg;
    emit("status", msg);
  }
  function syncPhase3() { if (window.Phase3?.sync) window.Phase3.sync(); }

  /* ---------------------------------------------------------------
     Historique (meubles uniquement — les retouches IA sont gérées
     par l'interface, qui conserve l'image précédente.)
     --------------------------------------------------------------- */
  const history = { past: [], future: [] };
  const snapshot = () => JSON.stringify({ items: state.items, selectedId: state.selectedId, z: state.zCounter });

  function pushHistory() {
    history.past.push(snapshot());
    if (history.past.length > HISTORY_LIMIT) history.past.shift();
    history.future.length = 0;
    emit("history", historyInfo());
    emit("push");
  }

  // Pour les manipulations continues : on retient l'état d'avant et on ne
  // l'enregistre que si le geste a réellement modifié quelque chose.
  let pendingChange = null;
  const beginChange = () => { pendingChange = snapshot(); };
  const cancelChange = () => { pendingChange = null; };
  function commitChange() {
    if (!pendingChange) return;
    history.past.push(pendingChange);
    if (history.past.length > HISTORY_LIMIT) history.past.shift();
    history.future.length = 0;
    pendingChange = null;
    emit("history", historyInfo());
    emit("push");
  }
  function applySnapshot(raw) {
    const data = JSON.parse(raw);
    state.items = data.items || [];
    state.selectedId = data.selectedId || null;
    state.zCounter = data.z || 1;
    if (!state.items.some(i => i.uid === state.selectedId)) state.selectedId = null;
    renderInspector(); draw(); syncPhase3();
    emit("items", state.items); emit("select", getSelected());
  }
  function undo() {
    if (!history.past.length) return false;
    history.future.push(snapshot());
    applySnapshot(history.past.pop());
    emit("history", historyInfo());
    return true;
  }
  function redo() {
    if (!history.future.length) return false;
    history.past.push(snapshot());
    applySnapshot(history.future.pop());
    emit("history", historyInfo());
    return true;
  }
  const historyInfo = () => ({ canUndo: history.past.length > 0, canRedo: history.future.length > 0 });

  /* --------------------------------------------------------------- */

  function resize() {
    const r = canvas.parentElement.getBoundingClientRect();
    const dpr = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
    canvas.width = Math.max(1, Math.round(r.width * dpr));
    canvas.height = Math.max(1, Math.round(r.height * dpr));
    canvas.style.width = `${r.width}px`;
    canvas.style.height = `${r.height}px`;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    state.cssWidth = r.width;
    state.cssHeight = r.height;
    draw();
    updateContextToolbar();
  }

  function getCanvasSize() {
    return {
      width: state.cssWidth || canvas.getBoundingClientRect().width,
      height: state.cssHeight || canvas.getBoundingClientRect().height,
    };
  }

  function roomFit() {
    if (!state.roomImage) return null;
    const { width: cw, height: ch } = getCanvasSize();
    const s = Math.min(cw / state.roomImage.naturalWidth, ch / state.roomImage.naturalHeight);
    const w = state.roomImage.naturalWidth * s;
    const h = state.roomImage.naturalHeight * s;
    return { x: (cw - w) / 2, y: (ch - h) / 2, w, h, scale: s };
  }

  const entryOf = id => (window.CATALOG || []).find(c => c.id === id) || null;
  function getSelected() { return state.items.find(i => i.uid === state.selectedId) || null; }
  function nextUid() { return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`; }
  function spriteOf(item) {
    // La bibliothèque produit décide : photo si elle existe, sinon sprite
    // vectoriel. Elle retombe d'elle-même sur le sprite si l'image casse.
    const fromCatalog = window.ProductCatalog?.spriteFor?.(item, item.color);
    if (fromCatalog) return fromCatalog;
    return window.spriteFor?.(item.catId, item.color) || window.CATALOG_IMGS?.[item.catId] || null;
  }

  /* ---------------- Placement assisté (js/placement.js) ------------ */

  function placementContext() {
    return {
      items: state.items,
      fit: roomFit(),
      analysis: state.analysis,
      floorMaskPx: state.floorMaskPx,
      floorMaskW: state.floorMaskW,
      floorMaskH: state.floorMaskH,
      floorMaskImg: state.floorMaskImg,
      roomModel: window.CigogneRoom?.model || null,
      sizeOf: itemSize,
      entryOf: item => entryOf(item.entryId) || entryOf(item.catId),
      constrainToFloor,
      enabled: window.CIGOGNE_CONFIG?.placementAssist !== false,
      spatialIntelligence: window.CIGOGNE_CONFIG?.spatialIntelligence !== false,
    };
  }

  function applyPlacement(item) {
    if (!item || !window.Placement) return null;
    const ctx = placementContext();
    if (!ctx.fit) return null;
    const result = window.Placement.resolve(item, ctx);
    item.x = result.x;
    item.y = result.y;
    item.rot = result.rot;
    state.guides = result.guides;
    state.collisions = result.collisions;
    state.clearance = result.clearance;
    state.snapped = result.snapped;
    return result;
  }

  function clearGuides() {
    if (!state.guides.length && !state.collisions.length && !state.clearance) return;
    state.guides = [];
    state.collisions = [];
    state.clearance = null;
    state.snapped = null;
  }

  function addItem(entryId, opts = {}) {
    const entry = entryOf(entryId);
    if (!entry) return null;
    const { width, height } = getCanvasSize();
    const fit = roomFit();
    const x = opts.x ?? (fit ? fit.x + fit.w * 0.5 : width * 0.5);
    const y = opts.y ?? (fit ? fit.y + fit.h * 0.72 : height * 0.5);
    pushHistory();
    const item = {
      uid: nextUid(),
      catId: entry.base,          // sprite + volume 3D
      entryId: entry.id,          // référence catalogue (prix, nuancier)
      name: entry.name,
      price: entry.price || 0,
      color: opts.color || entry.color,
      w: entry.w, d: entry.d,
      x, y, rot: 0, scale: 1, z: state.zCounter++,
    };
    state.items.push(item);
    select(item.uid);
    if (opts.x == null && opts.y == null && window.Placement) {
      const suggestion = window.Placement.suggestDrop(entry, placementContext());
      if (suggestion) { item.x = suggestion.x; item.y = suggestion.y; item.rot = suggestion.rot; }
    }
    if (state.analysis) constrainToFloor(item, true);
    applyPlacement(item);
    clearGuides();
    draw(); syncPhase3();
    emit("items", state.items);
    setStatus(`${entry.name} ajouté`);
    return item;
  }

  function updateItem(uid, patch = {}, {history: recordHistory = true, status = true} = {}) {
    const item = state.items.find(i => i.uid === uid);
    if (!item || !patch || typeof patch !== "object") return null;
    if (recordHistory) pushHistory();
    const before = { ...item };
    Object.assign(item, patch);
    if (patch.entryId || patch.catId || patch.color) {
      const entry = entryOf(item.entryId) || entryOf(item.catId);
      if (entry) {
        item.entryId = entry.id;
        item.catId = entry.base;
        item.name = entry.name;
        item.price = entry.price || 0;
        item.w = entry.w;
        item.d = entry.d;
      }
      const groups = window.Phase3?.state?.groups;
      const group = groups?.get(uid);
      if (group) { group.parent?.remove(group); groups.delete(uid); }
    }
    if (state.analysis && (patch.x != null || patch.y != null || patch.scale != null)) constrainToFloor(item, true);
    applyPlacement(item);
    renderInspector(); draw(); syncPhase3();
    emit("items", state.items); emit("select", item);
    if (status) setStatus(`${item.name} mis à jour`);
    return { item, before };
  }

  function addItemsBatch(entryIds, options = {}, meta = {}) {
    const ids = Array.isArray(entryIds) ? entryIds.filter(Boolean) : [];
    if (!ids.length) return [];
    const entries = ids.map(entryOf).filter(Boolean);
    if (!entries.length) return [];
    const fit = roomFit();
    if (meta.history !== false) pushHistory();
    const created = [];
    entries.forEach((entry, index) => {
      const opt = typeof options === "function" ? (options(entry, index, created) || {}) : (options || {});
      const x = opt.x ?? (fit ? fit.x + fit.w * (0.42 + Math.min(index, 4) * 0.08) : getCanvasSize().width * 0.5);
      const y = opt.y ?? (fit ? fit.y + fit.h * 0.72 : getCanvasSize().height * 0.5);
      const item = {
        uid: nextUid(), catId: entry.base, entryId: entry.id, name: entry.name,
        price: entry.price || 0, color: opt.color || entry.color, w: entry.w, d: entry.d,
        x, y, rot: Number(opt.rot || 0), scale: Number(opt.scale || 1), z: state.zCounter++,
      };
      state.items.push(item);
      const pctx = placementContext();
      if (opt._spatialIntent && window.SpatialIntelligence?.placeNewItem) {
        const spatial = window.SpatialIntelligence.placeNewItem(item, pctx, { relation: opt.relation || null });
        if (!spatial?.changed && opt.x == null && opt.y == null && window.Placement) {
          const suggestion = window.Placement.suggestDrop(entry, pctx);
          if (suggestion) { item.x = suggestion.x; item.y = suggestion.y; item.rot = suggestion.rot; }
        }
      } else if (opt.x == null && opt.y == null && window.Placement) {
        const suggestion = window.Placement.suggestDrop(entry, pctx);
        if (suggestion) { item.x = suggestion.x; item.y = suggestion.y; item.rot = suggestion.rot; }
      }
      if (state.analysis) constrainToFloor(item, true);
      applyPlacement(item);
      created.push(item);
    });
    state.selectedId = created[created.length - 1].uid;
    renderInspector(); draw(); syncPhase3();
    emit("items", state.items); emit("select", getSelected());
    setStatus(`${created.length} meuble${created.length > 1 ? "s" : ""} ajouté${created.length > 1 ? "s" : ""}`);
    return created;
  }

  function select(uid) {
    state.selectedId = uid;
    const item = getSelected();
    if (item) item.z = state.zCounter++;
    renderInspector();
    draw();
    syncPhase3();
    emit("select", item);
  }

  function deleteItem(uid) {
    const item = state.items.find(i => i.uid === uid);
    if (!item) return;
    pushHistory();
    state.items = state.items.filter(i => i.uid !== uid);
    if (state.selectedId === uid) state.selectedId = null;
    renderInspector(); draw(); syncPhase3();
    emit("items", state.items); emit("select", null);
    setStatus(`${item.name} retiré de la pièce`);
  }

  function duplicateItem(uid) {
    const src = state.items.find(i => i.uid === uid);
    if (!src) return;
    pushHistory();
    const copy = { ...src, uid: nextUid(), x: src.x + 34, y: src.y + 26, z: state.zCounter++ };
    state.items.push(copy);
    state.selectedId = copy.uid;
    if (state.analysis) constrainToFloor(copy, true);
    renderInspector(); draw(); syncPhase3();
    emit("items", state.items); emit("select", copy);
    setStatus(`${copy.name} dupliqué`);
  }

  function setColor(uid, color) {
    const item = state.items.find(i => i.uid === uid);
    if (!item || item.color === color) return;
    pushHistory();
    item.color = color;
    // Le module 3D met en cache un groupe par meuble : on le retire pour
    // qu'il soit reconstruit avec la nouvelle teinte.
    const groups = window.Phase3?.state?.groups;
    const group = groups?.get(uid);
    if (group) { group.parent?.remove(group); groups.delete(uid); }
    renderInspector(); draw(); syncPhase3();
    emit("items", state.items);
    setStatus(`${item.name} — nouvelle finition`);
  }

  function clearScene() {
    if (!state.items.length) return;
    pushHistory();
    state.items = []; state.selectedId = null; state.zCounter = 1;
    renderInspector(); draw(); syncPhase3();
    emit("items", state.items); emit("select", null);
    setStatus("Meubles retirés");
  }

  const totalPrice = () => state.items.reduce((sum, i) => sum + (i.price || 0), 0);

  /* ---------------- Ancrage au sol / perspective ------------------ */

  function sampleFloorAtCanvas(x, y) {
    if (!state.floorMaskPx || !state.floorMaskImg) return false;
    const fit = roomFit();
    if (!fit) return false;
    const W = state.floorMaskImg.naturalWidth;
    const H = state.floorMaskImg.naturalHeight;
    const ix = Math.round(((x - fit.x) / Math.max(1, fit.w)) * (W - 1));
    const iy = Math.round(((y - fit.y) / Math.max(1, fit.h)) * (H - 1));
    if (ix < 0 || iy < 0 || ix >= W || iy >= H) return false;
    return state.floorMaskPx[(iy * W + ix) * 4] > 128;
  }

  function getSupportPoint(item) {
    const { w, h } = itemSize(item);
    const corners = [[-w / 2, -h / 2], [w / 2, -h / 2], [w / 2, h / 2], [-w / 2, h / 2]];
    const cos = Math.cos(item.rot), sin = Math.sin(item.rot);
    let best = { x: item.x, y: item.y };
    for (const [lx, ly] of corners) {
      const x = item.x + lx * cos - ly * sin;
      const y = item.y + lx * sin + ly * cos;
      if (y > best.y) best = { x, y };
    }
    return best;
  }

  function clampToRoom(item) {
    const fit = roomFit();
    if (!fit) return;
    const { w, h } = itemSize(item);
    const pad = 2;
    item.x = Math.max(fit.x + pad + w / 2, Math.min(fit.x + fit.w - pad - w / 2, item.x));
    item.y = Math.max(fit.y + pad + h / 2, Math.min(fit.y + fit.h - pad - h / 2, item.y));
  }

  function constrainToFloor(item, silent = false) {
    if (!item) return;
    clampToRoom(item);
    if (!state.floorMaskPx || !state.floorMaskImg || !state.analysis) return;

    const support = getSupportPoint(item);
    if (sampleFloorAtCanvas(support.x, support.y)) return;

    const fit = roomFit();
    const W = state.floorMaskImg.naturalWidth;
    const H = state.floorMaskImg.naturalHeight;
    const ix = Math.max(0, Math.min(W - 1, Math.round(((support.x - fit.x) / fit.w) * (W - 1))));
    const startY = Math.max(0, Math.min(H - 1, Math.round(((support.y - fit.y) / fit.h) * (H - 1))));

    let targetY = -1;
    for (let radius = 0; radius <= Math.min(H, 180); radius++) {
      for (const iy of [startY + radius, startY - radius]) {
        if (iy < 0 || iy >= H) continue;
        if (state.floorMaskPx[(iy * W + ix) * 4] > 128) { targetY = iy; break; }
      }
      if (targetY >= 0) break;
    }

    if (targetY >= 0) {
      const targetCanvasY = fit.y + (targetY / Math.max(1, H - 1)) * fit.h;
      item.y += targetCanvasY - support.y;
      clampToRoom(item);
      if (!silent) setStatus(`${item.name} posé sur le sol`);
    }
  }

  function getPerspectiveFactor(item) {
    const a = state.analysis;
    if (!a) return 1;
    const fit = roomFit();
    if (!fit) return 1;

    let depthFactor = null;
    if (Array.isArray(a.floor_depth) && a.floor_depth.length) {
      const imageY = Math.max(0, Math.min(a.height - 1,
        Math.round(((item.y - fit.y) / Math.max(1, fit.h)) * (a.height - 1))));
      const depth = Number(a.floor_depth[imageY]);
      const lo = Number(a.depth_floor_low);
      const hi = Number(a.depth_floor_high);
      if (Number.isFinite(depth) && Number.isFinite(lo) && Number.isFinite(hi) && hi > lo + 1e-4) {
        const t = Math.max(0, Math.min(1, (depth - lo) / (hi - lo)));
        const polarity = a.depth_near_is_high === false ? 1 - t : t;
        depthFactor = 0.62 + polarity * 0.68;
      }
    }

    const geometricT = Math.max(0, Math.min(1, (item.y - fit.y) / Math.max(1, fit.h)));
    const geometricFactor = 0.62 + geometricT * 0.68;
    return Math.max(0.58, Math.min(1.32, depthFactor == null
      ? geometricFactor
      : depthFactor * 0.75 + geometricFactor * 0.25));
  }

  function itemSize(item) {
    const persp = getPerspectiveFactor(item);
    return { w: item.w * PPM * item.scale * persp, h: item.d * PPM * item.scale * persp };
  }

  /* ------------------------- Rendu -------------------------------- */

  function drawContactShadow(targetCtx, item, w, h) {
    if (item.catId === "rug") return;
    targetCtx.save();
    targetCtx.translate(item.x, item.y + h * 0.38);
    targetCtx.rotate(item.rot);
    targetCtx.scale(1, 0.30);
    const r = Math.max(6, w * 0.56);
    const grad = targetCtx.createRadialGradient(0, 0, r * 0.08, 0, 0, r);
    grad.addColorStop(0, "rgba(12,10,8,0.44)");
    grad.addColorStop(0.5, "rgba(12,10,8,0.18)");
    grad.addColorStop(1, "rgba(12,10,8,0)");
    targetCtx.fillStyle = grad;
    targetCtx.beginPath();
    targetCtx.arc(0, 0, r, 0, Math.PI * 2);
    targetCtx.fill();
    targetCtx.restore();
  }

  function drawItem(item, targetCtx = ctx) {
    const { w, h } = itemSize(item);
    const img = spriteOf(item);
    if (!window.RealisticRendering) drawContactShadow(targetCtx, item, w, h);
    targetCtx.save();
    targetCtx.translate(item.x, item.y);
    targetCtx.rotate(item.rot);
    if (img && img.complete && img.naturalWidth) {
      targetCtx.drawImage(img, -w / 2, -h / 2, w, h);
    } else {
      targetCtx.fillStyle = item.color || "#8A8F98";
      targetCtx.beginPath();
      targetCtx.roundRect(-w / 2, -h / 2, w, h, Math.min(12, w * 0.12));
      targetCtx.fill();
    }
    targetCtx.restore();
  }

  function drawHoverRing(item) {
    const { w, h } = itemSize(item);
    ctx.save();
    ctx.translate(item.x, item.y);
    ctx.rotate(item.rot);
    ctx.strokeStyle = "rgba(255,255,255,0.85)";
    ctx.lineWidth = 1.5;
    ctx.setLineDash([5, 5]);
    ctx.beginPath();
    ctx.roundRect(-w / 2 - 3, -h / 2 - 3, w + 6, h + 6, 8);
    ctx.stroke();
    ctx.restore();
  }

  function knob(x, y, glyph, tint) {
    ctx.save();
    ctx.beginPath();
    ctx.arc(x, y, 14, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(20,18,14,0.28)";
    ctx.fill();
    ctx.beginPath();
    ctx.arc(x, y, 13, 0, Math.PI * 2);
    ctx.fillStyle = "#ffffff";
    ctx.fill();
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = "rgba(20,21,26,0.16)";
    ctx.stroke();
    ctx.fillStyle = tint || "#1B1C21";
    ctx.font = "600 14px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(glyph, x, y + 1);
    ctx.restore();
  }

  /* Cadre d'angles façon viseur : lisible sur n'importe quelle photo. */
  function drawSelection(item) {
    const { w, h } = itemSize(item);
    const arm = Math.max(12, Math.min(30, Math.min(w, h) * 0.3));
    const hw = w / 2, hh = h / 2;
    const corners = [
      [-hw, -hh, 1, 1], [hw, -hh, -1, 1],
      [hw, hh, -1, -1], [-hw, hh, 1, -1],
    ];

    ctx.save();
    ctx.translate(item.x, item.y);
    ctx.rotate(item.rot);

    ctx.strokeStyle = "rgba(255,255,255,0.28)";
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 6]);
    ctx.strokeRect(-hw, -hh, w, h);
    ctx.setLineDash([]);

    const paint = (color, width) => {
      ctx.strokeStyle = color;
      ctx.lineWidth = width;
      ctx.lineCap = "round";
      ctx.beginPath();
      for (const [cx, cy, dx, dy] of corners) {
        ctx.moveTo(cx + dx * arm, cy);
        ctx.lineTo(cx, cy);
        ctx.lineTo(cx, cy + dy * arm);
      }
      ctx.stroke();
    };
    paint("rgba(18,16,12,0.45)", 6);
    paint("#FFC42E", 3);

    ctx.strokeStyle = "rgba(255,196,46,0.65)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(0, -hh);
    ctx.lineTo(0, -hh - 30);
    ctx.stroke();
    ctx.restore();

    // Poignées dessinées hors rotation pour rester lisibles.
    const rotate = localToCanvas(item, 0, -hh - 30);
    const scale = localToCanvas(item, hw, hh);
    knob(rotate.x, rotate.y, "↻");
    knob(scale.x, scale.y, "⤢");

    drawMeasure(item, h);
  }

  function localToCanvas(item, lx, ly) {
    const cos = Math.cos(item.rot), sin = Math.sin(item.rot);
    return { x: item.x + lx * cos - ly * sin, y: item.y + lx * sin + ly * cos };
  }

  function drawMeasure(item, h) {
    const label = `${(item.w * item.scale).toFixed(2).replace(".", ",")} × ${(item.d * item.scale).toFixed(2).replace(".", ",")} m`;
    ctx.save();
    ctx.font = "600 12px ui-sans-serif, system-ui, sans-serif";
    const tw = ctx.measureText(label).width;
    const pw = tw + 18, ph = 24;
    const x = item.x - pw / 2;
    const y = Math.min(getCanvasSize().height - ph - 8, item.y + h / 2 + 16);
    ctx.fillStyle = "rgba(17,18,22,0.86)";
    ctx.beginPath();
    ctx.roundRect(x, y, pw, ph, 12);
    ctx.fill();
    ctx.fillStyle = "#FFFFFF";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(label, item.x, y + ph / 2 + 0.5);
    ctx.restore();
  }

  function drawDebugOverlays() {
    const f = roomFit();
    if (!f || !state.analysis) return;

    if (state.showDepth && state.depthImg?.complete && state.depthImg.naturalWidth) {
      ctx.save(); ctx.globalAlpha = 0.45;
      ctx.drawImage(state.depthImg, f.x, f.y, f.w, f.h);
      ctx.restore();
    }
    if (state.showMask && state.floorMaskImg?.complete && state.floorMaskImg.naturalWidth) {
      ctx.save();
      ctx.globalAlpha = 0.34;
      ctx.globalCompositeOperation = "lighten";
      ctx.drawImage(state.floorMaskImg, f.x, f.y, f.w, f.h);
      ctx.restore();
    }
    if (state.showFurnitureZones && Array.isArray(state.analysis.scene?.furniture)) {
      const sx = f.w / Math.max(1, state.analysis.width);
      const sy = f.h / Math.max(1, state.analysis.height);
      ctx.save();
      ctx.lineWidth = 2;
      ctx.font = "600 12px ui-sans-serif, system-ui, sans-serif";
      ctx.textBaseline = "middle";
      state.analysis.scene.furniture.forEach((r, idx) => {
        const x = f.x + r.x * sx, y = f.y + r.y * sy;
        const w = r.width * sx, h = r.height * sy;
        ctx.strokeStyle = "rgba(107,77,246,0.95)";
        ctx.fillStyle = "rgba(107,77,246,0.12)";
        ctx.beginPath(); ctx.roundRect(x, y, w, h, 6); ctx.fill(); ctx.stroke();
        const label = `${idx + 1}. ${r.label}`;
        const tw = ctx.measureText(label).width + 16;
        const ly = Math.max(f.y + 11, y - 12);
        ctx.fillStyle = "rgba(41,26,110,0.92)";
        ctx.beginPath(); ctx.roundRect(x, ly - 11, tw, 22, 11); ctx.fill();
        ctx.fillStyle = "#EDE7FF";
        ctx.fillText(label, x + 8, ly + 1);
      });
      ctx.restore();
    }
  }

  function draw() {
    const { width, height } = getCanvasSize();
    ctx.clearRect(0, 0, width, height);
    if (state.roomImage) {
      const f = roomFit();
      ctx.save();
      ctx.shadowColor = "rgba(0,0,0,0.55)";
      ctx.shadowBlur = 40;
      ctx.shadowOffsetY = 10;
      ctx.drawImage(state.roomImage, f.x, f.y, f.w, f.h);
      ctx.restore();
      drawDebugOverlays();
    }
    // En vue 3D, les meubles sont rendus par le module 3D : on évite
    // de les dessiner deux fois.
    if (window.Phase3?.state?.enabled) return;
    // Phase 12 live integration: shadows are drawn on the room canvas before
    // the furniture, so the feature is visible immediately in Photo mode too.
    if (window.RealisticRendering?.drawIntegrationShadows && state.roomImage && !window.Phase3?.state?.enabled) {
      try {
        window.RealisticRendering.drawIntegrationShadows(ctx, window.App, roomFit(), width, height, { quality: '2k' });
      } catch (_) {
        try { window.RealisticRendering.drawIntegrationShadows(ctx, window.App, roomFit(), width, height, { quality: '2k' }); } catch (__) {}
      }
    }
    const ordered = [...state.items].sort((a, b) => a.z - b.z);
    ordered.forEach(item => drawItem(item));
    const hovered = state.items.find(i => i.uid === state.hoverId);
    if (hovered && hovered.uid !== state.selectedId && !state.eraserOn) drawHoverRing(hovered);
    const selected = getSelected();
    if (selected) drawSelection(selected);
    drawGuides();
  }

  /** Repères d'aimantation : visibles seulement pendant la manipulation. */
  function drawGuides() {
    if (!state.guides.length && !state.collisions.length) return;
    ctx.save();
    ctx.lineWidth = 1;
    ctx.setLineDash([5, 4]);
    for (const guide of state.guides) {
      ctx.strokeStyle = guide.type === "wall" ? "rgba(255,196,46,0.9)" : "rgba(107,77,246,0.9)";
      ctx.beginPath();
      if (guide.type === "wall" || guide.type === "align-h") {
        const y = guide.y;
        const from = guide.type === "wall" ? guide.from : Math.min(guide.from, guide.to) - 40;
        const to = guide.type === "wall" ? guide.to : Math.max(guide.from, guide.to) + 40;
        ctx.moveTo(from, y);
        ctx.lineTo(to, y);
      } else {
        ctx.moveTo(guide.x, Math.min(guide.from, guide.to) - 40);
        ctx.lineTo(guide.x, Math.max(guide.from, guide.to) + 40);
      }
      ctx.stroke();
    }
    ctx.setLineDash([]);
    // Un chevauchement se signale, il ne bloque pas : la photo n'est pas un plan.
    ctx.strokeStyle = "rgba(226,88,72,0.95)";
    ctx.lineWidth = 2;
    for (const uid of state.collisions) {
      const other = state.items.find(i => i.uid === uid);
      if (!other) continue;
      const size = itemSize(other);
      ctx.save();
      ctx.translate(other.x, other.y);
      ctx.rotate(other.rot);
      ctx.strokeRect(-size.w / 2, -size.h / 2, size.w, size.h);
      ctx.restore();
    }
    ctx.restore();
  }

  /* ---------------------- Interactions ---------------------------- */

  function pointerPos(e) {
    const r = canvas.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  }

  function localPoint(item, p) {
    const cos = Math.cos(-item.rot), sin = Math.sin(-item.rot);
    return { x: (p.x - item.x) * cos - (p.y - item.y) * sin, y: (p.x - item.x) * sin + (p.y - item.y) * cos };
  }

  function hitHandles(item, p) {
    const { w, h } = itemSize(item);
    const q = localPoint(item, p);
    if (Math.hypot(q.x, q.y + h / 2 + 30) < 20) return "rotate";
    if (Math.hypot(q.x - w / 2, q.y - h / 2) < 20) return "scale";
    return null;
  }

  function hitItem(p) {
    const sorted = [...state.items].sort((a, b) => b.z - a.z);
    for (const item of sorted) {
      const { w, h } = itemSize(item);
      const q = localPoint(item, p);
      if (Math.abs(q.x) <= w / 2 && Math.abs(q.y) <= h / 2) return item;
    }
    return null;
  }

  function updateInspectorLive() {
    const item = getSelected();
    if (!item) return;
    const q = id => document.getElementById(id);
    const deg = Math.round((((item.rot * 180 / Math.PI) % 360) + 360) % 360);
    const signedDeg = deg > 180 ? deg - 360 : deg;
    const rot = q("rotVal"), scale = q("scaleVal"), size = q("sizeMetric");
    const rotSlider = q("rotSlider"), scaleSlider = q("scaleSlider");
    const widthInput = q("dimWidth"), depthInput = q("dimDepth");
    if (rot) rot.textContent = `${signedDeg}°`;
    if (scale) scale.textContent = `${Math.round(item.scale * 100)} %`;
    if (size) size.textContent = `${(item.w * item.scale).toFixed(2)} × ${(item.d * item.scale).toFixed(2)} m`;
    if (rotSlider) rotSlider.value = Math.max(-180, Math.min(180, signedDeg));
    if (scaleSlider) scaleSlider.value = Math.round(item.scale * 100);
    if (widthInput && document.activeElement !== widthInput) widthInput.value = (item.w * item.scale).toFixed(2);
    if (depthInput && document.activeElement !== depthInput) depthInput.value = (item.d * item.scale).toFixed(2);
    updateContextToolbar();
  }

  function updateContextToolbar() { window.CigogneUI?.updateContext?.(); }

  canvas.addEventListener("pointerdown", e => {
    if (state.eraserOn) return;
    const p = pointerPos(e);
    const selected = getSelected();
    if (selected) {
      const handle = hitHandles(selected, p);
      if (handle) {
        beginChange();
        state.drag = { mode: handle, uid: selected.uid, dist0: Math.max(1, Math.hypot(p.x - selected.x, p.y - selected.y)) };
        canvas.setPointerCapture?.(e.pointerId);
        e.preventDefault();
        return;
      }
    }
    const item = hitItem(p);
    if (item) {
      beginChange();
      select(item.uid);
      state.drag = {
        mode: "move", uid: item.uid,
        startX: item.x, startY: item.y,
        pointerStartX: p.x, pointerStartY: p.y, pointerId: e.pointerId, moved: false,
      };
      canvas.setPointerCapture?.(e.pointerId);
      canvas.style.cursor = "grabbing";
      e.preventDefault();
      return;
    }
    if (state.selectedId) { state.selectedId = null; renderInspector(); draw(); emit("select", null); }
  });

  canvas.addEventListener("pointermove", e => {
    const p = pointerPos(e);

    if (!state.drag) {
      if (state.eraserOn) return;
      const over = hitItem(p);
      const uid = over?.uid || null;
      if (uid !== state.hoverId) {
        state.hoverId = uid;
        canvas.style.cursor = uid ? "grab" : "default";
        draw();
      } else if (getSelected() && hitHandles(getSelected(), p)) {
        canvas.style.cursor = "pointer";
      }
      return;
    }

    const item = state.items.find(i => i.uid === state.drag.uid);
    if (!item) return;

    if (state.drag.mode === "move") {
      const speed = e.shiftKey ? 0.35 : 1;   // Maj = réglage fin
      item.x = state.drag.startX + (p.x - state.drag.pointerStartX) * speed;
      item.y = state.drag.startY + (p.y - state.drag.pointerStartY) * speed;
      state.drag.moved = true;
      clampToRoom(item);
      if (!e.altKey) applyPlacement(item);   // Alt = placement libre
    } else if (state.drag.mode === "rotate") {
      item.rot = Math.atan2(p.y - item.y, p.x - item.x) + Math.PI / 2;
      if (e.shiftKey) { const step = Math.PI / 12; item.rot = Math.round(item.rot / step) * step; }
      updateInspectorLive();
    } else if (state.drag.mode === "scale") {
      const dist = Math.hypot(p.x - item.x, p.y - item.y);
      item.scale = Math.min(3, Math.max(0.3, item.scale * (dist / state.drag.dist0)));
      state.drag.dist0 = Math.max(1, dist);
      updateInspectorLive();
    }
    draw();
    updateContextToolbar();
  });

  canvas.addEventListener("pointerup", e => {
    if (!state.drag) return;
    const mode = state.drag.mode;
    const item = state.items.find(i => i.uid === state.drag.uid);
    if (mode !== "move" || state.drag.moved) commitChange(); else cancelChange();
    state.drag = null;
    canvas.style.cursor = state.hoverId ? "grab" : "default";
    try { canvas.releasePointerCapture?.(e.pointerId); } catch { /* ignoré */ }
    if (item) {
      if (mode === "move") constrainToFloor(item, false);
      const wasSnapped = state.snapped;
      const blocked = state.clearance;
      clearGuides();
      renderInspector(); draw(); syncPhase3();
      emit("items", state.items);
      if (mode === "move" && wasSnapped === "wall") setStatus(`${item.name} adossé au mur`);
      else if (blocked) setStatus(`${item.name} : prévoyez ${blocked.label} de passage devant`);
    } else {
      clearGuides();
    }
  });

  canvas.addEventListener("pointercancel", () => { cancelChange(); state.drag = null; });
  canvas.addEventListener("pointerleave", () => {
    if (state.drag || !state.hoverId) return;
    state.hoverId = null; draw();
  });

  canvas.addEventListener("wheel", e => {
    if (state.eraserOn) return;
    const item = getSelected() || hitItem(pointerPos(e));
    if (!item) return;
    e.preventDefault();
    if (e.ctrlKey || e.metaKey) {
      item.scale = Math.min(3, Math.max(.3, item.scale * (e.deltaY > 0 ? .96 : 1.04)));
      setStatus(`${item.name} — ${Math.round(item.scale * 100)} %`);
    } else {
      item.rot += e.deltaY > 0 ? 0.05 : -0.05;
      if (e.shiftKey) { const step = Math.PI / 12; item.rot = Math.round(item.rot / step) * step; }
      setStatus(`${item.name} — ${Math.round(item.rot * 180 / Math.PI)}°`);
    }
    if (state.selectedId !== item.uid) select(item.uid);
    else { updateInspectorLive(); draw(); syncPhase3(); }
  }, { passive: false });

  let nudgeTimer = 0;
  window.addEventListener("keydown", e => {
    const tag = document.activeElement?.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || document.activeElement?.isContentEditable) return;
    const item = getSelected();
    if ((e.key === "Delete" || e.key === "Backspace") && state.selectedId) { e.preventDefault(); deleteItem(state.selectedId); return; }
    if (!item) return;
    const nudge = e.shiftKey ? 12 : 3;
    const arrows = { ArrowLeft: [-nudge, 0], ArrowRight: [nudge, 0], ArrowUp: [0, -nudge], ArrowDown: [0, nudge] };
    if (arrows[e.key]) {
      e.preventDefault();
      if (!pendingChange) beginChange();
      clearTimeout(nudgeTimer);
      nudgeTimer = setTimeout(commitChange, 500);
      item.x += arrows[e.key][0]; item.y += arrows[e.key][1];
      clampToRoom(item); updateInspectorLive(); draw(); syncPhase3();
    }
    if (e.key.toLowerCase() === "d" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); duplicateItem(item.uid); }
  });

  /* ---------------- Panneau produit (inspecteur) ------------------ */

  function renderInspector() {
    const item = getSelected();
    if (!item) {
      inspectorBody.innerHTML = "";
      updateContextToolbar();
      return;
    }
    const entry = entryOf(item.entryId);
    const deg = Math.round((((item.rot * 180 / Math.PI) % 360) + 360) % 360);
    const signedDeg = deg > 180 ? deg - 360 : deg;
    const persp = Math.round(getPerspectiveFactor(item) * 100);
    const realW = (item.w * item.scale).toFixed(2);
    const realD = (item.d * item.scale).toFixed(2);
    const swatches = (entry?.colors || [item.color]).map(c => `
      <button class="swatch${c === item.color ? " is-active" : ""}" type="button" data-color="${c}"
        style="--swatch:${c}" aria-label="Finition ${c}"></button>`).join("");

    inspectorBody.innerHTML = `
      <div class="control-section">
        <div class="section-label">Finition</div>
        <div class="swatch-row">${swatches}</div>
      </div>

      <div class="control-section">
        <div class="section-label">Dimensions</div>
        <div class="dimensions-grid">
          <label class="field-inline"><span>Largeur</span><input id="dimWidth" inputmode="decimal" type="number" min="0.10" max="10" step="0.01" value="${realW}"><em>m</em></label>
          <label class="field-inline"><span>Profondeur</span><input id="dimDepth" inputmode="decimal" type="number" min="0.10" max="10" step="0.01" value="${realD}"><em>m</em></label>
        </div>
        <div class="readout">
          <span id="sizeMetric">${realW} × ${realD} m</span>
          <strong id="scaleVal">${Math.round(item.scale * 100)} %</strong>
        </div>
        <input class="big-range" id="scaleSlider" type="range" min="30" max="300" value="${Math.round(item.scale * 100)}" aria-label="Taille">
        <div class="micro-actions">
          <button id="scaleDown" class="btn outline" type="button">Réduire</button>
          <button id="scaleReset" class="btn outline" type="button">Taille réelle</button>
          <button id="scaleUp" class="btn outline" type="button">Agrandir</button>
        </div>
      </div>

      <div class="control-section">
        <div class="section-label">Orientation</div>
        <div class="readout"><span>Angle</span><strong id="rotVal">${signedDeg}°</strong></div>
        <input class="big-range" id="rotSlider" type="range" min="-180" max="180" value="${signedDeg}" aria-label="Rotation">
        <div class="micro-actions">
          <button id="rotMinus" class="btn outline" type="button">↶ 15°</button>
          <button id="rotReset" class="btn outline" type="button">Face</button>
          <button id="rotPlus" class="btn outline" type="button">15° ↷</button>
        </div>
      </div>

      <div class="control-section">
        <div class="section-label">Position</div>
        <div class="position-pad">
          <button class="nudge" data-dx="0" data-dy="-8" aria-label="Reculer">↑</button>
          <button class="nudge" data-dx="-8" data-dy="0" aria-label="Vers la gauche">←</button>
          <button class="nudge" data-dx="8" data-dy="0" aria-label="Vers la droite">→</button>
          <button class="nudge" data-dx="0" data-dy="8" aria-label="Avancer">↓</button>
        </div>
        <p class="hint">Vous pouvez aussi faire glisser le meuble directement sur la photo.</p>
      </div>

      <div class="action-row">
        <button class="btn outline" id="dupBtn" type="button">Dupliquer</button>
        <button class="btn outline danger" id="delBtn" type="button">Retirer</button>
      </div>

      <details class="advanced-details">
        <summary>Détails techniques</summary>
        <div class="advanced-copy">
          <div>Perspective appliquée <b>${persp} %</b></div>
          <div>Dimensions catalogue <b>${item.w.toFixed(2)} × ${item.d.toFixed(2)} m</b></div>
          <div>Échelle <b>${Math.round(item.scale * 100)} %</b></div>
        </div>
      </details>`;

    const q = id => document.getElementById(id);
    const refresh = () => { updateInspectorLive(); draw(); syncPhase3(); emit("items", state.items); };
    const setScale = value => { item.scale = Math.max(.3, Math.min(3, value)); refresh(); };

    q("scaleSlider").addEventListener("pointerdown", pushHistory, { once: true });
    q("rotSlider").addEventListener("pointerdown", pushHistory, { once: true });
    q("scaleSlider").oninput = e => setScale(Number(e.target.value) / 100);
    q("scaleDown").onclick = () => { pushHistory(); setScale(item.scale - .05); };
    q("scaleUp").onclick = () => { pushHistory(); setScale(item.scale + .05); };
    q("scaleReset").onclick = () => { pushHistory(); setScale(1); };

    const setDim = (which, value) => {
      const v = Number(value);
      if (!Number.isFinite(v) || v <= 0) return;
      pushHistory();
      item.scale = Math.max(.3, Math.min(3, v / (which === "w" ? item.w : item.d)));
      refresh();
    };
    q("dimWidth").onchange = e => setDim("w", e.target.value);
    q("dimDepth").onchange = e => setDim("d", e.target.value);

    q("rotSlider").oninput = e => { item.rot = Number(e.target.value) * Math.PI / 180; refresh(); };
    q("rotMinus").onclick = () => { pushHistory(); item.rot -= Math.PI / 12; refresh(); };
    q("rotPlus").onclick = () => { pushHistory(); item.rot += Math.PI / 12; refresh(); };
    q("rotReset").onclick = () => { pushHistory(); item.rot = 0; refresh(); };

    inspectorBody.querySelectorAll(".nudge").forEach(btn => btn.onclick = () => {
      pushHistory();
      item.x += Number(btn.dataset.dx); item.y += Number(btn.dataset.dy);
      clampToRoom(item); constrainToFloor(item, true); refresh();
    });
    inspectorBody.querySelectorAll(".swatch").forEach(btn => btn.onclick = () => setColor(item.uid, btn.dataset.color));
    q("dupBtn").onclick = () => duplicateItem(item.uid);
    q("delBtn").onclick = () => deleteItem(item.uid);
    updateContextToolbar();
  }

  /* ------------------------ Pièce / photo ------------------------- */

  function resetAnalysis() {
    state.analysis = null; state.floorMaskImg = null; state.floorMaskPx = null; state.depthImg = null;
    state.showMask = false; state.showDepth = false; state.showFurnitureZones = false;
    ["maskLabel", "depthLabel", "zonesLabel"].forEach(id => {
      const el = document.getElementById(id); if (el) el.style.display = "none";
    });
    ["maskChk", "depthChk", "zonesChk"].forEach(id => {
      const el = document.getElementById(id); if (el) el.checked = false;
    });
    emit("analysis", null);
  }

  function setRoomImage(img, name) {
    state.roomImage = img;
    state.roomFileName = name || state.roomFileName;
    emptyState.style.display = "none";
    emptyState.setAttribute("aria-hidden", "true");
    const sceneName = document.getElementById("sceneName");
    if (sceneName && name) sceneName.textContent = name.replace(/\.[^/.]+$/, "").slice(0, 40) || "Ma pièce";
    draw();
    emit("room", img);
  }

  function loadRoomFile(file) {
    if (!file) return;
    if (!file.type.startsWith("image/")) { setStatus("Ce fichier n'est pas une image. Choisissez un JPG, PNG ou WebP."); return; }
    if (state.roomObjectUrl) URL.revokeObjectURL(state.roomObjectUrl);
    state.roomObjectUrl = URL.createObjectURL(file);
    resetAnalysis();
    const img = new Image();
    img.onload = () => { setRoomImage(img, file.name); setStatus(`Pièce importée : ${file.name}`); };
    img.onerror = () => setStatus("Impossible de lire cette image. Essayez un autre fichier.");
    img.src = state.roomObjectUrl;
  }

  document.getElementById("roomUpload").addEventListener("change", e => {
    loadRoomFile(e.target.files?.[0]);
    e.target.value = "";
  });

  /* ------------------ Spatial Intelligence ----------------------- */
  function refreshSpatialCard() {
    const card = document.getElementById("spatialCard");
    const badge = document.getElementById("spatialBadge");
    if (!card) return;
    const ready = Boolean(state.analysis && window.SpatialIntelligence);
    card.hidden = !ready;
    if (badge) badge.textContent = ready ? "Actif" : "En attente";
  }

  function optimizeSpatialScene() {
    if (!state.analysis || !window.SpatialIntelligence || !state.items.length) {
      setStatus("Ajoutez au moins un meuble et analysez la pièce.");
      return;
    }
    pushHistory();
    const changed = window.SpatialIntelligence.optimizeScene(placementContext());
    state.items.forEach(item => { constrainToFloor(item, true); applyPlacement(item); });
    draw(); syncPhase3(); emit("items", state.items); emit("select", getSelected());
    const result = document.getElementById("spatialResult");
    if (result) {
      result.hidden = false;
      result.textContent = changed
        ? `${changed} meuble${changed > 1 ? "s" : ""} repositionné${changed > 1 ? "s" : ""} selon la profondeur et les relations détectées.`
        : "La composition est déjà cohérente avec les contraintes détectées.";
    }
    setStatus(changed
      ? `Placement intelligent : ${changed} meuble${changed > 1 ? "s" : ""} optimisé${changed > 1 ? "s" : ""}`
      : "Placement intelligent : aucune correction nécessaire");
  }

  document.getElementById("spatialOptimizeBtn")?.addEventListener("click", optimizeSpatialScene);
  on("analysis", refreshSpatialCard);
  on("room", refreshSpatialCard);
  on("items", refreshSpatialCard);

  /* ----------------------- Export simple -------------------------- */

  /** Photo + meubles sur un canvas hors écran (export, vignette, partage). */
  function renderComposite(maxSide = null) {
    const out = document.createElement("canvas");
    if (state.roomImage) { out.width = state.roomImage.naturalWidth; out.height = state.roomImage.naturalHeight; }
    else { const { width, height } = getCanvasSize(); out.width = Math.round(width); out.height = Math.round(height); }
    if (maxSide) {
      const ratio = Math.min(1, maxSide / Math.max(out.width, out.height));
      out.width = Math.max(1, Math.round(out.width * ratio));
      out.height = Math.max(1, Math.round(out.height * ratio));
    }
    const octx = out.getContext("2d");
    if (state.roomImage) octx.drawImage(state.roomImage, 0, 0, out.width, out.height);
    else { octx.fillStyle = "#101115"; octx.fillRect(0, 0, out.width, out.height); }
    const fit = roomFit();
    const ordered = [...state.items].sort((a, b) => a.z - b.z);
    if (fit) {
      const factor = out.width / Math.max(1, fit.w);
      octx.save();
      octx.scale(factor, factor);
      octx.translate(-fit.x, -fit.y);
      ordered.forEach(item => drawItem(item, octx));
      octx.restore();
    } else {
      ordered.forEach(item => drawItem(item, octx));
    }
    return out;
  }

  function exportPng(filename = "cigogne-composition.png") {
    const out = document.createElement("canvas");
    if (state.roomImage) { out.width = state.roomImage.naturalWidth; out.height = state.roomImage.naturalHeight; }
    else { const { width, height } = getCanvasSize(); out.width = Math.round(width); out.height = Math.round(height); }
    const octx = out.getContext("2d");
    if (state.roomImage) octx.drawImage(state.roomImage, 0, 0, out.width, out.height);
    else { octx.fillStyle = "#101115"; octx.fillRect(0, 0, out.width, out.height); }
    const fit = roomFit();
    if (fit) {
      const factor = out.width / Math.max(1, fit.w);
      octx.save();
      octx.scale(factor, factor);
      octx.translate(-fit.x, -fit.y);
      [...state.items].sort((a, b) => a.z - b.z).forEach(item => drawItem(item, octx));
      octx.restore();
    } else {
      [...state.items].sort((a, b) => a.z - b.z).forEach(item => drawItem(item, octx));
    }
    try {
      const a = document.createElement("a");
      a.download = filename;
      a.href = out.toDataURL("image/png");
      a.click();
      setStatus("Image enregistrée");
    } catch (err) {
      console.error(err);
      setStatus("Export impossible : l'image de la pièce provient d'un autre site.");
    }
  }

  /* -------------------------- Démarrage --------------------------- */

  const viewport = canvas.parentElement;
  if (window.ResizeObserver) {
    let frame = 0;
    new ResizeObserver(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(resize);
    }).observe(viewport);
  }
  window.addEventListener("resize", resize);

  window.App = {
    PPM, state, canvas, ctx,
    setStatus, draw, resize, roomFit, getCanvasSize,
    getSelected, hitItem, itemSize, resetAnalysis,
    getPerspectiveFactor, constrainToFloor, getSupportPoint, clampToRoom,
    on, emit, undo, redo, pushHistory, historyInfo,
    exportPng, renderComposite, clearScene, totalPrice, loadRoomFile, setRoomImage, spriteOf,
    applyPlacement, clearGuides, placementContext, entryOf, updateItem, addItemsBatch,
    optimizeSpatialScene,
  };
  window.AppActions = { addItem, addItemAt: (id, x, y) => addItem(id, { x, y }), addItemsBatch, updateItem, select, deleteItem, duplicateItem, setColor, renderInspector };

  // Alias historiques conservés pour les modules existants.
  window.roomFit = roomFit; window.setStatus = setStatus; window.getSelected = getSelected;
  window.hitItem = hitItem; window.itemSize = itemSize; window.draw = draw; window.PPM = PPM;

  resize();
  renderInspector();
  setStatus("Importez une photo pour commencer");
})();

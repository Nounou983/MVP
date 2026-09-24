/* =========================================================
   La Cigogne D'Ailleurs — Projets persistants
   Un projet est un document JSON : la photo (référence ou copie
   réduite), les meubles et leurs transformations, les couleurs,
   la caméra, l'éclairage et la trace des retouches IA.

   Deux destinations :
     • connecté   → API, donc récupérable depuis n'importe quel appareil ;
     • déconnecté → stockage local du navigateur, pour ne rien perdre.
   La bascule est automatique et la personne en est informée.
   ========================================================= */

(() => {
  "use strict";

  const cfg = window.CIGOGNE_CONFIG || {};
  const API = window.CigogneAPI;
  const App = window.App;
  if (!App) return;

  const SCHEMA = 1;
  const LOCAL_KEY = "cigogne.project.v1";
  const LOCAL_ROOM_MAX = 1400;       // px : copie réduite stockée localement

  const state = {
    id: null,             // id serveur, null tant que non sauvegardé
    name: "Ma composition",
    roomKey: null,        // clé de stockage serveur de la photo
    dirty: false,
    saving: false,
    lastSavedAt: null,
    lastError: null,
    aiEdits: [],
    target: "local",      // "cloud" | "local"
  };

  const listeners = new Set();
  const notify = () => listeners.forEach(fn => { try { fn(status()); } catch (e) { console.error(e); } });

  function status() {
    return {
      id: state.id,
      name: state.name,
      dirty: state.dirty,
      saving: state.saving,
      target: state.target,
      lastSavedAt: state.lastSavedAt,
      lastError: state.lastError,
    };
  }

  /* ------------------------- sérialisation ------------------------- */

  function cameraDoc() {
    const p3 = window.Phase3?.state;
    if (!p3) return null;
    const c = p3.calibration || {};
    return {
      fov: c.fov, pitch: c.pitch, height: c.height, depth: c.depth,
      targetY: c.targetY, auto: c.auto, mode: p3.mode,
    };
  }

  function lightingDoc() {
    const l = window.Phase3?.state?.lighting;
    if (!l) return null;
    return {
      auto: l.auto, temperature: l.temperature, exposure: l.exposure,
      key: l.key, ambient: l.ambient, fill: l.fill,
    };
  }

  function itemDoc(item) {
    return {
      uid: item.uid, entryId: item.entryId, catId: item.catId, name: item.name,
      price: item.price, color: item.color,
      w: item.w, d: item.d,
      x: item.x, y: item.y, rot: item.rot, scale: item.scale, z: item.z,
      custom3D: Boolean(item.custom3D),
      modelKey: item.modelKey || null,
    };
  }

  /** Coordonnées relatives à la photo : un projet rouvert sur un
      écran plus petit doit retrouver ses meubles au bon endroit. */
  function toRelative(item, fit) {
    if (!fit) return { rx: null, ry: null };
    return { rx: (item.x - fit.x) / fit.w, ry: (item.y - fit.y) / fit.h };
  }

  function serialize() {
    const fit = App.roomFit();
    const analysis = App.state.analysis;
    const roomModel = window.CigogneRoom?.model || null;
    return {
      schema: SCHEMA,
      app_version: "6.0.0",
      name: state.name,
      room: {
        key: state.roomKey,
        file_name: App.state.roomFileName || "",
        width: App.state.roomImage?.naturalWidth || null,
        height: App.state.roomImage?.naturalHeight || null,
      },
      items: App.state.items.map(item => Object.assign(itemDoc(item), toRelative(item, fit))),
      camera: cameraDoc(),
      lighting: lightingDoc(),
      analysis: analysis ? {
        present: true,
        width: analysis.width, height: analysis.height,
        floor_source: analysis.floor_source,
        furniture_count: analysis.scene?.furniture_count ?? null,
      } : { present: false },
      room_model: roomModel ? {
        confidence: roomModel.confidence,
        reliable: roomModel.reliable,
        dimensions: roomModel.dimensions,
      } : null,
      ai_edits: state.aiEdits.slice(-40),
      updated_at: new Date().toISOString(),
    };
  }

  /* --------------------------- restitution -------------------------- */

  function applyItems(doc) {
    const fit = App.roomFit();
    const items = (doc.items || []).map(raw => {
      const item = Object.assign({}, raw);
      if (fit && Number.isFinite(raw.rx) && Number.isFinite(raw.ry)) {
        item.x = fit.x + raw.rx * fit.w;
        item.y = fit.y + raw.ry * fit.h;
      }
      delete item.rx;
      delete item.ry;
      return item;
    });
    App.state.items = items;
    App.state.selectedId = null;
    App.state.zCounter = items.reduce((max, i) => Math.max(max, i.z || 0), 0) + 1;
    App.draw();
    App.emit("items", items);
    App.emit("select", null);
  }

  function applyCameraAndLighting(doc) {
    const p3 = window.Phase3?.state;
    if (!p3) return;
    if (doc.camera) {
      Object.assign(p3.calibration, {
        fov: doc.camera.fov ?? p3.calibration.fov,
        pitch: doc.camera.pitch ?? p3.calibration.pitch,
        height: doc.camera.height ?? p3.calibration.height,
        depth: doc.camera.depth ?? p3.calibration.depth,
        auto: doc.camera.auto ?? p3.calibration.auto,
      });
    }
    if (doc.lighting) Object.assign(p3.lighting, doc.lighting);
    window.Phase3?.sync?.();
  }

  function loadImageFrom(src, name) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.crossOrigin = "anonymous";
      img.onload = () => { App.setRoomImage(img, name || "projet"); resolve(img); };
      img.onerror = () => reject(new Error("Photo du projet illisible."));
      img.src = src;
    });
  }

  async function applyDocument(doc, { roomSrc } = {}) {
    if (!doc) return false;
    state.name = doc.name || state.name;
    state.roomKey = doc.room?.key || null;
    state.aiEdits = doc.ai_edits || [];

    if (roomSrc) {
      try {
        await loadImageFrom(roomSrc, doc.room?.file_name);
      } catch (err) {
        App.setStatus(err.message);
      }
    }
    applyItems(doc);
    applyCameraAndLighting(doc);

    const nameEl = document.getElementById("sceneName");
    if (nameEl) nameEl.textContent = state.name;
    state.dirty = false;
    notify();
    window.Analytics?.track("project_open", { item_count: (doc.items || []).length, source: state.target });
    return true;
  }

  /* ------------------------ photo de la pièce ----------------------- */

  /** Copie réduite de la photo affichée (retouches IA comprises). */
  function roomSnapshot(maxSide = LOCAL_ROOM_MAX, quality = 0.78) {
    const img = App.state.roomImage;
    if (!img?.naturalWidth) return null;
    const scale = Math.min(1, maxSide / Math.max(img.naturalWidth, img.naturalHeight));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(img.naturalWidth * scale));
    canvas.height = Math.max(1, Math.round(img.naturalHeight * scale));
    const ctx = canvas.getContext("2d");
    if (!ctx) return null;
    try {
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      return canvas.toDataURL("image/jpeg", quality);
    } catch (err) {
      console.warn("[projet] capture de la photo impossible", err);
      return null;
    }
  }

  function roomBlob(maxSide = 2400, quality = 0.9) {
    return new Promise(resolve => {
      const img = App.state.roomImage;
      if (!img?.naturalWidth) return resolve(null);
      const scale = Math.min(1, maxSide / Math.max(img.naturalWidth, img.naturalHeight));
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(img.naturalWidth * scale));
      canvas.height = Math.max(1, Math.round(img.naturalHeight * scale));
      const ctx = canvas.getContext("2d");
      if (!ctx) return resolve(null);
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      canvas.toBlob(blob => resolve(blob), "image/jpeg", quality);
    });
  }

  /* ---------------------------- local ------------------------------ */

  function saveLocal() {
    try {
      const doc = serialize();
      const payload = { doc, room: roomSnapshot(), saved_at: Date.now() };
      localStorage.setItem(LOCAL_KEY, JSON.stringify(payload));
      state.dirty = false;
      state.lastSavedAt = Date.now();
      state.lastError = null;
      state.target = "local";
      notify();
      return true;
    } catch (err) {
      // Quota dépassé : on retente sans la photo plutôt que de perdre le plan.
      try {
        localStorage.setItem(LOCAL_KEY, JSON.stringify({ doc: serialize(), room: null, saved_at: Date.now() }));
        state.lastError = "Photo trop lourde pour le stockage local : seul le plan est conservé.";
        notify();
        return true;
      } catch (inner) {
        state.lastError = "Sauvegarde locale impossible (stockage plein).";
        console.error("[projet] sauvegarde locale impossible", inner);
        notify();
        return false;
      }
    }
  }

  function readLocal() {
    try {
      const raw = localStorage.getItem(LOCAL_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  }

  async function restoreLocal() {
    const payload = readLocal();
    if (!payload?.doc) return false;
    state.target = "local";
    return applyDocument(payload.doc, { roomSrc: payload.room });
  }

  function clearLocal() {
    try { localStorage.removeItem(LOCAL_KEY); } catch { /* ignoré */ }
  }

  /* ----------------------------- cloud ----------------------------- */

  async function uploadRoomIfNeeded() {
    if (!API?.isSignedIn() || !App.state.roomImage) return null;
    const blob = await roomBlob();
    if (!blob) return null;
    const file = new File([blob], App.state.roomFileName || "piece.jpg", { type: "image/jpeg" });
    const uploaded = await API.uploadImage(file, "image", state.id);
    state.roomKey = uploaded.key;
    return uploaded.key;
  }

  /** Rendu aplati de la composition : c'est ce que voit le client
      dans le lien partagé et dans la liste des projets. */
  function compositeBlob(maxSide = 1600, quality = 0.88) {
    return new Promise(resolve => {
      const canvas = App.renderComposite?.(maxSide);
      if (!canvas) return resolve(null);
      try { canvas.toBlob(blob => resolve(blob), "image/jpeg", quality); }
      catch { resolve(null); }
    });
  }

  async function uploadThumbnail() {
    if (!API?.isSignedIn() || !App.state.roomImage) return null;
    const blob = await compositeBlob();
    if (!blob) return null;
    const file = new File([blob], "apercu.jpg", { type: "image/jpeg" });
    const uploaded = await API.uploadImage(file, "thumbnail", state.id);
    return uploaded.key;
  }

  async function saveCloud({ withRoom = true } = {}) {
    if (!API?.isSignedIn()) throw new API.ApiError("Connectez-vous pour enregistrer en ligne.", 401);
    state.saving = true;
    state.lastError = null;
    notify();
    try {
      if (withRoom && !state.roomKey) await uploadRoomIfNeeded();
      let thumbnailKey = null;
      try { thumbnailKey = await uploadThumbnail(); }
      catch (err) { console.warn("[projet] aperçu non envoyé", err.message); }
      const body = { name: state.name, state: serialize(), room_key: state.roomKey };
      if (thumbnailKey) body.thumbnail_key = thumbnailKey;
      const saved = state.id
        ? await API.saveProject(state.id, body)
        : await API.createProject(body);
      state.id = saved.id;
      state.dirty = false;
      state.lastSavedAt = Date.now();
      state.target = "cloud";
      window.Analytics?.track("project_save", { item_count: App.state.items.length, source: "cloud" });
      return saved;
    } catch (err) {
      state.lastError = err.message;
      throw err;
    } finally {
      state.saving = false;
      notify();
    }
  }

  async function openCloud(id) {
    const project = await API.readProject(id);
    state.id = project.id;
    state.target = "cloud";
    let roomSrc = null;
    if (project.state?.room?.key || project.room_key) {
      const key = project.state?.room?.key || project.room_key;
      roomSrc = `${API.base}/api/files/${key}`;
      // Le lien signé est fourni par l'API ; sinon on passe par le jeton.
      try {
        const blob = await API.raw(`/api/files/${key}`, { method: "GET" });
        if (typeof blob === "string" && blob.startsWith("data:")) roomSrc = blob;
      } catch { /* on tentera l'URL directe */ }
    }
    return applyDocument(project.state, { roomSrc });
  }

  async function listCloud() {
    if (!API?.isSignedIn()) return [];
    const payload = await API.listProjects();
    return payload.projects || [];
  }

  /* --------------------------- sauvegarde -------------------------- */

  async function save({ silent = false } = {}) {
    if (API?.isSignedIn()) {
      try {
        const saved = await saveCloud();
        if (!silent) App.setStatus("Projet enregistré en ligne");
        return saved;
      } catch (err) {
        saveLocal();
        if (!silent) App.setStatus(`Enregistré sur cet appareil — ${err.message}`);
        return null;
      }
    }
    const ok = saveLocal();
    if (!silent) App.setStatus(ok ? "Projet enregistré sur cet appareil" : state.lastError);
    window.Analytics?.track("project_save", { item_count: App.state.items.length, source: "local" });
    return null;
  }

  let autosaveTimer = 0;
  function markDirty() {
    state.dirty = true;
    notify();
    const delay = Number(cfg.autosaveDelay || 0);
    if (!delay) return;
    clearTimeout(autosaveTimer);
    autosaveTimer = setTimeout(() => save({ silent: true }), delay);
  }

  function recordAiEdit(entry) {
    state.aiEdits.push(Object.assign({ at: new Date().toISOString() }, entry || {}));
    markDirty();
  }

  ["items", "room", "push", "analysis"].forEach(event => App.on(event, markDirty));

  window.addEventListener("beforeunload", event => {
    if (!state.dirty) return;
    // Autosave local synchrone : mieux vaut une copie que rien.
    saveLocal();
    if (cfg.autosaveDelay === 0) {
      event.preventDefault();
      event.returnValue = "";
    }
  });

  window.Projects = {
    SCHEMA,
    status, onChange(fn) { listeners.add(fn); fn(status()); return () => listeners.delete(fn); },
    serialize, applyDocument,
    save, saveLocal, restoreLocal, clearLocal, hasLocal: () => Boolean(readLocal()),
    saveCloud, openCloud, listCloud,
    roomSnapshot, roomBlob, compositeBlob,
    markDirty, recordAiEdit,
    rename(name) { state.name = (name || "").trim().slice(0, 120) || "Ma composition"; markDirty(); },
    reset() {
      state.id = null;
      state.roomKey = null;
      state.name = "Ma composition";
      state.aiEdits = [];
      state.dirty = false;
      state.saving = false;
      state.lastSavedAt = null;
      state.lastError = null;
      state.target = "local";
      const nameEl = document.getElementById("sceneName");
      if (nameEl) nameEl.textContent = state.name;
      notify();
    },
    startNew() {
      this.reset();
    },
    get id() { return state.id; },
    get name() { return state.name; },
  };
})();

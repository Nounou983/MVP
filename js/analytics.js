/* =========================================================
   La Cigogne D'Ailleurs — Mesure d'usage
   Un seul point d'entrée : Analytics.track(nom, propriétés).

   Ce qui n'est PAS collecté : nom de fichier, contenu de photo,
   adresse e-mail, texte libre. Le backend refiltre de toute façon —
   ce filtre-ci évite simplement d'envoyer ce qui serait jeté.
   ========================================================= */

(() => {
  "use strict";

  const cfg = window.CIGOGNE_CONFIG || {};
  const API = window.CigogneAPI;

  const EVENTS = new Set([
    "room_upload", "room_sample", "analysis_start", "analysis_complete", "analysis_failed",
    "object_select", "object_remove", "object_remove_failed",
    "furniture_add", "furniture_replace", "furniture_remove", "furniture_color",
    "view_3d", "glb_import", "export", "project_save", "project_open", "share_create",
    "favorite_add", "collection_add", "signup", "login",
  ]);

  const PROPS = new Set([
    "product_id", "family", "duration_ms", "item_count", "surface", "mode", "ok",
    "confidence", "source", "count", "plan", "width", "height", "reason",
  ]);

  const queue = [];
  let timer = 0;
  let sessionId = "";

  try {
    sessionId = sessionStorage.getItem("cigogne.session_id") || "";
    if (!sessionId) {
      sessionId = (crypto.randomUUID?.() || String(Math.random()).slice(2)).replace(/-/g, "").slice(0, 32);
      sessionStorage.setItem("cigogne.session_id", sessionId);
    }
  } catch {
    sessionId = String(Date.now()).slice(-12);
  }

  const timers = new Map();

  function clean(props) {
    const out = {};
    Object.entries(props || {}).forEach(([key, value]) => {
      if (!PROPS.has(key)) return;
      if (typeof value === "string") out[key] = value.slice(0, 64);
      else if (typeof value === "number" && Number.isFinite(value)) out[key] = Math.round(value * 1000) / 1000;
      else if (typeof value === "boolean") out[key] = value;
    });
    return out;
  }

  async function flush() {
    timer = 0;
    if (!queue.length || cfg.analytics === false) { queue.length = 0; return; }
    const batch = queue.splice(0, 50);
    try {
      await API?.sendEvents?.(batch);
    } catch {
      // La mesure d'usage ne doit jamais gêner la personne : on abandonne
      // ce lot plutôt que de le réessayer indéfiniment.
    }
  }

  function track(name, props) {
    if (cfg.analytics === false) return;
    if (!EVENTS.has(name)) {
      console.debug(`[analytics] événement ignoré : ${name}`);
      return;
    }
    queue.push({ name, props: clean(props), session_id: sessionId });
    if (!timer) timer = setTimeout(flush, 2500);
  }

  function startTimer(key) { timers.set(key, performance.now()); }
  function endTimer(key) {
    const started = timers.get(key);
    if (started == null) return null;
    timers.delete(key);
    return Math.round(performance.now() - started);
  }

  window.addEventListener("pagehide", () => { if (queue.length) flush(); });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden" && queue.length) flush();
  });

  window.Analytics = { track, startTimer, endTimer, flush, sessionId, EVENTS };
})();

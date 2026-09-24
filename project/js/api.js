/* =========================================================
   La Cigogne D'Ailleurs — Client API
   Une seule porte vers le backend. Règles :
   • toute erreur remonte avec un message lisible (jamais d'échec muet) ;
   • le jeton d'accès est rafraîchi automatiquement une fois ;
   • hors ligne, les appels échouent proprement et l'appelant décide.
   ========================================================= */

(() => {
  "use strict";

  const cfg = window.CIGOGNE_CONFIG || {};
  const BASE = (cfg.apiBase || "").replace(/\/$/, "");
  const STORE_KEY = "cigogne.session.v1";

  class ApiError extends Error {
    constructor(message, status, payload) {
      super(message);
      this.name = "ApiError";
      this.status = status;
      this.payload = payload || null;
      this.offline = status === 0;
      this.quota = status === 402;
      this.auth = status === 401;
    }
  }

  const listeners = new Set();
  const emit = () => listeners.forEach(fn => { try { fn(session.user); } catch (e) { console.error(e); } });

  let session = { user: null, access: null, refresh: null };
  let available = null;          // null = inconnu, true/false après un appel

  function load() {
    try {
      const raw = localStorage.getItem(STORE_KEY);
      if (raw) session = Object.assign({ user: null, access: null, refresh: null }, JSON.parse(raw));
    } catch { /* stockage indisponible : on reste en mémoire */ }
  }
  function persist() {
    try {
      if (session.access) localStorage.setItem(STORE_KEY, JSON.stringify(session));
      else localStorage.removeItem(STORE_KEY);
    } catch { /* ignoré */ }
  }
  load();

  function setSession(payload) {
    if (payload?.access_token) {
      session = {
        user: payload.user || session.user,
        access: payload.access_token,
        refresh: payload.refresh_token || session.refresh,
      };
    } else if (payload?.user) {
      session.user = payload.user;
    }
    persist();
    emit();
    return session.user;
  }

  function clearSession() {
    session = { user: null, access: null, refresh: null };
    persist();
    emit();
  }

  async function parse(response) {
    const type = response.headers.get("content-type") || "";
    if (response.status === 204 || response.status === 304) return null;
    if (type.includes("application/json")) {
      try { return await response.json(); } catch { return null; }
    }
    return await response.text();
  }

  async function raw(path, options = {}, retry = true) {
    const url = path.startsWith("http") ? path : `${BASE}${path}`;
    const headers = Object.assign({}, options.headers || {});
    if (session.access && !headers.Authorization) headers.Authorization = `Bearer ${session.access}`;
    if (options.json !== undefined) {
      headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(options.json);
    }

    let response;
    try {
      response = await fetch(url, { ...options, headers });
    } catch (err) {
      available = false;
      throw new ApiError(
        "Service indisponible — vos modifications restent sur cet appareil.", 0, null
      );
    }
    available = true;

    if (response.status === 401 && retry && session.refresh) {
      const renewed = await refreshAccess();
      if (renewed) return raw(path, options, false);
      clearSession();
    }

    const payload = await parse(response);
    if (!response.ok) {
      const detail = (payload && payload.detail) || response.statusText || "Erreur inattendue";
      throw new ApiError(typeof detail === "string" ? detail : "Requête refusée", response.status, payload);
    }
    return payload;
  }

  async function refreshAccess() {
    try {
      const resp = await fetch(`${BASE}/api/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: session.refresh }),
      });
      if (!resp.ok) return false;
      const data = await resp.json();
      session.access = data.access_token;
      persist();
      return true;
    } catch {
      return false;
    }
  }

  const get = (path) => raw(path, { method: "GET" });
  const post = (path, json) => raw(path, { method: "POST", json: json ?? {} });
  const patch = (path, json) => raw(path, { method: "PATCH", json: json ?? {} });
  const del = (path) => raw(path, { method: "DELETE" });

  async function upload(path, file, fields = {}) {
    const form = new FormData();
    form.append("file", file, file.name || "upload");
    Object.entries(fields).forEach(([k, v]) => v != null && form.append(k, String(v)));
    return raw(path, { method: "POST", body: form });
  }

  /** Vérifie une fois si l'API répond ; mémorise le résultat. */
  async function probe() {
    if (available !== null) return available;
    try {
      const resp = await fetch(`${BASE}/api/health`, { cache: "no-store" });
      available = resp.ok;
    } catch {
      available = false;
    }
    return available;
  }

  window.CigogneAPI = {
    ApiError,
    base: BASE,
    get, post, patch, del, upload, raw, probe,
    isAvailable: () => available,
    onAuthChange(fn) { listeners.add(fn); fn(session.user); return () => listeners.delete(fn); },
    get user() { return session.user; },
    get token() { return session.access; },
    isSignedIn() { return Boolean(session.access); },

    async register(email, password, displayName) {
      return setSession(await post("/api/auth/register",
        { email, password, display_name: displayName || "" }));
    },
    async login(email, password) {
      return setSession(await post("/api/auth/login", { email, password }));
    },
    async me() {
      const data = await get("/api/auth/me");
      setSession({ user: data.user });
      return data;
    },
    logout() { clearSession(); },

    // --- projets ---
    listProjects: () => get("/api/projects"),
    createProject: (body) => post("/api/projects", body),
    readProject: (id) => get(`/api/projects/${id}`),
    saveProject: (id, body) => patch(`/api/projects/${id}`, body),
    deleteProject: (id) => del(`/api/projects/${id}`),
    duplicateProject: (id) => post(`/api/projects/${id}/duplicate`),

    // --- fichiers ---
    uploadImage: (file, kind, projectId) =>
      upload("/api/files/images", file, { kind: kind || "image", project_id: projectId }),
    uploadModel: (file, projectId) => upload("/api/files/models", file, { project_id: projectId }),

    // --- catalogue ---
    products: () => get("/api/products"),
    favorites: () => get("/api/favorites"),
    addFavorite: (productId) => post("/api/favorites", { product_id: productId }),
    removeFavorite: (productId) => del(`/api/favorites/${productId}`),
    collections: () => get("/api/collections"),
    createCollection: (name) => post("/api/collections", { name, note: "" }),
    addToCollection: (id, productId) => post(`/api/collections/${id}/items`, { product_id: productId }),
    removeFromCollection: (id, productId) => del(`/api/collections/${id}/items/${productId}`),
    deleteCollection: (id) => del(`/api/collections/${id}`),

    // --- partage ---
    createShare: (projectId, permission, days) =>
      post(`/api/projects/${projectId}/shares`, { permission, expires_in_days: days || null }),
    listShares: (projectId) => get(`/api/projects/${projectId}/shares`),
    revokeShare: (shareId) => del(`/api/shares/${shareId}`),
    readShared: (token) => get(`/api/shared/${token}`),
    sharedComments: (token) => get(`/api/shared/${token}/comments`),
    addSharedComment: (token, body) => post(`/api/shared/${token}/comments`, body),

    // --- tâches IA ---
    createJob: (body) => post("/api/jobs", body),
    readJob: (id) => get(`/api/jobs/${id}`),
    cancelJob: (id) => post(`/api/jobs/${id}/cancel`),

    entitlements: () => get("/api/account/entitlements"),
    sendEvents: (events) => post("/api/analytics/events", { events }),
  };
})();

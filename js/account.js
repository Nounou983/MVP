/* =========================================================
   La Cigogne D'Ailleurs — Compte, projets, favoris, partage
   L'application reste entièrement utilisable sans compte : les
   favoris et le projet en cours vivent alors dans le navigateur.
   Se connecter ne débloque pas l'outil, ça le rend portable.
   ========================================================= */

(() => {
  "use strict";

  const $ = id => document.getElementById(id);
  const API = window.CigogneAPI;
  const App = window.App;
  if (!App) return;

  const accountView = document.querySelector('.panel__view[data-view="account"] .panel__scroll');
  const projectsView = document.querySelector('.panel__view[data-view="projects"] .panel__scroll');

  const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, c => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
  const toast = (message, tone) => window.CigogneUI?.toast?.(message, tone) || App.setStatus(message);

  /* ========================== FAVORIS ============================= */

  const FAV_KEY = "cigogne.favorites.v1";
  const favListeners = new Set();
  let favorites = new Set();

  function readLocalFavorites() {
    try { return new Set(JSON.parse(localStorage.getItem(FAV_KEY) || "[]")); }
    catch { return new Set(); }
  }
  function writeLocalFavorites() {
    try { localStorage.setItem(FAV_KEY, JSON.stringify([...favorites])); } catch { /* ignoré */ }
  }
  const emitFavorites = () => favListeners.forEach(fn => { try { fn(favorites); } catch (e) { console.error(e); } });

  favorites = readLocalFavorites();

  async function syncFavorites() {
    if (!API?.isSignedIn()) { favorites = readLocalFavorites(); emitFavorites(); return; }
    try {
      const payload = await API.favorites();
      const remote = new Set((payload.favorites || []).map(f => f.product_id));
      // Fusion : ce qui a été aimé hors ligne remonte au compte.
      const pending = [...favorites].filter(id => !remote.has(id));
      for (const id of pending) {
        try { await API.addFavorite(id); remote.add(id); } catch { /* produit inconnu */ }
      }
      favorites = remote;
      writeLocalFavorites();
      emitFavorites();
    } catch (err) {
      console.warn("[favoris] synchronisation impossible", err.message);
    }
  }

  const Favorites = {
    has: id => favorites.has(id),
    list: () => [...favorites],
    count: () => favorites.size,
    onChange(fn) { favListeners.add(fn); fn(favorites); return () => favListeners.delete(fn); },
    async toggle(productId) {
      const adding = !favorites.has(productId);
      if (adding) favorites.add(productId); else favorites.delete(productId);
      writeLocalFavorites();
      emitFavorites();
      if (adding) window.Analytics?.track("favorite_add", { product_id: productId });
      if (API?.isSignedIn()) {
        try {
          if (adding) await API.addFavorite(productId);
          else await API.removeFavorite(productId);
        } catch (err) {
          toast(`Favori non synchronisé : ${err.message}`, "warn");
        }
      }
      return adding;
    },
  };

  /* ========================= COLLECTIONS ========================== */

  const COL_KEY = "cigogne.collections.v1";
  let collections = [];

  function readLocalCollections() {
    try { return JSON.parse(localStorage.getItem(COL_KEY) || "[]"); } catch { return []; }
  }
  function writeLocalCollections() {
    try { localStorage.setItem(COL_KEY, JSON.stringify(collections)); } catch { /* ignoré */ }
  }
  collections = readLocalCollections();

  async function syncCollections() {
    if (!API?.isSignedIn()) { collections = readLocalCollections(); return; }
    try {
      const payload = await API.collections();
      collections = payload.collections || [];
    } catch (err) {
      console.warn("[collections] indisponibles", err.message);
    }
  }

  const Collections = {
    list: () => collections,
    async create(name) {
      if (API?.isSignedIn()) {
        const created = await API.createCollection(name);
        collections.push(created);
        return created;
      }
      const local = { id: `local-${Date.now()}`, name, note: "", items: [] };
      collections.push(local);
      writeLocalCollections();
      return local;
    },
    async add(collectionId, productId) {
      const collection = collections.find(c => c.id === collectionId);
      if (!collection) return null;
      if (API?.isSignedIn() && !collectionId.startsWith("local-")) {
        const updated = await API.addToCollection(collectionId, productId);
        Object.assign(collection, updated);
      } else {
        if (!collection.items.some(i => i.product_id === productId)) {
          collection.items.push({ product_id: productId, position: collection.items.length });
        }
        writeLocalCollections();
      }
      window.Analytics?.track("collection_add", { product_id: productId });
      return collection;
    },
    async remove(collectionId, productId) {
      const collection = collections.find(c => c.id === collectionId);
      if (!collection) return;
      if (API?.isSignedIn() && !collectionId.startsWith("local-")) {
        const updated = await API.removeFromCollection(collectionId, productId);
        Object.assign(collection, updated);
      } else {
        collection.items = collection.items.filter(i => i.product_id !== productId);
        writeLocalCollections();
      }
    },
  };

  /* =========================== COMPTE ============================= */

  function signedOutMarkup() {
    return `
      <div class="card">
        <h3 class="card__title">Retrouvez vos projets partout</h3>
        <p class="card__hint">Sans compte, tout reste sur cet appareil. Avec un compte, vos pièces et vos favoris vous suivent.</p>
        <div class="segmented segmented--light" role="group" aria-label="Mode">
          <button class="segmented__btn is-active" type="button" data-auth-tab="login">Se connecter</button>
          <button class="segmented__btn" type="button" data-auth-tab="register">Créer un compte</button>
        </div>
        <form id="authForm" class="form" novalidate>
          <label class="field">
            <span>Adresse e-mail</span>
            <input id="authEmail" type="email" autocomplete="email" required placeholder="vous@exemple.dz">
          </label>
          <label class="field" id="authNameField" hidden>
            <span>Nom affiché</span>
            <input id="authName" type="text" autocomplete="name" maxlength="120" placeholder="Votre nom">
          </label>
          <label class="field">
            <span>Mot de passe</span>
            <input id="authPassword" type="password" autocomplete="current-password" required
                   minlength="10" placeholder="10 caractères minimum">
          </label>
          <p id="authError" class="form__error" role="alert" hidden></p>
          <button id="authSubmit" class="btn btn--primary btn--block" type="submit">Se connecter</button>
        </form>
      </div>
      <div class="card">
        <h3 class="card__title">Sur cet appareil</h3>
        <div class="card__row"><span class="card__label">Favoris</span><span class="card__value">${favorites.size}</span></div>
        <div class="card__row"><span class="card__label">Projet enregistré</span>
          <span class="card__value">${window.Projects?.hasLocal() ? "Oui" : "Aucun"}</span></div>
        <button id="restoreLocalBtn" class="btn btn--block" type="button"
          ${window.Projects?.hasLocal() ? "" : "disabled"}>Reprendre le projet local</button>
      </div>`;
  }

  function usageBar(label, used, limit) {
    const pct = limit ? Math.min(100, Math.round((used / limit) * 100)) : 0;
    return `
      <div class="usage">
        <div class="usage__head"><span>${escapeHtml(label)}</span><b>${used} / ${limit}</b></div>
        <div class="usage__track"><i style="width:${pct}%"></i></div>
      </div>`;
  }

  function signedInMarkup(user, ent) {
    const limits = ent?.limits || {};
    const usage = ent?.usage || {};
    return `
      <div class="card card--account">
        <div class="account-head">
          <span class="avatar" aria-hidden="true">${escapeHtml((user.display_name || user.email || "?").slice(0, 1).toUpperCase())}</span>
          <div>
            <strong>${escapeHtml(user.display_name || user.email)}</strong>
            <small>${escapeHtml(user.email)}</small>
          </div>
          <span class="plan-chip">${escapeHtml(ent?.label || user.plan)}</span>
        </div>
        ${usageBar("Projets", usage.projects ?? 0, limits.projects ?? 0)}
        ${usageBar("Traitements IA ce mois", Math.round(usage.ai_jobs ?? 0), limits.ai_jobs_per_month ?? 0)}
        <div class="panel__foot-actions">
          <button id="logoutBtn" class="btn btn--quiet" type="button">Se déconnecter</button>
          <button id="saveNowBtn" class="btn btn--primary" type="button">Enregistrer</button>
        </div>
      </div>
      <div class="card">
        <h3 class="card__title">Favoris</h3>
        <p class="card__hint" id="favSummary">${favorites.size} produit(s) enregistré(s).</p>
        <button id="showFavoritesBtn" class="btn btn--block" type="button">Voir mes favoris dans le catalogue</button>
      </div>
      <div class="card">
        <h3 class="card__title">Collections</h3>
        <div id="collectionList" class="collection-list"></div>
        <button id="newCollectionBtn" class="btn btn--block" type="button">Nouvelle collection</button>
      </div>`;
  }

  function renderCollections() {
    const host = $("collectionList");
    if (!host) return;
    if (!collections.length) {
      host.innerHTML = `<p class="card__hint">Aucune collection. Regroupez des produits par pièce ou par client.</p>`;
      return;
    }
    host.innerHTML = collections.map(c => `
      <div class="collection">
        <div><strong>${escapeHtml(c.name)}</strong><small>${(c.items || []).length} produit(s)</small></div>
        <button class="icon-btn" type="button" data-open-collection="${escapeHtml(c.id)}" aria-label="Ouvrir ${escapeHtml(c.name)}">
          <svg viewBox="0 0 24 24"><path d="m10 6 6 6-6 6"/></svg>
        </button>
      </div>`).join("");
    host.querySelectorAll("[data-open-collection]").forEach(btn => {
      btn.addEventListener("click", () => {
        const collection = collections.find(c => c.id === btn.dataset.openCollection);
        if (!collection) return;
        window.CigogneUI?.filterByProducts?.((collection.items || []).map(i => i.product_id), collection.name);
      });
    });
  }

  let authMode = "login";

  async function renderAccount() {
    if (!accountView) return;
    const user = API?.user;
    if (!user || !API.isSignedIn()) {
      accountView.innerHTML = signedOutMarkup();
      wireAuthForm();
      return;
    }
    accountView.innerHTML = signedInMarkup(user, null);
    try {
      const data = await API.me();
      accountView.innerHTML = signedInMarkup(data.user, data.entitlements);
    } catch (err) {
      if (err.auth) { API.logout(); return renderAccount(); }
    }
    renderCollections();
    wireAccountActions();
  }

  function wireAuthForm() {
    accountView.querySelectorAll("[data-auth-tab]").forEach(btn => {
      btn.addEventListener("click", () => {
        authMode = btn.dataset.authTab;
        accountView.querySelectorAll("[data-auth-tab]").forEach(b =>
          b.classList.toggle("is-active", b === btn));
        $("authNameField").hidden = authMode !== "register";
        $("authSubmit").textContent = authMode === "register" ? "Créer mon compte" : "Se connecter";
        $("authPassword").setAttribute("autocomplete", authMode === "register" ? "new-password" : "current-password");
      });
    });

    $("restoreLocalBtn")?.addEventListener("click", async () => {
      const ok = await window.Projects?.restoreLocal();
      toast(ok ? "Projet local restauré" : "Aucun projet local à reprendre");
    });

    $("authForm")?.addEventListener("submit", async event => {
      event.preventDefault();
      const email = $("authEmail").value.trim();
      const password = $("authPassword").value;
      const name = $("authName")?.value.trim();
      const error = $("authError");
      const submit = $("authSubmit");
      error.hidden = true;
      submit.disabled = true;
      submit.textContent = "Un instant…";
      try {
        if (authMode === "register") {
          await API.register(email, password, name);
          window.Analytics?.track("signup", {});
        } else {
          await API.login(email, password);
          window.Analytics?.track("login", {});
        }
        await Promise.all([syncFavorites(), syncCollections()]);
        await renderAccount();
        await refreshProjects();
        toast("Vous êtes connecté");
      } catch (err) {
        error.textContent = err.offline
          ? "Service indisponible. Vous pouvez continuer sans compte."
          : err.message;
        error.hidden = false;
        submit.disabled = false;
        submit.textContent = authMode === "register" ? "Créer mon compte" : "Se connecter";
      }
    });
  }

  function wireAccountActions() {
    $("logoutBtn")?.addEventListener("click", () => {
      API.logout();
      favorites = readLocalFavorites();
      emitFavorites();
      renderAccount();
      refreshProjects();
      toast("Déconnecté — vos projets restent sur cet appareil");
    });
    $("saveNowBtn")?.addEventListener("click", async () => {
      await window.Projects?.save();
      refreshProjects();
    });
    $("showFavoritesBtn")?.addEventListener("click", () => {
      window.CigogneUI?.filterByProducts?.(Favorites.list(), "Favoris");
    });
    $("newCollectionBtn")?.addEventListener("click", async () => {
      const name = prompt("Nom de la collection");
      if (!name) return;
      try {
        await Collections.create(name.trim().slice(0, 120));
        renderCollections();
      } catch (err) {
        toast(err.message, "warn");
      }
    });
  }

  /* ========================== PROJETS ============================= */

  function projectCard(project) {
    const thumb = project.thumbnail_url
      ? `<img src="${escapeHtml(project.thumbnail_url)}" alt="" loading="lazy">`
      : `<span class="project-card__blank" aria-hidden="true"></span>`;
    const price = window.formatPrice ? window.formatPrice(project.total_price) : `${project.total_price} DA`;
    return `
      <article class="project-card">
        <div class="project-card__media">${thumb}</div>
        <div class="project-card__body">
          <strong>${escapeHtml(project.name)}</strong>
          <small>${project.item_count} meuble(s) · ${price}</small>
        </div>
        <div class="project-card__actions">
          <button class="btn btn--quiet btn--sm" type="button" data-open="${escapeHtml(project.id)}">Ouvrir</button>
          <button class="icon-btn" type="button" data-duplicate="${escapeHtml(project.id)}" aria-label="Dupliquer">
            <svg viewBox="0 0 24 24"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h8"/></svg>
          </button>
          <button class="icon-btn icon-btn--danger" type="button" data-delete="${escapeHtml(project.id)}" aria-label="Supprimer">
            <svg viewBox="0 0 24 24"><path d="M5 7h14M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12"/></svg>
          </button>
        </div>
      </article>`;
  }

  async function refreshProjects() {
    if (!projectsView) return;
    const signedIn = API?.isSignedIn();
    const header = `
      <div class="card">
        <div class="card__row">
          <span class="card__label">Projet en cours</span>
          <span class="card__value" id="projectStatus">—</span>
        </div>
        <div class="panel__foot-actions">
          <button id="projectSave" class="btn btn--primary" type="button">Enregistrer</button>
          <button id="projectNew" class="btn btn--quiet" type="button">Nouveau</button>
        </div>
        <button id="projectShare" class="btn btn--block" type="button" ${signedIn ? "" : "disabled"}>
          Partager un lien client
        </button>
        <div id="shareResult" class="share-result" hidden></div>
      </div>`;

    if (!signedIn) {
      projectsView.innerHTML = `${header}
        <div class="card">
          <h3 class="card__title">Vos projets en ligne</h3>
          <p class="card__hint">Connectez-vous pour conserver plusieurs pièces et les rouvrir depuis un autre appareil.</p>
          <button class="btn btn--block" type="button" onclick="window.CigogneUI.showView('account')">Créer un compte</button>
        </div>`;
      wireProjectActions();
      return;
    }

    projectsView.innerHTML = `${header}<div class="card"><h3 class="card__title">Mes pièces</h3>
      <div id="projectList" class="project-list"><p class="card__hint">Chargement…</p></div></div>`;
    wireProjectActions();

    try {
      const projects = await window.Projects.listCloud();
      const list = $("projectList");
      list.innerHTML = projects.length
        ? projects.map(projectCard).join("")
        : `<p class="card__hint">Aucun projet enregistré pour l'instant.</p>`;
      list.querySelectorAll("[data-open]").forEach(btn => btn.addEventListener("click", async () => {
        try {
          await window.Projects.openCloud(btn.dataset.open);
          toast("Projet ouvert");
        } catch (err) { toast(err.message, "warn"); }
      }));
      list.querySelectorAll("[data-duplicate]").forEach(btn => btn.addEventListener("click", async () => {
        try { await API.duplicateProject(btn.dataset.duplicate); refreshProjects(); }
        catch (err) { toast(err.message, "warn"); }
      }));
      list.querySelectorAll("[data-delete]").forEach(btn => btn.addEventListener("click", async () => {
        if (!confirm("Supprimer ce projet ?")) return;
        try { await API.deleteProject(btn.dataset.delete); refreshProjects(); }
        catch (err) { toast(err.message, "warn"); }
      }));
    } catch (err) {
      $("projectList").innerHTML = `<p class="form__error">${escapeHtml(err.message)}</p>`;
    }
  }

  function wireProjectActions() {
    $("projectSave")?.addEventListener("click", async () => {
      await window.Projects.save();
      refreshProjects();
    });
    $("projectNew")?.addEventListener("click", () => {
      if (App.state.items.length && !confirm("Vider la pièce et démarrer un nouveau projet ?")) return;
      App.clearScene();
      window.Projects.reset();
      toast("Nouveau projet");
    });
    $("projectShare")?.addEventListener("click", async () => {
      const button = $("projectShare");
      button.disabled = true;
      try {
        if (!window.Projects.id) await window.Projects.saveCloud();
        const share = await API.createShare(window.Projects.id, "view", 30);
        const box = $("shareResult");
        box.hidden = false;
        box.innerHTML = `
          <label class="field">
            <span>Lien client (lecture seule, 30 jours)</span>
            <input type="text" readonly value="${escapeHtml(share.url)}" id="shareUrl">
          </label>
          <div class="panel__foot-actions">
            <button class="btn btn--quiet btn--sm" type="button" id="copyShare">Copier</button>
            <button class="btn btn--quiet btn--sm" type="button" id="revokeShare">Révoquer</button>
          </div>`;
        $("copyShare").addEventListener("click", async () => {
          try {
            await navigator.clipboard.writeText(share.url);
            toast("Lien copié");
          } catch {
            $("shareUrl").select();
            toast("Sélectionnez puis copiez le lien");
          }
        });
        $("revokeShare").addEventListener("click", async () => {
          try { await API.revokeShare(share.id); box.hidden = true; toast("Lien révoqué"); }
          catch (err) { toast(err.message, "warn"); }
        });
        window.Analytics?.track("share_create", {});
      } catch (err) {
        toast(err.message, "warn");
      } finally {
        button.disabled = false;
      }
    });

    window.Projects?.onChange(status => {
      const el = $("projectStatus");
      if (!el) return;
      if (status.saving) el.textContent = "Enregistrement…";
      else if (status.dirty) el.textContent = "Modifications non enregistrées";
      else if (status.lastSavedAt) {
        const where = status.target === "cloud" ? "en ligne" : "sur cet appareil";
        el.textContent = `Enregistré ${where}`;
      } else el.textContent = "Jamais enregistré";
    });
  }

  /* =========================== DÉMARRAGE ========================== */

  async function boot() {
    await renderAccount();
    await refreshProjects();
    if (API?.isSignedIn()) {
      await Promise.all([syncFavorites(), syncCollections()]);
      renderCollections();
    }
  }

  window.CigogneAccount = { Favorites, Collections, refreshProjects, renderAccount, boot };
  window.Favorites = Favorites;

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();

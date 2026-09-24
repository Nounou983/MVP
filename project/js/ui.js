/* =========================================================
   La Cigogne D'Ailleurs — Couche interface
   Panneaux, catalogue, options produit, parcours IA, récapitulatif.
   Cette couche ne modifie ni le moteur IA ni le backend : elle
   pilote les modules existants via leurs boutons et leur état.
   ========================================================= */

(() => {
  "use strict";

  const $ = id => document.getElementById(id);
  const App = window.App;
  const Actions = window.AppActions;
  if (!App) return;

  const panel = $("panel");
  const rail = $("rail");
  const stage = $("stage-area");
  const viewport = $("viewport");
  const objectBar = $("objectBar");
  const toasts = $("toasts");

  /* ---------------------------------------------------------------
     Panneaux
     --------------------------------------------------------------- */
  let activeView = "catalog";
  let lastLibraryView = "catalog";

  function showView(view, { collapse = false } = {}) {
    if (collapse && activeView === view && !panel.classList.contains("is-collapsed")) {
      panel.classList.add("is-collapsed");
      App.resize();
      return;
    }
    panel.classList.remove("is-collapsed");
    activeView = view;
    if (view !== "product") lastLibraryView = view;
    panel.querySelectorAll(".panel__view").forEach(section => {
      section.classList.toggle("is-active", section.dataset.view === view);
    });
    rail.querySelectorAll(".rail__btn").forEach(btn => {
      const target = btn.dataset.panel;
      const on = target === view || (view === "product" && target === "catalog");
      btn.classList.toggle("is-active", on);
      btn.setAttribute("aria-pressed", String(on));
    });
    App.resize();
  }

  rail.addEventListener("click", e => {
    const btn = e.target.closest(".rail__btn");
    if (!btn) return;
    showView(btn.dataset.panel, { collapse: true });
  });
  $("panelCollapse").onclick = () => { panel.classList.add("is-collapsed"); App.resize(); };
  $("panelExpand").onclick = () => { panel.classList.remove("is-collapsed"); App.resize(); };

  /* ---------------------------------------------------------------
     Catalogue
     --------------------------------------------------------------- */
  const catalogEl = $("catalog");
  const filtersEl = $("catalogFilters");
  const searchEl = $("catalogSearch");
  const styleFilterEl = $("catalogStyleFilter");
  const materialFilterEl = $("catalogMaterialFilter");
  const sortEl = $("catalogSort");
  let activeFamily = "all";

  (window.FAMILIES || []).forEach(f => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "chip" + (f.id === "all" ? " is-active" : "");
    b.dataset.family = f.id;
    b.textContent = f.label;
    filtersEl.appendChild(b);
  });

  function mediaMarkup(entry) {
    // Photo produit si le catalogue en fournit une, sinon sprite vectoriel.
    const markup = window.ProductCatalog?.thumbnailMarkup?.(entry, entry.color);
    if (markup) return markup;
    const sprite = window.spriteFor(entry.base, entry.color);
    return `<img src="${sprite.src}" alt="" draggable="false">`;
  }

  function cardMarkup(entry) {
    const availability = window.ProductCatalog?.availabilityLabel?.(entry) || "";
    const isFavorite = window.Favorites?.has?.(entry.id);
    return `
      <span class="product-card__media">${mediaMarkup(entry)}</span>
      <span class="product-card__body">
        <span class="product-card__name">${entry.name}</span>
        <span class="product-card__dims">${entry.w.toFixed(2)} × ${entry.d.toFixed(2)} m</span>
        <span class="product-card__price">${window.formatPrice(entry.price)}</span>
        ${availability ? `<span class="product-card__stock">${availability}</span>` : ""}
        <span class="product-card__meta-row">
          ${(entry.styles || []).slice(0,2).map(style => `<span class="product-card__tag">${style}</span>`).join("")}
          ${entry.assets?.model?.url ? `<span class="product-card__model">3D</span>` : ""}
        </span>
        ${entry.assets?.source?.provider ? `<span class="product-card__source">Visuel · ${entry.assets.source.provider}</span>` : ""}
      </span>
      <span class="product-card__fav ${isFavorite ? "is-on" : ""}" role="button" tabindex="0"
            data-fav="${entry.id}" aria-label="${isFavorite ? "Retirer des favoris" : "Ajouter aux favoris"}"
            aria-pressed="${isFavorite ? "true" : "false"}">
        <svg viewBox="0 0 24 24"><path d="M12 20s-7-4.4-7-9.2A4 4 0 0 1 12 8a4 4 0 0 1 7 2.8C19 15.6 12 20 12 20z"/></svg>
      </span>
      <span class="product-card__add" aria-hidden="true">Ajouter</span>`;
  }

  function buildCatalog() {
    catalogEl.innerHTML = "";
    (window.CATALOG || []).forEach(entry => {
      const card = document.createElement("button");
      card.type = "button";
      card.className = "product-card";
      card.dataset.entry = entry.id;
      card.dataset.family = entry.family;
      card.innerHTML = cardMarkup(entry);
      catalogEl.appendChild(card);
    });
  }

  let productSubset = null;      // liste d'ids imposée (favoris, collection)

  function filterCatalog() {
    const q = (searchEl.value || "").trim().toLowerCase();
    const style = styleFilterEl?.value || "all";
    const material = materialFilterEl?.value || "all";
    const sort = sortEl?.value || "recommended";
    const cards = [...catalogEl.querySelectorAll(".product-card")];
    cards.sort((a, b) => {
      const ea = window.catalogEntry(a.dataset.entry), eb = window.catalogEntry(b.dataset.entry);
      if (!ea || !eb) return 0;
      if (sort === "price-asc") return ea.price - eb.price;
      if (sort === "price-desc") return eb.price - ea.price;
      if (sort === "name") return ea.name.localeCompare(eb.name, "fr");
      if (sort === "size") return (ea.w * ea.d) - (eb.w * eb.d);
      return 0;
    });
    cards.forEach(c => catalogEl.appendChild(c));
    let visible = 0;
    cards.forEach(card => {
      const entry = window.catalogEntry(card.dataset.entry);
      if (!entry) { card.hidden = true; return; }
      const text = entry.searchText || `${entry.name} ${entry.blurb || ""} ${entry.sku || ""} ${(entry.materials || []).join(" ")}`.toLowerCase();
      const styleOk = style === "all" || (entry.styles || []).includes(style);
      const materialOk = material === "all" || (entry.materialTokens || []).some(m => m.includes(material));
      const hide = (q && !text.includes(q)) || !styleOk || !materialOk
        || (activeFamily !== "all" && entry.family !== activeFamily)
        || (productSubset && !productSubset.includes(entry.id));
      card.hidden = hide;
      if (!hide) visible++;
    });
    $("catalogEmpty").hidden = visible > 0;
  }

  /** Restreint le catalogue à une liste de produits (favoris, collection). */
  function filterByProducts(ids, label) {
    productSubset = Array.isArray(ids) && ids.length ? ids.slice() : null;
    activeFamily = "all";
    if (styleFilterEl) styleFilterEl.value = "all";
    if (materialFilterEl) materialFilterEl.value = "all";
    if (sortEl) sortEl.value = "recommended";
    filtersEl.querySelectorAll(".chip").forEach(c => c.classList.toggle("is-active", c.dataset.family === "all"));
    const notice = $("catalogNotice");
    if (notice) {
      if (productSubset) {
        notice.hidden = false;
        notice.innerHTML = `<span>${label || "Sélection"} · ${productSubset.length} produit(s)</span>
                            <button type="button" id="clearSubset">Tout afficher</button>`;
        notice.querySelector("#clearSubset").onclick = () => filterByProducts(null);
      } else {
        notice.hidden = true;
        notice.innerHTML = "";
      }
    }
    if (productSubset && !productSubset.length) {
      $("catalogEmpty").hidden = false;
    }
    filterCatalog();
    showView("catalog");
  }

  function rebuildCatalog() {
    buildCatalog();
    filterCatalog();
  }

  // Les familles et les produits arrivent du serveur : on reconstruit.
  window.ProductCatalog?.onChange?.(() => {
    const families = window.ProductCatalog.families || window.FAMILIES || [];
    if (families.length && filtersEl.children.length !== families.length) {
      filtersEl.innerHTML = "";
      families.forEach(f => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "chip" + (f.id === activeFamily ? " is-active" : "");
        b.dataset.family = f.id;
        b.textContent = f.label;
        filtersEl.appendChild(b);
      });
    }
    rebuildCatalog();
  });
  window.Favorites?.onChange?.(() => {
    catalogEl.querySelectorAll("[data-fav]").forEach(el => {
      const on = window.Favorites.has(el.dataset.fav);
      el.classList.toggle("is-on", on);
      el.setAttribute("aria-pressed", on ? "true" : "false");
    });
  });

  searchEl.addEventListener("input", filterCatalog);
  styleFilterEl?.addEventListener("change", filterCatalog);
  materialFilterEl?.addEventListener("change", filterCatalog);
  sortEl?.addEventListener("change", filterCatalog);
  filtersEl.addEventListener("click", e => {
    const chip = e.target.closest(".chip");
    if (!chip) return;
    activeFamily = chip.dataset.family;
    filtersEl.querySelectorAll(".chip").forEach(c => c.classList.toggle("is-active", c === chip));
    filterCatalog();
  });

  /* ------- Glisser-déposer du catalogue vers la pièce ------------- */
  let ghost = null;
  function startGhost(entry, x, y) {
    ghost = document.createElement("div");
    ghost.className = "drag-ghost";
    ghost.innerHTML = `<img src="${window.spriteFor(entry.base, entry.color).src}" alt="">`;
    document.body.appendChild(ghost);
    moveGhost(x, y);
  }
  function moveGhost(x, y) {
    if (ghost) { ghost.style.left = `${x}px`; ghost.style.top = `${y}px`; }
  }
  function endGhost() { ghost?.remove(); ghost = null; }

  /* Le cœur ne doit pas déclencher l'ajout du meuble. */
  catalogEl.addEventListener("click", e => {
    const fav = e.target.closest("[data-fav]");
    if (!fav) return;
    e.preventDefault();
    e.stopPropagation();
    window.Favorites?.toggle(fav.dataset.fav);
  });
  catalogEl.addEventListener("keydown", e => {
    const fav = e.target.closest("[data-fav]");
    if (!fav || (e.key !== "Enter" && e.key !== " ")) return;
    e.preventDefault();
    window.Favorites?.toggle(fav.dataset.fav);
  });

  catalogEl.addEventListener("pointerdown", e => {
    if (e.target.closest("[data-fav]")) return;
    const card = e.target.closest(".product-card");
    if (!card || e.button !== 0) return;
    const entry = window.catalogEntry(card.dataset.entry);
    if (!entry) return;
    const start = { x: e.clientX, y: e.clientY };
    let dragging = false;
    card.setPointerCapture(e.pointerId);

    const move = ev => {
      if (!dragging && Math.hypot(ev.clientX - start.x, ev.clientY - start.y) > 8) {
        if (!App.state.roomImage) return;
        dragging = true;
        viewport.classList.add("is-drop-target");
        startGhost(entry, ev.clientX, ev.clientY);
      }
      if (dragging) moveGhost(ev.clientX, ev.clientY);
    };
    const up = ev => {
      card.releasePointerCapture?.(e.pointerId);
      card.removeEventListener("pointermove", move);
      card.removeEventListener("pointerup", up);
      card.removeEventListener("pointercancel", up);
      viewport.classList.remove("is-drop-target");
      endGhost();
      const rect = viewport.getBoundingClientRect();
      const inside = ev.clientX >= rect.left && ev.clientX <= rect.right && ev.clientY >= rect.top && ev.clientY <= rect.bottom;
      if (dragging && inside) {
        Actions.addItemAt(entry.id, ev.clientX - rect.left, ev.clientY - rect.top);
      } else if (!dragging) {
        if (!App.state.roomImage) { toast("Importez d'abord une photo de votre pièce.", "warn"); showView("room"); return; }
        Actions.addItem(entry.id);
      }
    };
    card.addEventListener("pointermove", move);
    card.addEventListener("pointerup", up);
    card.addEventListener("pointercancel", up);
  });

  /* ---------------------------------------------------------------
     Panneau produit
     --------------------------------------------------------------- */
  function dropThreeGroup(uid) {
    const groups = window.Phase3?.state?.groups;
    const group = groups?.get(uid);
    if (group) { group.parent?.remove(group); groups.delete(uid); }
  }

  function swapProduct(uid, entryId) {
    const item = App.state.items.find(i => i.uid === uid);
    const entry = window.catalogEntry(entryId);
    if (!item || !entry) return;
    App.pushHistory();
    Object.assign(item, {
      entryId: entry.id, catId: entry.base, name: entry.name,
      price: entry.price, color: entry.color, w: entry.w, d: entry.d,
    });
    dropThreeGroup(uid);
    App.constrainToFloor(item, true);
    Actions.renderInspector();
    App.draw();
    window.Phase3?.sync?.();
    renderProduct();
    updateTotals();
    App.setStatus(`Remplacé par ${entry.name}`);
  }

  function renderProduct() {
    const item = App.getSelected();
    if (!item) return;
    const entry = window.catalogEntry(item.entryId);
    $("productThumb").innerHTML = entry
      ? mediaMarkup(Object.assign({}, entry, { color: item.color }))
      : `<img src="${window.spriteFor(item.catId, item.color).src}" alt="">`;
    $("productName").textContent = item.name;
    $("productBlurb").textContent = entry?.blurb || "";
    $("productPrice").textContent = window.formatPrice(item.price);
    renderCommerce(entry, item);

    const similar = (window.CATALOG || [])
      .filter(c => c.family === entry?.family && c.id !== item.entryId)
      .slice(0, 4);
    const row = $("similarRow");
    row.innerHTML = similar.map(c => `
      <button type="button" class="similar-card" data-entry="${c.id}">
        <img src="${window.spriteFor(c.base, c.color).src}" alt="">
        <span class="similar-card__name">${c.name}</span>
        <span class="similar-card__price">${window.formatPrice(c.price)}</span>
      </button>`).join("");
    $("similarBlock").hidden = similar.length === 0;
    row.querySelectorAll(".similar-card").forEach(btn => {
      btn.onclick = () => swapProduct(item.uid, btn.dataset.entry);
    });
  }

  /* Données commerciales : séparées du rendu, elles viennent telles
     quelles du catalogue et n'influencent rien d'autre que l'affichage. */
  /* Le catalogue peut arriver enrichi (API) ou brut (repli embarqué) :
     la fiche doit s'afficher dans les deux cas. */
  function formatDims(entry) {
    const dims = entry.dimensions || {};
    const w = Number(dims.width ?? entry.w);
    const d = Number(dims.depth ?? entry.d);
    const h = Number(dims.height ?? entry.h);
    if (!Number.isFinite(w) || !Number.isFinite(d)) return "—";
    const base = `${w.toFixed(2)} × ${d.toFixed(2)}`;
    return Number.isFinite(h) ? `${base} × ${h.toFixed(2)} m` : `${base} m`;
  }

  function renderCommerce(entry, item) {
    const host = $("productCommerce");
    if (!host) return;
    if (!entry) { host.hidden = true; return; }
    host.hidden = false;
    const stock = window.ProductCatalog?.availabilityLabel?.(entry) || "—";
    const variant = window.ProductCatalog?.variantName?.(entry, item.color) || "";
    const isFavorite = window.Favorites?.has?.(entry.id);
    const placeholder = window.ProductCatalog?.isPlaceholder?.(entry);
    const rows = [
      ["Référence", entry.sku || "—"],
      ["Finition", variant],
      ["Disponibilité", stock],
      ["Dimensions", formatDims(entry)],
      ["Matériaux", (entry.materials || []).join(", ") || "—"],
      ["Vendeur", entry.seller?.name || entry.manufacturer || "—"],
    ];
    host.innerHTML = `
      <h3 class="card__title">Fiche produit</h3>
      ${placeholder ? `<p class="card__hint card__hint--demo">Visuel de démonstration — remplacez ce sprite par une vraie photo/GLB dans le catalogue produit.</p>` : ""}
      ${rows.map(([label, value]) => `
        <div class="card__row"><span class="card__label">${label}</span><b class="card__value">${value}</b></div>`).join("")}
      <div class="panel__foot-actions">
        <button class="btn btn--quiet" type="button" id="productFav" aria-pressed="${isFavorite}">
          ${isFavorite ? "Retirer des favoris" : "Ajouter aux favoris"}
        </button>
        ${entry.product_url
          ? `<a class="btn btn--primary" href="${entry.product_url}" target="_blank" rel="noopener noreferrer">
               ${entry.cta?.label || "Voir la fiche"}</a>`
          : ""}
      </div>`;
    $("productFav")?.addEventListener("click", async () => {
      await window.Favorites?.toggle(entry.id);
      renderCommerce(entry, item);
    });
  }

  $("productBack").onclick = () => { Actions.select(null); showView(lastLibraryView); };

  /* ---------------------------------------------------------------
     Barre contextuelle sur la photo
     --------------------------------------------------------------- */
  function placeObjectBar() {
    const item = App.getSelected();
    if (!item || App.state.eraserOn) {
      objectBar.classList.remove("is-visible");
      objectBar.setAttribute("aria-hidden", "true");
      return;
    }
    const size = App.getCanvasSize();
    const { h } = App.itemSize(item);
    const rect = objectBar.getBoundingClientRect();
    const barW = rect.width || 300;
    const barH = rect.height || 48;
    let top = item.y - h / 2 - barH - 26;
    if (top < 12) top = Math.min(size.height - barH - 16, item.y + h / 2 + 34);
    const left = Math.max(12, Math.min(size.width - barW - 12, item.x - barW / 2));
    objectBar.style.transform = `translate(${Math.round(left)}px, ${Math.round(top)}px)`;
    objectBar.classList.add("is-visible");
    objectBar.setAttribute("aria-hidden", "false");
    $("objectBarName").textContent = item.name;
  }

  objectBar.addEventListener("click", e => {
    const btn = e.target.closest("[data-act]");
    const item = App.getSelected();
    if (!btn || !item) return;
    switch (btn.dataset.act) {
      case "rotate":
        App.pushHistory();
        item.rot -= Math.PI / 12;
        Actions.renderInspector(); App.draw(); window.Phase3?.sync?.();
        break;
      case "duplicate": Actions.duplicateItem(item.uid); break;
      case "options": showView("product"); break;
      case "delete": Actions.deleteItem(item.uid); break;
    }
  });

  /* ---------------------------------------------------------------
     Récapitulatif
     --------------------------------------------------------------- */
  function updateTotals() {
    const items = App.state.items;
    $("totalPrice").textContent = window.formatPrice(App.totalPrice());
    $("totalCount").textContent = items.length ? `${items.length} meuble${items.length > 1 ? "s" : ""}` : "Pièce vide";
    const railBadge = $("railSummaryBadge");
    railBadge.textContent = items.length;
    railBadge.hidden = items.length === 0;

    const list = $("summaryList");
    if (!items.length) {
      list.innerHTML = `<p class="panel__empty">Aucun meuble pour l'instant. Ouvrez le catalogue et glissez un meuble dans la pièce.</p>`;
    } else {
      const grouped = new Map();
      items.forEach(i => {
        const key = `${i.entryId}|${i.color}`;
        const row = grouped.get(key) || { item: i, count: 0 };
        row.count++; grouped.set(key, row);
      });
      list.innerHTML = [...grouped.values()].map(({ item, count }) => `
        <div class="summary-row" data-uid="${item.uid}">
          <img src="${window.spriteFor(item.catId, item.color).src}" alt="">
          <div>
            <strong>${item.name}</strong>
            <span>${count} × ${window.formatPrice(item.price)}</span>
          </div>
          <b>${window.formatPrice(item.price * count)}</b>
        </div>`).join("");
      list.querySelectorAll(".summary-row").forEach(row => {
        row.onclick = () => { Actions.select(row.dataset.uid); showView("product"); };
      });
    }
    $("summaryTotal").textContent = window.formatPrice(App.totalPrice());
  }

  $("summaryBtn").onclick = () => showView("summary");

  $("summaryClear").onclick = () => {
    if (!App.state.items.length) return;
    App.clearScene();
    toast("Tous les meubles ont été retirés.");
  };

  /* ---------------------------------------------------------------
     Pièce : import, exemples, analyse
     --------------------------------------------------------------- */
  const roomInput = $("roomUpload");
  document.querySelectorAll("[data-import-room]").forEach(btn => btn.onclick = () => roomInput.click());

  const SAMPLES = [
    { id: "salon", label: "Salon lumineux", url: "https://images.unsplash.com/photo-1600210492486-724fe5c67fb0?auto=format&fit=crop&w=1600&q=80" },
    { id: "chambre", label: "Chambre", url: "https://images.unsplash.com/photo-1616594039964-ae9021a400a0?auto=format&fit=crop&w=1600&q=80" },
    { id: "vide", label: "Pièce vide", url: "https://images.unsplash.com/photo-1524758631624-e2822e304c36?auto=format&fit=crop&w=1600&q=80" },
  ];

  $("sampleRow").innerHTML = SAMPLES.map(s => `
    <button type="button" class="sample" data-sample="${s.id}">
      <span class="sample__img" style="background-image:url('${s.url}&w=320')"></span>
      <span>${s.label}</span>
    </button>`).join("");

  $("sampleRow").addEventListener("click", async e => {
    const btn = e.target.closest("[data-sample]");
    if (!btn) return;
    const sample = SAMPLES.find(s => s.id === btn.dataset.sample);
    btn.classList.add("is-loading");
    App.setStatus("Chargement de la pièce d'exemple…");
    try {
      // Passage par un blob : l'image reste utilisable par le canvas et l'IA.
      const res = await fetch(sample.url, { mode: "cors" });
      if (!res.ok) throw new Error(res.status);
      const blob = await res.blob();
      window.Projects?.startNew?.();
      App.loadRoomFile(new File([blob], `${sample.label}.jpg`, { type: blob.type || "image/jpeg" }));
    } catch (err) {
      console.error(err);
      toast("Exemple indisponible hors connexion. Importez une photo depuis votre appareil.", "warn");
    } finally {
      btn.classList.remove("is-loading");
    }
  });

  // Dépôt d'une photo directement sur la pièce.
  ["dragenter", "dragover"].forEach(type => stage.addEventListener(type, e => {
    if (![...(e.dataTransfer?.types || [])].includes("Files")) return;
    e.preventDefault();
    stage.classList.add("is-file-over");
  }));
  ["dragleave", "drop"].forEach(type => stage.addEventListener(type, e => {
    if (type === "drop") e.preventDefault();
    if (type === "dragleave" && e.relatedTarget && stage.contains(e.relatedTarget)) return;
    stage.classList.remove("is-file-over");
  }));
  stage.addEventListener("drop", e => {
    const file = e.dataTransfer?.files?.[0];
    if (file) {
      window.Projects?.startNew?.();
      App.loadRoomFile(file);
    }
  });

  const analyzeProxy = $("analyzeBtn");
  $("analyzeAction").onclick = () => {
    if (!App.state.roomImage) { toast("Importez d'abord une photo.", "warn"); return; }
    analyzeProxy.click();
  };

  function renderAnalysis(data) {
    const card = $("analysisResult");
    if (!data) { card.hidden = true; $("overlayGroup").hidden = true; return; }
    const count = data.scene?.furniture_count ?? 0;
    const floor = data.masks?.floor
      ? (data.floor_source === "segformer" ? "segmenté" : "estimé")
      : "non détecté";
    card.hidden = false;
    $("analysisFloor").textContent = floor;
    $("analysisObjects").textContent = `${count}`;
    $("overlayGroup").hidden = false;
  }

  /* ---------------------------------------------------------------
     Vue photo / 3D — on pilote les boutons du module 3D existant.
     --------------------------------------------------------------- */
  const photoProxy = $("photoModeBtn");
  const threeProxy = $("threeModeBtn");

  $("viewPhoto").onclick = () => { window.Phase3?.disable?.(); syncViewButtons(); };
  $("viewThree").onclick = async () => {
    if (!App.state.roomImage) { toast("Importez une photo avant de passer en 3D.", "warn"); return; }
    $("viewThree").classList.add("is-loading");
    const p = await window.Phase3Boot;
    $("viewThree").classList.remove("is-loading");
    if (p?.enable) {
      p.enable();
      // On démarre calé sur la photo : c'est le cadrage utile pour meubler.
      p.setMode?.("photo");
      setThreeMode("photo");
    } else {
      toast("La vue 3D n'a pas pu démarrer sur ce navigateur.", "error");
    }
    syncViewButtons();
  };

  const threeModeGroup = $("threeModeGroup");
  function setThreeMode(mode) {
    threeModeGroup.querySelectorAll("[data-3dmode]").forEach(b => {
      b.classList.toggle("is-active", b.dataset["3dmode"] === mode);
    });
  }
  threeModeGroup.addEventListener("click", e => {
    const btn = e.target.closest("[data-3dmode]");
    if (!btn || !window.Phase3?.setMode) return;
    window.Phase3.setMode(btn.dataset["3dmode"]);
    setThreeMode(btn.dataset["3dmode"]);
  });
  $("glbAction").onclick = async () => {
    const p = await window.Phase3Boot;
    if (p?.loadSelectedGLB) p.loadSelectedGLB(); else $("glbUpload").click();
  };

  function syncViewButtons() {
    const on = threeProxy.classList.contains("active");
    $("viewThree").classList.toggle("is-active", on);
    $("viewPhoto").classList.toggle("is-active", !on);
    document.body.classList.toggle("is-3d", on);
    $("threeModeGroup").hidden = !on;
    App.draw();
  }
  new MutationObserver(syncViewButtons).observe(threeProxy, { attributes: true, attributeFilter: ["class"] });
  new MutationObserver(syncViewButtons).observe(photoProxy, { attributes: true, attributeFilter: ["class"] });

  /* ---------------------------------------------------------------
     IA — gomme intelligente (module eraser.js piloté, jamais modifié)
     --------------------------------------------------------------- */
  const eraserProxy = $("eraserBtn");
  const aiBanner = $("aiBanner");
  let eraserPhase = "idle";

  function readEraserPhase() {
    const label = eraserProxy.textContent || "";
    if (label.includes("Confirmer")) return "ready";
    if (label.includes("Ciblez")) return "targeting";
    return "idle";
  }

  function renderEraserPhase() {
    const phase = readEraserPhase();
    eraserPhase = phase;
    aiBanner.dataset.phase = phase;
    aiBanner.classList.toggle("is-visible", phase !== "idle");
    const selectedCount = document.querySelectorAll(".ai-detected-item.is-selected").length;
    $("aiBannerTitle").textContent = phase === "ready"
      ? `${selectedCount || 1} objet${(selectedCount || 1) > 1 ? "s" : ""} sélectionné${(selectedCount || 1) > 1 ? "s" : ""}`
      : "Cliquez sur un ou plusieurs objets";
    $("aiBannerHint").textContent = phase === "ready"
      ? "Vous pouvez continuer à sélectionner avant de confirmer."
      : "La détection IA sépare les objets voisins et conserve vos choix.";
    $("aiConfirm").hidden = phase !== "ready";
    $("aiConfirm").textContent = phase === "ready" ? `Confirmer${selectedCount > 1 ? ` (${selectedCount})` : ""}` : "Confirmer";
    $("eraseAction").classList.toggle("is-active", phase !== "idle");
    $("eraseStep1").classList.toggle("is-done", phase !== "idle");
    $("eraseStep2").classList.toggle("is-done", phase === "ready");
    document.body.classList.toggle("is-erasing", phase !== "idle");
  }

  new MutationObserver(renderEraserPhase).observe(eraserProxy, { childList: true, characterData: true, subtree: true });

  $("eraseAction").onclick = () => {
    if (!App.state.roomImage) { toast("Importez d'abord une photo.", "warn"); return; }
    if (eraserPhase === "ready") return;   // la confirmation passe par le bandeau
    eraserProxy.click();
  };
  $("aiConfirm").onclick = () => eraserProxy.click();
  $("aiCancel").onclick = () => cancelErase();
  function cancelErase() {
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
  }

  /* Progression IA : ces deux fonctions sont appelées par eraser.js. */
  let roomBeforeAI = null;
  const aiOverlay = $("aiProcessing");

  function showAI(text) {
    roomBeforeAI = App.state.roomImage;
    $("aiProcessingText").textContent = text || "L'IA travaille sur votre pièce…";
    aiOverlay.classList.add("is-visible");
    aiOverlay.setAttribute("aria-hidden", "false");
  }
  function hideAI() {
    aiOverlay.classList.remove("is-visible");
    aiOverlay.setAttribute("aria-hidden", "true");
    // Si la photo a changé, l'IA a bien retouché la pièce : on garde
    // l'image précédente pour pouvoir revenir en arrière.
    if (roomBeforeAI && App.state.roomImage && App.state.roomImage !== roomBeforeAI) {
      pushTimeline({ type: "ai", before: roomBeforeAI, after: App.state.roomImage });
      renderAnalysis(null);
      // La retouche fait partie du projet : elle doit se retrouver
      // après réouverture, au même titre qu'un meuble.
      window.Projects?.recordAiEdit({ kind: "remove" });
      window.Analytics?.track("object_remove", { ok: true });
    }
    roomBeforeAI = null;
  }

  function restoreRoom(img) {
    if (!img) return;
    App.state.roomImage = img;
    App.resetAnalysis();
    App.draw();
    window.Phase3?.sync?.();
  }

  /* ---------------------------------------------------------------
     Historique unifié : meubles + retouches IA
     --------------------------------------------------------------- */
  const timeline = [];
  const redoLine = [];

  function pushTimeline(entry) {
    timeline.push(entry);
    redoLine.length = 0;
    refreshHistoryButtons();
  }
  App.on("push", () => pushTimeline({ type: "scene" }));

  function refreshHistoryButtons() {
    $("undoBtn").disabled = timeline.length === 0;
    $("redoBtn").disabled = redoLine.length === 0;
  }

  function undo() {
    const entry = timeline.pop();
    if (!entry) return;
    if (entry.type === "ai") { restoreRoom(entry.before); App.setStatus("Retouche IA annulée"); }
    else App.undo();
    redoLine.push(entry);
    refreshHistoryButtons();
  }
  function redo() {
    const entry = redoLine.pop();
    if (!entry) return;
    if (entry.type === "ai") { restoreRoom(entry.after); App.setStatus("Retouche IA rétablie"); }
    else App.redo();
    timeline.push(entry);
    refreshHistoryButtons();
  }
  $("undoBtn").onclick = undo;
  $("redoBtn").onclick = redo;

  // Capture avant les autres modules pour garder un seul historique.
  window.addEventListener("keydown", e => {
    if (!(e.ctrlKey || e.metaKey)) return;
    const key = e.key.toLowerCase();
    if (key !== "z" && key !== "y") return;
    e.preventDefault();
    e.stopPropagation();
    if (key === "y" || e.shiftKey) redo(); else undo();
  }, true);

  /* ---------------------------------------------------------------
     Export
     --------------------------------------------------------------- */
  $("exportBtn").onclick = () => {
    if (!App.state.roomImage) { toast("Importez une photo avant d'exporter.", "warn"); return; }
    // En vue 3D, le module de rendu compose la photo et les volumes 3D.
    if (window.Phase3?.state?.enabled && window.Phase3E?.export) window.Phase3E.export();
    else App.exportPng();
  };
  $("previewBtn").onclick = () => {
    if (!App.state.roomImage) { toast("Importez une photo avant l'aperçu.", "warn"); return; }
    if (window.Phase3?.state?.enabled && window.Phase3E?.preview) window.Phase3E.preview();
    else App.exportPng();
  };

  $("newDesignBtn").onclick = () => {
    if (!App.state.items.length || confirm("Retirer tous les meubles de cette composition ?")) {
      App.clearScene();
      window.Projects?.startNew?.();
      showView("catalog");
    }
  };

  /* ---------------------------------------------------------------
     Nom de la composition
     --------------------------------------------------------------- */
  const sceneName = $("sceneName");
  sceneName.addEventListener("keydown", e => {
    if (e.key === "Enter") { e.preventDefault(); sceneName.blur(); }
  });
  sceneName.addEventListener("blur", () => {
    const text = sceneName.textContent.trim();
    sceneName.textContent = text.slice(0, 48) || "Ma composition";
  });

  /* ---------------------------------------------------------------
     Notifications
     --------------------------------------------------------------- */
  let lastToast = "";
  let toastTimer = 0;
  function toast(message, tone = "info") {
    if (!message) return;
    const el = document.createElement("div");
    el.className = `toast toast--${tone}`;
    el.textContent = message;
    toasts.appendChild(el);
    requestAnimationFrame(() => el.classList.add("is-in"));
    setTimeout(() => {
      el.classList.remove("is-in");
      setTimeout(() => el.remove(), 260);
    }, tone === "error" ? 6000 : 3400);
    while (toasts.children.length > 3) toasts.firstChild.remove();
  }

  App.on("status", message => {
    if (message === lastToast) return;
    lastToast = message;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { lastToast = ""; }, 800);
    const lower = message.toLowerCase();
    const tone = /échec|erreur|impossible|indisponible|hors ligne/.test(lower) ? "error"
      : /importez|vérifiez/.test(lower) ? "warn" : "info";
    if (tone === "info" && /—/.test(message)) return;   // retours continus (angle, taille)
    toast(message, tone);
  });

  /* ---------------------------------------------------------------
     Raccourcis clavier
     --------------------------------------------------------------- */
  window.addEventListener("keydown", e => {
    const tag = document.activeElement?.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || document.activeElement?.isContentEditable) return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    switch (e.key.toLowerCase()) {
      case "e": $("eraseAction").click(); break;
      case "1": $("viewPhoto").click(); break;
      case "2": $("viewThree").click(); break;
      case "c": showView("catalog"); break;
      case "?": showView("help"); break;
      default: break;
    }
  });

  /* ---------------------------------------------------------------
     Branchements moteur → interface
     --------------------------------------------------------------- */
  App.on("select", item => {
    placeObjectBar();
    if (item) { renderProduct(); showView("product"); }
    else if (activeView === "product") showView(lastLibraryView);
  });
  App.on("items", () => { updateTotals(); placeObjectBar(); });
  App.on("room", () => {
    $("roomState").textContent = App.state.roomFileName || "Photo importée";
    $("roomCard").classList.add("is-loaded");
    document.body.classList.add("has-room");
    renderAnalysis(App.state.analysis);
  });
  App.on("analysis", data => renderAnalysis(data));
  App.on("history", refreshHistoryButtons);

  // L'analyse est produite par ai.js : on surveille l'état applicatif.
  const analysisWatcher = new MutationObserver(() => {
    if (App.state.analysis) renderAnalysis(App.state.analysis);
  });
  analysisWatcher.observe($("status"), { childList: true, characterData: true, subtree: true });

  window.CigogneUI = {
    updateContext: placeObjectBar,
    hideContext: () => objectBar.classList.remove("is-visible"),
    showAI, hideAI, toast, showView,
    filterByProducts, rebuildCatalog,
  };

  /* Pastille « non enregistré » sur l'onglet Projets. */
  window.Projects?.onChange?.(status => {
    const dot = $("railProjectDot");
    if (dot) dot.hidden = !status.dirty;
  });

  window.addEventListener("keydown", event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
      event.preventDefault();
      window.Projects?.save();
    }
  });

  /* -------------------------- Démarrage --------------------------- */
  buildCatalog();
  filterCatalog();
  updateTotals();
  refreshHistoryButtons();
  renderEraserPhase();
  syncViewButtons();
  showView("catalog");
})();

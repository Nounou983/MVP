/* =========================================================
   La Cigogne D'Ailleurs — Bibliothèque produit
   Le catalogue devient une donnée, plus du code.

   Trois sources, dans l'ordre :
     1. l'API (/api/products) — la vérité en production ;
     2. data/products.json embarqué — hors ligne, ou API absente ;
     3. le tableau CATALOG codé en dur — dernier filet.

   Chaque produit porte SKU, vendeur, disponibilité, matériaux,
   variantes, dimensions réelles, URL fiche et, quand il existe,
   une photo et un modèle GLB. L'interface ne lit que ces champs :
   ajouter une vraie photo est un changement de données.
   ========================================================= */

(() => {
  "use strict";

  const cfg = window.CIGOGNE_CONFIG || {};
  const API = window.CigogneAPI;

  const state = {
    source: "builtin",
    families: window.FAMILIES || [],
    products: [],
    byId: new Map(),
    photoCache: new Map(),
    warned: new Set(),
  };

  const listeners = new Set();
  const notify = () => listeners.forEach(fn => { try { fn(state.products); } catch (e) { console.error(e); } });

  /* Règles de placement de repli, par type de meuble. Sans elles, un
     catalogue sans métadonnées (API injoignable, fichier absent) perdait
     l'aimantation au mur — une régression invisible mais bien réelle. */
  const DEFAULT_PLACEMENT = {
    sofa: { anchor: "wall", clearance_front: 0.75, against_wall: true },
    bed: { anchor: "wall", clearance_front: 0.60, against_wall: true },
    tvstand: { anchor: "wall", clearance_front: 0.90, against_wall: true },
    armchair: { anchor: "floor", clearance_front: 0.60 },
    chair: { anchor: "floor", clearance_front: 0.45 },
    table: { anchor: "floor", clearance_front: 0.70 },
    lamp: { anchor: "floor", clearance_front: 0.20 },
    plant: { anchor: "floor", clearance_front: 0.20 },
    rug: { anchor: "floor", clearance_front: 0, under_furniture: true },
  };

  /* ------------------------------------------------------------------
     Normalisation : un produit venant de n'importe quelle source doit
     ressortir avec la même forme, y compris les champs hérités (w, d,
     price, colors) que les modules existants lisent directement.
     ------------------------------------------------------------------ */
  function normalize(raw) {
    const dims = raw.dimensions || {};
    const base = raw.base || raw.catId || "sofa";
    const colors = Array.isArray(raw.colors) && raw.colors.length
      ? raw.colors
      : (raw.variants || []).map(v => v.hex).filter(Boolean);

    const rawText = [raw.name, raw.blurb, ...(raw.materials || []), ...(raw.tags || [])].filter(Boolean).join(" ").toLowerCase();
    const STYLE_RULES = [
      ["scandinave", /scandin|minimal|clair|lin|bois naturel/],
      ["moderne", /moderne|contempor|métal|acier|géométr|design/],
      ["bohème", /boh[eè]me|rotin|tiss|jute|terre cuite/],
      ["classique", /classique|tradition|bois massif|capiton/],
      ["industriel", /industri|métal|acier|tube|brut/],
      ["naturel", /bois|ch[eê]ne|h[eê]tre|plante|terre|laine/],
    ];
    const styles = STYLE_RULES.filter(([, re]) => re.test(rawText)).map(([name]) => name);
    if (!styles.length) styles.push(base === "plant" || base === "rug" ? "naturel" : "moderne");
    const materialTokens = (raw.materials || []).map(v => String(v).toLowerCase());

    const entry = {
      id: raw.id,
      sku: raw.sku || raw.id,
      base,
      family: raw.family || "decor",
      name: raw.name || raw.id,
      blurb: raw.blurb || "",
      w: Number(raw.w ?? dims.width ?? 1),
      d: Number(raw.d ?? dims.depth ?? 1),
      h: Number(raw.h ?? dims.height ?? 0.8),
      dimensions: {
        width: Number(dims.width ?? raw.w ?? 1),
        depth: Number(dims.depth ?? raw.d ?? 1),
        height: Number(dims.height ?? raw.h ?? 0.8),
        unit: dims.unit || "m",
      },
      price: Number(raw.price || 0),
      currency: raw.currency || "DZD",
      color: raw.color || colors[0] || "#8A8F98",
      colors,
      variants: raw.variants || colors.map((hex, i) => ({
        id: `${raw.id}--${i}`, hex, name: "Finition", available: true,
      })),
      materials: raw.materials || [],
      care: raw.care || "",
      seller: raw.seller || null,
      manufacturer: raw.manufacturer || "",
      availability: raw.availability || { status: "unknown", stock: null, lead_time_days: null },
      product_url: raw.product_url || "",
      cta: raw.cta || null,
      assets: Object.assign(
        { thumbnail: null, photos: [], sprite: { kind: "vector", maker: base }, model: null,
          is_placeholder: true, placeholder_reason: "Aucun visuel sous licence fourni." },
        raw.assets || {},
      ),
      placement: raw.placement || DEFAULT_PLACEMENT[base] || { anchor: "floor", clearance_front: 0.4 },
      tags: raw.tags || [raw.family, base],
      styles: raw.styles || styles,
      materialTokens,
      searchText: `${raw.name || ""} ${raw.blurb || ""} ${(raw.materials || []).join(" ")} ${(raw.tags || []).join(" ")} ${styles.join(" ")}`.toLowerCase(),
    };

    // Compatibilité : les modules existants appellent entry.make(couleur).
    const maker = window.MAKERS?.[base] || (window.CATALOG || []).find(c => c.base === base)?.make;
    entry.make = color => {
      if (window.MAKERS?.[base]) return window.MAKERS[base](color || entry.color);
      if (typeof maker === "function") return maker(color || entry.color);
      return "";
    };
    return entry;
  }

  function adopt(products, source) {
    const normalized = products.map(normalize).filter(p => {
      const ok = Boolean(window.MAKERS?.[p.base]) || Boolean(p.assets?.sprite?.maker);
      if (!ok && !state.warned.has(p.id)) {
        state.warned.add(p.id);
        console.warn(`[catalogue] produit ignoré, rendu inconnu : ${p.id}`);
      }
      return ok;
    });
    if (!normalized.length) return false;

    state.products = normalized;
    state.byId = new Map(normalized.map(p => [p.id, p]));
    state.source = source;

    // On remplace le contenu du tableau sans changer la référence :
    // app.js, phase3.js et ui.js gardent la leur.
    const target = window.CATALOG;
    if (Array.isArray(target)) {
      target.length = 0;
      normalized.forEach(p => target.push(p));
    } else {
      window.CATALOG = normalized;
    }
    window.catalogEntry = id => state.byId.get(id) || null;
    notify();
    return true;
  }

  /* ------------------------------------------------------------------
     Images : photo produit si elle existe, sinon vignette, sinon le
     sprite vectoriel. Le repli est silencieux pour la personne mais
     visible en console, pour qu'un lien mort se remarque.
     ------------------------------------------------------------------ */
  function loadWithFallback(sources, fallbackImage) {
    const key = sources.join("|");
    if (state.photoCache.has(key)) return state.photoCache.get(key);

    const img = new Image();
    img.decoding = "async";
    img.crossOrigin = "anonymous";
    let index = 0;
    const next = () => {
      if (index < sources.length) {
        img.src = sources[index++];
        return;
      }
      if (fallbackImage?.src) img.src = fallbackImage.src;
    };
    img.addEventListener("error", () => {
      console.warn(`[catalogue] image indisponible : ${img.src}`);
      next();
    });
    next();
    state.photoCache.set(key, img);
    return img;
  }

  function vectorSprite(entry, color) {
    return window.spriteFor?.(entry.base, color || entry.color) || null;
  }

  /** Image utilisée pour le rendu dans la pièce. */
  function spriteFor(entryOrItem, color) {
    const entry = typeof entryOrItem === "string"
      ? state.byId.get(entryOrItem)
      : (state.byId.get(entryOrItem?.entryId) || entryOrItem);
    if (!entry) return null;
    const photos = entry.assets?.photos || [];
    const chosen = color || entryOrItem?.color || entry.color;
    // Une photo par variante si elle est fournie, sinon la première.
    const variantIndex = entry.colors.indexOf(chosen);
    const photo = photos[variantIndex] || photos[0];
    if (photo) return loadWithFallback([photo], vectorSprite(entry, chosen));
    return vectorSprite(entry, chosen);
  }

  /** Image utilisée dans la grille du catalogue. */
  function thumbnailFor(entry) {
    if (!entry) return null;
    const sources = [entry.assets?.thumbnail, entry.assets?.photos?.[0]].filter(Boolean);
    if (sources.length) return loadWithFallback(sources, vectorSprite(entry, entry.color));
    return vectorSprite(entry, entry.color);
  }

  /** Markup de vignette : <img> si photo, sinon le SVG en ligne. */
  function thumbnailMarkup(entry, color) {
    const photo = entry?.assets?.thumbnail || entry?.assets?.photos?.[0];
    if (photo) {
      const alt = (entry.name || "").replace(/"/g, "&quot;");
      return `<img src="${photo}" alt="${alt}" loading="lazy" decoding="async"
                   onerror="this.replaceWith(window.ProductCatalog.svgNode(this.dataset.pid))"
                   data-pid="${entry.id}">`;
    }
    return entry?.make ? entry.make(color || entry.color) : "";
  }

  function svgNode(productId) {
    const entry = state.byId.get(productId);
    const holder = document.createElement("span");
    holder.className = "thumb-fallback";
    holder.innerHTML = entry?.make ? entry.make(entry.color) : "";
    return holder;
  }

  const modelUrl = entry => entry?.assets?.model?.url || null;
  const hasModel = entry => Boolean(modelUrl(entry));
  /** Vrai tant qu'aucune photo/GLB sous licence n'a remplacé le sprite. */
  const isPlaceholder = entry => Boolean(entry?.assets?.is_placeholder ?? true);

  function availabilityLabel(entry) {
    const a = entry?.availability || {};
    if (a.status === "in_stock") {
      return a.stock > 5 ? "En stock" : `Plus que ${a.stock} en stock`;
    }
    if (a.status === "made_to_order") return "Sur commande";
    if (a.status === "out_of_stock") return "Épuisé";
    return a.lead_time_days ? `Livraison ${a.lead_time_days} j` : "";
  }

  function variantName(entry, hex) {
    return (entry?.variants || []).find(v => v.hex?.toLowerCase() === String(hex).toLowerCase())?.name || "Finition";
  }

  /* ------------------------------------------------------------------ */
  async function fromApi() {
    if (!API) return false;
    try {
      const payload = await API.products();
      if (!payload?.products?.length) return false;
      if (payload.families) state.families = payload.families;
      return adopt(payload.products, "api");
    } catch {
      return false;
    }
  }

  async function fromBundle() {
    const url = cfg.productsUrl || "data/products.json";
    try {
      const resp = await fetch(url, { cache: "no-cache" });
      if (!resp.ok) return false;
      const payload = await resp.json();
      if (payload.families) state.families = payload.families;
      return adopt(payload.products || [], "bundled");
    } catch {
      return false;
    }
  }

  const ready = (async () => {
    // Le catalogue codé en dur est déjà chargé : l'application est
    // utilisable immédiatement, l'enrichissement arrive après.
    adopt(window.CATALOG || [], "builtin");
    if (await fromApi()) return state.source;
    if (await fromBundle()) return state.source;
    console.info("[catalogue] métadonnées étendues indisponibles, repli sur le catalogue embarqué.");
    return state.source;
  })();

  window.ProductCatalog = {
    ready,
    get source() { return state.source; },
    get families() { return state.families; },
    all: () => state.products,
    byId: id => state.byId.get(id) || null,
    byFamily: family => (!family || family === "all"
      ? state.products
      : state.products.filter(p => p.family === family)),
    search(text) {
      const needle = (text || "").trim().toLowerCase();
      if (!needle) return state.products;
      return state.products.filter(p =>
        p.name.toLowerCase().includes(needle)
        || p.blurb.toLowerCase().includes(needle)
        || p.sku.toLowerCase().includes(needle)
        || (p.materials || []).some(m => m.toLowerCase().includes(needle))
        || (p.tags || []).some(t => String(t).toLowerCase().includes(needle)));
    },
    spriteFor, thumbnailFor, thumbnailMarkup, svgNode,
    modelUrl, hasModel, isPlaceholder, availabilityLabel, variantName,
    onChange(fn) { listeners.add(fn); return () => listeners.delete(fn); },
  };
})();

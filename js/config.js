/* =========================================================
   La Cigogne D'Ailleurs — Configuration front
   Chargé en premier. Tout ce qui dépend du déploiement vit ici :
   en production, remplacez ce fichier (ou définissez
   window.CIGOGNE_CONFIG avant lui) au lieu de modifier le code.

   Aucune clé secrète ne doit figurer dans ce fichier : le front
   ne connaît que des URL publiques et le jeton de la personne
   connectée, obtenu à l'exécution.
   ========================================================= */

(() => {
  "use strict";

  const host = window.location.hostname || "127.0.0.1";
  const isFile = window.location.protocol === "file:";
  const local = /^(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])$/.test(host);

  const defaults = {
    /** Service API (comptes, projets, catalogue, file de tâches). */
    apiBase: isFile || local ? `http://${isFile ? "127.0.0.1" : host}:8100` : `${window.location.origin}`,

    /** Service de modèles. "gateway" fait passer l'IA par la file d'attente. */
    aiMode: "direct",            // "direct" | "gateway"
    aiApi: null,                 // calculé plus bas si non fourni

    /** Catalogue produit embarqué, utilisé si l'API est injoignable. */
    productsUrl: "data/products.json",

    /** Envoi des événements d'usage. */
    analytics: !local,

    /** Sauvegarde automatique des projets (ms). 0 = désactivée. */
    autosaveDelay: 4000,

    /** Aides au placement (aimantation, collisions, repères). */
    placementAssist: true,

    /** Placement spatial : sol + profondeur relative + obstacles détectés. */
    spatialIntelligence: true,
  };

  const config = Object.assign({}, defaults, window.CIGOGNE_CONFIG || {});

  if (!config.aiApi) {
    config.aiApi = config.aiMode === "gateway"
      ? `${config.apiBase.replace(/\/$/, "")}/api/ai`
      : (isFile ? "http://127.0.0.1:8000" : `${window.location.protocol}//${host}:8000`);
  }
  config.apiBase = config.apiBase.replace(/\/$/, "");

  window.CIGOGNE_CONFIG = config;
})();

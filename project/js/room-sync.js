/* =========================================================
   La Cigogne D'Ailleurs — Liaison reconstruction
   Écoute l'analyse, construit le modèle spatial (js/room-model.js),
   le publie pour les autres modules et met à jour la carte « Pièce ».

   Règle : sous le seuil de confiance, on ne remplace rien. Le proxy
   d'origine de la vue 3D reste en place et l'interface le dit, plutôt
   que d'afficher des dimensions inventées.
   ========================================================= */

(() => {
  "use strict";

  const App = window.App;
  if (!App) return;

  const $ = id => document.getElementById(id);
  const store = { model: null, building: false };

  function renderCard(model) {
    const card = $("roomGeometry");
    if (!card) return;
    if (!model) {
      card.hidden = true;
      return;
    }
    card.hidden = false;
    const described = window.RoomModel.describe(model);
    const pct = Math.round((model.confidence || 0) * 100);
    const openings = model.openings || [];
    const windows = openings.filter(o => o.type === "window").length;
    const doors = openings.filter(o => o.type === "door").length;

    $("roomDims").textContent = described.title;
    $("roomDimsDetail").textContent = described.detail;
    $("roomConfidence").textContent = `${pct} %`;
    $("roomConfidenceBar").style.width = `${pct}%`;
    $("roomConfidenceBar").dataset.level = model.reliable ? "ok" : "low";
    $("roomOpenings").textContent = openings.length
      ? `${windows} fenêtre(s), ${doors} porte(s)`
      : "Aucune détectée";
    $("roomAssumption").textContent = model.reliable
      ? `Hypothèses : appareil à ${model.camera.height.toFixed(2)} m, champ ${model.camera.fov}°, plafond ${model.ceiling.toFixed(2)} m (valeurs courantes, non mesurées).`
      : "Géométrie trop incertaine : la vue 3D garde son volume de secours.";
  }

  async function rebuild(analysis) {
    if (!analysis || !window.RoomModel) {
      store.model = null;
      window.CigogneRoom.model = null;
      renderCard(null);
      return null;
    }
    if (store.building) return store.model;
    store.building = true;
    try {
      const model = await window.RoomModel.build(analysis);
      store.model = model;
      window.CigogneRoom.model = model;
      renderCard(model);
      // La 3D adopte les dimensions estimées uniquement si elles tiennent.
      if (model?.reliable && window.Phase3?.applyRoomModel) {
        window.Phase3.applyRoomModel(model);
      }
      App.emit("roomModel", model);
      return model;
    } catch (err) {
      console.warn("[pièce] reconstruction impossible", err);
      store.model = null;
      renderCard(null);
      return null;
    } finally {
      store.building = false;
    }
  }

  App.on("analysis", analysis => { rebuild(analysis); });
  App.on("room", () => { store.model = null; window.CigogneRoom.model = null; renderCard(null); });

  window.CigogneRoom = {
    model: null,
    rebuild,
    /** Point du sol (mètres) visé par un pixel du canvas. */
    floorAt(point) {
      if (!store.model) return null;
      return window.RoomModel.canvasToFloor(point, store.model, App.roomFit());
    },
    /** Position canvas d'un point du sol. */
    project(pointOnFloor) {
      if (!store.model) return null;
      return window.RoomModel.floorToCanvas(pointOnFloor, store.model, App.roomFit());
    },
  };
})();

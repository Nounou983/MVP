# Mask Engine V4 — 2026-09-19

- Replaced the V3 absolute candidate rejection path with click-first relative SAM ranking.
- Added SegFormer-independent multi-scale prompt boxes.
- Added independent boundary probes for detached furniture parts.
- `/select-mask` now reports `selection_version: 4`.


## Phase 9.0 — Reconstruction Quality
- Added surface-aware wall/floor/rug reconstruction candidate.
- Added contact-shadow reconstruction for large furniture footprints.
- Added candidate ensemble scoring across surface-guided, RORem and LaMa paths.
- Fixed texture transfer to use the actual reconstructed surface, including rugs.
- Added reconstruction diagnostics to `/inpaint`.
- Added deterministic Phase 9 helper tests.

# Changelog — La Cigogne D'Ailleurs

Ce fichier résume l'historique du projet. Le détail technique complet de
chaque phase 5.2.x reste dans les fichiers `PHASE_5_2_*.md` individuels,
conservés à la racine du projet pour référence.

## Phase 7 — 2026-09-16

Finalisation : correction du dernier test en échec, documentation complète,
audit d'intégration, empaquetage.

### Corrigé
- **`ai.py:205` — `KeyError: 'mask_key'`.** La passerelle `/api/ai/*`
  supposait qu'une tâche réussie contenait toujours la clé de résultat
  attendue. Un résultat de tâche incomplet (exécuteur mal configuré, worker
  décalé) levait une exception non gérée au lieu d'une erreur HTTP propre.
  Les trois routes concernées (`select-mask`, `inpaint`, `remove`) vérifient
  désormais explicitement la forme du résultat et renvoient **502** avec un
  message clair si elle est inattendue.

### Ajouté
- Marquage explicite des visuels de démonstration
  (`assets.is_placeholder`), affiché sur la fiche produit, pour qu'un sprite
  vectoriel ne soit jamais confondu avec un vrai visuel produit.
- Deux tests backend supplémentaires couvrant la garde ajoutée
  (`test_gateway_rejects_malformed_result_on_every_route`, mise à jour de
  `test_gateway_works_without_an_account`).
- Documentation complète : `README.md` (racine), `docs/API.md`,
  `docs/SCHEMA.md`, `docs/PROJECT_SCHEMA.md`, `docs/DEPLOYMENT.md`,
  `docs/TEST_REPORT.md`, `.env.example`.

### Vérifié
- Suite complète : 47 tests backend + 124 vérifications frontend + 27
  vérifications d'intégration ponctuelles = **198/198, zéro échec**.
- Non-régression confirmée sur les sept routes du service de modèles
  d'origine, sur `js/eraser.js` (non modifié), et sur l'API publique de
  `js/app.js` et `js/phase3.js`.

---

## Phase 6 — plateforme complète (comptes, projets, IA en file d'attente)

Reconstruction du front autour du nouveau système, plus un backend API
entièrement nouveau à côté du service de modèles d'origine (laissé intact).

### Backend — nouveau service API (`server/app/`, `server/api.py`)
- Comptes : inscription, connexion, jetons JWT, rafraîchissement,
  révocation de session, changement de mot de passe.
- Projets persistants : sauvegarde/chargement complet de la composition
  (photo, meubles, transformations, couleurs, caméra, éclairage, trace des
  retouches IA), historique de 20 versions, duplication.
- Stockage abstrait (local ou S3), upload validé par signature de fichier
  (pas par extension), noms de fichiers assainis côté serveur.
- File de tâches IA : mise en file, tirage atomique par les workers,
  nouvelles tentatives, délais, annulation, reprise après panne d'un worker.
- Passerelle `/api/ai/*` : mêmes contrats multipart que le service de
  modèles d'origine, mais passant par la file d'attente — quotas et limites
  de débit inclus, sans changement du frontend existant.
- Qualité du retrait IA : découpe en fenêtre haute résolution sur les
  grandes photos, nouvelles tentatives à masque élargi, score de confiance.
- Catalogue produit orienté métadonnées : SKU, vendeur, disponibilité,
  matériaux, variantes, dimensions réelles ; prêt pour de vrais visuels.
- Favoris, collections, liens de partage en lecture seule (avec
  commentaires en option), mesure d'usage à liste blanche, formules et
  quotas, points d'accroche pour la facturation.
- Sécurité : authentification par jeton, validation stricte des téléversements,
  limites de débit, CORS strict en production, isolation totale des données
  par utilisateur, aucun secret en dur.

### Frontend — nouveaux modules
- `placement.js` : aimantation au mur, alignement, détection de collision
  (séparation d'axes), dégagement minimal.
- `room-model.js` : reconstruction spatiale (caméra, sol, murs, ouvertures,
  score de confiance), avec repli explicite quand la confiance est trop
  basse plutôt que d'afficher de fausses dimensions.
- `assets.js` : bibliothèque produit orientée métadonnées, avec repli en
  cascade API → fichier embarqué → catalogue codé en dur.
- `projects.js`, `account.js` : persistance de projet, comptes, favoris,
  collections, partage — tout en restant utilisable sans compte.
- `config.js`, `api.js`, `analytics.js` : configuration de déploiement,
  client API avec erreurs typées, mesure d'usage à liste blanche.
- `share.html` : vue client en lecture seule, avec commentaires optionnels.

### Corrigé (interface, hérité des phases précédentes)
- `CigogneUI.showAI()`/`hideAI()` appelés par `eraser.js` mais jamais
  définis — implémentés.
- Redimensionnement de fenêtre cassé — `ResizeObserver` branché.
- Bascules de calques qui ne basculaient jamais.
- `#status` hors écran.
- Double rendu des meubles en vue 3D.
- Canevas 3D invisible qui volait les clics après usage de la gomme.

## Phases 5.2.15 à 5.2.37

Refonte progressive du pipeline de retrait d'objet par IA (politique de
sélection RORem/LaMa/diffusion, gestion des grands objets, garde-fous de
qualité) et de l'interface consommateur (voir `PHASE_5_2_29_UI_OVERHAUL.md`
à `PHASE_5_2_37_SPATIAL_UI.md`). Détail complet dans les fichiers individuels
à la racine du projet ; `PHASE_5_2_15_README_ARCHIVE.md` conserve le README
de cette période pour référence.

## Phase 9.4 — AI removal quality repair
- Corrected RORem inference to use CFG and a content-irrelevant prompt.
- Reduced RORem internal dilation from 18/24/30px to 0/6/12px.
- Promoted surface-aware reconstruction to a first-class large-object candidate.
- Added texture/structure-aware fallback selection across RORem, surface reconstruction and LaMa.

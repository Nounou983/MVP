# Rapport de tests — Phase 7

Exécuté le 2026-09-16, sur l'environnement de développement de ce projet
(pas de GPU, pas d'appareil mobile physique — voir la section « Non vérifié »).

## Résultat global

| Suite | Résultat | Détail |
|---|---|---|
| Backend (`server/tests/`, pytest) | **47/47 réussis** | `test_api.py` (39) + `test_removal.py` (8) |
| Frontend (`test/run.mjs`) | **41/41 réussis** | Interface Phase 6 (catalogue, produit, IA, undo/redo, export) |
| Frontend (`test/run2.mjs`) | **21/21 réussis** | Interactions clavier/souris, glisser-déposer, historique |
| Frontend (`test/run3.mjs`) | **62/62 réussis** | Modules Phase 7 (placement, reconstruction, projets, favoris, API, analytics) |
| Audit d'intégration (script ponctuel) | **27/27 réussis** | Voir « Audit d'intégration » ci-dessous |
| **Total** | **198/198 réussis, 0 échec** | |

Commandes pour reproduire :
```bash
cd server && python3 -m pytest tests/ -v
cd ../test && node run.mjs && node run2.mjs && node run3.mjs
```

## Détail backend (47 tests)

```
test_register_login_and_me
test_weak_password_is_refused
test_duplicate_email_is_refused
test_wrong_password_and_missing_token
test_password_change_revokes_old_tokens
test_refresh_token_flow
test_project_roundtrip_and_versions
test_projects_are_isolated_between_users
test_project_quota_is_enforced
test_oversized_project_state_is_refused
test_image_upload_and_signed_download
test_upload_rejects_disguised_file
test_upload_rejects_fake_glb
test_uploaded_file_is_not_readable_by_another_user
test_storage_key_ignores_client_filename
test_job_lifecycle_with_worker
test_job_cannot_use_another_users_image
test_job_cancellation
test_ai_quota_is_enforced
test_queue_claim_is_single_flight
test_stalled_job_is_requeued
test_product_catalogue_metadata
test_favorites_and_collections
test_collections_are_isolated
test_share_link_is_read_only_and_public
test_comment_permission_requires_pro_plan
test_share_comments_when_enabled
test_analytics_drops_unknown_events_and_props
test_admin_routes_are_closed_to_normal_users
test_plans_endpoint
test_health_reports_queue
test_security_headers_present
test_rate_limit_triggers_on_auth
test_production_requires_a_secret_key
test_production_refuses_wildcard_cors
test_large_photo_is_processed_through_a_crop
test_pixels_outside_the_window_are_untouched
test_small_photo_is_sent_whole
test_failed_gate_retries_with_a_wider_mask
test_good_result_stops_after_one_pass
test_confidence_is_monotone
test_total_failure_raises_instead_of_returning_a_smear
test_empty_mask_is_reported
test_gateway_analyze_returns_the_legacy_shape
test_gateway_reports_model_failure_instead_of_hanging
test_gateway_works_without_an_account
test_gateway_rejects_malformed_result_on_every_route
```
(47 tests au total, listés ci-dessus.)

## Fonctionnalités vérifiées par des tests automatisés

| Fonctionnalité | Testée par |
|---|---|
| Import photo / drag-and-drop / pièces d'exemple | `run.mjs`, `run2.mjs` |
| Placement 2D, déplacement, rotation, échelle, dimensions | `run2.mjs`, `run3.mjs` (`Placement.resolve`) |
| Catalogue, recherche, filtres par famille | `run.mjs`, `run3.mjs` |
| Variantes de couleur, remplacement de meuble | `run.mjs` |
| Undo/redo (meubles + retouches IA unifiées) | `run.mjs`, `run2.mjs` |
| Export (aperçu, canvas) | `run.mjs` |
| Aimantation au mur, alignement, collisions, dégagement | `run3.mjs` (`Placement`) |
| Reconstruction de pièce (caméra, sol, murs, ouvertures, confiance) | `run3.mjs` (`RoomModel`) |
| Comptes, jetons, isolation entre utilisateurs | `test_api.py` |
| Projets : sauvegarde, historique, restauration | `test_api.py`, `run3.mjs` |
| Fichiers : validation, assainissement, téléchargement signé | `test_api.py` |
| File de tâches IA : mise en file, tirage atomique, annulation, relance après panne worker | `test_api.py` |
| Retrait IA (qualité) : découpe en fenêtre haute résolution, nouvelles tentatives, confiance | `test_removal.py` |
| Passerelle de compatibilité `/api/ai/*` (mêmes contrats que le service d'origine) | `test_api.py` |
| Favoris, collections | `test_api.py`, `run3.mjs` |
| Partage : lien public, permissions, révocation | `test_api.py` |
| Limites de débit | `test_api.py` |
| Garde-fous de production (clé secrète, CORS) | `test_api.py` |
| Client API : erreurs typées, jamais d'échec muet hors ligne | `run3.mjs` |
| Mesure d'usage : liste blanche d'événements et de propriétés | `run3.mjs`, `test_api.py` |

## Audit d'intégration (script ponctuel, 27 vérifications)

Exécuté une fois contre une instance vivante du service API
(`TestClient`), au-delà de la suite pytest, pour répondre point par point
à la demande de vérification finale :

- ✅ URL de base de l'API configurable par variable d'environnement
- ✅ Authentification (inscription, connexion, jeton invalide rejeté)
- ✅ Projets : sauvegarde, relecture
- ✅ Isolation entre comptes (404, pas 403)
- ✅ Favoris : ajout, persistance
- ✅ Collections : création, ajout de produit, persistance
- ✅ Partage : création de lien, accès public sans compte, révocation → 404
- ✅ File de tâches : mise en file, statut interrogeable, annulation prise en charge
- ✅ Tâche effectivement traitée par un worker réel (`WorkerLoop`)
- ✅ Limite de débit appliquée (429 après le seuil)
- ✅ Upload : fichier invalide rejeté, nom de fichier assaini
- ✅ CORS générique refusé en production, origine explicite acceptée
- ✅ Clé secrète obligatoire en production
- ✅ Routes d'administration protégées (403 pour un compte normal)

## Non-régression — fonctionnalités préexistantes

Confirmée par relecture directe du code, pas seulement par les tests
automatisés (le service de modèles n'a pas de suite de tests dans ce projet
et n'a pas été modifié en profondeur) :

| Fonctionnalité | État |
|---|---|
| Les 7 routes du service de modèles (`/analyze`, `/select-mask`, `/inpaint`, `/remove`, `/inpaint-status`, `/health`, `/`) | Intactes — `diff` contre la version précédente confirme que seuls le bloc CORS et le montage optionnel en fin de fichier ont changé |
| `js/eraser.js` | **Non modifié**, comme demandé dès le début du projet |
| `js/phase3.js` (visionneuse 3D, import GLB/GLTF) | Une fonction ajoutée (`applyRoomModel`), une fonction ajoutée (`renderAtSize`) ; le reste intact |
| `js/phase3e.js` (rendu final) | Compositing existant conservé ; ajout de l'accord colorimétrique et des ombres de contact par meuble, activés en plus de l'existant, pas à sa place |
| `js/ai.js` | Adresse du service IA rendue configurable ; émission d'un événement `analysis` en plus du comportement existant |
| `js/app.js` | API publique inchangée (`window.App`, `window.AppActions` et tous leurs alias) ; ajout du placement assisté et des repères visuels |

## Non vérifié — limites assumées

Ces points ne peuvent pas être validés dans cet environnement d'exécution ;
ils sont listés ici plutôt que passés sous silence.

- **Le chemin GPU réel.** Aucune carte CUDA n'est disponible ici. La file de
  tâches, la concurrence, les nouvelles tentatives et la reprise après panne
  sont testées avec un exécuteur factice (`EchoExecutor`) qui simule un
  succès/échec sans jamais appeler de modèle de diffusion. Le raccordement
  au vrai service de modèles (`HttpAIExecutor`) est testé pour sa logique
  (découpe en fenêtre, retries, calcul de confiance) avec un service HTTP
  factice, jamais avec RORem/LaMa réels.
- **Les appareils mobiles physiques.** Aucun iPhone ni Android physique
  disponible ici. La disposition tactile (cibles ≥ 44 px, feuille du bas,
  `touch-action: none` sur le canevas) a été implémentée et relue, et les
  largeurs d'écran ont été vérifiées par redimensionnement du canevas jsdom
  dans les harnais, mais aucun geste tactile réel (pincer/zoomer, glisser à
  deux doigts en 3D) n'a été exercé sur un appareil.
- **Photographie produit et modèles GLB sous licence.** Le projet ne contient
  aucun visuel sous licence — chaque produit est marqué
  `assets.is_placeholder: true` et retombe sur un sprite vectoriel. Voir
  `server/tools/build_catalog.py` pour l'endroit exact où brancher de vrais
  fichiers.
- **PostgreSQL et S3 en conditions réelles.** Le code des deux backends de
  stockage/base de données est écrit et le chemin SQLite/disque est testé de
  bout en bout ; le chemin PostgreSQL/S3 n'a pas pu être exercé faute d'accès
  réseau sortant vers ces services dans cet environnement (voir la liste des
  domaines autorisés en sandbox).

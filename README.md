# La Cigogne D'Ailleurs

Visualiseur de pièce assisté par IA — importez une photo de votre intérieur,
placez du mobilier réaliste, retirez ce qui gêne, visualisez en 3D, et
enregistrez/partagez le résultat. Interface en français, marque algérienne.

**Statut du projet : Phase 10.3 — catalogue intelligent + assistant de composition renforcé.**
Voir `docs/TEST_REPORT.md` pour le détail des vérifications et leurs limites.

---

## Ce que fait l'application

- **Photo → composition.** Importez une photo (ou choisissez une pièce
  d'exemple), glissez des meubles dessus, déplacez/tournez/redimensionnez,
  changez de couleur, remplacez un meuble par un autre.
- **Placement assisté.** Aimantation au mur, alignement sur les meubles
  voisins, détection de chevauchement, dégagement minimal devant un canapé —
  signalés, jamais imposés : une photo n'est pas un plan.
- **Retrait d'objet par IA.** Sélection au point cliqué (SAM), reconstruction
  remove-only (RORem/LaMa), avec repérage en fenêtre haute résolution sur les
  grandes photos et une seconde tentative automatique si le résultat est
  jugé insuffisant.
- **Vue 3D.** Three.js, import GLB/GLTF de vos propres modèles, éclairage et
  ombres, caméra calée sur une reconstruction spatiale estimée de la pièce
  quand la confiance est suffisante.
- **Rendu final.** Accord colorimétrique entre les meubles 3D et la photo,
  ombres de contact par objet, export à la résolution native.
- **Comptes et projets.** Optionnels — l'application est entièrement
  utilisable sans compte (tout reste sur l'appareil). Se connecter permet de
  retrouver ses pièces depuis un autre appareil, de partager un lien client
  en lecture seule, et de gérer des favoris/collections.

---

## Architecture

```
Frontend statique (index.html, js/, css/)
        │
        ├── Service API (server/api.py, port 8100)
        │     comptes · projets · catalogue · fichiers · file de tâches
        │     PAS de GPU requis
        │
        └── Service de modèles (server/main.py, port 8000)
              analyse · sélection · retrait IA — GPU recommandé
```

Le frontend n'a pas d'étape de build : ce sont des fichiers statiques,
servis tels quels. Les deux services backend sont indépendants et peuvent
être déployés, mis à l'échelle et redémarrés séparément — voir
`docs/DEPLOYMENT.md`.

### Frontend (`js/`)
| Fichier | Rôle |
|---|---|
| `config.js` | Configuration de déploiement (adresses des API) |
| `api.js` | Client API : jetons, erreurs typées, jamais d'échec muet |
| `analytics.js` | Mesure d'usage (liste blanche d'événements) |
| `furniture-data.js` | Catalogue de repli codé en dur, sprites vectoriels |
| `assets.js` | Bibliothèque produit : API → fichier embarqué → repli, photo/GLB/sprite |
| `placement.js` | Aimantation, alignement, collisions, dégagement |
| `room-model.js` | Reconstruction spatiale : caméra, sol, murs, ouvertures, confiance |
| `app.js` | Moteur canvas 2D — sélection, historique, rendu |
| `ui.js` | Interface : navigation, catalogue, fiche produit, retouche IA |
| `room-sync.js` | Relie l'analyse IA à la reconstruction spatiale et à la 3D |
| `projects.js` | Sérialisation, sauvegarde locale/en ligne, autosauvegarde |
| `account.js` | Compte, favoris, collections, partage |
| `ai.js` | Appels au service de modèles (analyse, calques) |
| `eraser.js` | Retrait d'objet — **non modifié depuis l'origine du projet** |
| `phase3.js` | Vue 3D Three.js, import GLB |
| `phase3e.js` | Rendu final photoréaliste |

### Backend (`server/`)
| Chemin | Rôle |
|---|---|
| `main.py` | Service de modèles — **inchangé**, sauf CORS configurable |
| `api.py` | Point d'entrée du service API |
| `worker.py` | Point d'entrée du worker de tâches (processus séparé) |
| `app/config.py` | Configuration par variables d'environnement, formules |
| `app/models.py` | Schéma de base de données (SQLAlchemy) |
| `app/security.py` | Mots de passe, jetons, limites de débit |
| `app/storage.py` | Stockage local ou S3, validation d'upload |
| `app/routers/` | Routes : auth, projets, fichiers, catalogue, partage, IA, système |
| `app/services/jobs.py` | File de tâches (mise en file, tirage atomique, reprise) |
| `app/services/executors.py` | Exécution des tâches IA, qualité du retrait |
| `app/services/worker.py` | Boucle du worker, concurrence GPU |
| `tools/build_catalog.py` | Génère `data/products.json` à partir des données produit |

---

## Démarrage rapide

```bash
# Service API
cd server
pip install fastapi "uvicorn[standard]" python-multipart sqlalchemy httpx pillow --break-system-packages
cp ../.env.example .env
uvicorn api:app --reload --port 8100

# Service de modèles (autre terminal)
cd server
pip install -r requirements.txt --break-system-packages
uvicorn main:app --reload --port 8000

# Frontend (autre terminal)
python3 -m http.server 5500
```

Ouvrez `http://localhost:5500`. Détail complet, y compris le déploiement en
production, dans `docs/DEPLOYMENT.md`.

---

## Tests

```bash
cd server && python3 -m pytest tests/ -v      # 47 tests
cd ../test && node run.mjs && node run2.mjs && node run3.mjs   # 124 vérifications
```

Résultat au moment de la livraison : **198/198 réussis, 0 échec**. Détail
complet dans `docs/TEST_REPORT.md`, y compris ce qui n'a pas pu être vérifié
dans cet environnement (chemin GPU réel, appareils mobiles physiques).

---

## Documentation

| Document | Contenu |
|---|---|
| `docs/API.md` | Toutes les routes des deux services |
| `docs/SCHEMA.md` | Schéma de base de données |
| `docs/PROJECT_SCHEMA.md` | Format du document de projet (`state`) |
| `docs/DEPLOYMENT.md` | Développement local et déploiement en production |
| `docs/TEST_REPORT.md` | Résultats de tests et limites assumées |
| `.env.example` | Modèle de configuration, les deux services |
| `CHANGELOG.md` | Historique des phases |

---

## Visuels produit : état actuel

Aucune photo ni modèle 3D sous licence n'est fourni avec ce projet. Chaque
produit du catalogue porte `assets.is_placeholder: true` et retombe sur un
sprite vectoriel — l'interface l'indique explicitement sur la fiche produit.
L'architecture prend déjà en charge `assets.thumbnail`, `assets.photos[]` et
`assets.model.url` : remplacer un placeholder par un vrai visuel est un
changement de données, pas de code. Voir le commentaire dans
`server/tools/build_catalog.py` pour la marche à suivre exacte.

---

## Phase 10.3 — assistant renforcé

L’assistant catalogue comprend les quantités, les ancres spatiales, les commandes multi-meubles et vérifie les objets réellement créés avant d’annoncer un succès. Voir `PHASE_10_3_ASSISTANT_QUALITY.md`.

## Ce qui n'a volontairement pas changé

Trois éléments sont restés hors périmètre depuis la demande initiale du
projet, et le sont restés à travers toutes les phases suivantes :

- `server/main.py` — le service de modèles d'origine. Seuls le bloc CORS et
  un montage optionnel en fin de fichier ont été ajoutés ; les sept routes
  et toute la logique de reconstruction sont intactes.
- `js/eraser.js` — le flux de retrait d'objet par IA. Non modifié.
- La philosophie d'interface : la pièce reste le sujet central en plein
  écran, les commandes restent flottantes et contextuelles. Aucune refonte
  en tableau de bord.

# Déploiement — La Cigogne D'Ailleurs

## Vue d'ensemble

Trois processus, indépendants :

1. **Frontend** — fichiers statiques (`index.html`, `js/`, `css/`, `share.html`).
   Aucune étape de build.
2. **Service API** (`server/api.py`) — comptes, projets, catalogue, fichiers,
   file de tâches. Pas de GPU nécessaire.
3. **Service de modèles** (`server/main.py`) — analyse, sélection, retrait
   IA. GPU fortement recommandé.

Le worker de tâches (`server/worker.py`) tourne soit dans le processus de
l'API (`CIGOGNE_INLINE_WORKER=1`, par défaut), soit séparément.

```
┌──────────┐      HTTPS       ┌──────────────┐     HTTP interne    ┌──────────────┐
│ Frontend │ ───────────────▶ │  Service API │ ──────────────────▶ │   Service    │
│ (statique)│                 │  (port 8100) │   (worker → CIGOGNE_AI_URL)        │
└──────────┘                  └──────┬───────┘                     │  de modèles  │
                                      │                             │  (port 8000)│
                                PostgreSQL/                         └──────┬───────┘
                                SQLite + stockage                          │
                                                                          GPU
```

---

## Développement local

### Prérequis
- Python 3.11+
- Node.js (uniquement pour lancer les harnais de test, pas pour le frontend lui-même)
- Un GPU CUDA pour le service de modèles avec de vrais poids ; sans GPU, ce
  service tourne quand même mais renvoie des résultats dégradés/factices
  selon les moteurs disponibles (voir `server/main.py`).

### Étape par étape

```bash
# 1. Dépendances du service API
cd server
python3 -m venv .venv && source .venv/bin/activate
pip install fastapi "uvicorn[standard]" python-multipart sqlalchemy httpx pillow

# 2. Configuration
cp ../.env.example .env
# Éditez .env si besoin — les valeurs par défaut fonctionnent en local.

# 3. Générer le catalogue (déjà fourni dans server/data/products.json et
#    data/products.json, à relancer seulement après avoir modifié
#    server/tools/build_catalog.py)
python tools/build_catalog.py

# 4. Lancer le service API (comptes, projets, catalogue, file de tâches)
uvicorn api:app --reload --port 8100

# 5. Dans un second terminal : le service de modèles (dépendances du
#    projet d'origine — torch, diffusers, transformers, etc., voir
#    server/requirements.txt)
pip install -r requirements.txt
uvicorn main:app --reload --port 8000

# 6. Dans un troisième terminal : le frontend
cd ..
python3 -m http.server 5500
```

Ouvrez `http://localhost:5500`. `js/config.js` détecte automatiquement
`localhost`/`127.0.0.1` et pointe vers `http://127.0.0.1:8100` (API) et
`http://127.0.0.1:8000` (modèles) sans configuration supplémentaire.

### Un seul processus pour tout tester rapidement

Pour éviter de lancer deux services Python, montez l'API sur le service de
modèles :

```bash
cd server
CIGOGNE_SINGLE_PROCESS=1 uvicorn main:app --reload --port 8000
```

Dans ce mode, définissez `apiBase: "http://127.0.0.1:8000"` dans
`js/config.local.js` (voir `.env.example`) puisque tout répond sur le port
8000. À ne jamais faire en production : le service de modèles doit pouvoir
être mis à l'échelle indépendamment de l'API.

### Lancer les tests

```bash
# Backend (47 tests)
cd server && python3 -m pytest tests/ -v

# Frontend (124 vérifications, trois harnais)
cd ../test && node run.mjs && node run2.mjs && node run3.mjs
```

---

## Production

### Base de données
PostgreSQL recommandé :
```bash
DATABASE_URL=postgresql+psycopg://cigogne:motdepasse@db-host:5432/cigogne
```
Installez le pilote : `pip install psycopg[binary]`. Aucune migration
Alembic n'est fournie (voir `docs/SCHEMA.md`) — `init_db()` crée les tables
manquantes au démarrage sans jamais modifier une table existante.

### Stockage
```bash
CIGOGNE_STORAGE=s3
CIGOGNE_S3_BUCKET=cigogne-production
CIGOGNE_S3_REGION=eu-west-3
# CIGOGNE_S3_ENDPOINT= et CIGOGNE_S3_PUBLIC_BASE= pour R2/MinIO
```
Installez `boto3`. Aucun changement de code ailleurs — `app/storage.py`
bascule automatiquement d'implémentation.

### Secrets
```bash
CIGOGNE_ENV=production
CIGOGNE_SECRET_KEY=$(python3 -c "import secrets;print(secrets.token_urlsafe(48))")
```
Sans `CIGOGNE_SECRET_KEY` explicite, le service **refuse de démarrer** en
production (`app/config.py::get_settings`). Ne jamais committer cette valeur ;
injectez-la via le gestionnaire de secrets de votre plateforme (variables
d'environnement du conteneur, Secrets Manager, Vault…).

### CORS
```bash
CIGOGNE_CORS_ORIGINS=https://cigogne.example,https://www.cigogne.example
CIGOGNE_AI_CORS_ORIGINS=https://api-interne.cigogne.example
```
`CORS=*` est refusé au démarrage dès que `CIGOGNE_ENV=production` — sur les
deux services.

### GPU et file de tâches
- Un worker par carte GPU : `CIGOGNE_GPU_CONCURRENCY=1` sur chacun.
- Lancez le worker séparément du service API :
  ```bash
  CIGOGNE_INLINE_WORKER=0 uvicorn api:app --host 0.0.0.0 --port 8100 --workers 4
  python worker.py     # un processus par GPU, sur la machine qui a le GPU
  ```
- Le worker parle au service de modèles via `CIGOGNE_AI_URL` — mettez l'URL
  interne (pas publique) de ce service.

### Health checks
`GET /api/health` (service API) et `GET /health` (service de modèles)
renvoient chacun un JSON avec un champ `ok`. Branchez votre orchestrateur
(Kubernetes `livenessProbe`/`readinessProbe`, ECS health check, etc.) dessus.
Le premier inclut l'état de la base et de la file de tâches.

### Frontend
Fichiers statiques : servez `index.html`, `js/`, `css/`, `share.html`, et
`data/products.json` (copie de repli hors ligne) depuis un CDN ou un serveur
statique quelconque (Nginx, Cloudflare Pages, S3+CloudFront…). Créez
`js/config.local.js` avant `js/config.js` dans `index.html` pour pointer
vers vos domaines de production :

```html
<script>
  window.CIGOGNE_CONFIG = {
    apiBase: "https://api.cigogne.example",
    aiMode: "gateway",   // recommandé en production : quotas + file d'attente
  };
</script>
<script src="js/config.js"></script>
```

### Démarrage local vs production — résumé
| | Local | Production |
|---|---|---|
| Base de données | SQLite (fichier) | PostgreSQL |
| Stockage | Disque local | S3/R2/MinIO |
| Worker | Dans le processus API | Processus séparé, par GPU |
| CORS | `*` toléré | Origines explicites obligatoires |
| Clé secrète | Générée automatiquement | Obligatoire, via secret manager |
| Processus | `CIGOGNE_SINGLE_PROCESS=1` possible | Toujours séparés |
| Dépendance à une machine de développeur | — | **Aucune** : les trois services sont des conteneurs/processus indépendants, redémarrables séparément |

### Journalisation et redémarrage
Chaque service journalise sur la sortie standard (`logging.basicConfig` dans
`server/api.py` et `server/worker.py`) — laissez votre orchestrateur
collecter et faire tourner les journaux. Un worker qui meurt en cours de
tâche est détecté par `reap_stalled()` (appelée toutes les 60 s) : la tâche
est remise en file automatiquement, jusqu'à `CIGOGNE_JOB_MAX_ATTEMPTS`.

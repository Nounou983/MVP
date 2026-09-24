# Schéma de base de données — La Cigogne D'Ailleurs

Service concerné : l'API (`server/app/models.py`). Le service de modèles
(`server/main.py`) n'a pas de base de données — il est sans état.

Moteur : SQLite en développement (`server/data/cigogne.db`), PostgreSQL
recommandé en production via `DATABASE_URL`. Les modèles sont écrits en
SQLAlchemy 2.x et fonctionnent sur les deux sans changement de code.

Aucune migration Alembic n'est fournie : `init_db()` (appelée au démarrage
des deux services) exécute `Base.metadata.create_all()`, qui crée les tables
manquantes mais ne modifie jamais une table existante. Pour un changement de
schéma en production, ajoutez Alembic (`pip install alembic`) et générez une
migration à partir de `app/models.py` — la table `schema_version` existe déjà
pour que la migration future puisse savoir d'où elle part.

## Vue d'ensemble

```
users ──< projects ──< project_versions
  │           │
  │           └──< assets (project_id optionnel)
  │
  ├──< favorites
  ├──< collections ──< collection_items
  ├──< shares ──< share_comments
  ├──< assets (user_id)
  ├──< usage_counters
  └──< jobs (user_id optionnel — une tâche peut être anonyme)

analytics_events (user_id optionnel, jamais de clé étrangère stricte :
                   un événement doit survivre à la suppression du compte)
```

## Tables

### `users`
Un compte. `password_hash` est au format `pbkdf2_sha256$itérations$sel$empreinte`
(voir `app/security.py`) — jamais de mot de passe en clair, jamais de
dépendance externe pour le hachage. `token_epoch` s'incrémente à chaque
changement de mot de passe ou déconnexion globale : tout jeton émis avant
devient invalide sans avoir à le lister nulle part (comparaison d'un entier).
`plan` référence une clé de `PLANS` dans `app/config.py` (`free` | `pro` |
`business`) — un webhook de facturation n'a qu'à modifier cette colonne.

### `projects`
Un document de composition. `state` (JSON) contient l'intégralité de la
scène — voir `docs/PROJECT_SCHEMA.md`. `item_count` et `total_price` sont des
colonnes dénormalisées, recalculées à chaque écriture (`_state_stats()` dans
`app/routers/projects.py`), pour lister les projets sans désérialiser chaque
`state`. `deleted_at` est une suppression douce : le projet disparaît des
listes mais n'est physiquement effacé que si `purge=true` est passé à la
suppression.

### `project_versions`
Historique des vingt derniers `state`, le plus récent en premier après tri
par date. Chaque écriture de projet pousse l'ancien `state` ici avant de
remplacer le courant ; au-delà de vingt versions, les plus anciennes sont
supprimées. Sert de filet de récupération, pas de contrôle de version complet.

### `assets`
Un fichier stocké (`kind` : `image` | `render` | `model` | `mask` |
`thumbnail`). `storage_key` est le chemin dans le backend de stockage
(`app/storage.py`), jamais dérivé du nom fourni par la personne —
voir `app/storage.py::build_key`. `project_id` est nullable : un fichier
peut exister avant que le projet ne soit enregistré.

### `favorites`
Une paire `(user_id, product_id)`, contrainte d'unicité pour qu'ajouter deux
fois le même favori soit sans effet plutôt qu'une erreur.

### `collections` / `collection_items`
Une collection nommée et ses produits, ordonnés par `position`. Contrainte
d'unicité sur `(collection_id, product_id)` : un produit n'apparaît qu'une
fois par collection.

### `shares` / `share_comments`
Un lien de partage (`token`, 48 caractères aléatoires) donne un accès en
lecture (`permission="view"`) ou lecture + commentaires
(`permission="comment"`) à **un seul projet**. `revoked` et `expires_at`
sont vérifiés à chaque lecture publique (`app/routers/shares.py::_resolve`).
Les commentaires n'existent que si la permission le permet ; l'écriture est
refusée sinon, même avec un jeton de lien valide.

### `jobs`
Une tâche de traitement IA. `status` suit
`queued → running → succeeded | failed | cancelled`, avec `attempts` et
`max_attempts` pour les nouvelles tentatives automatiques
(`app/services/jobs.py`). `params` et `result` sont des JSON libres — leur
forme dépend de `type` (`analyze` | `select-mask` | `remove` | `inpaint`),
documentée dans `docs/API.md`. `heartbeat_at` permet de détecter un worker
mort (`reap_stalled()`) et de remettre la tâche en file plutôt que de la
laisser bloquée indéfiniment.

### `usage_counters`
Un compteur mensuel par utilisateur et par métrique (`ai_jobs`, `exports`,
`storage_bytes`), remis à zéro implicitement par le changement de `period`
(`AAAA-MM`). Lu par `app/services/entitlements.py` pour appliquer les quotas
de la formule.

### `analytics_events`
Un événement d'usage, dont le nom doit figurer dans la liste blanche
`ALLOWED_EVENTS` et dont les propriétés sont filtrées par `ALLOWED_PROPS`
(`app/routers/system.py`) — tout le reste est silencieusement rejeté avant
d'atteindre la base. `session_id` est un identifiant tournant, jamais une
donnée personnelle.

### `schema_version`
Une seule ligne, mise à jour à chaque démarrage. Existe pour qu'une future
migration Alembic sache d'où partir.

## Index notables
- `projects (user_id, updated_at)` — liste des projets d'un compte, triée.
- `jobs (status, priority, created_at)` — la requête de la file d'attente
  (`claim_next()`) est un scan de cet index, pas de la table entière.
- Unicité `(user_id, product_id)` sur `favorites`,
  `(collection_id, product_id)` sur `collection_items`,
  `(user_id, period, metric)` sur `usage_counters`.

## Isolation des données
Aucune requête de lecture ou d'écriture ne filtre sur autre chose qu'un
`user_id` correspondant à la personne authentifiée (voir les fonctions
`_owned()` / `_owned_collection()` dans les routeurs). Un identifiant de
projet, de fichier ou de collection appartenant à quelqu'un d'autre renvoie
404, jamais 403 — pour ne pas confirmer qu'un identifiant existe.

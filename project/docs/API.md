# Documentation API — La Cigogne D'Ailleurs

Deux services, deux bases d'URL. Une documentation interactive complète
(schémas, essai en direct) est aussi générée automatiquement par FastAPI :

- Service API : `http://localhost:8100/docs`
- Service de modèles : `http://localhost:8000/docs`

Ce document donne la vue d'ensemble ; `/docs` fait foi pour le détail exact
de chaque champ.

---

## Service API (`server/api.py`, port 8100)

Toutes les routes sont préfixées `/api`. Authentification par en-tête
`Authorization: Bearer <jeton>`, sauf mention contraire.

### Authentification — `/api/auth`

| Route | Méthode | Auth | Description |
|---|---|---|---|
| `/auth/register` | POST | — | `{email, password, display_name?}` → session (jetons + utilisateur) |
| `/auth/login` | POST | — | `{email, password}` → session |
| `/auth/refresh` | POST | — | `{refresh_token}` → nouveau jeton d'accès |
| `/auth/me` | GET | requise | Profil + formule + usage courant |
| `/auth/me` | PATCH | requise | `{display_name?, locale?}` |
| `/auth/password` | POST | requise | `{current_password, new_password}` — révoque tous les jetons existants |
| `/auth/logout-all` | POST | requise | Révoque tous les jetons sans changer le mot de passe |

Limite de débit : 10 requêtes/minute/IP sur ce groupe de routes.

### Projets — `/api/projects`

| Route | Méthode | Description |
|---|---|---|
| `/projects` | GET | Liste des projets de la personne connectée (pagination `limit`/`offset`) |
| `/projects` | POST | `{name, state, room_key?, thumbnail_key?}` → crée un projet |
| `/projects/{id}` | GET | Document complet, y compris `state` |
| `/projects/{id}` | PATCH | Met à jour un ou plusieurs champs ; pousse l'ancien `state` dans l'historique |
| `/projects/{id}` | DELETE | Suppression douce (`?purge=true` efface aussi les fichiers) |
| `/projects/{id}/duplicate` | POST | Copie complète, nouveau `id` |
| `/projects/{id}/versions` | GET | Historique (20 dernières versions) |
| `/projects/{id}/versions/{vid}/restore` | POST | Restaure une version antérieure |

Un projet appartenant à quelqu'un d'autre renvoie **404**, jamais 403.
Un `state` de plus de 6 Mo (configurable) est refusé avec **413**.
Au-delà du quota de la formule, **402** avec un message explicite.

### Fichiers — `/api/files`

| Route | Méthode | Description |
|---|---|---|
| `/files/images` | POST | Formulaire multipart `file` (+ `kind`, `project_id` optionnels). JPG/PNG/WebP, magic-bytes vérifiés. |
| `/files/models` | POST | Formulaire multipart `file`. GLB/GLTF, signature vérifiée. |
| `/files/{key}` | GET | Téléchargement. Accessible soit via jeton (propriétaire), soit via lien signé `?t=` (utilisé dans les `<img src>`). |
| `/files/{asset_id}` | DELETE | Supprime le fichier et son entrée. |

Toute image dépassant 18 Mo (configurable) est rejetée **avant** d'être
entièrement lue en mémoire. Le nom de fichier fourni n'est jamais utilisé
comme chemin de stockage — voir `app/storage.py::build_key`.

### Catalogue — `/api/products`, favoris, collections

| Route | Méthode | Description |
|---|---|---|
| `/products` | GET | `?family=`, `?search=`. Pas d'authentification requise. Réponse mise en cache (`ETag`). |
| `/products/{id}` | GET | Fiche complète d'un produit. |
| `/favorites` | GET / POST / DELETE `/favorites/{id}` | Favoris de la personne connectée. |
| `/collections` | GET / POST | Collections nommées. |
| `/collections/{id}` | PATCH / DELETE | Renommer / supprimer. |
| `/collections/{id}/items` | POST | Ajoute un produit. |
| `/collections/{id}/items/{pid}` | DELETE | Retire un produit. |

### Partage — `/api/projects/{id}/shares`, `/api/shared/{token}`

| Route | Méthode | Auth | Description |
|---|---|---|---|
| `/projects/{id}/shares` | POST | requise | `{permission: "view"\|"comment", expires_in_days?}` → lien |
| `/projects/{id}/shares` | GET | requise | Liste des liens actifs du projet |
| `/shares/{id}` | DELETE | requise | Révoque un lien |
| `/shared/{token}` | GET | **publique** | Aperçu en lecture seule du projet |
| `/shared/{token}/comments` | GET | **publique** | Commentaires (si `permission="comment"`) |
| `/shared/{token}/comments` | POST | **publique** | Ajoute un commentaire (si autorisé) |

Le partage de projet (`share`) et les commentaires (`comments`) sont des
fonctionnalités de formule payante (`app/services/entitlements.py`) —
**402** sinon.

### File de tâches IA — `/api/jobs`

| Route | Méthode | Description |
|---|---|---|
| `/jobs` | POST | `{type, image_key, mask_key?, x?, y?, label?, project_id?}` → tâche `queued` |
| `/jobs/{id}` | GET | Statut, progression, résultat (URLs signées incluses) |
| `/jobs` | GET | Dernières tâches de la personne |
| `/jobs/{id}/cancel` | POST | Annulation (immédiate si en file, best-effort si en cours) |

`type` ∈ `analyze` \| `select-mask` \| `remove` \| `inpaint`. `image_key` et
`mask_key` doivent appartenir à la personne authentifiée (préfixe
`{user_id}/…`), sinon **403**.

### Passerelle compatible — `/api/ai/*`

Mêmes contrats multipart que le service de modèles d'origine
(`/analyze`, `/select-mask`, `/inpaint`, `/remove`), mais en passant par la
file d'attente : mise en cache de l'image, job créé, attente bloquante
jusqu'au résultat (ou **504** avec l'identifiant de tâche si le traitement
dépasse 240 s — la tâche continue en arrière-plan). Utilisable sans compte.
Un résultat de tâche mal formé renvoie **502** avec un message explicite,
jamais une erreur non gérée.

### Système — `/api/health`, `/api/plans`, `/api/analytics`, `/api/admin/*`

| Route | Méthode | Auth | Description |
|---|---|---|---|
| `/health` | GET | — | État du service, de la base, de la file d'attente |
| `/plans` | GET | — | Formules disponibles et leurs limites |
| `/account/entitlements` | GET | requise | Formule et usage du mois courant |
| `/analytics/events` | POST | optionnelle | Lot d'événements (liste blanche stricte) |
| `/admin/stats` | GET | **admin** | Statistiques globales |
| `/admin/jobs/cleanup` | POST | **admin** | Purge des tâches terminées anciennes |

Aucune route de debug n'est exposée publiquement ; `/admin/*` exige
`user.is_admin = true` (**403** sinon).

---

## Service de modèles (`server/main.py`, port 8000)

**Inchangé** par Phase 6, à deux ajouts près : les origines CORS sont
désormais configurables par variable d'environnement (`CIGOGNE_AI_CORS_ORIGINS`,
refusées à `*` en production), et `CIGOGNE_SINGLE_PROCESS=1` peut monter
l'API sur ce même processus pour le développement. Les sept routes d'origine
sont intactes :

| Route | Méthode | Description |
|---|---|---|
| `/` | GET | Bannière du service |
| `/health` | GET | État, modèles chargés |
| `/analyze` | POST | Profondeur + segmentation d'une photo |
| `/select-mask` | POST | Masque d'objet au point cliqué (SAM) |
| `/inpaint` | POST | Reconstruction remove-only (RORem/LaMa) à partir d'un masque fourni |
| `/remove` | POST | Sélection + reconstruction en un seul appel |
| `/inpaint-status` | GET | Disponibilité des moteurs de reconstruction |

Consultez `/docs` sur ce service pour le détail exact des champs — ils n'ont
pas changé.

---

## Codes d'erreur communs

| Code | Signification |
|---|---|
| 400 | Requête malformée |
| 401 | Authentification absente ou jeton invalide/expiré |
| 402 | Quota de formule dépassé ou fonctionnalité non incluse |
| 403 | Action interdite (ex. route admin) |
| 404 | Ressource absente **ou appartenant à quelqu'un d'autre** |
| 409 | Conflit (ex. adresse e-mail déjà utilisée) |
| 413 | Fichier ou document trop volumineux |
| 415 | Type de fichier non reconnu |
| 429 | Limite de débit atteinte (en-tête `Retry-After`) |
| 502 | Le service IA a échoué ou renvoyé un résultat inattendu |
| 504 | La passerelle IA synchrone a dépassé son délai (la tâche continue) |

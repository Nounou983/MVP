# Schéma du document de projet (`state`)

Ce document est ce qui est sérialisé par `js/projects.js::serialize()` et
stocké dans `projects.state` (colonne JSON). C'est la totalité d'une
composition : photo, meubles, caméra, éclairage, trace des retouches IA.

```json
{
  "schema": 1,
  "app_version": "6.0.0",
  "name": "Salon Tipaza",
  "room": {
    "key": "3f2a.../image/9c1b....jpg",
    "file_name": "salon.jpg",
    "width": 4032,
    "height": 3024
  },
  "items": [
    {
      "uid": "it_7f3a91",
      "entryId": "sofa-3",
      "catId": "sofa",
      "name": "Canapé Tipaza 3 places",
      "price": 89000,
      "color": "#5A6270",
      "w": 2.2,
      "d": 0.95,
      "x": 812.4,
      "y": 540.1,
      "rot": 0,
      "scale": 1,
      "z": 3,
      "custom3D": false,
      "modelKey": null,
      "rx": 0.412,
      "ry": 0.588
    }
  ],
  "camera": {
    "fov": 52, "pitch": 0.31, "height": 1.55, "depth": 4.2,
    "targetY": null, "auto": true, "mode": "photo"
  },
  "lighting": {
    "auto": true, "temperature": 0, "exposure": 0,
    "key": 1, "ambient": 0.6, "fill": 0.4
  },
  "analysis": {
    "present": true,
    "width": 4032, "height": 3024,
    "floor_source": "segformer",
    "furniture_count": 2
  },
  "room_model": {
    "confidence": 0.71,
    "reliable": true,
    "dimensions": { "width": 4.1, "depth": 3.6, "height": 2.7, "unit": "m" }
  },
  "ai_edits": [
    { "kind": "remove", "at": "2026-09-16T10:04:22.104Z" }
  ],
  "updated_at": "2026-09-16T10:05:03.881Z"
}
```

## Champs

| Chemin | Type | Notes |
|---|---|---|
| `schema` | entier | Version du document. `1` actuellement. Une future version incompatible doit l'incrémenter ; `Projects.applyDocument` peut alors migrer sur lecture. |
| `room.key` | chaîne \| `null` | Clé de stockage de la photo côté API (`assets.storage_key`). `null` si le projet n'a jamais été enregistré en ligne. |
| `items[].uid` | chaîne | Identifiant local, stable pour la session mais pas garanti entre deux ouvertures. |
| `items[].entryId` | chaîne | Identifiant produit du catalogue (`products.json`). |
| `items[].x`, `.y` | nombre | Coordonnées **canvas**, valables uniquement pour la taille d'affichage au moment de la sauvegarde. |
| `items[].rx`, `.ry` | nombre 0–1 | Coordonnées **relatives à la photo**, indépendantes de la taille d'écran. C'est ce que `Projects.applyDocument` utilise pour repositionner les meubles à la réouverture — `x`/`y` sont recalculés à partir d'elles. |
| `items[].rot` | radians | Rotation autour du centre. |
| `items[].z` | entier | Ordre d'empilement (dernier posé = devant). |
| `camera` | objet \| `null` | Calibration de la vue 3D (`js/phase3.js`). `null` si la 3D n'a jamais été ouverte. |
| `lighting` | objet \| `null` | Réglages d'éclairage de la vue 3D. |
| `analysis.present` | booléen | `true` si une analyse IA a été exécutée sur cette photo. Le détail complet (masques, profondeur) n'est pas conservé dans le document — seuls les champs utiles à la reprise le sont ; relancer l'analyse régénère le reste. |
| `room_model` | objet \| `null` | Résultat de la reconstruction spatiale (`js/room-model.js`). Absent si l'analyse n'a jamais atteint le seuil de confiance minimal. |
| `ai_edits` | tableau | Trace des retouches IA (repérage, retrait), conservée pour l'historique — les 40 dernières entrées seulement. |

## Compatibilité
Les champs inconnus d'une version antérieure sont ignorés à la lecture ;
un champ absent d'un document plus ancien prend sa valeur par défaut. Aucun
champ n'est actuellement supprimé entre versions.

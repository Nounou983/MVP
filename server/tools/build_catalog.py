#!/usr/bin/env python3
"""Build the product catalogue.

One source of truth, two outputs:
  server/data/products.json   — served by the API
  data/products.json          — bundled copy so the frontend works offline

Run after editing SPEC:
    python server/tools/build_catalog.py
"""
from __future__ import annotations

import json
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = SERVER_DIR.parent

SCHEMA_VERSION = 2
SELLER = {
    "id": "cigogne",
    "name": "La Cigogne D'Ailleurs",
    "country": "DZ",
    "contact_email": "contact@lacigognedailleurs.dz",
    "contact_phone": "+213 25 00 00 00",
}

FAMILIES = [
    {"id": "all", "label": "Tout"},
    {"id": "seating", "label": "Assises"},
    {"id": "tables", "label": "Tables"},
    {"id": "bedroom", "label": "Chambre"},
    {"id": "decor", "label": "Déco"},
]

COLOR_NAMES = {
    "#5A6270": "Bleu ardoise", "#2F3339": "Anthracite", "#9B8B7A": "Lin", "#6E7F6A": "Vert olivier",
    "#B4614C": "Terracotta", "#3A4049": "Encre", "#8C6A4F": "Noyer", "#C0B4A4": "Sable",
    "#2E7D64": "Vert cèdre", "#1F2429": "Noir mat", "#C9A227": "Ambre", "#3A3F45": "Graphite",
    "#D8CDBF": "Craie", "#6B4DF6": "Indigo", "#6E4E34": "Chêne foncé", "#4B5563": "Gris acier",
    "#9CA3AF": "Gris perle", "#C87A34": "Safran", "#7C8B9A": "Bleu brume", "#8E9B6C": "Olive claire",
    "#B5ADA0": "Grège", "#E8B33A": "Or doux", "#F2EDE3": "Ivoire", "#2F8A63": "Vert feuille",
    "#4C744E": "Vert forêt", "#7FA06B": "Vert tendre",
}

# id, base, family, name, w, d, h, price, colors, blurb, materials, sku, lead_days, stock
SPEC = [
    ("sofa-3", "sofa", "seating", "Canapé Tipaza 3 places", 2.20, 0.95, 0.82, 89000,
     ["#5A6270", "#2F3339", "#9B8B7A", "#6E7F6A"],
     "Assise profonde, accoudoirs larges.",
     ["Tissu polyester recyclé", "Mousse HR 30 kg/m³", "Piètement hêtre massif"], "CIG-SOF-220", 14, 6),
    ("sofa-2", "sofa", "seating", "Canapé Tipaza 2 places", 1.60, 0.90, 0.82, 67500,
     ["#9B8B7A", "#5A6270", "#2F3339", "#B4614C"],
     "Le même confort pour les petits salons.",
     ["Tissu polyester recyclé", "Mousse HR 30 kg/m³", "Piètement hêtre massif"], "CIG-SOF-160", 14, 9),
    ("armchair", "armchair", "seating", "Fauteuil Oran", 0.95, 0.90, 0.86, 34500,
     ["#3A4049", "#8C6A4F", "#6E7F6A", "#C0B4A4"],
     "Dossier enveloppant, pieds en bois.",
     ["Velours côtelé", "Contreplaqué cintré", "Chêne huilé"], "CIG-ARM-095", 10, 12),
    ("chair", "chair", "seating", "Chaise Sahel", 0.45, 0.45, 0.84, 7400,
     ["#2E7D64", "#1F2429", "#C9A227", "#B4614C"],
     "Coque moulée, empilable.",
     ["Polypropylène teinté masse", "Acier époxy"], "CIG-CHR-045", 4, 48),
    ("stool", "chair", "seating", "Tabouret Blida", 0.38, 0.38, 0.65, 4900,
     ["#C9A227", "#2E7D64", "#1F2429", "#9B8B7A"],
     "Appoint léger, se range sous la table.",
     ["Frêne massif", "Vernis mat"], "CIG-STL-038", 4, 60),

    ("coffee", "table", "tables", "Table basse Annaba", 1.10, 0.60, 0.40, 18900,
     ["#8C6A4F", "#3A3F45", "#D8CDBF", "#6B4DF6"],
     "Plateau chêne, structure fine.",
     ["Placage chêne", "Acier laqué"], "CIG-TBL-110", 7, 15),
    ("dining", "table", "tables", "Table à manger Béjaïa", 1.60, 0.90, 0.75, 54000,
     ["#6E4E34", "#3A3F45", "#D8CDBF", "#8C6A4F"],
     "Six couverts, plateau massif.",
     ["Chêne massif huilé", "Acier noir"], "CIG-TBL-160", 21, 4),
    ("tvstand", "tvstand", "tables", "Meuble TV Tlemcen", 1.80, 0.40, 0.45, 31500,
     ["#4B5563", "#8C6A4F", "#D8CDBF", "#1F2429"],
     "Deux portes, passe-câbles.",
     ["Panneau mélaminé", "Charnières à frein"], "CIG-TVS-180", 10, 8),

    ("bed-160", "bed", "bedroom", "Lit Ghardaïa 160×200", 1.60, 2.00, 1.05, 74000,
     ["#9CA3AF", "#8C6A4F", "#2F3339", "#C0B4A4"],
     "Tête de lit capitonnée.",
     ["Tissu chiné", "Sommier à lattes", "Pin massif"], "CIG-BED-160", 18, 5),
    ("bed-90", "bed", "bedroom", "Lit Ghardaïa 90×190", 0.90, 1.90, 1.00, 42000,
     ["#C0B4A4", "#9CA3AF", "#6E7F6A", "#2F3339"],
     "Format simple, sommier inclus.",
     ["Tissu chiné", "Sommier à lattes", "Pin massif"], "CIG-BED-090", 18, 7),

    ("rug-160", "rug", "decor", "Tapis Ghardaïa 160×110", 1.60, 1.10, 0.02, 22000,
     ["#C87A34", "#7C8B9A", "#8E9B6C", "#B5ADA0"],
     "Laine tissée main.",
     ["Laine 100 %", "Trame coton"], "CIG-RUG-160", 6, 11),
    ("rug-240", "rug", "decor", "Grand tapis 240×170", 2.40, 1.70, 0.02, 39000,
     ["#7C8B9A", "#C87A34", "#8E9B6C", "#B5ADA0"],
     "Assez large pour un salon entier.",
     ["Laine 100 %", "Trame coton"], "CIG-RUG-240", 6, 6),
    ("lamp", "lamp", "decor", "Lampadaire Sétif", 0.35, 0.35, 1.55, 9900,
     ["#E8B33A", "#F2EDE3", "#2F3339", "#6E7F6A"],
     "Abat-jour tissu, variateur.",
     ["Lin", "Laiton brossé"], "CIG-LMP-155", 5, 22),
    ("plant", "plant", "decor", "Plante d'intérieur", 0.45, 0.45, 0.80, 4200,
     ["#2F8A63", "#4C744E", "#7FA06B"],
     "Pot en terre cuite inclus.",
     ["Terre cuite", "Feuillage artificiel"], "CIG-PLT-080", 3, 40),
    ("plant-xl", "plant", "decor", "Grande plante", 0.70, 0.70, 1.45, 8600,
     ["#4C744E", "#2F8A63", "#7FA06B"],
     "Pour habiller un angle vide.",
     ["Terre cuite", "Feuillage artificiel"], "CIG-PLT-145", 3, 18),
]

# Placement hints consumed by js/placement.js. `anchor` decides how a piece
# meets the room; `clearance` is the free space it needs in front to be usable.
PLACEMENT = {
    "sofa": {"anchor": "wall", "clearance_front": 0.75, "against_wall": True, "stack": False},
    "armchair": {"anchor": "floor", "clearance_front": 0.60, "against_wall": False, "stack": False},
    "chair": {"anchor": "floor", "clearance_front": 0.45, "against_wall": False, "stack": True},
    "table": {"anchor": "floor", "clearance_front": 0.70, "against_wall": False, "stack": False},
    "bed": {"anchor": "wall", "clearance_front": 0.60, "against_wall": True, "stack": False},
    "tvstand": {"anchor": "wall", "clearance_front": 0.90, "against_wall": True, "stack": False},
    "rug": {"anchor": "floor", "clearance_front": 0.0, "against_wall": False, "stack": False,
            "under_furniture": True},
    "lamp": {"anchor": "floor", "clearance_front": 0.20, "against_wall": False, "stack": False},
    "plant": {"anchor": "floor", "clearance_front": 0.20, "against_wall": False, "stack": False},
}


def variant(hex_code: str, product_id: str, index: int) -> dict:
    return {
        "id": f"{product_id}--{index}",
        "name": COLOR_NAMES.get(hex_code.upper(), "Finition"),
        "hex": hex_code,
        "available": True,
    }


def build() -> dict:
    products = []
    for (pid, base, family, name, w, d, h, price, colors, blurb, materials, sku, lead, stock) in SPEC:
        products.append({
            "id": pid,
            "sku": sku,
            "base": base,                      # sprite + 3D volume key
            "family": family,
            "name": name,
            "blurb": blurb,
            "dimensions": {"width": w, "depth": d, "height": h, "unit": "m"},
            # Legacy flat fields: older frontend code reads w/d directly.
            "w": w, "d": d, "h": h,
            "price": price,
            "currency": "DZD",
            "color": colors[0],
            "colors": colors,
            "variants": [variant(c, pid, i) for i, c in enumerate(colors)],
            "materials": materials,
            "care": "Dépoussiérer régulièrement, éviter l'exposition directe au soleil.",
            "seller": SELLER,
            "manufacturer": "Atelier Cigogne",
            "availability": {
                "status": "in_stock" if stock > 0 else "made_to_order",
                "stock": stock,
                "lead_time_days": lead,
                "regions": ["DZ"],
            },
            "product_url": f"https://lacigognedailleurs.dz/produits/{pid}",
            "cta": {"type": "contact", "label": "Demander un devis"},
            "assets": {
                # Aucune photo ni modèle 3D sous licence n'est fourni avec ce
                # projet : `is_placeholder` le dit explicitement, pour que
                # l'interface (et vous) ne confondiez jamais le sprite de
                # démonstration avec un vrai visuel produit.
                #
                # Pour remplacer par de vrais visuels, éditez SPEC ci-dessus
                # ou modifiez directement server/data/products.json puis
                # relancez ce script — aucune autre partie du code ne change :
                #   "thumbnail": "https://cdn.example.com/sofa-3/thumb.jpg",
                #   "photos": ["https://cdn.example.com/sofa-3/1.jpg", "..."],
                #   "model": {"url": "https://cdn.example.com/sofa-3.glb", ...}
                "thumbnail": None,
                "photos": [],
                "sprite": {"kind": "vector", "maker": base},
                "model": {"url": None, "format": "glb", "up_axis": "Y", "scale": 1.0},
                "is_placeholder": True,
                "placeholder_reason": "Aucun visuel sous licence fourni : repli sur le sprite vectoriel.",
            },
            "placement": PLACEMENT.get(base, {"anchor": "floor", "clearance_front": 0.4}),
            "tags": [family, base],
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "currency": "DZD",
        "families": FAMILIES,
        "seller": SELLER,
        "products": products,
    }


def main() -> None:
    payload = build()
    targets = [SERVER_DIR / "data" / "products.json", PROJECT_DIR / "data" / "products.json"]
    for target in targets:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {target.relative_to(PROJECT_DIR)} ({len(payload['products'])} products)")


if __name__ == "__main__":
    main()

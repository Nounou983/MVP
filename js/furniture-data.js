/* =========================================================
   La Cigogne D'Ailleurs — Catalogue
   Sprites vectoriels + données produit (prix, couleurs, familles).
   Les "makers" SVG servent à la fois de vignette catalogue et de
   rendu dans la pièce : ce que l'on voit est ce que l'on place.
   ========================================================= */

function shade(hex, pct) {
  const n = parseInt(hex.slice(1), 16);
  const amt = Math.round(2.55 * pct);
  const r = Math.min(255, Math.max(0, (n >> 16) + amt));
  const g = Math.min(255, Math.max(0, ((n >> 8) & 0xff) + amt));
  const b = Math.min(255, Math.max(0, (n & 0xff) + amt));
  return "#" + ((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1);
}

function svgUrl(svg) {
  return "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
}

const MAKERS = {
  sofa(c) { return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 150">
    <defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop stop-color="${shade(c,18)}"/><stop offset="1" stop-color="${shade(c,-10)}"/></linearGradient><filter id="s"><feDropShadow dx="0" dy="7" stdDeviation="7" flood-opacity=".18"/></filter></defs>
    <ellipse cx="120" cy="130" rx="88" ry="10" fill="#000" opacity=".10"/><g filter="url(#s)"><rect x="38" y="55" width="164" height="55" rx="14" fill="url(#g)"/><rect x="45" y="35" width="150" height="48" rx="16" fill="${shade(c,10)}"/><rect x="25" y="43" width="32" height="69" rx="14" fill="${shade(c,-18)}"/><rect x="183" y="43" width="32" height="69" rx="14" fill="${shade(c,-18)}"/><path d="M63 48h48v38H63zM117 48h48v38h-48z" fill="${shade(c,20)}" opacity=".75"/><path d="M58 111h124" stroke="${shade(c,-28)}" stroke-width="7" stroke-linecap="round"/></g>
  </svg>`; },
  armchair(c) { return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 170 160"><defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop stop-color="${shade(c,17)}"/><stop offset="1" stop-color="${shade(c,-12)}"/></linearGradient></defs><ellipse cx="85" cy="141" rx="55" ry="8" fill="#000" opacity=".10"/><rect x="42" y="58" width="86" height="65" rx="13" fill="url(#g)"/><rect x="48" y="36" width="74" height="59" rx="15" fill="${shade(c,12)}"/><rect x="29" y="45" width="31" height="78" rx="14" fill="${shade(c,-20)}"/><rect x="110" y="45" width="31" height="78" rx="14" fill="${shade(c,-20)}"/><rect x="61" y="62" width="48" height="42" rx="12" fill="${shade(c,24)}" opacity=".72"/><path d="M50 124l-8 14M120 124l8 14" stroke="#5b5149" stroke-width="6" stroke-linecap="round"/></svg>`; },
  bed(c) { return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 190 230"><defs><linearGradient id="b" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#faf9f5"/><stop offset="1" stop-color="#ded9cf"/></linearGradient><filter id="s"><feDropShadow dx="0" dy="7" stdDeviation="7" flood-opacity=".16"/></filter></defs><ellipse cx="95" cy="211" rx="62" ry="9" fill="#000" opacity=".10"/><g filter="url(#s)"><rect x="26" y="23" width="138" height="184" rx="13" fill="${shade(c,-12)}"/><rect x="34" y="31" width="122" height="52" rx="11" fill="${shade(c,-20)}"/><rect x="37" y="76" width="116" height="118" rx="10" fill="url(#b)"/><rect x="48" y="88" width="43" height="28" rx="8" fill="#fff"/><rect x="99" y="88" width="43" height="28" rx="8" fill="#fff"/><path d="M45 125c20 8 38 10 50 9s30-1 50-9v43c-26 10-75 10-100 0z" fill="${shade(c,5)}" opacity=".34"/><path d="M34 196h122" stroke="${shade(c,-26)}" stroke-width="6"/></g></svg>`; },
  table(c) { return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 210 150"><defs><linearGradient id="t" x1="0" y1="0" x2="1" y2="1"><stop stop-color="${shade(c,24)}"/><stop offset="1" stop-color="${shade(c,-12)}"/></linearGradient></defs><ellipse cx="105" cy="128" rx="68" ry="9" fill="#000" opacity=".10"/><path d="M48 55L105 30l57 25-57 25z" fill="url(#t)"/><path d="M48 55v16l57 26V80zM162 55v16l-57 26V80z" fill="${shade(c,-15)}"/><path d="M65 76l-8 48M145 76l8 48M105 92v33" stroke="${shade(c,-28)}" stroke-width="6" stroke-linecap="round"/><path d="M61 124h8M141 124h8M101 124h8" stroke="#5b5149" stroke-width="6" stroke-linecap="round"/></svg>`; },
  chair(c) { return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 140 160"><defs><linearGradient id="c" x1="0" y1="0" x2="1" y2="1"><stop stop-color="${shade(c,20)}"/><stop offset="1" stop-color="${shade(c,-14)}"/></linearGradient></defs><ellipse cx="70" cy="140" rx="39" ry="7" fill="#000" opacity=".10"/><path d="M39 68h62l-8 47H47z" fill="url(#c)"/><rect x="44" y="35" width="52" height="47" rx="9" fill="${shade(c,10)}"/><path d="M48 114l-7 28M92 114l7 28M57 114l-5 28M83 114l5 28" stroke="#625b53" stroke-width="5" stroke-linecap="round"/><path d="M51 74h38" stroke="${shade(c,28)}" stroke-width="5" opacity=".7"/></svg>`; },
  lamp(c) { return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 150 180"><defs><radialGradient id="l"><stop stop-color="#fff5bf"/><stop offset=".45" stop-color="${c}"/><stop offset="1" stop-color="${shade(c,-18)}"/></radialGradient></defs><ellipse cx="75" cy="158" rx="35" ry="7" fill="#000" opacity=".10"/><path d="M75 47v93" stroke="#6f665c" stroke-width="7"/><path d="M43 57h64l-12 39H55z" fill="url(#l)"/><ellipse cx="75" cy="57" rx="32" ry="9" fill="${shade(c,12)}"/><path d="M57 143h36l10 13H47z" fill="#625b53"/><circle cx="75" cy="53" r="8" fill="#fff6c8" opacity=".9"/></svg>`; },
  plant(c) { return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 180"><defs><linearGradient id="p" x1="0" y1="0" x2="1" y2="1"><stop stop-color="${shade(c,18)}"/><stop offset="1" stop-color="${shade(c,-22)}"/></linearGradient></defs><ellipse cx="80" cy="160" rx="38" ry="7" fill="#000" opacity=".10"/><path d="M58 109h44l-7 48H65z" fill="#a36b3c"/><path d="M55 110h50l-8 10H63z" fill="#74482d"/><path d="M80 112V54M80 92L49 67M80 84l34-31M80 75L60 39M80 63l29-11" stroke="#4c744e" stroke-width="5" stroke-linecap="round"/><ellipse cx="46" cy="63" rx="24" ry="12" transform="rotate(28 46 63)" fill="url(#p)"/><ellipse cx="113" cy="51" rx="24" ry="12" transform="rotate(-28 113 51)" fill="url(#p)"/><ellipse cx="57" cy="37" rx="23" ry="12" transform="rotate(12 57 37)" fill="url(#p)"/><ellipse cx="109" cy="80" rx="23" ry="12" transform="rotate(25 109 80)" fill="url(#p)"/></svg>`; },
  rug(c) { return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 220 150"><defs><linearGradient id="r" x1="0" y1="0" x2="1" y2="1"><stop stop-color="${shade(c,12)}"/><stop offset="1" stop-color="${shade(c,-14)}"/></linearGradient></defs><ellipse cx="110" cy="128" rx="88" ry="8" fill="#000" opacity=".08"/><path d="M24 46l86-27 86 27-86 27z" fill="url(#r)"/><path d="M24 46v60l86 28V73zM196 46v60l-86 28V73z" fill="${shade(c,-10)}"/><path d="M45 49l65-20 65 20-65 20z" fill="none" stroke="${shade(c,30)}" stroke-width="4" opacity=".8"/><path d="M66 51l44-14 44 14-44 14z" fill="none" stroke="${shade(c,-28)}" stroke-width="4" opacity=".7"/></svg>`; },
  tvstand(c) { return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 145"><defs><linearGradient id="v" x1="0" y1="0" x2="0" y2="1"><stop stop-color="${shade(c,14)}"/><stop offset="1" stop-color="${shade(c,-16)}"/></linearGradient></defs><ellipse cx="120" cy="122" rx="83" ry="8" fill="#000" opacity=".10"/><rect x="30" y="48" width="180" height="60" rx="7" fill="url(#v)"/><rect x="43" y="59" width="67" height="38" rx="4" fill="${shade(c,18)}"/><rect x="130" y="59" width="67" height="38" rx="4" fill="${shade(c,18)}"/><path d="M52 108v14M188 108v14" stroke="#5e554d" stroke-width="6" stroke-linecap="round"/><rect x="56" y="18" width="128" height="35" rx="3" fill="#202326"/><rect x="62" y="23" width="116" height="25" fill="#6f8792" opacity=".75"/></svg>`; }
};

/* Familles utilisées par les filtres du catalogue. */
const FAMILIES = [
  { id: "all",     label: "Tout" },
  { id: "seating", label: "Assises" },
  { id: "tables",  label: "Tables" },
  { id: "bedroom", label: "Chambre" },
  { id: "decor",   label: "Déco" }
];

/*  base   → sprite 2D + volume 3D réutilisés (compatibilité Phase 3D)
    colors → nuancier réellement appliqué au meuble placé
    price  → dinars algériens                                        */
const CATALOG = [
  { id: "sofa-3",   base: "sofa",     family: "seating", name: "Canapé Tipaza 3 places", w: 2.20, d: 0.95, price: 89000,
    color: "#5A6270", colors: ["#5A6270", "#2F3339", "#9B8B7A", "#6E7F6A"], blurb: "Assise profonde, accoudoirs larges." },
  { id: "sofa-2",   base: "sofa",     family: "seating", name: "Canapé Tipaza 2 places", w: 1.60, d: 0.90, price: 67500,
    color: "#9B8B7A", colors: ["#9B8B7A", "#5A6270", "#2F3339", "#B4614C"], blurb: "Le même confort pour les petits salons." },
  { id: "armchair", base: "armchair", family: "seating", name: "Fauteuil Oran", w: 0.95, d: 0.90, price: 34500,
    color: "#3A4049", colors: ["#3A4049", "#8C6A4F", "#6E7F6A", "#C0B4A4"], blurb: "Dossier enveloppant, pieds en bois." },
  { id: "chair",    base: "chair",    family: "seating", name: "Chaise Sahel", w: 0.45, d: 0.45, price: 7400,
    color: "#2E7D64", colors: ["#2E7D64", "#1F2429", "#C9A227", "#B4614C"], blurb: "Coque moulée, empilable." },
  { id: "stool",    base: "chair",    family: "seating", name: "Tabouret Blida", w: 0.38, d: 0.38, price: 4900,
    color: "#C9A227", colors: ["#C9A227", "#2E7D64", "#1F2429", "#9B8B7A"], blurb: "Appoint léger, se range sous la table." },

  { id: "coffee",   base: "table",    family: "tables",  name: "Table basse Annaba", w: 1.10, d: 0.60, price: 18900,
    color: "#8C6A4F", colors: ["#8C6A4F", "#3A3F45", "#D8CDBF", "#6B4DF6"], blurb: "Plateau chêne, structure fine." },
  { id: "dining",   base: "table",    family: "tables",  name: "Table à manger Béjaïa", w: 1.60, d: 0.90, price: 54000,
    color: "#6E4E34", colors: ["#6E4E34", "#3A3F45", "#D8CDBF", "#8C6A4F"], blurb: "Six couverts, plateau massif." },
  { id: "tvstand",  base: "tvstand",  family: "tables",  name: "Meuble TV Tlemcen", w: 1.80, d: 0.40, price: 31500,
    color: "#4B5563", colors: ["#4B5563", "#8C6A4F", "#D8CDBF", "#1F2429"], blurb: "Deux portes, passe-câbles." },

  { id: "bed-160",  base: "bed",      family: "bedroom", name: "Lit Ghardaïa 160×200", w: 1.60, d: 2.00, price: 74000,
    color: "#9CA3AF", colors: ["#9CA3AF", "#8C6A4F", "#2F3339", "#C0B4A4"], blurb: "Tête de lit capitonnée." },
  { id: "bed-90",   base: "bed",      family: "bedroom", name: "Lit Ghardaïa 90×190", w: 0.90, d: 1.90, price: 42000,
    color: "#C0B4A4", colors: ["#C0B4A4", "#9CA3AF", "#6E7F6A", "#2F3339"], blurb: "Format simple, sommier inclus." },

  { id: "rug-160",  base: "rug",      family: "decor",   name: "Tapis Ghardaïa 160×110", w: 1.60, d: 1.10, price: 22000,
    color: "#C87A34", colors: ["#C87A34", "#7C8B9A", "#8E9B6C", "#B5ADA0"], blurb: "Laine tissée main." },
  { id: "rug-240",  base: "rug",      family: "decor",   name: "Grand tapis 240×170", w: 2.40, d: 1.70, price: 39000,
    color: "#7C8B9A", colors: ["#7C8B9A", "#C87A34", "#8E9B6C", "#B5ADA0"], blurb: "Assez large pour un salon entier." },
  { id: "lamp",     base: "lamp",     family: "decor",   name: "Lampadaire Sétif", w: 0.35, d: 0.35, price: 9900,
    color: "#E8B33A", colors: ["#E8B33A", "#F2EDE3", "#2F3339", "#6E7F6A"], blurb: "Abat-jour tissu, variateur." },
  { id: "plant",    base: "plant",    family: "decor",   name: "Plante d'intérieur", w: 0.45, d: 0.45, price: 4200,
    color: "#2F8A63", colors: ["#2F8A63", "#4C744E", "#7FA06B"], blurb: "Pot en terre cuite inclus." },
  { id: "plant-xl", base: "plant",    family: "decor",   name: "Grande plante", w: 0.70, d: 0.70, price: 8600,
    color: "#4C744E", colors: ["#4C744E", "#2F8A63", "#7FA06B"], blurb: "Pour habiller un angle vide." }
];

/* Chaque entrée garde un accès direct à son maker (compatibilité). */
CATALOG.forEach(entry => { entry.make = color => MAKERS[entry.base](color || entry.color); });

/* ---------- Sprites ----------------------------------------------------
   CATALOG_IMGS reste indexé par "base" pour les modules existants ;
   spriteFor() ajoute un cache par couleur pour le nuancier.          */
const CATALOG_IMGS = {};
const SPRITE_CACHE = new Map();

function spriteFor(base, color) {
  const maker = MAKERS[base];
  if (!maker) return null;
  const key = `${base}|${color || "default"}`;
  let img = SPRITE_CACHE.get(key);
  if (!img) {
    img = new Image();
    img.src = svgUrl(maker(color || "#8A8F98"));
    SPRITE_CACHE.set(key, img);
  }
  return img;
}

Object.keys(MAKERS).forEach(base => {
  const sample = CATALOG.find(c => c.base === base);
  CATALOG_IMGS[base] = spriteFor(base, sample ? sample.color : "#8A8F98");
});

const priceFormatter = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 0 });
function formatPrice(value) {
  return `${priceFormatter.format(Math.round(value || 0))} DA`;
}

window.CATALOG = CATALOG;
window.CATALOG_IMGS = CATALOG_IMGS;
window.FAMILIES = FAMILIES;
window.spriteFor = spriteFor;
window.formatPrice = formatPrice;
window.catalogEntry = id => CATALOG.find(c => c.id === id) || null;

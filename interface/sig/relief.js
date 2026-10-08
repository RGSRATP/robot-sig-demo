// Relief : modèles numériques de terrain de la Géoplateforme IGN (config/sig.yaml, section « relief »), appelés par
// le navigateur. Le service WMS renvoie des altitudes NGF brutes (flottants 32 bits, tuiles Web Mercator) ; un
// protocole MapLibre les convertit à la volée en tuiles d'altitude codées en couleur (encodage « mapbox »), lues par
// le terrain 3D et l'ombrage. Les pixels sans donnée sont complétés par la source de secours (LiDAR HD → RGE ALTI),
// sinon mis à 0.
import { addProtocol } from 'maplibre-gl';
import { el } from './ui.js';

const PROTOCOLE = 'sig-relief';
const TAILLE = 256;
const MEMOIRE_MAX = 256;   // tuiles d'altitude gardées en mémoire (le terrain et l'ombrage lisent les mêmes)

/** Identifiants MapLibre d'une source de relief : terrain et ombrage (sources distinctes, conseil MapLibre). */
export const idsRelief = (id) => ({ terrain: `relief-${id}`, ombrage: `relief-${id}-ombrage`, couche: `relief-${id}-ombrage` });

/** Code une altitude (m) en couleur, encodage « mapbox » : h = -10000 + (R·65536 + G·256 + B) × 0,1. */
export function coderAltitudes(h) {
  const px = new Uint8ClampedArray(h.length * 4);
  for (let i = 0; i < h.length; i += 1) {
    const v = Math.max(0, Math.round((h[i] + 10000) * 10));
    px[i * 4] = (v >> 16) & 255;
    px[i * 4 + 1] = (v >> 8) & 255;
    px[i * 4 + 2] = v & 255;
    px[i * 4 + 3] = 255;
  }
  return px;
}

/** Altitudes d'une réponse BIL 32 bits (petit-boutiste) ; erreur si ce n'est pas une tuile de TAILLE². */
export function lireBil(tampon) {
  if (tampon.byteLength !== TAILLE * TAILLE * 4) {
    const debut = new TextDecoder().decode(tampon.slice(0, 200)).replace(/\s+/g, ' ');
    throw new Error(`réponse inattendue du service d'altitude (${tampon.byteLength} octets) : ${debut}`);
  }
  const vue = new DataView(tampon);
  const h = new Float32Array(TAILLE * TAILLE);
  for (let i = 0; i < h.length; i += 1) h[i] = vue.getFloat32(i * 4, true);
  return h;
}

/** Complète les pixels sans donnée (< seuil) par `secours` (même tuile), sinon 0. Renvoie le nombre de trous. */
export function combler(h, seuil, secours) {
  let trous = 0;
  for (let i = 0; i < h.length; i += 1) {
    if (h[i] >= seuil) continue;
    trous += 1;
    h[i] = secours && secours[i] >= seuil ? secours[i] : 0;
  }
  return trous;
}

let installe = null;

/** Enregistre le protocole (une seule fois) pour les sources de `relief`. */
function installerProtocole(relief) {
  if (installe) return;
  const sources = new Map(relief.sources.map((s) => [s.id, s]));
  const memoire = new Map();   // url -> Promise<Float32Array>
  const altitudes = (source, bbox, signal) => {
    const url = source.url.replace('{bbox-epsg-3857}', bbox);
    let p = memoire.get(url);
    if (!p) {
      p = fetch(url, { signal }).then((r) => {
        if (!r.ok) throw new Error(`service d'altitude : HTTP ${r.status}`);
        return r.arrayBuffer();
      }).then(lireBil);
      p.catch(() => memoire.delete(url));
      memoire.set(url, p);
      if (memoire.size > MEMOIRE_MAX) memoire.delete(memoire.keys().next().value);
    }
    return p;
  };
  addProtocol(PROTOCOLE, async (params, abort) => {
    const m = /^sig-relief:\/\/([^/]+)\/(.+)$/.exec(params.url);
    const source = m && sources.get(m[1]);
    if (!source) throw new Error(`source de relief inconnue : ${params.url}`);
    const h = Float32Array.from(await altitudes(source, m[2], abort.signal));
    let secours = null;
    if (source.secours && h.some((v) => v < relief.valeur_absente_sous)) {
      secours = await altitudes(sources.get(source.secours), m[2], abort.signal).catch(() => null);
    }
    combler(h, relief.valeur_absente_sous, secours);
    const image = new ImageData(coderAltitudes(h), TAILLE, TAILLE);
    const data = await createImageBitmap(image, { premultiplyAlpha: 'none', colorSpaceConversion: 'none' });
    return { data };
  });
  installe = relief;
}

/** Sources et couches d'ombrage à ajouter au style initial (avant les couches SIG). */
export function styleRelief(relief, style) {
  if (!relief) return;
  installerProtocole(relief);
  for (const s of relief.sources) {
    const ids = idsRelief(s.id);
    const def = { type: 'raster-dem', tiles: [`${PROTOCOLE}://${s.id}/{bbox-epsg-3857}`], tileSize: TAILLE,
      maxzoom: relief.zoom_max, encoding: 'mapbox', attribution: s.attribution };
    style.sources[ids.terrain] = def;
    style.sources[ids.ombrage] = { ...def };
    style.layers.push({ id: ids.couche, type: 'hillshade', source: ids.ombrage,
      layout: { visibility: 'none' },
      paint: { 'hillshade-exaggeration': 0.35, 'hillshade-shadow-color': '#3d4650', 'hillshade-highlight-color': '#ffffff' } });
  }
}

/** Relief de la carte : activation, source, ombrage. `surChangement(actif)` prévient la 3D et la caméra. */
export class GestionnaireRelief {
  constructor(carte, relief, { surChangement }) {
    this.carte = carte;
    this.cfg = relief;
    this.surChangement = surChangement;
    this.actif = false;
    this.source = relief.defaut;
    this.ombrage = relief.ombrage;
  }

  sourcesIds() {
    return new Set(this.cfg.sources.flatMap((s) => [idsRelief(s.id).terrain, idsRelief(s.id).ombrage]));
  }

  appliquer() {
    const c = this.carte;
    c.setTerrain(this.actif ? { source: idsRelief(this.source).terrain, exaggeration: 1 } : null);
    for (const s of this.cfg.sources) {
      const visible = this.actif && this.ombrage && s.id === this.source;
      c.setLayoutProperty(idsRelief(s.id).couche, 'visibility', visible ? 'visible' : 'none');
    }
    this.surChangement(this.actif);
  }

  /** Section « Relief » du volet des fonds. */
  section({ aTravers, surATravers }) {
    const r = this.cfg;
    const coche = (libelle, valeur, changer) => {
      const i = el('input', { type: 'checkbox', checked: valeur });
      i.addEventListener('change', () => changer(i.checked));
      return { i, ligne: el('label', { className: 'sig-option' }, i, ` ${libelle}`) };
    };
    const actif = coche('Afficher le relief (terrain 3D)', this.actif, (v) => { this.actif = v; this.appliquer(); maj(); });
    const ombrage = coche('Ombrage du relief', this.ombrage, (v) => { this.ombrage = v; this.appliquer(); });
    const travers = coche('Voir les ouvrages à travers le terrain', aTravers, surATravers);
    const choix = el('select', {},
      ...r.sources.map((s) => el('option', { value: s.id, textContent: s.libelle, selected: s.id === this.source })));
    choix.addEventListener('change', () => { this.source = choix.value; this.appliquer(); });
    const maj = () => {
      for (const x of [ombrage.i, travers.i, choix]) x.disabled = !this.actif;
      actif.i.checked = this.actif;
    };
    maj();
    this.majCase = maj;
    return el('section', { className: 'sig-section' }, el('h3', { textContent: 'Relief' }),
      el('div', { className: 'sig-options sig-options--colonne' }, actif.ligne),
      el('div', { className: 'sig-reglage' }, el('span', { className: 'sig-reglage__libelle', textContent: 'Modèle de terrain' }), choix),
      el('div', { className: 'sig-options sig-options--colonne' }, ombrage.ligne, travers.ligne),
      el('p', { className: 'sig-info', textContent: 'Altitudes NGF de la Géoplateforme IGN. Le relief se voit en vue inclinée (bouton 3D). '
        + 'Les maquettes y sont placées à leur altitude NGF ; le terrain masque les ouvrages enterrés, sauf avec « voir à travers ».' }));
  }
}

// Bâtiments 3D : BD TOPO de l'IGN (config/sig.yaml, section « batiments »), tuiles vectorielles appelées par le
// navigateur, extrudées à leur hauteur et colorées selon leur usage. Sur le relief, MapLibre les pose sur le terrain.
// Les bâtiments situés sous une maquette affichée en 3D sont masqués (hauteur 0 par état d'objet) : une gare
// reconstruite ne se superpose pas au bâti existant de la BD TOPO.
import { el } from './ui.js';

export const SOURCE_BATIMENTS = 'sig-batiments';
export const COUCHE_BATIMENTS = 'sig-batiments-3d';

/** Source et couche des bâtiments à ajouter au style initial (avant les couches SIG). */
export function styleBatiments(b, style) {
  if (!b) return;
  style.sources[SOURCE_BATIMENTS] = { type: 'vector', tiles: [b.url], minzoom: b.zoom_min, maxzoom: b.zoom_max_tuiles,
    attribution: b.attribution, promoteId: { [b.couche_source]: b.champ_identifiant } };
  const couleur = ['match', ['get', b.champ_couleur]];
  for (const [valeur, c] of Object.entries(b.couleurs)) couleur.push(valeur, c);
  couleur.push(b.couleur_defaut);
  style.layers.push({
    id: COUCHE_BATIMENTS, type: 'fill-extrusion', source: SOURCE_BATIMENTS, 'source-layer': b.couche_source,
    minzoom: b.zoom_min, layout: { visibility: b.visible ? 'visible' : 'none' },
    paint: {
      'fill-extrusion-color': ['case', ['boolean', ['feature-state', 'choisi'], false], '#e8b400', couleur],
      'fill-extrusion-height': ['case', ['boolean', ['feature-state', 'masque'], false], 0,
        ['coalesce', ['get', b.champ_hauteur], b.hauteur_defaut_m]],
      'fill-extrusion-base': 0,
      'fill-extrusion-opacity': b.opacite,
      'fill-extrusion-vertical-gradient': true,
    },
  });
}

/** Point dans un anneau (lon, lat), règle pair-impair. */
function dansAnneau([x, y], anneau) {
  let dedans = false;
  for (let i = 0, j = anneau.length - 1; i < anneau.length; j = i, i += 1) {
    const [xi, yi] = anneau[i];
    const [xj, yj] = anneau[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) dedans = !dedans;
  }
  return dedans;
}

const polygones = (g) => (g.type === 'Polygon' ? [g.coordinates] : g.type === 'MultiPolygon' ? g.coordinates : []);
const dansPolygone = (p, poly) => dansAnneau(p, poly[0]) && !poly.slice(1).some((trou) => dansAnneau(p, trou));

function boite(polys) {
  const b = [Infinity, Infinity, -Infinity, -Infinity];
  for (const poly of polys) for (const [x, y] of poly[0]) {
    b[0] = Math.min(b[0], x); b[1] = Math.min(b[1], y); b[2] = Math.max(b[2], x); b[3] = Math.max(b[3], y);
  }
  return b;
}
const disjointes = (a, b) => a[2] < b[0] || b[2] < a[0] || a[3] < b[1] || b[3] < a[1];

/** Vrai si le bâtiment (polygones) recoupe l'emprise : un sommet de l'un dans l'autre. */
export function recoupe(batiment, emprise) {
  return batiment.some((pb) => emprise.some((pe) => pb[0].some((s) => dansPolygone(s, pe)) || pe[0].some((s) => dansPolygone(s, pb))));
}

export class GestionnaireBatiments {
  /** emprises3d() : emprises GeoJSON (WGS84) des maquettes affichées en 3D. */
  constructor(carte, cfg, { emprises3d, surChangement }) {
    this.carte = carte;
    this.cfg = cfg;
    this.emprises3d = emprises3d;
    this.surChangement = surChangement;
    this.visible = cfg.visible;
    this.masquer = cfg.masquer_sous_maquettes;
    this.masques = new Set();   // identifiants des bâtiments masqués
    this.choisi = null;
    carte.on('idle', () => this._majMasques());
  }

  get actifs() {
    return this.visible && this.carte.getZoom() >= this.cfg.zoom_min;
  }

  _etat(id, etat) {
    this.carte.setFeatureState({ source: SOURCE_BATIMENTS, sourceLayer: this.cfg.couche_source, id }, etat);
  }

  /** Masque les bâtiments sous les maquettes 3D (recalculé quand la carte est au repos : tuiles, maquettes). */
  _majMasques() {
    const voulus = new Set();
    if (this.actifs && this.masquer) {
      const emprises = this.emprises3d().map((g) => { const p = polygones(g); return { p, b: boite(p) }; });
      if (emprises.length) {
        const feats = this.carte.querySourceFeatures(SOURCE_BATIMENTS, { sourceLayer: this.cfg.couche_source });
        for (const f of feats) {
          if (f.id == null || voulus.has(f.id)) continue;
          const pb = polygones(f.geometry);
          const bb = boite(pb);
          if (emprises.some((e) => !disjointes(e.b, bb) && recoupe(pb, e.p))) voulus.add(f.id);
        }
      }
    }
    for (const id of this.masques) if (!voulus.has(id)) this._etat(id, { masque: false });
    for (const id of voulus) if (!this.masques.has(id)) this._etat(id, { masque: true });
    this.masques = voulus;
  }

  /** Bâtiment sous le point écran (propriétés et identifiant), ou null. */
  sous(point) {
    if (!this.actifs || !this.carte.getLayer(COUCHE_BATIMENTS)) return null;
    const f = this.carte.queryRenderedFeatures(point, { layers: [COUCHE_BATIMENTS] })
      .find((x) => !this.masques.has(x.id));
    return f ? { id: f.id, proprietes: f.properties } : null;
  }

  selectionner(id) {
    if (this.choisi != null) this._etat(this.choisi, { choisi: false });
    this.choisi = id;
    if (id != null) this._etat(id, { choisi: true });
  }

  appliquer() {
    this.carte.setLayoutProperty(COUCHE_BATIMENTS, 'visibility', this.visible ? 'visible' : 'none');
    if (!this.visible) this.selectionner(null);
    this._majMasques();
    this.surChangement(this.visible);
  }

  /** Section « Bâtiments 3D » du volet des fonds : affichage, opacité, masquage, légende des usages. */
  section() {
    const c = this.cfg;
    const coche = (libelle, valeur, changer) => {
      const i = el('input', { type: 'checkbox', checked: valeur });
      i.addEventListener('change', () => changer(i.checked));
      return el('label', { className: 'sig-option' }, i, ` ${libelle}`);
    };
    const opacite = el('input', { type: 'range', min: 0.1, max: 1, step: 0.05, value: c.opacite });
    const valO = el('output', { textContent: `${Math.round(c.opacite * 100)} %` });
    opacite.addEventListener('input', () => {
      this.carte.setPaintProperty(COUCHE_BATIMENTS, 'fill-extrusion-opacity', Number(opacite.value));
      valO.textContent = `${Math.round(Number(opacite.value) * 100)} %`;
    });
    const legende = el('ul', { className: 'sig-legende' },
      ...Object.entries(c.couleurs).map(([v, coul]) => el('li', {},
        el('span', { className: 'sig-legende__pastille', style: `background:${coul}` }), el('span', { textContent: v }))),
      el('li', {}, el('span', { className: 'sig-legende__pastille', style: `background:${c.couleur_defaut}` }),
        el('span', { textContent: 'Autre / indifférencié' })));
    return el('section', { className: 'sig-section' }, el('h3', { textContent: 'Bâtiments 3D' }),
      el('div', { className: 'sig-options sig-options--colonne' },
        coche(`Afficher les bâtiments (à partir du zoom ${c.zoom_min})`, this.visible, (v) => { this.visible = v; this.appliquer(); }),
        coche('Masquer les bâtiments sous les maquettes 3D', this.masquer, (v) => { this.masquer = v; this._majMasques(); })),
      el('div', { className: 'sig-reglage' }, el('span', { className: 'sig-reglage__libelle', textContent: 'Opacité' }), opacite, valO),
      legende,
      el('p', { className: 'sig-info', textContent: `${c.libelle}, hauteurs de la base IGN, colorés selon l'usage. `
        + 'Inclinez la vue (bouton 3D) pour les voir en volume ; clic sur un bâtiment : sa fiche.' }));
  }
}

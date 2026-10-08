// Mesures 2D et 3D : distance (ligne brisée), surface, hauteur entre deux points, coordonnées d'un point.
// Chaque point est pris sur ce qui est sous le curseur : objet de maquette, local, point LiDAR (scène 3D), sinon le
// terrain (relief) ou le sol de la carte plate. Distances horizontales et surfaces calculées en Lambert 93 ; les
// distances 3D ajoutent l'écart d'altitude. Tracé sur la carte (lignes, surface, étiquettes) et dans la 3D (segments
// en hauteur, visibles à travers les objets).
import * as THREE from 'three';
import { versLambert93 } from './lambert93.js';
import { el } from './ui.js';

const SOURCE = 'sig-mesure';
const COULEUR = '#e8590c';
const MODES = {
  distance: { libelle: 'Distance', aide: 'Cliquez les points de la ligne ; double-clic ou Entrée pour terminer.' },
  surface: { libelle: 'Surface', aide: 'Cliquez les sommets ; double-clic ou Entrée pour fermer le polygone.' },
  hauteur: { libelle: 'Hauteur', aide: 'Cliquez deux points (bas et haut, sur des objets 3D ou le terrain).' },
  point: { libelle: 'Point', aide: 'Cliquez un point : coordonnées et altitude.' },
};

const nf = (v, d = 2) => v.toLocaleString('fr-FR', { minimumFractionDigits: d, maximumFractionDigits: d });
const longueur = (m) => (m >= 1000 ? `${nf(m / 1000, 3)} km` : `${nf(m, 2)} m`);
const aire = (m2) => (m2 >= 1e6 ? `${nf(m2 / 1e6, 4)} km²` : m2 >= 1e4 ? `${nf(m2, 0)} m² (${nf(m2 / 1e4, 2)} ha)` : `${nf(m2, 2)} m²`);

/** Aire (m²) et périmètre (m) d'un polygone en coordonnées planes [x, y]. */
export function airePolygone(pts) {
  let a = 0;
  let p = 0;
  for (let i = 0; i < pts.length; i += 1) {
    const [x1, y1] = pts[i];
    const [x2, y2] = pts[(i + 1) % pts.length];
    a += x1 * y2 - x2 * y1;
    p += Math.hypot(x2 - x1, y2 - y1);
  }
  return { aire: Math.abs(a) / 2, perimetre: p };
}

export class OutilMesure {
  constructor(carte, c3d, { panneau, police }) {
    this.carte = carte;
    this.police = police;
    this.c3d = c3d;
    this.panneau = panneau;
    this.mode = null;
    this.points = [];      // { lon, lat, z, x, y (Lambert 93), nature }
    this.termine = false;
    this.curseur = null;
    this.groupe3d = new THREE.Group();
    this.matLigne = new THREE.LineBasicMaterial({ color: COULEUR, depthTest: false, transparent: true });
    this.matPoint = new THREE.PointsMaterial({ color: COULEUR, size: 9, sizeAttenuation: false, depthTest: false, transparent: true });
    carte.on('mousemove', (e) => { if (this.mode && !this.termine && this.points.length) { this.curseur = e.lngLat; this._tracer(); } });
    document.addEventListener('keydown', (e) => {
      if (!this.mode) return;
      if (e.key === 'Escape') this.effacer();
      if (e.key === 'Enter') this.terminer();
    });
  }

  get actif() { return this.mode != null; }

  installer() {
    this.carte.addSource(SOURCE, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
    this.carte.addLayer({ id: `${SOURCE}-surface`, type: 'fill', source: SOURCE, filter: ['==', ['geometry-type'], 'Polygon'],
      paint: { 'fill-color': COULEUR, 'fill-opacity': 0.15 } });
    this.carte.addLayer({ id: `${SOURCE}-ligne`, type: 'line', source: SOURCE, filter: ['!=', ['geometry-type'], 'Point'],
      paint: { 'line-color': COULEUR, 'line-width': 2.5 } });
    this.carte.addLayer({ id: `${SOURCE}-points`, type: 'circle', source: SOURCE, filter: ['all', ['==', ['geometry-type'], 'Point'], ['!', ['has', 'texte']]],
      paint: { 'circle-radius': 4.5, 'circle-color': '#ffffff', 'circle-stroke-color': COULEUR, 'circle-stroke-width': 2 } });
    this.carte.addLayer({ id: `${SOURCE}-textes`, type: 'symbol', source: SOURCE, filter: ['has', 'texte'],
      layout: { 'text-field': ['get', 'texte'], 'text-font': [this.police || 'Source Sans Pro Regular'], 'text-size': 12, 'text-allow-overlap': true },
      paint: { 'text-color': '#7a2e00', 'text-halo-color': '#ffffff', 'text-halo-width': 2 } });
    this.c3d.aides.add(this.groupe3d);
    this._rendre();
  }

  choisir(mode) {
    this.effacer(false);
    this.mode = this.mode === mode ? null : mode;
    if (this.mode) this.carte.doubleClickZoom.disable();
    else this.carte.doubleClickZoom.enable();
    this.carte.getCanvas().style.cursor = this.mode ? 'crosshair' : '';
    this._rendre();
  }

  /** Point mesuré sous le point écran (objet 3D, sinon terrain ou sol). */
  _pointer(e) {
    const mpp = (78271.517 * Math.cos((e.lngLat.lat * Math.PI) / 180)) / 2 ** this.carte.getZoom();
    const p3 = this.c3d.pointSous(e.point, Math.max(0.05, mpp * 4));
    let lon; let lat; let z; let nature;
    if (p3) {
      ({ lon, lat, z } = this.c3d.depuisScene(p3.point));
      nature = p3.nature;
    } else {
      ({ lng: lon, lat } = e.lngLat);
      const t = this.carte.getTerrain() ? this.carte.queryTerrainElevation(e.lngLat) : null;
      z = t ?? 0;
      nature = t != null ? 'terrain' : 'sol';
    }
    const [x, y] = versLambert93(lon, lat);
    return { lon, lat, z, x, y, nature };
  }

  /** Clic sur la carte en mode mesure (carte.js) ; vrai si le clic est consommé. */
  clic(e) {
    if (!this.mode) return false;
    if (this.termine) this.effacer(false);
    this.points.push(this._pointer(e));
    if (this.mode === 'point' || (this.mode === 'hauteur' && this.points.length === 2)) this.termine = true;
    this._tracer();
    this._rendre();
    return true;
  }

  terminer() {
    if (!this.mode || this.points.length < (this.mode === 'surface' ? 3 : 2)) return;
    this.termine = true;
    this.curseur = null;
    this._tracer();
    this._rendre();
  }

  /** Double-clic : termine la ligne ou la surface (le second clic du double-clic a déjà ajouté un point). */
  doubleClic() {
    if (!this.mode) return false;
    if (this.points.length > 1 && !this.termine) this.points.pop();
    this.terminer();
    return true;
  }

  effacer(rendre = true) {
    this.points = [];
    this.termine = false;
    this.curseur = null;
    this._tracer();
    if (rendre) this._rendre();
  }

  // ------------------------------------------------------------ calculs

  _resultats() {
    const p = this.points;
    const altitude = this.carte.getTerrain() ? 'Altitude NGF' : 'Hauteur / terrain';
    if (!p.length) return [];
    if (this.mode === 'point') {
      return [['X Lambert 93', nf(p[0].x)], ['Y Lambert 93', nf(p[0].y)], ['WGS84', `${nf(p[0].lat, 7)}° N ${nf(p[0].lon, 7)}° E`],
        [altitude, `${nf(p[0].z)} m`], ['Pris sur', p[0].nature]];
    }
    const segments = [];
    for (let i = 1; i < p.length; i += 1) {
      const h = Math.hypot(p[i].x - p[i - 1].x, p[i].y - p[i - 1].y);
      const dz = p[i].z - p[i - 1].z;
      segments.push({ h, dz, d3: Math.hypot(h, dz) });
    }
    if (this.mode === 'surface' && p.length >= 3) {
      const { aire: a, perimetre } = airePolygone(p.map((q) => [q.x, q.y]));
      return [['Surface (projection horizontale)', aire(a)], ['Périmètre', longueur(perimetre)], ['Sommets', String(p.length)]];
    }
    const h = segments.reduce((t, s) => t + s.h, 0);
    const d3 = segments.reduce((t, s) => t + s.d3, 0);
    const lignes = [['Distance horizontale', longueur(h)], ['Distance 3D (avec altitudes)', longueur(d3)]];
    if (p.length >= 2) {
      const dz = p[p.length - 1].z - p[0].z;
      lignes.push(['Dénivelé (dernier − premier)', `${dz >= 0 ? '+' : '−'}${nf(Math.abs(dz))} m`]);
      if (h > 0.01) lignes.push(['Pente moyenne', `${nf((dz / h) * 100, 1)} %`]);
    }
    if (this.mode === 'hauteur' && p.length === 2) {
      lignes.unshift(['Hauteur (écart vertical)', `${nf(Math.abs(p[1].z - p[0].z))} m`]);
    }
    if (segments.length > 1) lignes.push(['Segments', segments.map((s) => longueur(s.h)).join(' + ')]);
    lignes.push(['Points pris sur', [...new Set(p.map((q) => q.nature))].join(', ')]);
    return lignes;
  }

  // ------------------------------------------------------------ tracé

  _tracer() {
    const p = this.points;
    const feats = [];
    const coords = p.map((q) => [q.lon, q.lat]);
    const provisoire = !this.termine && this.curseur ? [this.curseur.lng, this.curseur.lat] : null;
    if (this.mode === 'surface' && coords.length >= 2) {
      const anneau = [...coords, ...(provisoire ? [provisoire] : []), coords[0]];
      if (anneau.length >= 4) feats.push({ type: 'Feature', geometry: { type: 'Polygon', coordinates: [anneau] }, properties: { provisoire: !this.termine } });
      else feats.push({ type: 'Feature', geometry: { type: 'LineString', coordinates: anneau }, properties: { provisoire: true } });
    } else if (coords.length >= 1 && this.mode !== 'point') {
      const ligne = [...coords, ...(provisoire ? [provisoire] : [])];
      if (ligne.length >= 2) feats.push({ type: 'Feature', geometry: { type: 'LineString', coordinates: ligne }, properties: { provisoire: false } });
    }
    for (const c of coords) feats.push({ type: 'Feature', geometry: { type: 'Point', coordinates: c }, properties: {} });
    // longueur de chaque segment, au milieu
    if (this.mode !== 'surface' && this.mode !== 'point') {
      for (let i = 1; i < p.length; i += 1) {
        const h = Math.hypot(p[i].x - p[i - 1].x, p[i].y - p[i - 1].y);
        const dz = p[i].z - p[i - 1].z;
        const texte = this.mode === 'hauteur' ? `Δz ${nf(Math.abs(dz))} m · ${longueur(Math.hypot(h, dz))}` : longueur(h);
        feats.push({ type: 'Feature', geometry: { type: 'Point', coordinates: [(p[i].lon + p[i - 1].lon) / 2, (p[i].lat + p[i - 1].lat) / 2] }, properties: { texte } });
      }
    }
    if (this.mode === 'surface' && this.termine && p.length >= 3) {
      const c = coords.reduce((t, q) => [t[0] + q[0] / coords.length, t[1] + q[1] / coords.length], [0, 0]);
      feats.push({ type: 'Feature', geometry: { type: 'Point', coordinates: c }, properties: { texte: aire(airePolygone(p.map((q) => [q.x, q.y])).aire) } });
    }
    this.carte.getSource(SOURCE)?.setData({ type: 'FeatureCollection', features: feats });
    // 3D : segments à la hauteur des points pris sur des objets (visibles à travers eux)
    for (const o of [...this.groupe3d.children]) { this.groupe3d.remove(o); o.geometry.dispose(); }
    if (p.length && p.some((q) => q.nature !== 'sol')) {
      const v = p.map((q) => this.c3d.versPointScene([q.lon, q.lat], q.z));
      if (this.mode === 'surface' && this.termine) v.push(v[0]);
      const geoPts = new THREE.BufferGeometry().setFromPoints(v);
      const pts = new THREE.Points(geoPts, this.matPoint);
      pts.renderOrder = 10;
      this.groupe3d.add(pts);
      if (v.length >= 2) {
        const ligne = new THREE.Line(new THREE.BufferGeometry().setFromPoints(v), this.matLigne);
        ligne.renderOrder = 10;
        this.groupe3d.add(ligne);
      }
    }
    this.carte.triggerRepaint();
  }

  // ------------------------------------------------------------ volet

  _rendre() {
    const boutons = el('div', { className: 'sig-groupe-boutons' }, ...Object.entries(MODES).map(([m, d]) => {
      const b = el('button', { type: 'button', className: `sig-bouton${this.mode === m ? ' sig-bouton--actif' : ''}`, textContent: d.libelle });
      b.addEventListener('click', () => this.choisir(m));
      return b;
    }));
    const effacer = el('button', { type: 'button', className: 'sig-lien', textContent: 'Effacer la mesure' });
    effacer.addEventListener('click', () => this.effacer());
    const res = this._resultats();
    const dl = el('dl', { className: 'sig-dl' });
    for (const [k, v] of res) dl.append(el('dt', { textContent: k }), el('dd', { textContent: v }));
    this.panneau.replaceChildren(...[
      el('section', { className: 'sig-section' }, el('h3', { textContent: 'Mesurer' }), boutons,
        el('p', { className: 'sig-info', textContent: this.mode ? MODES[this.mode].aide + ' Échap : effacer.' : 'Choisissez une mesure.' })),
      res.length ? el('section', { className: 'sig-section' }, el('h3', { textContent: this.termine ? 'Résultat' : 'En cours' }), dl, effacer) : null,
      el('p', { className: 'sig-info', textContent: 'Points pris sur les objets 3D (maquettes, locaux, nuage LiDAR) sous le curseur, '
        + 'sinon sur le terrain (relief) ou le sol. Distances horizontales et surfaces en Lambert 93 (projection horizontale) ; '
        + 'altitudes NGF en relief, hauteurs par rapport au terrain sur la carte plate.' }),
    ].filter(Boolean));
  }
}

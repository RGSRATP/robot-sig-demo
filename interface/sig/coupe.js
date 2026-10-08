// Plan de coupe ajustable (inspiré de l'outil « Slice » d'ArcGIS) : une ligne tracée sur la carte définit un plan
// vertical qui coupe les objets. Réglages : poignées aux extrémités (déplaçables), décalage, rotation, inclinaison,
// épaisseur de tranche (deux plans parallèles), côté conservé, vue de face.
//
// Objets coupés : maquettes, locaux et nuage LiDAR (plans de coupe de la scène Three.js) ; bâtiments 3D (selon leur
// centre) et points et lignes des couches SIG (filtre « within ») quand le plan est vertical. Le terrain et les
// polygones 2D ne sont pas coupés (limite de MapLibre). Géométrie calculée en Lambert 93.
import * as THREE from 'three';
import { Marker } from 'maplibre-gl';
import { cadrer } from './camera.js';
import { depuisLambert93, versLambert93 } from './lambert93.js';
import { el } from './ui.js';

const SOURCE = 'sig-coupe';
const COULEUR = '#e8590c';
const LOIN_M = 30000;                  // demi-plans 2D : rectangles de 30 km du côté retiré
const PAS_BORD_M = [0, 100, 1000, 10000, LOIN_M];   // points du bord le long du plan (précision près du centre)
const HAUT_M = 80;                     // hauteur du panneau dessiné au-dessus et au-dessous du pivot
const BAS_M = 60;

const ajout = (a, b, k = 1) => [a[0] + b[0] * k, a[1] + b[1] * k];
const versLL = ([x, y]) => depuisLambert93(x, y);

/** Côté d'un point (Lambert 93) par rapport au plan vertical : distance signée le long de la normale. */
export function distanceSignee(p, origine, normale) {
  return (p[0] - origine[0]) * normale[0] + (p[1] - origine[1]) * normale[1];
}

export class OutilCoupe {
  constructor(carte, c3d, { couches, batiments }) {
    this.carte = carte;
    this.c3d = c3d;
    this.couches = couches;
    this.batiments = batiments;
    this.a = null; this.b = null;   // extrémités tracées (Lambert 93)
    this.trace = null;              // capture en cours : null | [] | [premier point]
    this.reglages = { decalage: 0, rotation: 0, inclinaison: 0, epaisseur: 0, inverse: false, couper2d: true, couperBatiments: true };
    this.groupe3d = new THREE.Group();
    this.matPanneau = new THREE.MeshBasicMaterial({ color: COULEUR, transparent: true, opacity: 0.06, side: THREE.DoubleSide, depthWrite: false });
    this.matBord = new THREE.LineBasicMaterial({ color: COULEUR });
    this.poignees = [];
    carte.on('mousemove', (e) => { if (this.trace?.length === 1) this._dessinerTrace([this.trace[0], versLambert93(e.lngLat.lng, e.lngLat.lat)]); });
  }

  installer(police) {
    this.carte.addSource(SOURCE, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
    this.carte.addLayer({ id: `${SOURCE}-ligne`, type: 'line', source: SOURCE, filter: ['==', ['geometry-type'], 'LineString'],
      paint: { 'line-color': COULEUR, 'line-width': 3, 'line-dasharray': [3, 2] } });
    this.carte.addLayer({ id: `${SOURCE}-texte`, type: 'symbol', source: SOURCE, filter: ['has', 'texte'],
      layout: { 'text-field': ['get', 'texte'], 'text-font': [police], 'text-size': 12, 'text-allow-overlap': true },
      paint: { 'text-color': '#7a2e00', 'text-halo-color': '#ffffff', 'text-halo-width': 2 } });
    this.c3d.aides.add(this.groupe3d);
  }

  // ------------------------------------------------------------ tracé

  commencerTrace() {
    this.trace = [];
    this.carte.getCanvas().style.cursor = 'crosshair';
    this.carte.doubleClickZoom.disable();
    this._rendre();
  }

  /** Clic sur la carte pendant le tracé (carte.js) ; vrai si le clic est consommé. */
  clic(e) {
    if (!this.trace) return false;
    this.trace.push(versLambert93(e.lngLat.lng, e.lngLat.lat));
    if (this.trace.length === 2) {
      [this.a, this.b] = this.trace;
      if (Math.hypot(this.b[0] - this.a[0], this.b[1] - this.a[1]) < 0.5) { this.trace = [this.a]; return true; }
      this.trace = null;
      this.carte.getCanvas().style.cursor = '';
      this.carte.doubleClickZoom.enable();
      Object.assign(this.reglages, { decalage: 0, rotation: 0, inclinaison: 0 });
      this.appliquer();
    }
    return true;
  }

  supprimer() {
    this.a = this.b = this.trace = null;
    this.carte.getCanvas().style.cursor = '';
    this.carte.doubleClickZoom.enable();
    this.appliquer();
  }

  // ------------------------------------------------------------ géométrie

  /** Plan effectif (Lambert 93) : centre, direction, normale du côté conservé, demi-longueur, extrémités. */
  geometrie() {
    if (!this.a || !this.b) return null;
    const r = this.reglages;
    const lg = Math.hypot(this.b[0] - this.a[0], this.b[1] - this.a[1]);
    const t = (r.rotation * Math.PI) / 180;
    const d0 = [(this.b[0] - this.a[0]) / lg, (this.b[1] - this.a[1]) / lg];
    const dir = [d0[0] * Math.cos(t) - d0[1] * Math.sin(t), d0[0] * Math.sin(t) + d0[1] * Math.cos(t)];
    const gauche = [-dir[1], dir[0]];
    const centre = ajout([(this.a[0] + this.b[0]) / 2, (this.a[1] + this.b[1]) / 2], gauche, r.decalage);
    const normale = r.inverse ? [-gauche[0], -gauche[1]] : gauche;
    return { centre, dir, normale, demi: lg / 2, a: ajout(centre, dir, -lg / 2), b: ajout(centre, dir, lg / 2) };
  }

  /** Vrai si le point (Lambert 93) est retiré par la coupe verticale. */
  _retire(p, g) {
    const s = distanceSignee(p, g.centre, g.normale);
    return this.reglages.epaisseur > 0 ? Math.abs(s) > this.reglages.epaisseur / 2 : s < 0;
  }

  /** Zone retirée en 2D (WGS84) : un rectangle (demi-espace) ou deux (tranche). */
  _zoneRetiree(g) {
    const rect = (origine, n) => {
      const bord = [...PAS_BORD_M.slice(1).reverse().map((k) => -k), ...PAS_BORD_M].map((k) => ajout(origine, g.dir, k));
      const loin = [...bord].reverse().map((p) => ajout(p, n, LOIN_M));
      return [[...bord, ...loin, bord[0]].map(versLL)];
    };
    const e = this.reglages.epaisseur;
    const moins = [-g.normale[0], -g.normale[1]];
    if (e > 0) return { type: 'MultiPolygon', coordinates: [rect(ajout(g.centre, g.normale, e / 2), g.normale), rect(ajout(g.centre, moins, e / 2), moins)] };
    return { type: 'MultiPolygon', coordinates: [rect(g.centre, moins)] };
  }

  appliquer() {
    const g = this.geometrie();
    const r = this.reglages;
    // 3D : plans de la scène
    let plans = [];
    for (const o of [...this.groupe3d.children]) { this.groupe3d.remove(o); o.geometry.dispose(); }
    if (g) {
      const ll = versLL(g.centre);
      const sol = this.carte.getTerrain() ? this.carte.queryTerrainElevation(ll) ?? 0 : 0;
      const m = this.c3d.versPointScene(ll, sol);
      const versScene = (p) => this.c3d.versPointScene(versLL(p), sol).sub(m);
      const ns = versScene(ajout(g.centre, g.normale, 10)).normalize();
      const ds = versScene(ajout(g.centre, g.dir, 10)).normalize();
      const i = (r.inclinaison * Math.PI) / 180;
      const haut = new THREE.Vector3(0, 0, 1);
      const normale = ns.clone().multiplyScalar(Math.cos(i)).addScaledVector(haut, Math.sin(i)).normalize();
      if (r.epaisseur > 0) {
        plans = [
          new THREE.Plane().setFromNormalAndCoplanarPoint(normale, m.clone().addScaledVector(normale, -r.epaisseur / 2)),
          new THREE.Plane().setFromNormalAndCoplanarPoint(normale.clone().negate(), m.clone().addScaledVector(normale, r.epaisseur / 2)),
        ];
      } else {
        plans = [new THREE.Plane().setFromNormalAndCoplanarPoint(normale, m)];
      }
      // panneau dessiné dans le plan (et le second plan d'une tranche)
      const montee = haut.clone().multiplyScalar(Math.cos(i)).addScaledVector(ns, -Math.sin(i)).normalize();
      const demi = g.demi * 1.1;
      for (const p of plans) {
        const c = m.clone().addScaledVector(p.normal, -p.distanceToPoint(m));   // projection du pivot sur ce plan
        const coins = [[-demi, -BAS_M], [demi, -BAS_M], [demi, HAUT_M], [-demi, HAUT_M]]
          .map(([u, v]) => c.clone().addScaledVector(ds, u).addScaledVector(montee, v));
        const geo = new THREE.BufferGeometry().setFromPoints([coins[0], coins[1], coins[2], coins[0], coins[2], coins[3]]);
        this.groupe3d.add(new THREE.Mesh(geo, this.matPanneau));
        this.groupe3d.add(new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(coins), this.matBord));
      }
    }
    this.c3d.definirPlansCoupe(plans);
    // 2D : points et lignes des couches SIG, bâtiments 3D (plan vertical seulement)
    const vertical = g && r.inclinaison === 0;
    this.couches.definirCoupe(vertical && r.couper2d ? ['!', ['within', this._zoneRetiree(g)]] : null);
    this.batiments?.definirCoupe(vertical && r.couperBatiments ? (lon, lat) => this._retire(versLambert93(lon, lat), g) : null);
    this._dessinerTrace(g ? [g.a, g.b] : this.trace?.length ? this.trace : null, g);
    this._poignees(g);
    this.carte.triggerRepaint();
    this._rendre();
  }

  _dessinerTrace(ext, g = null) {
    const feats = [];
    if (ext && ext.length === 2) {
      feats.push({ type: 'Feature', geometry: { type: 'LineString', coordinates: ext.map(versLL) }, properties: {} });
      if (g) {
        const lg = 2 * g.demi;
        const texte = this.reglages.epaisseur > 0 ? `tranche de ${this.reglages.epaisseur.toLocaleString('fr-FR')} m` : '▲ côté conservé';
        feats.push({ type: 'Feature', geometry: { type: 'Point', coordinates: versLL(ajout(g.centre, g.normale, Math.max(3, lg / 8))) }, properties: { texte } });
      }
    }
    this.carte.getSource(SOURCE)?.setData({ type: 'FeatureCollection', features: feats });
  }

  /** Poignées déplaçables aux extrémités : déplacer l'une redéfinit la ligne (décalage et rotation intégrés). */
  _poignees(g) {
    for (const p of this.poignees) p.remove();
    this.poignees = [];
    if (!g) return;
    for (const [k, p] of [[0, g.a], [1, g.b]]) {
      const div = el('div', { className: 'sig-poignee-coupe', title: 'Déplacer l\'extrémité du plan de coupe' });
      const mk = new Marker({ element: div, draggable: true }).setLngLat(versLL(p)).addTo(this.carte);
      mk.on('dragend', () => {
        const q = versLambert93(mk.getLngLat().lng, mk.getLngLat().lat);
        const autre = k === 0 ? g.b : g.a;
        [this.a, this.b] = k === 0 ? [q, autre] : [autre, q];
        Object.assign(this.reglages, { decalage: 0, rotation: 0 });
        this.appliquer();
      });
      this.poignees.push(mk);
    }
  }

  /** Vue de face : regarde la coupe depuis le côté retiré, perpendiculairement au plan, cadrée sur sa longueur. */
  vueDeFace() {
    const g = this.geometrie();
    if (!g) return;
    const ll = versLL(g.centre);
    const sol = this.carte.getTerrain() ? this.carte.queryTerrainElevation(ll) ?? 0 : 0;
    this.carte.setBearing((Math.atan2(g.normale[0], g.normale[1]) * 180) / Math.PI);
    cadrer(this.carte, { centre: ll, hauteur: sol, taille: g.demi * 2 }, { pitch: 82 });
  }

  // ------------------------------------------------------------ volet

  section() {
    this.zone = el('div');
    this._rendre();
    return el('section', { className: 'sig-section' }, el('h3', { textContent: 'Plan de coupe' }), this.zone);
  }

  _rendre() {
    if (!this.zone) return;
    const r = this.reglages;
    const bouton = (texte, action, actif = false, desactive = false) => {
      const b = el('button', { type: 'button', className: `sig-bouton${actif ? ' sig-bouton--actif' : ''}`, textContent: texte, disabled: desactive });
      b.addEventListener('click', action);
      return b;
    };
    const sans = !this.a;
    const curseur = (libelle, cle, min, max, pas, unite) => {
      const i = el('input', { type: 'range', min, max, step: pas, value: r[cle], disabled: sans });
      const o = el('output', { textContent: `${Number(r[cle]).toLocaleString('fr-FR')} ${unite}` });
      i.addEventListener('input', () => { r[cle] = Number(i.value); o.textContent = `${Number(i.value).toLocaleString('fr-FR')} ${unite}`; this.appliquer(); });
      return el('div', { className: 'sig-reglage' }, el('span', { className: 'sig-reglage__libelle', textContent: libelle }), i, o);
    };
    const coche = (libelle, cle) => {
      const i = el('input', { type: 'checkbox', checked: r[cle], disabled: sans });
      i.addEventListener('change', () => { r[cle] = i.checked; this.appliquer(); });
      return el('label', { className: 'sig-option' }, i, ` ${libelle}`);
    };
    this.zone.replaceChildren(
      el('div', { className: 'sig-groupe-boutons' },
        bouton(this.trace ? 'Cliquez 2 points…' : sans ? 'Tracer la ligne de coupe' : 'Retracer', () => this.commencerTrace(), Boolean(this.trace)),
        bouton('Inverser le côté', () => { r.inverse = !r.inverse; this.appliquer(); }, false, sans),
        bouton('Vue de face', () => this.vueDeFace(), false, sans),
        bouton('Supprimer', () => this.supprimer(), false, sans && !this.trace)),
      curseur('Décalage', 'decalage', -100, 100, 0.5, 'm'),
      curseur('Rotation', 'rotation', -90, 90, 1, '°'),
      curseur('Inclinaison', 'inclinaison', 0, 85, 1, '°'),
      curseur('Épaisseur de tranche (0 : demi-espace)', 'epaisseur', 0, 100, 0.5, 'm'),
      el('div', { className: 'sig-options sig-options--colonne' },
        coche('Couper aussi les points et lignes des couches SIG', 'couper2d'),
        coche('Couper les bâtiments 3D', 'couperBatiments')),
      el('p', { className: 'sig-info', textContent: 'Tracez une ligne sur la carte (2 clics) : le plan vertical qui la porte coupe les maquettes, '
        + 'les locaux et le nuage LiDAR, et, s\'il est vertical, les bâtiments 3D et les points et lignes SIG. Poignées orange : '
        + 'déplacer les extrémités. Le terrain et les polygones 2D ne sont pas coupés.' }),
    );
  }
}

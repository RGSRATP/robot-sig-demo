// Plan de coupe ajustable (inspiré de l'outil « Slice » d'ArcGIS et de la coupe de Trimble Connect) : une ligne
// tracée sur la carte définit un plan vertical qui coupe les objets. Réglages : poignées aux extrémités (déplaçables),
// poignée centrale de décalage (glissée perpendiculairement au plan) ; dans le volet, décalage, rotation, inclinaison
// et épaisseur de tranche par boutons − / + au pas choisi ou par valeur saisie ; côté conservé, vue de face.
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
// Réglages du volet : bornes et pas proposés (le pas du décalage sert aussi à la poignée centrale)
const REGLAGES = {
  decalage: { libelle: 'Décalage', unite: 'm', min: -5000, max: 5000, pas: [0.1, 1, 10], defaut: 1 },
  rotation: { libelle: 'Rotation', unite: '°', min: -180, max: 180, pas: [1, 5, 15], defaut: 0 },
  inclinaison: { libelle: 'Inclinaison', unite: '°', min: 0, max: 85, pas: [1, 5, 15], defaut: 0 },
  epaisseur: { libelle: 'Épaisseur de tranche (0 : demi-espace)', unite: 'm', min: 0, max: 1000, pas: [0.1, 1, 10], defaut: 1 },
};
const REPETITION_MS = 400;             // bouton − / + maintenu : premier délai, puis de plus en plus vite
const REPETITION_MIN_MS = 60;

const ajout = (a, b, k = 1) => [a[0] + b[0] * k, a[1] + b[1] * k];
const versLL = ([x, y]) => depuisLambert93(x, y);
const nombre = (v) => v.toLocaleString('fr-FR', { maximumFractionDigits: 2 });

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
    this.pas = Object.fromEntries(Object.entries(REGLAGES).map(([k, d]) => [k, d.pas[d.defaut]]));
    this.groupe3d = new THREE.Group();
    this.matPanneau = new THREE.MeshBasicMaterial({ color: COULEUR, transparent: true, opacity: 0.06, side: THREE.DoubleSide, depthWrite: false });
    this.matBord = new THREE.LineBasicMaterial({ color: COULEUR });
    this.poignees = [];             // extrémités
    this.poigneeDecalage = null;    // poignée centrale
    carte.on('mousemove', (e) => { if (this.trace?.length === 1) this._dessinerTrace([this.trace[0], versLambert93(e.lngLat.lng, e.lngLat.lat)]); });
    carte.on('rotate', () => this._orienterFleche());
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

  /** Direction de la ligne après rotation (vecteur unitaire, Lambert 93). */
  _direction() {
    const lg = Math.hypot(this.b[0] - this.a[0], this.b[1] - this.a[1]);
    const t = (this.reglages.rotation * Math.PI) / 180;
    const d0 = [(this.b[0] - this.a[0]) / lg, (this.b[1] - this.a[1]) / lg];
    return [d0[0] * Math.cos(t) - d0[1] * Math.sin(t), d0[0] * Math.sin(t) + d0[1] * Math.cos(t)];
  }

  /** Plan effectif (Lambert 93) : centre, direction, normale du côté conservé, demi-longueur, extrémités. */
  geometrie() {
    if (!this.a || !this.b) return null;
    const r = this.reglages;
    const lg = Math.hypot(this.b[0] - this.a[0], this.b[1] - this.a[1]);
    const dir = this._direction();
    const gauche = [-dir[1], dir[0]];
    const centre = ajout([(this.a[0] + this.b[0]) / 2, (this.a[1] + this.b[1]) / 2], gauche, r.decalage);
    const normale = r.inverse ? [-gauche[0], -gauche[1]] : gauche;
    return { centre, dir, gauche, normale, demi: lg / 2, a: ajout(centre, dir, -lg / 2), b: ajout(centre, dir, lg / 2) };
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

  /** leger : pendant un glissement, la 3D, le tracé et les poignées seulement (filtres 2D au relâcher). */
  appliquer({ leger = false } = {}) {
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
    this._dessinerTrace(g ? [g.a, g.b] : this.trace?.length ? this.trace : null, g);
    this._poignees(g);
    this.carte.triggerRepaint();
    this._majValeurs();
    if (leger) return;
    // 2D : points et lignes des couches SIG, bâtiments 3D (plan vertical seulement)
    const vertical = g && r.inclinaison === 0;
    this.couches.definirCoupe(vertical && r.couper2d ? ['!', ['within', this._zoneRetiree(g)]] : null);
    this.batiments?.definirCoupe(vertical && r.couperBatiments ? (lon, lat) => this._retire(versLambert93(lon, lat), g) : null);
  }

  /** Réglage modifié (volet ou poignée) : borné, appliqué. */
  regler(cle, valeur, options) {
    const d = REGLAGES[cle];
    const v = Math.min(d.max, Math.max(d.min, Number(valeur)));
    if (!Number.isFinite(v) || !this.a) return;
    this.reglages[cle] = Math.round(v * 1000) / 1000;
    this.appliquer(options);
  }

  _dessinerTrace(ext, g = null) {
    const feats = [];
    if (ext && ext.length === 2) {
      feats.push({ type: 'Feature', geometry: { type: 'LineString', coordinates: ext.map(versLL) }, properties: {} });
      if (g) {
        const lg = 2 * g.demi;
        const texte = this.reglages.epaisseur > 0 ? `tranche de ${nombre(this.reglages.epaisseur)} m` : '▲ côté conservé';
        // à côté de la poignée centrale, pas dessous
        const pos = ajout(ajout(g.centre, g.normale, Math.max(3, lg / 8)), g.dir, lg / 4);
        feats.push({ type: 'Feature', geometry: { type: 'Point', coordinates: versLL(pos) }, properties: { texte } });
      }
    }
    this.carte.getSource(SOURCE)?.setData({ type: 'FeatureCollection', features: feats });
  }

  // ------------------------------------------------------------ poignées

  /** Poignées : extrémités (déplacer l'une redéfinit la ligne, décalage et rotation intégrés) et poignée centrale de
   *  décalage, contrainte à la perpendiculaire du plan. Créées une fois puis seulement déplacées : un marqueur
   *  recréé pendant son glissement l'interromprait. */
  _poignees(g) {
    if (!g) {
      for (const p of this.poignees) p.remove();
      this.poignees = [];
      this.poigneeDecalage?.remove();
      this.poigneeDecalage = null;
      return;
    }
    if (!this.poignees.length) {
      this.poignees = [0, 1].map((k) => {
        const div = el('div', { className: 'sig-poignee-coupe', title: 'Déplacer l’extrémité du plan de coupe' });
        const mk = new Marker({ element: div, draggable: true }).setLngLat(versLL(k === 0 ? g.a : g.b)).addTo(this.carte);
        mk.on('dragend', () => {
          const g0 = this.geometrie();
          const q = versLambert93(mk.getLngLat().lng, mk.getLngLat().lat);
          [this.a, this.b] = k === 0 ? [q, g0.b] : [g0.a, q];
          Object.assign(this.reglages, { decalage: 0, rotation: 0 });
          this.appliquer();
        });
        return mk;
      });
      this._creerPoigneeDecalage(g);
    }
    this.poignees[0].setLngLat(versLL(g.a));
    this.poignees[1].setLngLat(versLL(g.b));
    if (!this._glisse) this.poigneeDecalage.setLngLat(versLL(g.centre));
    const r = this.reglages;
    this.poigneeDecalage.getElement().querySelector('.sig-poignee-decalage__valeur').textContent =
      `${r.decalage > 0 ? '+' : ''}${nombre(r.decalage)} m`;
    this._orienterFleche();
  }

  /** Flèche de la poignée centrale orientée selon la normale du plan, à l'écran. */
  _orienterFleche() {
    const g = this.poigneeDecalage && this.geometrie();
    if (!g) return;
    const angle = (Math.atan2(g.gauche[0], g.gauche[1]) * 180) / Math.PI - this.carte.getBearing();
    this.poigneeDecalage.getElement().querySelector('.sig-poignee-decalage__fleche').style.transform = `rotate(${angle}deg)`;
  }

  _creerPoigneeDecalage(g) {
    const div = el('div', { className: 'sig-poignee-decalage', title: 'Glisser pour décaler le plan de coupe (au pas choisi dans le volet Vue 3D)' },
      el('span', { className: 'sig-poignee-decalage__fleche', textContent: '⇕' }),
      el('span', { className: 'sig-poignee-decalage__valeur' }));
    const mk = new Marker({ element: div, draggable: true }).setLngLat(versLL(g.centre)).addTo(this.carte);
    let image = null;
    mk.on('dragstart', () => { this._glisse = true; });
    mk.on('drag', () => {
      // décalage = distance du point glissé à la ligne d'origine, le long de sa perpendiculaire, arrondie au pas
      const q = versLambert93(mk.getLngLat().lng, mk.getLngLat().lat);
      const dir = this._direction();
      const milieu = [(this.a[0] + this.b[0]) / 2, (this.a[1] + this.b[1]) / 2];
      const pas = this.pas.decalage;
      const v = Math.round(distanceSignee(q, milieu, [-dir[1], dir[0]]) / pas) * pas;
      if (Math.abs(v - this.reglages.decalage) < 1e-9 || image) return;
      image = requestAnimationFrame(() => {
        image = null;
        this.regler('decalage', v, { leger: true });
      });
    });
    mk.on('dragend', () => {
      this._glisse = false;
      this.appliquer();   // poignée replacée sur la perpendiculaire, filtres 2D
    });
    this.poigneeDecalage = mk;
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

  /** Volet : redessiné seulement quand son contenu change (ligne tracée ou non, tracé en cours) ; les valeurs sont
   *  mises à jour sur place (redessiner pendant un réglage interrompait le curseur et la saisie). */
  _rendre() {
    if (!this.zone) return;
    const r = this.reglages;
    const sans = !this.a;
    const etat = `${sans}-${Boolean(this.trace)}`;
    if (etat === this._etatVolet) return;
    this._etatVolet = etat;
    const bouton = (texte, action, actif = false, desactive = false) => {
      const b = el('button', { type: 'button', className: `sig-bouton${actif ? ' sig-bouton--actif' : ''}`, textContent: texte, disabled: desactive });
      b.addEventListener('click', action);
      return b;
    };
    this._champs = {};
    const reglage = (cle) => {
      const d = REGLAGES[cle];
      const saisie = el('input', { type: 'number', className: 'sig-pas__valeur', min: d.min, max: d.max, step: 'any', value: r[cle],
        disabled: sans, title: 'Saisir une valeur (Entrée) ; flèches haut et bas : un pas' });
      saisie.addEventListener('change', () => this.regler(cle, saisie.value));
      saisie.addEventListener('keydown', (e) => {
        if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;
        e.preventDefault();
        this.regler(cle, r[cle] + (e.key === 'ArrowUp' ? 1 : -1) * this.pas[cle]);
        saisie.value = r[cle];
      });
      const choix = el('select', { className: 'sig-pas__choix', disabled: sans,
        title: cle === 'decalage' ? 'Pas des boutons − / + et de la poignée centrale' : 'Pas des boutons − / +' },
      ...d.pas.map((p) => el('option', { value: p, selected: p === this.pas[cle], textContent: `pas ${nombre(p)} ${d.unite}` })));
      choix.addEventListener('change', () => { this.pas[cle] = Number(choix.value); });
      const pousser = (sens) => {
        const b = el('button', { type: 'button', className: 'sig-pas__bouton', textContent: sens < 0 ? '−' : '+', disabled: sans,
          title: `${sens < 0 ? 'Diminuer' : 'Augmenter'} d’un pas (maintenir pour répéter)` });
        let minuterie = null;
        const arreter = () => { clearTimeout(minuterie); minuterie = null; };
        const repeter = (delai) => {
          this.regler(cle, r[cle] + sens * this.pas[cle]);
          minuterie = setTimeout(() => repeter(Math.max(REPETITION_MIN_MS, delai * 0.8)), delai);
        };
        b.addEventListener('pointerdown', (e) => { if (e.button === 0) { e.preventDefault(); arreter(); repeter(REPETITION_MS); } });
        for (const ev of ['pointerup', 'pointerleave', 'pointercancel']) b.addEventListener(ev, arreter);
        return b;
      };
      const zero = el('button', { type: 'button', className: 'sig-pas__zero', textContent: '0', disabled: sans, title: 'Remettre à zéro' });
      zero.addEventListener('click', () => this.regler(cle, 0));
      this._champs[cle] = saisie;
      return el('div', { className: 'sig-pas' },
        el('span', { className: 'sig-pas__libelle', textContent: `${d.libelle} (${d.unite})` }),
        pousser(-1), saisie, pousser(1), choix, zero);
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
      ...Object.keys(REGLAGES).map(reglage),
      el('div', { className: 'sig-options sig-options--colonne' },
        coche('Couper aussi les points et lignes des couches SIG', 'couper2d'),
        coche('Couper les bâtiments 3D', 'couperBatiments')),
      el('p', { className: 'sig-info', textContent: 'Tracez une ligne sur la carte (2 clics) : le plan vertical qui la porte coupe les maquettes, '
        + 'les locaux et le nuage LiDAR, et, s’il est vertical, les bâtiments 3D et les points et lignes SIG. Sur la carte : poignées '
        + 'rondes aux extrémités pour déplacer la ligne, poignée centrale ⇕ à glisser pour décaler le plan (au pas choisi). '
        + 'Ici : boutons − / + (maintenus, ils répètent), flèches haut et bas dans la valeur, ou saisie directe. '
        + 'Le terrain et les polygones 2D ne sont pas coupés.' }),
    );
  }

  /** Valeurs du volet mises à jour sans le redessiner (la valeur en cours de saisie est laissée). */
  _majValeurs() {
    this._rendre();
    for (const [cle, champ] of Object.entries(this._champs || {})) {
      if (document.activeElement !== champ) champ.value = this.reglages[cle];
    }
  }
}

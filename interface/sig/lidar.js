// Nuage de points LiDAR HD (config/sig.yaml, section « lidar ») : dalles de 1 km² de l'IGN au format COPC, listées
// par le WFS de la Géoplateforme, lues par morceaux (requêtes partielles) par le navigateur. Seuls les nœuds de
// l'octree qui recoupent la vue, jusqu'au niveau de détail utile au zoom, sont lus, dans la limite d'un budget de
// points. Décompression LAZ par laz-perf (WebAssembly servi par l'interface).
//
// Dessin dans la scène Three.js des maquettes (vue3d.js) : un groupe par dalle, positions en mètres depuis le coin
// sud-ouest de la dalle (Lambert 93), placé par trois points de contrôle comme une maquette. Les altitudes sont des
// altitudes IGN69 (NGF) : exactes en relief ; sur la carte plate, la dalle est abaissée de l'altitude moyenne de
// son sol. Un objet THREE.Points par nœud et par classe : afficher ou masquer une classe ne recalcule rien.
import * as THREE from 'three';
import { Copc } from 'copc';
import { createLazPerf } from 'laz-perf';
import urlWasm from 'laz-perf/lib/web/laz-perf.wasm?url';
import { depuisLambert93, versLambert93 } from './lambert93.js';
import { el } from './ui.js';

const PAS_CONTROLE_M = 100;
const M_PAR_PX_Z0 = 78271.517;   // mètres par pixel CSS au zoom 0 (tuiles de 512 px)
const CHEMIN_DALLE = /LHD_FXX_(\d{4})_(\d{4})_/;   // coin nord-ouest de la dalle, en km Lambert 93

let lazPerf = null;
const decodeur = () => (lazPerf ??= createLazPerf({ locateFile: (f) => (f.endsWith('.wasm') ? urlWasm : f) }));

// Le service de téléchargement de l'IGN limite les lectures (de l'ordre d'une par seconde, refus « 429 » au-delà) :
// une seule lecture à la fois, toutes dalles confondues, et nouvel essai après un délai croissant en cas de refus.
const ESSAIS_429 = 6;
let file = Promise.resolve();
const attendre = (ms) => new Promise((r) => { setTimeout(r, ms); });

/** Lecture par plages d'octets d'un fichier distant (requête HTTP Range), mise en file. */
function lecteur(url, surAttente) {
  const lire = async (debut, fin) => {
    for (let essai = 0; ; essai += 1) {
      const r = await fetch(url, { headers: { Range: `bytes=${debut}-${fin - 1}` } });
      if (r.status === 429 && essai < ESSAIS_429) {
        surAttente?.();
        await attendre(1000 * 2 ** essai);
        continue;
      }
      if (!r.ok) throw new Error(r.status === 429 ? 'serveur IGN saturé (trop de lectures), réessayez plus tard' : `HTTP ${r.status}`);
      return new Uint8Array(await r.arrayBuffer());
    }
  };
  return (debut, fin) => {
    const p = file.then(() => lire(debut, fin));
    file = p.catch(() => {});
    return p;
  };
}

/** Emprise (Lambert 93) d'un nœud « d-x-y-z » de l'octree. */
function boiteNoeud(cube, cle) {
  const [d, x, y, z] = cle.split('-').map(Number);
  const t = (cube[3] - cube[0]) / 2 ** d;
  return { d, min: [cube[0] + x * t, cube[1] + y * t, cube[2] + z * t], t };
}

export class GestionnaireLidar {
  /** c3d : couche 3D des maquettes ; avantActivation() : affiche le relief (altitudes NGF communes). */
  constructor(carte, c3d, cfg, { avantActivation }) {
    this.carte = carte;
    this.c3d = c3d;
    this.cfg = cfg;
    this.avantActivation = avantActivation;
    this.actif = false;
    this.mode = cfg.couleur;
    this.taille = cfg.taille_point_px;
    this.classes = new Map(cfg.classes.map((c) => [c.code, { ...c }]));
    this.dalles = new Map();      // url -> { copc, noeuds (hiérarchie), groupe, affichage, charges: Map(cle -> objets), sol }
    this.messages = new Map();    // url -> message d'erreur
    this.materiaux = new Map();   // code de classe -> PointsMaterial (couleur de la classe)
    this.matSommets = new THREE.PointsMaterial({ size: this.taille, sizeAttenuation: false, vertexColors: true });
    this.seq = 0;
    carte.on('moveend', () => this._planifier());
  }

  _materiau(code) {
    if (!this.materiaux.has(code)) {
      const coul = this.classes.get(code)?.couleur ?? this.cfg.couleur_autres_classes;
      this.materiaux.set(code, new THREE.PointsMaterial({ size: this.taille, sizeAttenuation: false, color: coul }));
    }
    return this.materiaux.get(code);
  }

  // ------------------------------------------------------------ choix des dalles et des nœuds

  _planifier() {
    clearTimeout(this._minuterie);
    this._minuterie = setTimeout(() => this.mettreAJour(), 250);
  }

  async mettreAJour() {
    const seq = (this.seq += 1);
    if (!this.actif || this.carte.getZoom() < this.cfg.zoom_min) {
      this._etat(this.actif ? `Zoomez (niveau ${this.cfg.zoom_min} ou plus) pour charger le nuage de points.` : '');
      if (!this.actif) this._viderTout();
      return;
    }
    const b = this.carte.getBounds();
    const urlWfs = this.cfg.wfs_dalles.replace('{sud}', b.getSouth()).replace('{ouest}', b.getWest())
      .replace('{nord}', b.getNorth()).replace('{est}', b.getEast());
    let dalles;
    try {
      const r = await fetch(urlWfs);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      dalles = (await r.json()).features || [];
    } catch (e) {
      this._etat(`Liste des dalles LiDAR indisponible (${e.message}) : service WFS de la Géoplateforme injoignable ?`, true);
      return;
    }
    if (seq !== this.seq) return;
    this.ralenti = false;
    const c = this.carte.getCenter();
    const [cx, cy] = versLambert93(c.lng, c.lat);
    const retenues = dalles.map((f) => {
      const url = f.properties[this.cfg.champ_url];
      const m = CHEMIN_DALLE.exec(url || '');
      if (!m) return null;
      const x0 = Number(m[1]) * 1000;
      const y1 = Number(m[2]) * 1000;
      return { url, x0, y0: y1 - 1000, date: f.properties[this.cfg.champ_date], dist: Math.hypot(cx - x0 - 500, cy - y1 + 500) };
    }).filter(Boolean).sort((p, q) => p.dist - q.dist).slice(0, this.cfg.max_dalles);
    if (!retenues.length) {
      this._etat('Aucune dalle LiDAR HD publiée sur cette zone.');
      this._viderTout();
      return;
    }
    // vue en Lambert 93 (enveloppe des quatre coins) et niveau de détail voulu
    const coins = [b.getSouthWest(), b.getNorthWest(), b.getNorthEast(), b.getSouthEast()].map((p) => versLambert93(p.lng, p.lat));
    const vue = [Math.min(...coins.map((p) => p[0])), Math.min(...coins.map((p) => p[1])),
      Math.max(...coins.map((p) => p[0])), Math.max(...coins.map((p) => p[1]))];
    const mpp = (M_PAR_PX_Z0 * Math.cos((c.lat * Math.PI) / 180)) / 2 ** this.carte.getZoom();
    for (const url of [...this.dalles.keys()]) if (!retenues.some((d) => d.url === url)) this._retirerDalle(url);
    // budget réparti selon la proximité du centre : la dalle sous le centre de la vue reçoit la plus grosse part
    const poids = retenues.map((_, i) => 1 / (i + 1.5));
    const somme = poids.reduce((t, w) => t + w, 0);
    this.messages.clear();
    await Promise.all(retenues.map((d, i) => this._chargerDalle(d, vue, [cx, cy], mpp,
      Math.floor((this.cfg.budget_points * poids[i]) / somme), seq).catch((e) => {
      this.messages.set(d.url, `${d.url.split('/').pop()} : ${e.message || e}`);
    })));
    if (seq === this.seq) this._majEtat();
  }

  async _ouvrirDalle(d) {
    if (this.dalles.has(d.url)) {   // ouverte ou en cours d'ouverture (mise à jour précédente)
      const e = this.dalles.get(d.url);
      await e.ouverture;
      return e;
    }
    const get = lecteur(d.url, () => { this.ralenti = true; });
    const entree = { url: d.url, get, x0: d.x0, y0: d.y0, date: d.date, charges: new Map(), sol: null };
    entree.ouverture = (async () => {
      const copc = await Copc.create(get);
      if (copc.wkt && !/2154|Lambert[-_ ]?93/i.test(copc.wkt)) {
        throw new Error(`système des points différent de ${this.cfg.systeme} : dalle ignorée`);
      }
      const page = await Copc.loadHierarchyPage(get, copc.info.rootHierarchyPage);
      entree.copc = copc;
      entree.noeuds = page.nodes;
      entree.pages = page.pages;
      const [o, ax, ay] = [[d.x0, d.y0], [d.x0 + PAS_CONTROLE_M, d.y0], [d.x0, d.y0 + PAS_CONTROLE_M]].map(([x, y]) => depuisLambert93(x, y));
      entree.affichage = { origine: o, axe_x: ax, axe_y: ay, pas_m: PAS_CONTROLE_M, decalage_z_m: 0, nuage: true, sol: 0 };
      entree.groupe = new THREE.Group();
      entree.groupe.matrixAutoUpdate = false;
      entree.groupe.userData.affichage = entree.affichage;
      this.c3d.ajouterNuage(entree.groupe);
    })();
    this.dalles.set(d.url, entree);
    try {
      await entree.ouverture;
    } catch (e) {
      this.dalles.delete(d.url);
      throw e;
    }
    return entree;
  }

  /** Charge les nœuds de la dalle utiles à la vue (du grossier au fin, les plus proches du centre de la vue
   *  d'abord, à chaque niveau), retire les autres. */
  async _chargerDalle(d, vue, centre, mpp, budget, seq) {
    const dalle = await this._ouvrirDalle(d);
    if (seq !== this.seq) return;
    const { cube, spacing } = dalle.copc.info;
    // pages de hiérarchie non encore lues (grosses dalles) : lues à la demande
    for (const [cle, page] of Object.entries(dalle.pages || {})) {
      if (page && !dalle.pagesLues?.has(cle)) {
        const sous = await Copc.loadHierarchyPage(dalle.get, page);
        Object.assign(dalle.noeuds, sous.nodes);
        (dalle.pagesLues ??= new Set()).add(cle);
      }
    }
    const profondeurMax = Math.max(0, Math.ceil(Math.log2(spacing / (mpp * 1.5))));
    const voulus = [];
    let total = 0;
    // file à priorité : niveau, puis distance du centre de la vue au centre du nœud
    const file = [{ cle: '0-0-0-0', bx: boiteNoeud(cube, '0-0-0-0') }];
    const rang = (e) => e.bx.d * 1e9 + Math.hypot(e.bx.min[0] + e.bx.t / 2 - centre[0], e.bx.min[1] + e.bx.t / 2 - centre[1]);
    while (file.length) {
      let k = 0;
      for (let i = 1; i < file.length; i += 1) if (rang(file[i]) < rang(file[k])) k = i;
      const { cle, bx } = file.splice(k, 1)[0];
      const n = dalle.noeuds[cle];
      if (!n) continue;
      if (bx.min[0] > vue[2] || bx.min[0] + bx.t < vue[0] || bx.min[1] > vue[3] || bx.min[1] + bx.t < vue[1]) continue;
      if (n.pointCount > 0) {
        if (total + n.pointCount > budget && voulus.length) break;
        voulus.push(cle);
        total += n.pointCount;
      }
      if (bx.d >= profondeurMax) continue;
      const [, x, y, z] = cle.split('-').map(Number);
      for (let i = 0; i < 8; i += 1) {
        const enfant = `${bx.d + 1}-${2 * x + (i & 1)}-${2 * y + ((i >> 1) & 1)}-${2 * z + ((i >> 2) & 1)}`;
        if (dalle.noeuds[enfant]) file.push({ cle: enfant, bx: boiteNoeud(cube, enfant) });
      }
    }
    const garder = new Set(voulus);
    for (const cle of [...dalle.charges.keys()]) if (!garder.has(cle)) this._retirerNoeud(dalle, cle);
    for (const cle of voulus) {
      if (seq !== this.seq) return;
      if (!dalle.charges.has(cle)) await this._chargerNoeud(dalle, cle);
    }
  }

  async _chargerNoeud(dalle, cle) {
    dalle.charges.set(cle, null);   // réservé : pas de double lecture
    const vue = await Copc.loadPointDataView(dalle.get, dalle.copc, dalle.noeuds[cle], { lazPerf: await decodeur() });
    if (!this.dalles.has(dalle.url) || !dalle.charges.has(cle)) return;
    const n = vue.pointCount;
    const X = vue.getter('X'); const Y = vue.getter('Y'); const Z = vue.getter('Z');
    const C = vue.getter('Classification');
    const rgb = ['Red', 'Green', 'Blue'].every((k) => vue.dimensions[k]) ? ['Red', 'Green', 'Blue'].map((k) => vue.getter(k)) : null;
    const [zmin, zmax] = [dalle.copc.header.min[2], dalle.copc.header.max[2]];
    // regroupement par classe
    const parClasse = new Map();
    for (let i = 0; i < n; i += 1) {
      const c = C(i);
      if (!parClasse.has(c)) parClasse.set(c, []);
      parClasse.get(c).push(i);
    }
    // sol moyen de la dalle (carte plate), d'après le premier nœud lu (racine)
    if (dalle.sol == null) {
      const sol = parClasse.get(2) || [];
      dalle.sol = sol.length ? sol.reduce((t, i) => t + Z(i), 0) / sol.length : (zmin + zmax) / 2;
      dalle.affichage.sol = dalle.sol;
      dalle.groupe.matrix.copy(this.c3d.matriceNuage(dalle.affichage));
    }
    const objets = [];
    for (const [code, indices] of parClasse) {
      const pos = new Float32Array(indices.length * 3);
      const alt = new Uint8Array(indices.length * 3);
      const vrai = rgb ? new Uint8Array(indices.length * 3) : null;
      indices.forEach((i, k) => {
        pos[k * 3] = X(i) - dalle.x0; pos[k * 3 + 1] = Y(i) - dalle.y0; pos[k * 3 + 2] = Z(i);
        const t = Math.min(1, Math.max(0, (Z(i) - zmin) / Math.max(1, zmax - zmin)));
        alt[k * 3] = 40 + 215 * t; alt[k * 3 + 1] = 90 + 120 * (1 - Math.abs(t - 0.5) * 2); alt[k * 3 + 2] = 220 - 200 * t;
        if (vrai) { for (let j = 0; j < 3; j += 1) vrai[k * 3 + j] = rgb[j](i) > 255 ? rgb[j](i) >> 8 : rgb[j](i); }
      });
      const geo = new THREE.BufferGeometry();
      geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
      geo.userData.altitude = new THREE.BufferAttribute(alt, 3, true);
      geo.userData.rgb = vrai && new THREE.BufferAttribute(vrai, 3, true);
      const pts = new THREE.Points(geo);
      pts.userData.classe = code;
      pts.frustumCulled = false;
      this._styler(pts);
      dalle.groupe.add(pts);
      objets.push(pts);
    }
    dalle.groupe.updateMatrixWorld(true);
    dalle.charges.set(cle, objets);
    this.c3d.carte?.triggerRepaint();
    this._majEtat();
  }

  _styler(pts) {
    const c = this.classes.get(pts.userData.classe);
    pts.visible = c ? c.visible : true;
    const geo = pts.geometry;
    const sommets = this.mode === 'altitude' ? geo.userData.altitude : this.mode === 'rgb' ? geo.userData.rgb : null;
    if (sommets) {
      geo.setAttribute('color', sommets);
      pts.material = this.matSommets;
    } else {
      pts.material = this._materiau(pts.userData.classe);
    }
  }

  _restyler() {
    for (const m of [...this.materiaux.values(), this.matSommets]) m.size = this.taille;
    for (const d of this.dalles.values()) for (const objets of d.charges.values()) for (const p of objets || []) this._styler(p);
    this.c3d.carte?.triggerRepaint();
    this._majEtat();
  }

  _retirerNoeud(dalle, cle) {
    for (const p of dalle.charges.get(cle) || []) {
      dalle.groupe.remove(p);
      p.geometry.dispose();
    }
    dalle.charges.delete(cle);
  }

  _retirerDalle(url) {
    const d = this.dalles.get(url);
    if (!d) return;
    for (const cle of [...d.charges.keys()]) this._retirerNoeud(d, cle);
    if (d.groupe) this.c3d.retirerNuage(d.groupe);
    this.dalles.delete(url);
  }

  _viderTout() {
    for (const url of [...this.dalles.keys()]) this._retirerDalle(url);
    this.messages.clear();
    this.c3d.carte?.triggerRepaint();
  }

  // ------------------------------------------------------------ interface

  nbPoints() {
    let n = 0;
    for (const d of this.dalles.values()) for (const objets of d.charges.values()) for (const p of objets || []) {
      if (p.visible) n += p.geometry.getAttribute('position').count;
    }
    return n;
  }

  _majEtat() {
    const dalles = [...this.dalles.values()];
    const sansRgb = this.mode === 'rgb' && dalles.length && dalles.every((d) => [...d.charges.values()].every((o) => !o?.[0]?.geometry.userData.rgb));
    const texte = dalles.length
      ? `${this.nbPoints().toLocaleString('fr-FR')} points affichés · ${dalles.length} dalle(s) LiDAR HD`
        + (dalles[0].date ? ` (acquisition ${String(dalles[0].date).slice(0, 10)})` : '')
        + (sansRgb ? ' · pas de couleurs réelles dans ces dalles' : '')
        + (this.ralenti ? ' · lecture ralentie par le serveur IGN (limite de débit)' : '')
      : '';
    const erreurs = [...this.messages.values()];
    this._etat([texte, ...erreurs].filter(Boolean).join(' · '), erreurs.length > 0);
  }

  _etat(texte, erreur = false) {
    if (!this.zoneEtat) return;
    this.zoneEtat.textContent = texte;
    this.zoneEtat.classList.toggle('sig-alerte', erreur);
  }

  activer(v) {
    this.actif = v;
    if (this._case) this._case.checked = v;
    if (v) this.avantActivation();
    this.messages.clear();
    this.mettreAJour();
  }

  /** Section « Nuage de points LiDAR HD » du volet des fonds. */
  section() {
    const actif = el('input', { type: 'checkbox', checked: this.actif });
    actif.addEventListener('change', () => this.activer(actif.checked));
    this._case = actif;
    const mode = el('select', {},
      ...[['classe', 'Par classe'], ['altitude', 'Par altitude'], ['rgb', 'Couleurs réelles']]
        .map(([v, t]) => el('option', { value: v, textContent: t, selected: v === this.mode })));
    mode.addEventListener('change', () => { this.mode = mode.value; this._restyler(); });
    const taille = el('input', { type: 'range', min: 1, max: 6, step: 0.5, value: this.taille });
    const valT = el('output', { textContent: `${this.taille} px` });
    taille.addEventListener('input', () => { this.taille = Number(taille.value); valT.textContent = `${this.taille} px`; this._restyler(); });
    const classes = el('ul', { className: 'sig-legende' }, ...[...this.classes.values()].map((c) => {
      const i = el('input', { type: 'checkbox', checked: c.visible });
      i.addEventListener('change', () => { c.visible = i.checked; this._restyler(); });
      return el('li', {}, i, el('span', { className: 'sig-legende__pastille', style: `background:${c.couleur}` }),
        el('span', { textContent: c.libelle }));
    }));
    this.zoneEtat = el('p', { className: 'sig-info' });
    return el('section', { className: 'sig-section' }, el('h3', { textContent: 'Nuage de points LiDAR HD' }),
      el('div', { className: 'sig-options sig-options--colonne' },
        el('label', { className: 'sig-option' }, actif, ` Afficher le nuage (à partir du zoom ${this.cfg.zoom_min})`)),
      el('div', { className: 'sig-reglage' }, el('span', { className: 'sig-reglage__libelle', textContent: 'Couleurs' }), mode),
      el('div', { className: 'sig-reglage' }, el('span', { className: 'sig-reglage__libelle', textContent: 'Taille des points' }), taille, valT),
      classes, this.zoneEtat,
      el('p', { className: 'sig-info', textContent: `Dalles de 1 km² de l'IGN (${this.cfg.attribution.replace(/^© /, '')}), lues par morceaux : `
        + `au plus ${this.cfg.max_dalles} dalles et ${Number(this.cfg.budget_points).toLocaleString('fr-FR')} points. `
        + 'Altitudes NGF : le relief est affiché avec le nuage.' }));
  }
}

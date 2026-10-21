// Carte de gestion de crise (lot C1) : l'utilisateur place l'événement, tire un cercle au rayon libre (ou dessine un
// polygone) et voit ce qui est touché, zone par zone. Le serveur (/api/sig/crise/analyse) renvoie tous les objets
// jusqu'au rayon maximal avec leur distance au point : pendant le glissement, l'appartenance aux cercles est
// recalculée ici (distance ≤ rayon), sans appel ; au relâchement, une analyse complète suit (longueurs, PLAI…).
// Cercles calculés en Lambert 93 (vrais cercles au sol), jamais dans la projection de la carte.
import { lire } from './api.js';
import { depuisLambert93, versLambert93 } from './lambert93.js';
import { el } from './ui.js';
import { exporterGeojson, exporterPdf, telecharger } from './crise_export.js';

const SOURCE_ZONES = 'sig-crise-zones';
const SOURCE_ELEMENTS = 'sig-crise-elements';
const POINTS_CERCLE = 180;
const TOLERANCE_PX = 10;          // prise d'une poignée ou du centre
const CLIC_SANS_GLISSER_PX = 6;   // en dessous : simple clic, rayon par défaut
const MAX_LISTE = 40;             // éléments montrés par catégorie (les autres : dans l'export)
const COULEUR_EVENEMENT = '#c92a2a';
const COULEUR_REMPLACEMENT = '#2b8a3e';
const DELAI_ANALYSE_MS = 60000;   // au-delà : abandon, message et bouton « Relancer »

const nf = (v, d = 0) => Number(v).toLocaleString('fr-FR', { maximumFractionDigits: d });
const distance = (m) => (m >= 1000 ? `${nf(m / 1000, 2)} km` : `${nf(m)} m`);
const heure = (iso) => (iso ? new Date(iso).toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' }) : '');

async function poster(chemin, corps, params = {}, signal = undefined) {
  const url = new URL(`api/sig/${chemin}`, document.baseURI);
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);
  const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(corps), signal });
  if (!r.ok) {
    const e = await r.json().catch(() => ({}));
    throw new Error(e.detail || `réponse ${r.status}`);
  }
  return r;
}

/** Anneau [lon, lat] d'un vrai cercle au sol (Lambert 93). */
function cercle([lon, lat], rayon) {
  const [cx, cy] = versLambert93(lon, lat);
  const anneau = [];
  for (let i = 0; i <= POINTS_CERCLE; i += 1) {
    const a = (2 * Math.PI * i) / POINTS_CERCLE;
    anneau.push(depuisLambert93(cx + rayon * Math.sin(a), cy + rayon * Math.cos(a)));
  }
  return anneau;
}

const CLE_AUTEUR = 'sig-crise-auteur';
function lireAuteur() {
  try { return localStorage.getItem(CLE_AUTEUR) || ''; } catch { return ''; }
}
function garderAuteur(nom) {
  try { localStorage.setItem(CLE_AUTEUR, nom); } catch { /* navigateur sans stockage : nom gardé pour la session */ }
}

function decaler([lon, lat], dx, dy) {
  const [x, y] = versLambert93(lon, lat);
  return depuisLambert93(x + dx, y + dy);
}

function distanceL93(a, b) {
  const [x1, y1] = versLambert93(a[0], a[1]);
  const [x2, y2] = versLambert93(b[0], b[1]);
  return Math.hypot(x2 - x1, y2 - y1);
}

/** Saisie de coordonnées : « X Y » en Lambert 93 ou « lat, lon » en degrés. */
export function lireCoordonnees(texte) {
  const n = String(texte).replace(/,(?=\d)/g, '.').split(/[\s;,]+/).filter(Boolean).map(Number);
  if (n.length !== 2 || n.some((v) => !Number.isFinite(v))) return null;
  const [a, b] = n;
  if (a > 1000 && b > 1000) return depuisLambert93(a, b);           // Lambert 93
  if (Math.abs(a) <= 90 && Math.abs(b) <= 180) return [b, a];        // lat, lon
  return null;
}

export class OutilCrise {
  constructor(carte, cfg, { panneau, coque, couches, maquettes, police, ouvrirVolet }) {
    this.carte = carte;
    this.cfg = cfg;
    this.panneau = panneau;
    this.coque = coque;
    this.couches = couches;
    this.maquettes = maquettes;
    this.police = police;
    this.ouvrirVolet = ouvrirVolet;
    this.roles = cfg.zones.map((z) => z.id);
    this.zonesCfg = new Map(cfg.zones.map((z, i) => [z.id, { ...z, rang: i }]));
    this.evt = null;            // { id, titre, type, mode, debut, description, centre, zones }
    this.resultat = null;       // dernière analyse du serveur
    this.mode = null;           // null | 'placement' | 'polygone'
    this.glisse = null;         // { type: 'rayon' | 'centre', index, depart, nouveau }
    this.polygone = [];         // sommets en cours de saisie
    this.rolePolygone = this.roles[0];
    this.historique = [];
    this.assombrir = true;
    this._requete = 0;
    this._rendu = null;
    this.enregistres = null;
    this.message = null;
    this.statuts = new Map(cfg.statuts.map((x) => [x.id, x]));
    this.statutDefaut = cfg.statuts[0].id;
    this.categoriesCfg = new Map(cfg.categories.map((k) => [k.id, k]));
    this.auteur = lireAuteur() || cfg.auteur_defaut || '';
    this.changements = null;      // dernier changement de périmètre (ajouts, retraits)
    this.etatSauvegarde = '';
    this.bandeau = el('div', { className: 'sig-crise-bandeau', hidden: true });
    coque.zone.append(this.bandeau);
  }

  // ------------------------------------------------------------ installation

  installer() {
    const c = this.carte;
    const vide = { type: 'FeatureCollection', features: [] };
    c.addSource(SOURCE_ZONES, { type: 'geojson', data: vide });
    c.addSource(SOURCE_ELEMENTS, { type: 'geojson', data: vide });
    const police = [this.police || 'Source Sans Pro Regular'];
    c.addLayer({ id: 'sig-crise-masque', type: 'fill', source: SOURCE_ZONES, filter: ['==', ['get', 'nature'], 'masque'],
      paint: { 'fill-color': '#1b1e24', 'fill-opacity': 0.28 } });
    c.addLayer({ id: 'sig-crise-zones', type: 'fill', source: SOURCE_ZONES, filter: ['==', ['get', 'nature'], 'zone'],
      layout: { 'fill-sort-key': ['-', 100, ['get', 'rang']] },
      paint: { 'fill-color': ['get', 'couleur'], 'fill-opacity': 0.16 } });
    c.addLayer({ id: 'sig-crise-contours', type: 'line', source: SOURCE_ZONES, filter: ['==', ['get', 'nature'], 'zone'],
      paint: { 'line-color': ['get', 'couleur'], 'line-width': 2.5 } });
    c.addLayer({ id: 'sig-crise-polygone', type: 'line', source: SOURCE_ZONES, filter: ['==', ['get', 'nature'], 'saisie'],
      paint: { 'line-color': ['get', 'couleur'], 'line-width': 2, 'line-dasharray': [2, 1.5] } });
    c.addLayer({ id: 'sig-crise-lignes', type: 'line', source: SOURCE_ELEMENTS, filter: ['==', ['geometry-type'], 'LineString'],
      layout: { 'line-cap': 'round' },
      paint: { 'line-color': ['get', 'couleur'], 'line-width': 6, 'line-dasharray': [1.5, 1] } });
    c.addLayer({ id: 'sig-crise-ouvrages', type: 'line', source: SOURCE_ELEMENTS, filter: ['==', ['geometry-type'], 'Polygon'],
      paint: { 'line-color': ['get', 'couleur'], 'line-width': 3 } });
    c.addLayer({ id: 'sig-crise-points', type: 'circle', source: SOURCE_ELEMENTS, filter: ['==', ['geometry-type'], 'Point'],
      layout: { 'circle-sort-key': ['get', 'ordre'] },
      paint: { 'circle-radius': ['interpolate', ['linear'], ['zoom'], 11, ['*', ['get', 'rayon'], 0.35], 15, ['get', 'rayon']],
        'circle-opacity': ['case', ['coalesce', ['get', 'secondaire'], false], 0.75, 1], 'circle-stroke-opacity': ['case', ['coalesce', ['get', 'secondaire'], false], 0.6, 1],
        'circle-color': ['case', ['coalesce', ['get', 'creux'], false], '#ffffff', ['get', 'couleur']],
        'circle-stroke-color': ['case', ['coalesce', ['get', 'inutilisable'], false], '#000000',
          ['coalesce', ['get', 'creux'], false], ['get', 'couleur'], '#1b1e24'],
        'circle-stroke-width': ['case', ['coalesce', ['get', 'inutilisable'], false], 3, ['coalesce', ['get', 'creux'], false], 2.5, 1.5] } });
    c.addLayer({ id: 'sig-crise-croix', type: 'symbol', source: SOURCE_ELEMENTS,
      filter: ['all', ['==', ['geometry-type'], 'Point'], ['has', 'barre']],
      layout: { 'text-field': '×', 'text-font': police, 'text-size': ['interpolate', ['linear'], ['zoom'], 11, 8, 15, 17],
        'text-allow-overlap': true, 'text-ignore-placement': true },
      paint: { 'text-color': '#ffffff', 'text-halo-color': '#1b1e24', 'text-halo-width': 0.8 } });
    c.addLayer({ id: 'sig-crise-etiquettes', type: 'symbol', source: SOURCE_ELEMENTS,
      filter: ['all', ['==', ['geometry-type'], 'Point'], ['has', 'etiquette']],
      layout: { 'text-field': ['get', 'etiquette'], 'text-font': police, 'text-size': 12, 'text-offset': [0, 1.3],
        'text-anchor': 'top', 'text-max-width': 12, 'symbol-sort-key': ['get', 'ordre'] },
      paint: { 'text-color': '#1b1e24', 'text-halo-color': '#ffffff', 'text-halo-width': 2 } });
    c.addLayer({ id: 'sig-crise-rayons', type: 'symbol', source: SOURCE_ZONES, filter: ['==', ['get', 'nature'], 'libelle'],
      layout: { 'text-field': ['get', 'texte'], 'text-font': police, 'text-size': 13, 'text-allow-overlap': true, 'text-anchor': 'bottom' },
      paint: { 'text-color': ['get', 'couleur'], 'text-halo-color': '#ffffff', 'text-halo-width': 2.5 } });
    c.addLayer({ id: 'sig-crise-poignees', type: 'circle', source: SOURCE_ZONES, filter: ['==', ['get', 'nature'], 'poignee'],
      paint: { 'circle-radius': 7, 'circle-color': '#ffffff', 'circle-stroke-color': ['get', 'couleur'], 'circle-stroke-width': 3 } });
    c.addLayer({ id: 'sig-crise-centre', type: 'circle', source: SOURCE_ZONES, filter: ['==', ['get', 'nature'], 'centre'],
      paint: { 'circle-radius': 9, 'circle-color': COULEUR_EVENEMENT, 'circle-stroke-color': '#ffffff', 'circle-stroke-width': 3 } });
    c.addLayer({ id: 'sig-crise-curseur', type: 'symbol', source: SOURCE_ZONES, filter: ['==', ['get', 'nature'], 'curseur'],
      layout: { 'text-field': ['get', 'texte'], 'text-font': police, 'text-size': 22, 'text-allow-overlap': true,
        'text-anchor': 'left', 'text-offset': [1.2, 0] },
      paint: { 'text-color': ['get', 'couleur'], 'text-halo-color': '#ffffff', 'text-halo-width': 3 } });

    c.on('mousedown', (e) => this._appui(e));
    c.on('touchstart', (e) => { if (e.points?.length === 1) this._appui(e); });
    c.on('mousemove', (e) => this._deplacement(e));
    c.on('touchmove', (e) => this._deplacement(e));
    c.on('mouseup', (e) => this._relache(e));
    c.on('touchend', (e) => this._relache(e));
    document.addEventListener('keydown', (e) => this._touche(e));
    this._rendre();
  }

  // ------------------------------------------------------------ interactions carte

  _sous(point, couches) {
    const b = [[point.x - TOLERANCE_PX, point.y - TOLERANCE_PX], [point.x + TOLERANCE_PX, point.y + TOLERANCE_PX]];
    return this.carte.queryRenderedFeatures(b, { layers: couches.filter((x) => this.carte.getLayer(x)) })[0] || null;
  }

  /** Curseur de la carte selon ce qui est sous la souris (carte.js). */
  survol(point) {
    if (this.mode) return 'crosshair';
    if (!this.evt?.centre) return null;
    const f = this._sous(point, ['sig-crise-poignees', 'sig-crise-centre']);
    if (!f) return null;
    return f.properties.nature === 'centre' ? 'move' : 'ew-resize';
  }

  _appui(e) {
    if (this.mode === 'placement') {
      e.preventDefault();   // pas de déplacement de la carte pendant le tracé
      this._memoriser();
      const nouveau = !this.evt.zones.some((z) => z.forme === 'cercle');
      if (nouveau) this.evt.zones.unshift({ role: this.roles[0], forme: 'cercle', rayon_m: this.cfg.rayon_defaut_m });
      this.evt.centre = [e.lngLat.lng, e.lngLat.lat];
      const index = this.evt.zones.findIndex((z) => z.forme === 'cercle');
      this.glisse = { type: 'rayon', index, depart: e.point, nouveau: true, curseur: null };
      this.mode = null;
      this.analyser();   // les objets arrivent pendant que l'utilisateur tire le cercle
      this._dessiner();
      return;
    }
    if (this.mode || !this.evt?.centre) return;
    const f = this._sous(e.point, ['sig-crise-poignees', 'sig-crise-centre']);
    if (!f) return;
    e.preventDefault();
    this._memoriser();
    this.glisse = f.properties.nature === 'centre'
      ? { type: 'centre', depart: e.point }
      : { type: 'rayon', index: f.properties.index, depart: e.point };
  }

  _deplacement(e) {
    if (this.mode === 'polygone' && this.polygone.length) {
      this._curseurPolygone = [e.lngLat.lng, e.lngLat.lat];
      this._dessiner();
      return;
    }
    const g = this.glisse;
    if (!g) return;
    const ici = [e.lngLat.lng, e.lngLat.lat];
    if (g.type === 'centre') {
      this.evt.centre = ici;
    } else {
      const shift = e.originalEvent?.shiftKey;
      const pas = shift ? 1 : this.cfg.pas_m;
      let r = Math.round(distanceL93(this.evt.centre, ici) / pas) * pas;
      r = Math.min(Math.max(r, pas), this.cfg.rayon_max_m);
      this.evt.zones[g.index].rayon_m = r;
      g.curseur = ici;
    }
    this._dessiner();
    this._rendreBientot();
  }

  _relache(e) {
    const g = this.glisse;
    if (!g) return;
    this.glisse = null;
    this._finGeste = Date.now();   // le « click » qui suit le relâchement n'ouvre pas la fiche d'un objet
    if (g.type === 'rayon' && g.nouveau) {
      const bouge = Math.hypot(e.point.x - g.depart.x, e.point.y - g.depart.y);
      if (bouge < CLIC_SANS_GLISSER_PX) this.evt.zones[g.index].rayon_m = this.cfg.rayon_defaut_m;
    }
    this.analyser();
    this._dessiner();
    this._rendre();
  }

  /** Clic sur la carte (carte.js) ; vrai si consommé (placement, polygone, poignée). */
  clic(e) {
    if (Date.now() - (this._finGeste || 0) < 500) return true;
    if (this.mode === 'polygone') {
      this.polygone.push([e.lngLat.lng, e.lngLat.lat]);
      this._dessiner();
      this._rendre();
      return true;
    }
    if (this.mode === 'placement') return true;
    return Boolean(this.evt?.centre && this._sous(e.point, ['sig-crise-poignees', 'sig-crise-centre']));
  }

  doubleClic() {
    if (this.mode !== 'polygone') return false;
    if (this.polygone.length > 1) this.polygone.pop();   // second clic du double-clic
    this._terminerPolygone();
    return true;
  }

  _touche(e) {
    if (!this.evt) return;
    const saisie = /^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement?.tagName || '');
    if (e.key === 'Escape' && (this.mode || this.glisse)) {
      if (this.glisse) { this._annuler(); this.glisse = null; }
      this.mode = null;
      this.polygone = [];
      this.carte.getCanvas().style.cursor = '';
      this._dessiner();
      this._rendre();
    } else if (e.key === 'Enter' && this.mode === 'polygone' && !saisie) {
      this._terminerPolygone();
    } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z' && !saisie) {
      e.preventDefault();
      this._annuler();
    }
  }

  // ------------------------------------------------------------ historique

  _memoriser() {
    this.historique.push(JSON.stringify({ centre: this.evt.centre, zones: this.evt.zones }));
    if (this.historique.length > 100) this.historique.shift();
  }

  _annuler() {
    const h = this.historique.pop();
    if (!h) return;
    Object.assign(this.evt, JSON.parse(h));
    this.analyser();
    this._dessiner();
    this._rendre();
  }

  // ------------------------------------------------------------ événement et zones

  nouvelEvenement({ titre, type, mode, description }) {
    this.evt = { id: null, titre, type, mode, description, debut: new Date().toISOString(), centre: null, zones: [],
      statuts: {}, journal: [], versions: [], auteur: this.auteur };
    this.resultat = null;
    this.changements = null;
    this.historique = [];
    this._noter('création', `Événement créé : ${type}, ${mode === 'exercice' ? 'exercice' : 'événement réel'}${description ? ` — ${description}` : ''}`);
    this.placer();
  }

  placer() {
    this.mode = 'placement';
    this.carte.getCanvas().style.cursor = 'crosshair';
    this._rendre();
  }

  centrerSur(position) {
    this._memoriser();
    this.evt.centre = position;
    if (!this.evt.zones.length) this.evt.zones.push({ role: this.roles[0], forme: 'cercle', rayon_m: this.cfg.rayon_defaut_m });
    this.mode = null;
    this.carte.getCanvas().style.cursor = '';
    this._cadrer();
    this.analyser();
    this._dessiner();
    this._rendre();
  }

  _cadrer() {
    const r = Math.max(...this.evt.zones.filter((z) => z.forme === 'cercle').map((z) => z.rayon_m), 300);
    const so = decaler(this.evt.centre, -r * 1.15, -r * 1.15);
    const ne = decaler(this.evt.centre, r * 1.15, r * 1.15);
    // marge du haut : le bandeau de l'événement ne cache pas les rayons écrits au nord des cercles
    this.carte.fitBounds([so, ne], { padding: { top: 90, bottom: 30, left: 30, right: 30 }, pitch: 0, bearing: 0, duration: 900 });
  }

  ajouterCercle() {
    this._memoriser();
    const cercles = this.evt.zones.filter((z) => z.forme === 'cercle');
    const r = Math.min((cercles.length ? Math.max(...cercles.map((z) => z.rayon_m)) : 0) + 300, this.cfg.rayon_max_m);
    const pris = new Set(cercles.map((z) => z.role));
    const role = this.roles.find((x) => !pris.has(x)) || this.roles[this.roles.length - 1];
    this.evt.zones.push({ role, forme: 'cercle', rayon_m: r });
    this._cadrer();
    this.analyser();
    this._dessiner();
    this._rendre();
  }

  commencerPolygone() {
    this.mode = this.mode === 'polygone' ? null : 'polygone';
    this.polygone = [];
    this.carte.doubleClickZoom[this.mode ? 'disable' : 'enable']();
    this.carte.getCanvas().style.cursor = this.mode ? 'crosshair' : '';
    this._rendre();
  }

  _terminerPolygone() {
    if (this.polygone.length >= 3) {
      this._memoriser();
      this.evt.zones.push({ role: this.rolePolygone, forme: 'polygone', coordonnees: this.polygone });
      this.analyser();
    }
    this.polygone = [];
    this.mode = null;
    this.carte.doubleClickZoom.enable();
    this.carte.getCanvas().style.cursor = '';
    this._dessiner();
    this._rendre();
  }

  modifierZone(i, changement) {
    this._memoriser();
    Object.assign(this.evt.zones[i], changement);
    if ('rayon_m' in changement) this._cadrer();   // rayon saisi ou raccourci : tout le cercle à l'écran
    this.analyser();
    this._dessiner();
    this._rendre();
  }

  supprimerZone(i) {
    this._memoriser();
    this.evt.zones.splice(i, 1);
    this.analyser();
    this._dessiner();
    this._rendre();
  }

  terminer() {
    if (this._sauvegarde) { clearTimeout(this._sauvegarde); this.enregistrer(); }
    this._requete += 1;   // une analyse en cours est ignorée
    this._abandon?.abort();
    clearInterval(this._minuteur);
    this.chargement = false;
    this.erreurAnalyse = null;
    this.evt = null;
    this.resultat = null;
    this.mode = null;
    this.polygone = [];
    this.carte.getCanvas().style.cursor = '';
    this._dessiner();
    this._rendre();
  }

  // ------------------------------------------------------------ serveur

  async analyser() {
    if (!this.evt?.centre) return;
    const n = (this._requete += 1);
    this._abandon?.abort();
    const abandon = new AbortController();
    this._abandon = abandon;
    const delai = setTimeout(() => abandon.abort(new Error(`pas de réponse du serveur après ${DELAI_ANALYSE_MS / 1000} s`)), DELAI_ANALYSE_MS);
    this.chargement = true;
    this.erreurAnalyse = null;
    this._debutAnalyse = Date.now();
    clearInterval(this._minuteur);   // temps écoulé affiché : on voit que l'analyse avance
    this._minuteur = setInterval(() => this._majEtat(), 1000);
    this._majEtat();
    try {
      const r = await poster('crise/analyse', { centre: this.evt.centre, zones: this.evt.zones }, {}, abandon.signal);
      const res = await r.json();
      if (n !== this._requete) return;   // une demande plus récente est partie
      this.resultat = res;
      if (!this.glisse) this._versionner(res);   // pendant le tracé : la version vient au relâchement
    } catch (e) {
      if (n !== this._requete) return;
      const cause = abandon.signal.reason?.message || e.message;
      this.erreurAnalyse = /fetch|network|réseau/i.test(cause) ? `serveur injoignable (${cause}) : vérifiez qu'il est lancé` : cause;
    } finally {
      clearTimeout(delai);
    }
    if (n !== this._requete) return;
    clearInterval(this._minuteur);
    this.chargement = false;
    if (!this.glisse) this._dessiner();
    this._rendre();
  }

  /** Ligne d'état de l'analyse (en cours avec le temps écoulé, ou erreur avec « Relancer »). */
  _etatAnalyse() {
    if (this.chargement) {
      const s = Math.round((Date.now() - (this._debutAnalyse || Date.now())) / 1000);
      return el('p', { className: 'sig-info', textContent: `${this.resultat ? 'Mise à jour de l’analyse' : 'Analyse en cours'}… ${s ? `${s} s` : ''}` });
    }
    if (this.erreurAnalyse) {
      const relancer = el('button', { type: 'button', className: 'sig-bouton sig-bouton--petit', textContent: 'Relancer l’analyse' });
      relancer.addEventListener('click', () => this.analyser());
      return el('div', {}, el('p', { className: 'sig-erreur', textContent: `Analyse impossible : ${this.erreurAnalyse}` }), relancer);
    }
    return null;
  }

  _majEtat() {
    if (!this._zoneEtat) return;
    const e = this._etatAnalyse();
    this._zoneEtat.replaceChildren(...(e ? [e] : []));
  }

  /** Éléments pour les exports : zone vivante et décision de chaque élément touché ou décidé. */
  _pourExport() {
    return (this.resultat?.categories || []).map((k) => {
      const vus = new Set();
      const elements = k.elements.filter((e) => this._zone(e) || this._decision(k, e)).map((e) => {
        vus.add(e.cle);
        return { libelle: e.libelle, zone: this._zone(e), decision: this._decision(k, e) };
      });
      for (const [cle, d] of Object.entries(this.evt.statuts || {})) {   // décidés, au-delà des objets cherchés
        const [cat, cleE] = [cle.slice(0, cle.indexOf('|')), cle.slice(cle.indexOf('|') + 1)];
        const st = this.statuts.get(d.statut);
        if (cat === k.id && !vus.has(cleE) && st?.decide) elements.push({ libelle: d.libelle || cleE, zone: null, decision: { ...d, ...st, statut: d.statut } });
      }
      return { libelle: k.libelle, elements };
    });
  }

  async exporter(format) {
    const evt = this.evt;
    try {
      if (format === 'excel') {
        const r = await poster('crise/export/excel', { ...evt, auteur: this.auteur });
        const nom = (r.headers.get('Content-Disposition') || '').match(/filename="([^"]+)"/)?.[1] || 'crise.xlsx';
        telecharger(await r.blob(), nom);
      } else if (format === 'geojson') {
        exporterGeojson(evt, this.zonesCfg);
      } else {
        await exporterPdf({ evt: { ...evt, auteur: this.auteur }, zonesCfg: this.zonesCfg, statuts: this.statuts,
          categories: this._pourExport(), sources: this.resultat?.sources || [], anomalies: this.resultat?.anomalies || [],
          avertissement: this.cfg.avertissement, carte: this.carte, format });
      }
      this._noter('export', `Export ${format === 'excel' ? 'Excel' : format === 'geojson' ? 'GeoJSON du périmètre' : `PDF ${format.toUpperCase()}`}`);
      this.message = null;
    } catch (e) {
      this.message = `Export impossible : ${e.message}`;
    }
    this._rendre();
  }

  /** Enregistrement automatique (une écriture à la fois : le premier enregistrement donne l'identifiant). */
  enregistrer() {
    const evt = this.evt;
    if (!evt) return Promise.resolve();
    this._file = (this._file || Promise.resolve()).then(async () => {
      try {
        const r = await poster('crise/evenements', { ...evt, auteur: this.auteur });
        const e = await r.json();
        evt.id = e.id;
        this.etatSauvegarde = `Enregistré automatiquement à ${new Date(e.modifie).toLocaleTimeString('fr-FR')}`;
        this.enregistres = null;
      } catch (e) {
        this.etatSauvegarde = `Enregistrement impossible : ${e.message}`;
      }
      if (this._zoneSauvegarde && this.evt === evt) {
        this._zoneSauvegarde.textContent = this.etatSauvegarde;
        this._zoneSauvegarde.className = /impossible/.test(this.etatSauvegarde) ? 'sig-erreur' : 'sig-info';
      }
    });
    return this._file;
  }

  async rouvrir(id) {
    try {
      const e = await lire(`crise/evenements/${id}`);
      this.evt = { ...e, zones: e.zones || [], statuts: e.statuts || {}, journal: e.journal || [], versions: e.versions || [] };
      this.changements = null;
      this.etatSauvegarde = `Rouvert (dernier enregistrement : ${heure(e.modifie)})`;
      this.historique = [];
      this.mode = null;
      if (this.evt.centre) { this._cadrer(); this.analyser(); }
      this._dessiner();
    } catch (e) {
      this.message = `Ouverture impossible : ${e.message}`;
    }
    this._rendre();
  }

  // ------------------------------------------------------------ zones vivantes (pendant le glissement)

  /** Rôle de zone d'un élément : cercles recalculés ici (distance ≤ rayon) ; polygones : dernier calcul du serveur. */
  _zone(e) {
    const zones = this.evt?.zones || [];
    let meilleur = null;
    const garder = (role) => {
      if (role && (meilleur == null || this.zonesCfg.get(role).rang < this.zonesCfg.get(meilleur).rang)) meilleur = role;
    };
    for (const z of zones) if (z.forme === 'cercle' && e.distance_m <= z.rayon_m) garder(z.role);
    if (zones.some((z) => z.forme === 'polygone')) garder(e.zone);
    return meilleur;
  }

  _compteurs() {
    const t = new Map();
    for (const k of this.resultat?.categories || []) {
      const par = new Map(this.roles.map((r) => [r, 0]));
      for (const e of k.elements) { const z = this._zone(e); if (z) par.set(z, par.get(z) + 1); }
      t.set(k.id, par);
    }
    return t;
  }

  // ------------------------------------------------------------ dessin

  _dessiner() {
    const zSrc = this.carte.getSource(SOURCE_ZONES);
    const eSrc = this.carte.getSource(SOURCE_ELEMENTS);
    if (!zSrc || !eSrc) return;
    const f = [];
    const evt = this.evt;
    this.bandeau.hidden = !evt;
    if (evt) {
      const exo = evt.mode === 'exercice';
      this.bandeau.className = `sig-crise-bandeau ${exo ? 'sig-crise-bandeau--exercice' : 'sig-crise-bandeau--reel'}`;
      const type = evt.type && !evt.titre.includes(evt.type) ? ` — ${evt.type}` : '';
      this.bandeau.textContent = `${exo ? 'EXERCICE' : 'ÉVÉNEMENT RÉEL'} — ${evt.titre}${type} — depuis ${heure(evt.debut)}`;
    }
    if (evt?.centre) {
      const cercles = evt.zones.map((z, i) => ({ ...z, i })).filter((z) => z.forme === 'cercle');
      const plusGrand = cercles.reduce((a, z) => (!a || z.rayon_m > a.rayon_m ? z : a), null);
      if (this.assombrir && plusGrand) {
        f.push({ type: 'Feature', properties: { nature: 'masque' },
          geometry: { type: 'Polygon', coordinates: [[[-180, -85], [180, -85], [180, 85], [-180, 85], [-180, -85]], cercle(evt.centre, plusGrand.rayon_m)] } });
      }
      evt.zones.forEach((z, i) => {
        const zc = this.zonesCfg.get(z.role);
        const props = { nature: 'zone', couleur: zc.couleur, rang: zc.rang, index: i };
        if (z.forme === 'cercle') {
          f.push({ type: 'Feature', properties: props, geometry: { type: 'Polygon', coordinates: [cercle(evt.centre, z.rayon_m)] } });
          f.push({ type: 'Feature', properties: { nature: 'poignee', couleur: zc.couleur, index: i },
            geometry: { type: 'Point', coordinates: decaler(evt.centre, z.rayon_m, 0) } });
          f.push({ type: 'Feature', properties: { nature: 'libelle', couleur: zc.couleur, texte: `${distance(z.rayon_m)} · ${zc.libelle}` },
            geometry: { type: 'Point', coordinates: decaler(evt.centre, 0, z.rayon_m) } });
        } else {
          const anneau = [...z.coordonnees, z.coordonnees[0]];
          f.push({ type: 'Feature', properties: props, geometry: { type: 'Polygon', coordinates: [anneau] } });
          f.push({ type: 'Feature', properties: { nature: 'libelle', couleur: zc.couleur, texte: zc.libelle },
            geometry: { type: 'Point', coordinates: z.coordonnees.reduce((a, p) => (p[1] > a[1] ? p : a)) } });
        }
      });
      f.push({ type: 'Feature', properties: { nature: 'centre' }, geometry: { type: 'Point', coordinates: evt.centre } });
      const g = this.glisse;
      if (g?.type === 'rayon' && g.curseur) {
        const z = evt.zones[g.index];
        f.push({ type: 'Feature', properties: { nature: 'curseur', couleur: this.zonesCfg.get(z.role).couleur, texte: distance(z.rayon_m) },
          geometry: { type: 'Point', coordinates: g.curseur } });
      }
    }
    if (this.mode === 'polygone' && this.polygone.length) {
      const couleur = this.zonesCfg.get(this.rolePolygone).couleur;
      const pts = [...this.polygone, ...(this._curseurPolygone ? [this._curseurPolygone] : []), this.polygone[0]];
      f.push({ type: 'Feature', properties: { nature: 'saisie', couleur }, geometry: { type: 'LineString', coordinates: pts } });
    }
    zSrc.setData({ type: 'FeatureCollection', features: f });
    eSrc.setData({ type: 'FeatureCollection', features: this.glisse ? this._elementsVivants() : this._elements() });
  }

  _proprietes(k, e, z, ordre) {
    const zc = this.zonesCfg.get(z);
    const principal = ['stations', 'plai', 'maquettes'].includes(k.role);
    const p = { couleur: zc.couleur, ordre: ordre - zc.rang * 10 + (principal ? 5 : 0), secondaire: !principal,
      rayon: k.role === 'stations' ? 8 : principal ? 6.5 : k.role === 'acces' ? 4 : 4.5 };
    // étiquettes : stations, ouvrages et points de rassemblement (les autres pictogrammes des PLAI : fiche)
    if (k.role === 'stations' || k.role === 'maquettes') p.etiquette = e.libelle;
    if (k.role === 'plai' && e.type === 'rassemblement') p.etiquette = `${e.inutilisable ? 'INUTILISABLE — ' : ''}Rassemblement ${e.detail || ''}`.trim();
    if (e.inutilisable) p.inutilisable = true;
    const d = this._decision(k, e);
    if (d) { if (d.barre) p.barre = true; } else p.creux = true;   // creux : reste à décider
    return p;
  }

  /** Statut décidé d'un élément (objet du statut + heure, auteur), ou null s'il reste « à décider ». */
  _decision(k, e) {
    const d = this.evt?.statuts?.[`${k.id}|${e.cle}`];
    const st = d && this.statuts.get(d.statut);
    return st?.decide ? { ...d, ...st, statut: d.statut } : null;
  }

  /** Statut choisi pour une liste d'éléments d'une catégorie (journal, carte, enregistrement). */
  decider(k, elements, statut, precision = '') {
    const evt = this.evt;
    evt.statuts ||= {};
    const maintenant = new Date().toISOString();
    for (const e of elements) {
      const cle = `${k.id}|${e.cle}`;
      if (statut === this.statutDefaut) delete evt.statuts[cle];
      else evt.statuts[cle] = { statut, heure: maintenant, auteur: this.auteur, libelle: e.libelle };
    }
    const lib = this.statuts.get(statut).libelle;
    this._noter('décision', elements.length === 1
      ? `${k.libelle} « ${elements[0].libelle} » : ${lib}${precision}`
      : `${k.libelle} : ${elements.length} éléments passés à « ${lib} »${precision}`);
    this._dessiner();
    this._rendre();
  }

  // ------------------------------------------------------------ main courante et enregistrement automatique

  _noter(type, texte) {
    if (!this.evt) return;
    (this.evt.journal ||= []).push({ heure: new Date().toISOString(), auteur: this.auteur, type, texte });
    this._sauverBientot();
  }

  _sauverBientot() {
    clearTimeout(this._sauvegarde);
    this._sauvegarde = setTimeout(() => this.enregistrer(), 700);
  }

  /** Mise à jour du périmètre analysée : nouvelle version, changements des éléments touchés, main courante. */
  _versionner(res) {
    const evt = this.evt;
    const signature = JSON.stringify({ centre: evt.centre, zones: evt.zones });
    const versions = (evt.versions ||= []);
    const prec = versions[versions.length - 1];
    if (prec?.signature === signature) return;
    const touches = {};
    const noms = {};
    for (const k of res.categories) {
      touches[k.id] = k.elements.filter((e) => e.zone).map((e) => e.cle);
      for (const e of k.elements) noms[`${k.id}|${e.cle}`] = e.libelle;
    }
    const description = evt.zones.map((z) => `${this.zonesCfg.get(z.role).libelle} ${z.forme === 'cercle' ? distance(z.rayon_m) : 'polygone'}`).join(', ');
    const heure_ = new Date().toISOString();
    if (prec) {
      const ajouts = [];
      const retraits = [];
      for (const k of res.categories) {
        const avant = new Set(prec.touches?.[k.id] || []);
        const apres = new Set(touches[k.id]);
        const plus = [...apres].filter((c) => !avant.has(c));
        const moins = [...avant].filter((c) => !apres.has(c));
        if (plus.length) ajouts.push({ categorie: k.libelle, noms: plus.map((c) => noms[`${k.id}|${c}`] || c) });
        if (moins.length) retraits.push({ categorie: k.libelle, noms: moins.map((c) => noms[`${k.id}|${c}`] || c) });
      }
      const resume = [...ajouts.map((a) => `+${a.noms.length} ${a.categorie.toLowerCase()}`),
        ...retraits.map((x) => `-${x.noms.length} ${x.categorie.toLowerCase()}`)].join(', ');
      this.changements = { heure: heure_, resume, ajouts, retraits };
      this._noter('périmètre', `Périmètre modifié : ${description}${resume ? ` (${resume})` : ' (mêmes éléments touchés)'}`);
    } else {
      this._noter('placement', `Événement placé (${evt.centre[1].toFixed(6)} N, ${evt.centre[0].toFixed(6)} E) : ${description}`);
    }
    versions.push({ heure: heure_, auteur: this.auteur, signature, centre: evt.centre, zones: JSON.parse(JSON.stringify(evt.zones)), touches });
  }

  /** Pendant le glissement : seulement les points (géométries des tronçons recalculées au relâchement). */
  _elementsVivants() {
    const f = [];
    for (const k of this.resultat?.categories || []) {
      if (k.role === 'lignes') continue;
      for (const e of k.elements) {
        const z = this._zone(e);
        if (z) f.push({ type: 'Feature', properties: this._proprietes(k, e, z, 0), geometry: { type: 'Point', coordinates: e.position } });
      }
    }
    return f;
  }

  _elements() {
    const f = [];
    for (const k of this.resultat?.categories || []) {
      for (const e of k.elements) {
        const z = this._zone(e);
        if (!z) continue;
        const p = this._proprietes(k, e, z, k.role === 'stations' ? 3 : 0);
        if (e.geometrie) {
          const geoms = e.geometrie.type.startsWith('Multi') ? e.geometrie.coordinates.map((c) => ({ type: e.geometrie.type.slice(5), coordinates: c }))
            : e.geometrie.type === 'GeometryCollection' ? e.geometrie.geometries : [e.geometrie];
          for (const g of geoms) f.push({ type: 'Feature', properties: p, geometry: g });
        }
        if (k.role !== 'lignes') f.push({ type: 'Feature', properties: p, geometry: { type: 'Point', coordinates: e.position } });
        if (e.remplacement) {
          f.push({ type: 'Feature', properties: { couleur: COULEUR_REMPLACEMENT, rayon: 8, ordre: 9, etiquette: `Rassemblement proposé : ${e.remplacement.libelle}` },
            geometry: { type: 'Point', coordinates: e.remplacement.position } });
        }
      }
    }
    return f;
  }

  // ------------------------------------------------------------ volet

  _rendreBientot() {
    if (this._rendu) return;
    this._rendu = requestAnimationFrame(() => { this._rendu = null; this._rendre(true); });
  }

  /** Volet « Crise » ; leger : pendant le glissement, seuls les compteurs sont refaits. */
  _rendre(leger = false) {
    if (leger && this._zoneCompteurs) {
      this._zoneCompteurs.replaceChildren(this._tableCompteurs());
      return;
    }
    const sections = [];
    if (!this.evt) sections.push(...this._voletDepart());
    else sections.push(...this._voletEvenement());
    // la liste reste à la même hauteur après une décision (le volet défile, ou son conteneur)
    const defilants = [this.panneau, this.panneau.parentElement].filter(Boolean).map((x) => [x, x.scrollTop]);
    this.panneau.replaceChildren(...sections.filter(Boolean));
    for (const [x, haut] of defilants) x.scrollTop = haut;
  }

  _voletDepart() {
    const type = el('select', { className: 'sig-champ' }, ...this.cfg.types.map((t) => el('option', { value: t, textContent: t })));
    const titre = el('input', { className: 'sig-champ', type: 'text', placeholder: 'Titre (ex. : Explosion place de la Bastille)' });
    const exercice = el('input', { type: 'radio', name: 'sig-crise-mode', value: 'exercice', checked: true });
    const reel = el('input', { type: 'radio', name: 'sig-crise-mode', value: 'reel' });
    const description = el('textarea', { className: 'sig-champ', rows: 2, placeholder: 'Description (facultatif)' });
    const demarrer = el('button', { type: 'button', className: 'sig-bouton sig-bouton--actif', textContent: 'Placer l’événement sur la carte' });
    demarrer.addEventListener('click', () => this.nouvelEvenement({
      titre: titre.value.trim() || type.value,
      type: type.value, mode: reel.checked ? 'reel' : 'exercice', description: description.value.trim(),
    }));
    if (this.enregistres == null) {
      this.enregistres = [];
      lire('crise/evenements').then((r) => { this.enregistres = r.evenements; if (!this.evt) this._rendre(); }).catch(() => {});
    }
    const liste = this.enregistres.length ? el('ul', { className: 'sig-crise-liste' }, ...this.enregistres.slice(0, 10).map((e) => {
      const b = el('button', { type: 'button', className: 'sig-lien', textContent: `${e.mode === 'exercice' ? '[Exercice] ' : ''}${e.titre}` });
      b.addEventListener('click', () => this.rouvrir(e.id));
      return el('li', {}, b, el('span', { className: 'sig-info', textContent: ` ${heure(e.modifie)}` }));
    })) : null;
    return [
      el('section', { className: 'sig-section' }, el('h3', { textContent: 'Nouvel événement' }),
        el('label', { className: 'sig-crise-ligne' }, el('span', { textContent: 'Type' }), type),
        el('label', { className: 'sig-crise-ligne' }, el('span', { textContent: 'Titre' }), titre),
        el('div', { className: 'sig-options' }, el('label', { className: 'sig-option' }, exercice, ' Exercice'),
          el('label', { className: 'sig-option' }, reel, ' Événement réel')),
        description, el('div', { className: 'sig-groupe-boutons' }, demarrer),
        el('p', { className: 'sig-info', textContent: 'Puis cliquez sur le lieu de l’événement et glissez pour tracer le cercle. '
          + 'Un simple clic pose un cercle de ' + distance(this.cfg.rayon_defaut_m) + '.' })),
      liste && el('section', { className: 'sig-section' }, el('h3', { textContent: 'Événements enregistrés' }), liste),
      this.cfg.avertissement ? el('p', { className: 'sig-info', textContent: this.cfg.avertissement }) : null,
    ];
  }

  _voletEvenement() {
    const evt = this.evt;
    const sections = [];
    const terminer = el('button', { type: 'button', className: 'sig-lien', textContent: 'Fermer l’événement' });
    terminer.addEventListener('click', () => this.terminer());
    const auteur = el('input', { className: 'sig-champ', type: 'text', value: this.auteur, placeholder: 'Votre nom' });
    auteur.addEventListener('change', () => {
      this.auteur = auteur.value.trim();
      evt.auteur = this.auteur;
      garderAuteur(this.auteur);
      this._sauverBientot();
    });
    this._zoneSauvegarde = el('p', { className: /impossible/.test(this.etatSauvegarde) ? 'sig-erreur' : 'sig-info', textContent: this.etatSauvegarde });
    sections.push(el('section', { className: 'sig-section' },
      el('h3', { textContent: evt.titre }),
      el('p', { className: `sig-crise-mode sig-crise-mode--${evt.mode}`, textContent: evt.mode === 'exercice' ? 'EXERCICE' : 'ÉVÉNEMENT RÉEL' }),
      el('p', { className: 'sig-info', textContent: `${evt.type} · depuis ${heure(evt.debut)}` }),
      evt.description ? el('p', { textContent: evt.description }) : null,
      el('label', { className: 'sig-crise-ligne' }, el('span', { textContent: 'Auteur' }), auteur),
      this._zoneSauvegarde, terminer));

    if (!evt.centre || this.mode === 'placement') {
      sections.push(this._sectionLieu());
      return sections;
    }
    sections.push(this._sectionZones());
    this._zoneCompteurs = el('div', {}, this._tableCompteurs());
    this._zoneEtat = el('div');
    this._majEtat();
    sections.push(el('section', { className: 'sig-section' }, el('h3', { textContent: 'Synthèse' }), this._zoneEtat, this._zoneCompteurs));
    sections.push(this._sectionChangements());
    if (this.message) sections.push(el('p', { className: /impossible/.test(this.message) ? 'sig-erreur' : 'sig-info', textContent: this.message }));
    if (this.resultat) sections.push(...this._listes());
    sections.push(this._sectionExports());
    sections.push(this._sectionJournal());
    if (this.resultat) sections.push(this._sources());
    return sections;
  }

  _sectionChangements() {
    const c = this.changements;
    if (!c) return null;
    const fermer = el('button', { type: 'button', className: 'sig-lien', textContent: 'Masquer' });
    fermer.addEventListener('click', () => { this.changements = null; this._rendre(); });
    const liste = (titre, groupes) => (groupes.length ? el('div', {}, el('strong', { textContent: titre }),
      el('ul', { className: 'sig-crise-liste' }, ...groupes.map((g) => el('li', {},
        `${g.categorie} (${g.noms.length}) : ${g.noms.slice(0, 8).join(', ')}${g.noms.length > 8 ? '…' : ''}`)))) : null);
    return el('section', { className: 'sig-section sig-crise-changements' },
      el('h3', { textContent: `Changements à ${new Date(c.heure).toLocaleTimeString('fr-FR', { timeStyle: 'short' })}` }),
      el('p', { textContent: c.resume || 'Mêmes éléments touchés qu’avant la modification.' }),
      liste('Nouveaux éléments touchés', c.ajouts), liste('Plus touchés', c.retraits), fermer);
  }

  _sectionExports() {
    const format = el('select', { className: 'sig-champ sig-crise-format' }, el('option', { value: 'a4', textContent: 'A4' }), el('option', { value: 'a3', textContent: 'A3' }));
    const bouton = (texte, action) => {
      const b = el('button', { type: 'button', className: 'sig-bouton', textContent: texte });
      b.addEventListener('click', async () => { b.disabled = true; try { await action(); } finally { b.disabled = false; } });
      return b;
    };
    return el('section', { className: 'sig-section' }, el('h3', { textContent: 'Exports' }),
      el('div', { className: 'sig-groupe-boutons' }, bouton('PDF', () => this.exporter(format.value)), format,
        bouton('Excel', () => this.exporter('excel')), bouton('GeoJSON du périmètre', () => this.exporter('geojson'))),
      el('p', { className: 'sig-info', textContent: 'PDF : la carte telle qu’affichée, la légende, les chiffres, les décisions et la main courante. Fichiers enregistrés sur ce poste, jamais envoyés.' }));
  }

  _sectionJournal() {
    const journal = this.evt.journal || [];
    const note = el('textarea', { className: 'sig-champ', rows: 2, placeholder: 'Note libre (consigne, appel, constat…)' });
    const ajouter = el('button', { type: 'button', className: 'sig-bouton sig-bouton--petit', textContent: 'Ajouter à la main courante' });
    ajouter.addEventListener('click', () => {
      if (!note.value.trim()) return;
      this._noter('note', note.value.trim());
      this._rendre();
    });
    const ligne = (j) => el('li', {}, el('span', { className: 'sig-crise-journal__heure', textContent: new Date(j.heure).toLocaleTimeString('fr-FR', { timeStyle: 'short' }) }),
      ` ${j.texte}`, j.auteur ? el('span', { className: 'sig-info', textContent: ` — ${j.auteur}` }) : null);
    const recents = journal.slice(-8).reverse();
    return el('section', { className: 'sig-section' }, el('h3', { textContent: `Main courante (${journal.length})` }), note, ajouter,
      el('ul', { className: 'sig-crise-liste sig-crise-journal' }, ...recents.map(ligne)),
      journal.length > recents.length ? el('details', {}, el('summary', {}, 'Toute la main courante'),
        el('ul', { className: 'sig-crise-liste sig-crise-journal' }, ...[...journal].reverse().map(ligne))) : null);
  }

  _sectionLieu() {
    const q = el('input', { className: 'sig-champ', type: 'search', placeholder: 'Station ou accès (ex. : Bastille)' });
    const res = el('ul', { className: 'sig-crise-liste' });
    let jeton = 0;
    q.addEventListener('input', async () => {
      const n = (jeton += 1);
      if (q.value.trim().length < 2) { res.replaceChildren(); return; }
      try {
        const r = await lire('crise/lieux', { q: q.value.trim() });
        if (n !== jeton) return;
        res.replaceChildren(...r.resultats.map((l) => {
          const b = el('button', { type: 'button', className: 'sig-lien', textContent: `${l.type} : ${l.libelle}` });
          b.addEventListener('click', () => this.centrerSur(l.position));
          return el('li', {}, b);
        }));
      } catch (e) {
        res.replaceChildren(el('li', { className: 'sig-erreur', textContent: e.message }));
      }
    });
    const coords = el('input', { className: 'sig-champ', type: 'text', placeholder: 'X Y (Lambert 93) ou lat, lon' });
    const ok = el('button', { type: 'button', className: 'sig-bouton', textContent: 'Placer' });
    const erreur = el('p', { className: 'sig-erreur', hidden: true });
    ok.addEventListener('click', () => {
      const p = lireCoordonnees(coords.value);
      if (!p) { erreur.textContent = 'Coordonnées non reconnues.'; erreur.hidden = false; return; }
      this.centrerSur(p);
    });
    return el('section', { className: 'sig-section' }, el('h3', { textContent: 'Lieu de l’événement' }),
      el('p', { className: 'sig-crise-consigne', textContent: 'Cliquez sur la carte au lieu de l’événement et, sans relâcher, glissez pour tracer le cercle.' }),
      el('p', { className: 'sig-info', textContent: 'Ou cherchez une station :' }), q, res,
      el('p', { className: 'sig-info', textContent: 'Ou saisissez des coordonnées :' }), el('div', { className: 'sig-crise-ligne' }, coords, ok), erreur);
  }

  _sectionZones() {
    const evt = this.evt;
    const lignes = evt.zones.map((z, i) => {
      const zc = this.zonesCfg.get(z.role);
      const role = el('select', { className: 'sig-champ sig-crise-role' }, ...this.cfg.zones.map((x) => el('option', { value: x.id, textContent: x.libelle, selected: x.id === z.role })));
      role.addEventListener('change', () => this.modifierZone(i, { role: role.value }));
      const suppr = el('button', { type: 'button', className: 'sig-lien', textContent: 'Supprimer', title: 'Supprimer cette zone' });
      suppr.addEventListener('click', () => this.supprimerZone(i));
      const tete = el('div', { className: 'sig-crise-zone__tete' }, el('span', { className: 'sig-legende__pastille', style: `background:${zc.couleur}` }), role, suppr);
      if (z.forme !== 'cercle') return el('div', { className: 'sig-crise-zone' }, tete, el('p', { className: 'sig-info', textContent: `Polygone de ${z.coordonnees.length} sommets` }));
      const rayon = el('input', { className: 'sig-champ sig-crise-rayon', type: 'number', min: 1, max: this.cfg.rayon_max_m, step: 1, value: Math.round(z.rayon_m) });
      rayon.addEventListener('change', () => {
        const v = Number(rayon.value);
        if (v > 0 && v <= this.cfg.rayon_max_m) this.modifierZone(i, { rayon_m: v });
      });
      const raccourcis = this.cfg.raccourcis_m.map((r) => {
        const b = el('button', { type: 'button', className: `sig-bouton sig-bouton--petit${z.rayon_m === r ? ' sig-bouton--actif' : ''}`, textContent: distance(r) });
        b.addEventListener('click', () => this.modifierZone(i, { rayon_m: r }));
        return b;
      });
      return el('div', { className: 'sig-crise-zone' }, tete,
        el('div', { className: 'sig-crise-ligne' }, el('span', { textContent: 'Rayon (m)' }), rayon), el('div', { className: 'sig-groupe-boutons' }, ...raccourcis));
    });
    const cercle = el('button', { type: 'button', className: 'sig-bouton', textContent: '+ Cercle' });
    cercle.addEventListener('click', () => this.ajouterCercle());
    const rolePoly = el('select', { className: 'sig-champ sig-crise-role' }, ...this.cfg.zones.map((x) => el('option', { value: x.id, textContent: x.libelle, selected: x.id === this.rolePolygone })));
    rolePoly.addEventListener('change', () => { this.rolePolygone = rolePoly.value; this._dessiner(); });
    const poly = el('button', { type: 'button', className: `sig-bouton${this.mode === 'polygone' ? ' sig-bouton--actif' : ''}`, textContent: this.mode === 'polygone' ? 'Polygone : cliquez les sommets…' : '+ Polygone' });
    poly.addEventListener('click', () => this.commencerPolygone());
    const replacer = el('button', { type: 'button', className: 'sig-lien', textContent: 'Replacer l’événement' });
    replacer.addEventListener('click', () => this.placer());
    const masque = el('input', { type: 'checkbox', checked: this.assombrir });
    masque.addEventListener('change', () => { this.assombrir = masque.checked; this._dessiner(); });
    return el('section', { className: 'sig-section' }, el('h3', { textContent: 'Zones' }), ...lignes,
      el('div', { className: 'sig-groupe-boutons' }, cercle, poly), this.mode === 'polygone' ? el('div', { className: 'sig-crise-ligne' }, el('span', { textContent: 'Rôle' }), rolePoly) : null,
      el('p', { className: 'sig-info', textContent: this.mode === 'polygone'
        ? 'Cliquez les sommets ; double-clic ou Entrée pour fermer ; Échap pour annuler.'
        : 'Tirez la poignée blanche pour changer le rayon (Maj : au mètre près), le point rouge pour déplacer l’événement. Ctrl+Z : annuler.' }),
      el('div', { className: 'sig-options' }, el('label', { className: 'sig-option' }, masque, ' Assombrir l’extérieur'), replacer));
  }

  _tableCompteurs() {
    if (!this.resultat) return el('p', { className: 'sig-info', textContent: this.chargement ? 'Les compteurs s’afficheront à la fin de la première analyse.' : 'Pas encore de résultat.' });
    const t = this._compteurs();
    const actifs = this.roles.filter((r) => this.evt.zones.some((z) => z.role === r));
    if (!actifs.length) return el('p', { className: 'sig-info', textContent: 'Aucune zone.' });
    const tete = el('tr', {}, el('th', { textContent: '' }), ...actifs.map((r) => {
      const zc = this.zonesCfg.get(r);
      return el('th', { title: zc.libelle }, el('span', { className: 'sig-legende__pastille', style: `background:${zc.couleur}` }), ` ${zc.libelle.split(/[ /]/)[0]}`);
    }));
    const lignes = this.resultat.categories.map((k) => el('tr', {}, el('th', { scope: 'row', textContent: k.libelle }),
      ...actifs.map((r) => { const n = t.get(k.id)?.get(r) || 0; return el('td', { className: n ? 'sig-crise-nombre' : 'sig-crise-zero', textContent: n ? String(n) : '—' }); })));
    return el('table', { className: 'sig-crise-table' }, el('thead', {}, tete), el('tbody', {}, ...lignes));
  }

  _listes() {
    return this.resultat.categories.map((k) => {
      const touches = k.elements.filter((e) => this._zone(e))
        .sort((a, b) => this.zonesCfg.get(this._zone(a)).rang - this.zonesCfg.get(this._zone(b)).rang || a.distance_m - b.distance_m);
      const horsZone = k.elements.filter((e) => !this._zone(e) && this._decision(k, e));
      if (!touches.length && !horsZone.length) return null;
      const items = touches.slice(0, MAX_LISTE).map((e) => this._item(k, e));
      const reste = touches.length - items.length;
      const decides = touches.filter((e) => this._decision(k, e)).length;
      const ouvert = this._ouvertes?.has(k.id) ?? ['stations', 'plai', 'maquettes', 'lignes'].includes(k.role);
      const d = el('details', { className: 'sig-crise-categorie', open: ouvert },
        el('summary', {}, `${k.libelle} (${touches.length}${decides ? ` · ${decides} décidé(s)` : ''})`),
        touches.length ? this._actionMasse(k, touches) : null,
        el('ul', { className: 'sig-crise-elements' }, ...items),
        reste > 0 ? el('p', { className: 'sig-info', textContent: `… et ${reste} autre(s) : voir l’export Excel.` }) : null,
        horsZone.length ? el('div', {}, el('p', { className: 'sig-crise-hors-zone', textContent: `Décidés, hors zone désormais (${horsZone.length})` }),
          el('ul', { className: 'sig-crise-elements' }, ...horsZone.map((e) => this._item(k, e)))) : null);
      d.addEventListener('toggle', () => { (this._ouvertes ||= new Map()).set(k.id, d.open); });
      return d;
    });
  }

  /** « Pour la zone … : statut … » appliqué à tous les éléments de la catégorie dans cette zone. */
  _actionMasse(k, touches) {
    const roles = this.roles.filter((r) => touches.some((e) => this._zone(e) === r));
    const zone = el('select', { className: 'sig-champ sig-crise-statut' }, ...roles.map((r) => el('option', { value: r, textContent: this.zonesCfg.get(r).libelle })));
    const ids = this.categoriesCfg.get(k.id)?.statuts || [...this.statuts.keys()];
    const statut = el('select', { className: 'sig-champ sig-crise-statut' }, ...ids.filter((i) => i !== this.statutDefaut).map((i) => el('option', { value: i, textContent: this.statuts.get(i).libelle })));
    const appliquer = el('button', { type: 'button', className: 'sig-bouton sig-bouton--petit', textContent: 'Appliquer' });
    appliquer.addEventListener('click', () => {
      const cibles = touches.filter((e) => this._zone(e) === zone.value);
      if (cibles.length) this.decider(k, cibles, statut.value, ` (zone ${this.zonesCfg.get(zone.value).libelle})`);
    });
    return el('div', { className: 'sig-crise-masse' }, el('span', { className: 'sig-info', textContent: 'Tous ceux de la zone' }), zone, statut, appliquer);
  }

  _item(k, e) {
    const role = this._zone(e);
    const zc = role ? this.zonesCfg.get(role) : { couleur: '#adb5bd', libelle: 'hors périmètre' };
    const nom = el('button', { type: 'button', className: 'sig-lien sig-crise-nom', textContent: e.libelle });
    nom.addEventListener('click', () => this._ouvrir(e));
    const lignes = [
      el('div', { className: 'sig-crise-element__tete' }, el('span', { className: 'sig-legende__pastille', style: `background:${zc.couleur}`, title: zc.libelle }), nom,
        el('span', { className: 'sig-crise-distance', textContent: `${distance(e.distance_m)} ${e.direction}` })),
      e.detail ? el('div', { className: 'sig-info', textContent: e.detail }) : null,
      e.cause ? el('div', { className: 'sig-info', textContent: `Touchée ${e.cause}` }) : null,
      e.proposition ? el('div', { className: `sig-crise-proposition${e.inutilisable || e.puits ? ' sig-crise-proposition--alerte' : ''}`, textContent: `→ ${e.proposition}` }) : null,
    ];
    const ids = this.categoriesCfg.get(k.id)?.statuts || [...this.statuts.keys()];
    const d = this.evt.statuts?.[`${k.id}|${e.cle}`];
    const choix = el('select', { className: `sig-champ sig-crise-statut${d ? ' sig-crise-statut--decide' : ''}`, title: 'Statut décidé' },
      ...ids.map((i) => el('option', { value: i, textContent: this.statuts.get(i).libelle, selected: i === (d?.statut || this.statutDefaut) })));
    choix.addEventListener('change', () => this.decider(k, [e], choix.value));
    lignes.push(el('div', { className: 'sig-crise-decision' }, choix,
      d ? el('span', { className: 'sig-info', textContent: ` ${new Date(d.heure).toLocaleTimeString('fr-FR', { timeStyle: 'short' })}${d.auteur ? ` · ${d.auteur}` : ''}` }) : null));
    if (e.remplacement) {
      const voir = el('button', { type: 'button', className: 'sig-lien', textContent: 'Voir le point proposé' });
      voir.addEventListener('click', () => this.carte.flyTo({ center: e.remplacement.position, zoom: 17, duration: 800 }));
      lignes.push(voir);
    }
    return el('li', { className: 'sig-crise-element' }, ...lignes);
  }

  _ouvrir(e) {
    if (e.maquettes?.length) {
      this.maquettes.ouvrirFiche(e.maquettes[0], true);
      return;
    }
    this.carte.flyTo({ center: e.position, zoom: Math.max(this.carte.getZoom(), 17), duration: 800 });
    if (e.couche != null && e.n != null) this.couches.ouvrirObjet(e.couche, e.n, false);
  }

  _sources() {
    const r = this.resultat;
    const vues = new Set();
    const items = r.sources.filter((s) => s.couche == null || !vues.has(s.couche) && vues.add(s.couche)).map((s) => el('li', {},
      `${s.libelle}${s.date ? ` — données du ${heure(s.date)}` : ''}${s.origine && !['cache', 'fichier', 'service', 'maquettes'].includes(s.origine) ? ` (${s.origine})` : ''}`));
    return el('details', { className: 'sig-crise-categorie' }, el('summary', {}, `Sources et anomalies${r.anomalies.length ? ` (${r.anomalies.length} anomalie(s))` : ''}`),
      ...r.anomalies.map((a) => el('p', { className: 'sig-erreur', textContent: a })),
      el('ul', { className: 'sig-crise-liste' }, ...items),
      el('p', { className: 'sig-info', textContent: `Analyse du ${heure(r.date)} ; objets cherchés jusqu’à ${distance(r.rayon_max_m)}. ${this.cfg.avertissement || ''}` }));
  }
}

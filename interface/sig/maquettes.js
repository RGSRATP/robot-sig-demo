// Maquettes IFC : arborescence (dossiers, puis lots quand un ouvrage en contient beaucoup), état des traitements,
// emprises au sol sur la carte, modèles 3D dans la carte (vue3d.js), fiche d'une maquette et de ses objets.
// Prévu pour des milliers de maquettes : liste allégée reçue par différence (/api/sig/maquettes?depuis=…),
// points regroupés en vue éloignée, emprises demandées pour la zone affichée (/api/sig/maquettes/emprises),
// description complète demandée à l'ouverture d'une fiche (/api/sig/maquettes/{id}).
import { envoyer, lire } from './api.js';
import { auSol, cadrer } from './camera.js';
import { afficherMaquette, afficherObjetIfc } from './fiche.js';
import { bouton, el, icone } from './ui.js';
import { affichage3d } from './vue3d.js';

const ATTENTE_SUIVI_MS = 3000;     // rafraîchissement pendant les traitements
const ATTENTE_VEILLE_MS = 30000;   // sans traitement : nouvelles maquettes copiées dans le dossier
const MAX_LIGNES_GROUPE = 200;     // maquettes listées par groupe ouvert (les suivantes à la demande)
const COULEUR_EMPRISE = '#0079c1';
const SEUIL_SOUS_GROUPES = 12;     // au-delà, les maquettes d'un dossier sont regroupées par lot

// Nom lisible : sans le préfixe de codification documentaire (PN1424_05_PRB_BIM_005762_15_…), sans le code
// de l'ouvrage (déjà donné par l'arborescence) ni les suffixes de copie « (1) ».
export function nomCourt(nom, ouvrage = '') {
  let n = nom.replace(/^(?:PN|DC)\d{3,4}(?:-\d)?_[0-9A-Z]{2,3}_(?:[A-Z]{3}_)?BIM_[0-9X]+_\d+_?/i, '');
  n = n.replace(/^\d{2}-SID-\d+-/, '').replace(/(\s*\(\d+\))+$/g, '');
  const code = ouvrage.split(' ')[0];
  if (code) n = n.replace(new RegExp(`(^|[_ -])${code}(?=[_ -]|$)`, 'g'), '$1');
  n = n.replace(/_+/g, ' ').replace(/\s+/g, ' ').trim();
  return n || nom;
}

function lot(court) {
  const t = court.split(' ')[0] || '';
  return /^[A-Z][A-Z0-9-]{1,7}$/.test(t) ? t : 'Autres';
}

export class GestionnaireMaquettes {
  constructor(carte, couche3d, { panneau, anomalies, surEtat, surSelection, complementsFiche, planLocaux }) {
    this.carte = carte;
    this.c3d = couche3d;
    this.panneau = panneau;
    this.panneauAnomalies = anomalies;
    this.surEtat = surEtat || (() => {});
    this.surSelection = surSelection || (() => {});
    this.complementsFiche = complementsFiche || (() => []);
    this.planLocaux = planLocaux || (() => {});
    this.maquettes = new Map();   // id -> données serveur
    this.affichees = new Set();   // ids dont la 3D est demandée
    this.masquees = new Set();    // ids masqués par l'utilisateur (pas de rechargement automatique)
    this.details = new Set();     // ids dont les petits éléments sont chargés
    this.progression = new Map(); // id -> texte de chargement 3D
    this.ouverts = new Set();     // nœuds dépliés de l'arborescence
    this.automatiques = new Set(); // ids chargés automatiquement : déchargés quand ils sortent de la vue
    this.complets = new Set();    // nœuds de l'arborescence dont toutes les maquettes sont listées
    this.filtre = '';
    this.ficheOuverte = null;
    this._minuteur = null;
    this._premier = true;
    this._jeton = null;
    this._requeteEmprises = null;
  }

  async demarrer(police) {
    const vide = { type: 'FeatureCollection', features: [] };
    this.carte.addSource('sig-maquettes-points', { type: 'geojson', data: vide, cluster: true, clusterRadius: 40, clusterMaxZoom: 14 });
    this.carte.addLayer({ id: 'sig-maquettes-groupes', type: 'circle', source: 'sig-maquettes-points', filter: ['has', 'point_count'],
      paint: { 'circle-color': COULEUR_EMPRISE, 'circle-opacity': 0.85, 'circle-stroke-color': '#fff', 'circle-stroke-width': 2,
               'circle-radius': ['step', ['get', 'point_count'], 13, 10, 17, 100, 22, 1000, 28] } });
    this.carte.addLayer({ id: 'sig-maquettes-point', type: 'circle', source: 'sig-maquettes-points', filter: ['!', ['has', 'point_count']],
      paint: { 'circle-color': COULEUR_EMPRISE, 'circle-radius': 6, 'circle-stroke-color': '#fff', 'circle-stroke-width': 1.5 } });
    if (police && this.carte.getStyle().glyphs) {
      this.carte.addLayer({ id: 'sig-maquettes-groupes-nb', type: 'symbol', source: 'sig-maquettes-points', filter: ['has', 'point_count'],
        layout: { 'text-field': ['get', 'point_count_abbreviated'], 'text-size': 12, 'text-font': [police], 'text-allow-overlap': true },
        paint: { 'text-color': '#fff' } });
    }
    this.carte.addSource('sig-maquettes', { type: 'geojson', data: vide, promoteId: 'id' });
    this.carte.addLayer({ id: 'sig-maquettes-fond', type: 'fill', source: 'sig-maquettes',
      paint: { 'fill-color': COULEUR_EMPRISE, 'fill-opacity': ['case', ['boolean', ['feature-state', 'charge'], false], 0.04, 0.18] } });
    this.carte.addLayer({ id: 'sig-maquettes-contour', type: 'line', source: 'sig-maquettes',
      paint: { 'line-color': COULEUR_EMPRISE, 'line-width': ['case', ['boolean', ['feature-state', 'choisie'], false], 3.5, 1.6],
               'line-dasharray': [3, 1.5] } });
    if (police && this.carte.getStyle().glyphs) {
      this.carte.addLayer({ id: 'sig-maquettes-nom', type: 'symbol', source: 'sig-maquettes', minzoom: 14, maxzoom: 17.5,
        layout: { 'text-field': ['get', 'nom'], 'text-size': 11, 'text-font': [police], 'text-optional': true },
        paint: { 'text-color': '#003a5d', 'text-halo-color': '#fff', 'text-halo-width': 1.5 } });
    }
    this.carte.addLayer(this.c3d);
    this.carte.on('moveend', () => { this._majVue(); this._chargerALaVue(); this._details(); });
    this.panneau.replaceChildren(el('p', { className: 'sig-info', textContent: 'Inventaire des maquettes…' }));
    await this.actualiser();
  }

  /** Distance (carré, degrés) du centre d'une maquette au centre de la vue : les plus proches d'abord. */
  _eloignement(m) {
    const c = this.carte.getCenter();
    const [x, y] = m.positionnement.centre_wgs84;
    return (x - c.lng) ** 2 + (y - c.lat) ** 2;
  }

  /** Triangles déjà chargés (modèles et petits éléments) : la charge de la carte graphique, pour le budget
   *  du chargement automatique. */
  _charge() {
    let n = 0;
    for (const id of this.affichees) {
      const a = this.maquettes.get(id)?.positionnement?.affichage;
      if (a) n += a.triangles + (this.details.has(id) ? a.triangles_detail : 0);
    }
    return n;
  }

  /** Charge en 3D les maquettes légères visibles, à partir du zoom configuré, les plus proches du centre d'abord,
   *  dans la limite du budget (les autres restent disponibles à la demande). */
  _chargerALaVue() {
    const seuil = this.inventaire?.zoom_chargement_automatique;
    if (seuil == null) return;
    const b = this.carte.getBounds();
    // les maquettes chargées automatiquement et sorties de la vue (avec une marge) libèrent le budget
    const mx = (b.getEast() - b.getWest()) * 0.5;
    const my = (b.getNorth() - b.getSouth()) * 0.5;
    for (const id of [...this.automatiques]) {
      const e = this.maquettes.get(id)?.positionnement?.emprise_wgs84;
      const dehors = !e || e[2] < b.getWest() - mx || e[0] > b.getEast() + mx || e[3] < b.getSouth() - my || e[1] > b.getNorth() + my;
      if (dehors && id !== this.ficheOuverte && this.c3d.selection?.id !== id) this._decharger(id);
    }
    if (this.carte.getZoom() < seuil) return;
    const candidates = [];
    for (const m of this.maquettes.values()) {
      const pos = m.positionnement;
      if (m.etat.phase !== 'pret' || pos?.statut !== 'positionnee' || pos.locaux_seuls || !pos.affichage.chargement_automatique) continue;
      if (this.affichees.has(m.id) || this.masquees.has(m.id)) continue;
      const [x1, y1, x2, y2] = pos.emprise_wgs84;
      if (x2 < b.getWest() || x1 > b.getEast() || y2 < b.getSouth() || y1 > b.getNorth()) continue;
      candidates.push(m);
    }
    let n = this._charge();
    const budget = this.inventaire.budget_triangles;
    for (const m of candidates.sort((p, q) => this._eloignement(p) - this._eloignement(q))) {
      if (n + m.positionnement.affichage.triangles > budget) continue;
      n += m.positionnement.affichage.triangles;
      this.automatiques.add(m.id);
      this.afficher3d(m.id, true);
    }
  }

  /** Retire la 3D d'une maquette chargée automatiquement (elle pourra se recharger en revenant). */
  _decharger(id) {
    this.automatiques.delete(id);
    this.affichees.delete(id);
    this.details.delete(id);
    this.c3d.retirer(id);
    this.progression.delete(id);
    this.carte.setFeatureState({ source: 'sig-maquettes', id }, { charge: false });
    this._rendrePlusTard();
  }

  /** Petits éléments (boulons, platines…) des maquettes affichées : en vue rapprochée, dans la limite du budget. */
  _details() {
    const seuil = this.inventaire?.zoom_details;
    if (seuil == null) return;
    const proche = this.carte.getZoom() >= seuil;
    let n = this._charge();
    const budget = this.inventaire.budget_triangles;
    const ids = [...this.affichees].filter((id) => this.maquettes.get(id)?.positionnement?.affichage?.detail)
      .sort((p, q) => this._eloignement(this.maquettes.get(p)) - this._eloignement(this.maquettes.get(q)));
    for (const id of ids) {
      const a = this.maquettes.get(id).positionnement.affichage;
      if (proche && !this.details.has(id)) {
        if (n + a.triangles_detail > budget) continue;
        n += a.triangles_detail;
        this.details.add(id);
      }
      if (this.details.has(id)) this.c3d.montrerDetail(id, `api/sig/maquettes/${id}/detail.glb`, proche);
    }
  }

  // ------------------------------------------------------------ données

  /** Liste des maquettes : seules les modifications depuis l'appel précédent sont reçues.
   *  forcer : le serveur reparcourt tout le dossier (nouvelles maquettes). */
  async actualiser(forcer = false) {
    clearTimeout(this._minuteur);
    let inv;
    try {
      const params = {};
      if (this._jeton) params.depuis = this._jeton;
      if (forcer) params.actualiser = '1';
      inv = await lire('maquettes', params);
    } catch (e) {
      if (!this.maquettes.size) {
        this.panneau.replaceChildren(el('p', { className: 'sig-erreur', textContent: `Maquettes indisponibles : ${e.message}` }));
      }
      this._minuteur = setTimeout(() => this.actualiser(), ATTENTE_SUIVI_MS * 5);
      return;
    }
    this.inventaire = inv;
    this._jeton = inv.jeton;
    const signature = (m) => m && JSON.stringify([m.etat.phase, m.positionnement?.statut, m.locaux?.phase, m.locaux?.nb]);
    const ficheAvant = signature(this.maquettes.get(this.ficheOuverte));
    if (inv.complet) this.maquettes.clear();
    for (const id of inv.supprimees || []) {
      if (this.affichees.has(id)) this._decharger(id);
      this.maquettes.delete(id);
    }
    for (const m of inv.maquettes) {
      m.ouvrage = m.dossier.split('/').pop();
      m.nomCourt = nomCourt(m.nom, m.ouvrage);
      this.maquettes.set(m.id, m);
    }
    const change = inv.complet || inv.maquettes.length || (inv.supprimees || []).length;
    if (this._premier) {
      // déplier les premiers niveaux (ligne, type d'ouvrage)
      for (const m of inv.maquettes) {
        const parties = m.dossier.split('/');
        for (let i = 1; i <= Math.min(2, parties.length - 1); i += 1) this.ouverts.add(parties.slice(0, i).join('/'));
      }
      this._premier = false;
    }
    if (change) {
      this._majPoints();
      this._majVue(true);
      this._rendre();
      this._chargerALaVue();
    }
    // la fiche ouverte n'est redessinée que si son état change (sinon, les résultats affichés seraient perdus)
    if (this.ficheOuverte && this.maquettes.has(this.ficheOuverte)
        && signature(this.maquettes.get(this.ficheOuverte)) !== ficheAvant) {
      this.ouvrirFiche(this.ficheOuverte, false, false);
    }
    let enCours = 0;
    let courant = null;
    for (const m of this.maquettes.values()) {
      if (m.etat.phase === 'en_cours' || m.etat.phase === 'en_attente'
        || (m.locaux && m.locaux.phase !== 'pret' && m.locaux.phase !== 'erreur')) enCours += 1;
      if (!courant && m.etat.phase === 'en_cours') courant = m;
    }
    this.surEtat({ total: this.maquettes.size, enCours, courant });
    this._minuteur = setTimeout(() => this.actualiser(), enCours ? ATTENTE_SUIVI_MS : ATTENTE_VEILLE_MS);
  }

  /** Points (centres) des maquettes positionnées, regroupés en vue éloignée. */
  _majPoints() {
    const features = [];
    for (const m of this.maquettes.values()) {
      const pos = m.positionnement;
      if (pos?.statut !== 'positionnee') continue;
      features.push({ type: 'Feature', geometry: { type: 'Point', coordinates: pos.centre_wgs84 }, properties: { id: m.id } });
    }
    this.carte.getSource('sig-maquettes-points').setData({ type: 'FeatureCollection', features });
  }

  /** Vue éloignée : points regroupés ; vue rapprochée : emprises de la zone affichée, demandées au serveur. */
  async _majVue(forcer = false) {
    const seuil = this.inventaire?.zoom_emprises ?? 13;
    const proche = this.carte.getZoom() >= seuil;
    for (const c of ['sig-maquettes-groupes', 'sig-maquettes-point', 'sig-maquettes-groupes-nb']) {
      if (this.carte.getLayer(c)) this.carte.setLayoutProperty(c, 'visibility', proche ? 'none' : 'visible');
    }
    if (!proche) return;
    const b = this.carte.getBounds();
    const w = b.getEast() - b.getWest();
    const h = b.getNorth() - b.getSouth();
    // zone demandée un peu plus large que la vue : pas de nouvelle demande pour un petit déplacement
    const dedans = this._zone && b.getWest() >= this._zone[0] && b.getSouth() >= this._zone[1]
      && b.getEast() <= this._zone[2] && b.getNorth() <= this._zone[3] && w > (this._zone[2] - this._zone[0]) / 4;
    if (dedans && !forcer) return;
    const zone = [b.getWest() - w / 2, b.getSouth() - h / 2, b.getEast() + w / 2, b.getNorth() + h / 2];
    const requete = (this._requeteEmprises = Symbol('emprises'));
    let r;
    try {
      r = await lire('maquettes/emprises', { bbox: zone.map((v) => v.toFixed(6)).join(',') });
    } catch {
      return;
    }
    if (requete !== this._requeteEmprises) return;   // déplacement entre-temps : réponse périmée
    this._zone = zone;
    for (const f of r.features) f.properties.nom = this.maquettes.get(f.properties.id)?.nomCourt || '';
    this.carte.getSource('sig-maquettes').setData(r);
    this.emprisesZone = r;
    for (const id of this.affichees) {
      this.carte.setFeatureState({ source: 'sig-maquettes', id }, { charge: this.c3d.estCharge(id) });
    }
    if (this.ficheOuverte) this.carte.setFeatureState({ source: 'sig-maquettes', id: this.ficheOuverte }, { choisie: true });
  }

  /** Regroupe les redessins de la liste demandés pendant la même image. */
  _rendrePlusTard() {
    if (this._rendreDemande) return;
    this._rendreDemande = requestAnimationFrame(() => { this._rendreDemande = null; this._rendre(); });
  }

  // ------------------------------------------------------------ 3D

  async afficher3d(id, automatique = false) {
    const m = this.maquettes.get(id);
    const pos = m?.positionnement;
    if (pos?.statut !== 'positionnee') return;
    if (!automatique) this.automatiques.delete(id);
    if (this.affichees.has(id) && this.c3d.estCharge(id)) return;
    this.affichees.add(id);
    this.masquees.delete(id);
    this.progression.set(id, 'chargement 3D…');
    this._rendrePlusTard();
    try {
      await this.c3d.charger(id, `api/sig/maquettes/${id}/modele.glb`, affichage3d(pos), (p) => {
        const texte = p != null ? `chargement 3D ${Math.round(p * 100)} %` : 'chargement 3D…';
        if (this.progression.get(id) !== texte) {
          this.progression.set(id, texte);
          const zone = this.panneau.querySelector(`[data-progression="${id}"]`);
          if (zone) zone.textContent = texte;
        }
      });
      this.progression.delete(id);
      this.c3d.definir(id, { visible: true });
      this._details();
      // types IFC et familles des objets : filtres de la vue 3D
      lire(`maquettes/${id}/types`).then((t) => this.c3d.definirTypes(id, t)).catch(() => {});
    } catch (e) {
      this.affichees.delete(id);
      this.automatiques.delete(id);
      this.progression.set(id, `3D indisponible : ${e.message || e}`);
    }
    this.carte.setFeatureState({ source: 'sig-maquettes', id }, { charge: this.c3d.estCharge(id) });
    this._rendrePlusTard();
    if (!automatique && this.ficheOuverte === id) this.ouvrirFiche(id, false, false);
  }

  masquer3d(id) {
    this.automatiques.delete(id);
    this.affichees.delete(id);
    this.details.delete(id);
    this.masquees.add(id);
    this.c3d.retirer(id);
    this.progression.delete(id);
    this.carte.setFeatureState({ source: 'sig-maquettes', id }, { charge: false });
    this._rendre();
    if (this.ficheOuverte === id) this.ouvrirFiche(id, false, false);
  }

  basculer3d(id) {
    if (this.affichees.has(id)) this.masquer3d(id);
    else this.afficher3d(id);
  }

  isoler(id) {
    this.c3d.isoler(this.c3d.isole === id ? null : id);
    this._rendre();
    if (this.ficheOuverte === id) this.ouvrirFiche(id, false, false);
  }

  _emprise(ids) {
    let b = null;
    for (const id of ids) {
      const e = this.maquettes.get(id)?.positionnement?.emprise_wgs84;
      if (!e) continue;
      b = b ? [Math.min(b[0], e[0]), Math.min(b[1], e[1]), Math.max(b[2], e[2]), Math.max(b[3], e[3])] : [...e];
    }
    return b;
  }

  zoomer(ids) {
    const b = this._emprise([ids].flat());
    if (!b) return;
    auSol(this.carte);
    this.carte.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 80, maxZoom: 19, pitch: Math.max(this.carte.getPitch(), 55),
      bearing: this.carte.getBearing() });
  }

  async recalculer(id) {
    this.masquer3d(id);
    this.masquees.delete(id);
    try {
      await envoyer(`maquettes/${id}/recalculer`);
    } catch (e) {
      this.progression.set(id, `relance impossible : ${e.message}`);
    }
    await this.actualiser(true);
  }

  // ------------------------------------------------------------ sélection et fiches

  /** Emprise de maquette sous le point écran (id) ou null. */
  empriseSous(point) {
    const couches = ['sig-maquettes-fond', 'sig-maquettes-point'].filter((c) => this.carte.getLayer(c));
    const f = this.carte.queryRenderedFeatures([[point.x - 3, point.y - 3], [point.x + 3, point.y + 3]], { layers: couches })[0];
    return f ? f.properties.id : null;
  }

  /** Groupe de maquettes (vue éloignée) sous le point : zoom pour le déplier. Vrai si un groupe a été touché. */
  async deplierGroupe(point) {
    if (!this.carte.getLayer('sig-maquettes-groupes')) return false;
    const f = this.carte.queryRenderedFeatures([[point.x - 3, point.y - 3], [point.x + 3, point.y + 3]],
      { layers: ['sig-maquettes-groupes'] })[0];
    if (!f) return false;
    const zoom = await this.carte.getSource('sig-maquettes-points').getClusterExpansionZoom(f.properties.cluster_id);
    this.carte.easeTo({ center: f.geometry.coordinates, zoom: Math.max(zoom, this.carte.getZoom() + 1) });
    return true;
  }

  /** Révèle une maquette dans l'arborescence (dossiers dépliés). */
  reveler(id) {
    const m = this.maquettes.get(id);
    if (!m) return;
    const parties = m.dossier.split('/');
    for (let i = 1; i <= parties.length; i += 1) this.ouverts.add(parties.slice(0, i).join('/'));
    this.ouverts.add(`${m.dossier}#${lot(m.nomCourt)}`);
    this._rendre();
    this.panneau.querySelector(`[data-maquette="${id}"]`)?.scrollIntoView({ block: 'nearest' });
  }

  async ouvrirFiche(id, zoomer = false, reveler = true) {
    if (!this.maquettes.has(id)) return;
    if (this.ficheOuverte && this.ficheOuverte !== id) {
      this.carte.setFeatureState({ source: 'sig-maquettes', id: this.ficheOuverte }, { choisie: false });
    }
    this.ficheOuverte = id;
    if (this.maquettes.get(id).positionnement?.statut === 'positionnee') {
      this.carte.setFeatureState({ source: 'sig-maquettes', id }, { choisie: true });
    }
    if (zoomer) this.zoomer(id);
    if (reveler) this.reveler(id);
    else this._rendrePlusTard();
    let m;
    try {
      m = { ...this.maquettes.get(id), ...(await lire(`maquettes/${id}`)) };
    } catch {
      m = this.maquettes.get(id);   // description complète indisponible : fiche avec la ligne de la liste
    }
    if (this.ficheOuverte !== id || !m) return;   // autre fiche ouverte entre-temps
    m.ouvrage = m.dossier.split('/').pop();
    m.nomCourt = nomCourt(m.nom, m.ouvrage);
    afficherMaquette({ ...m, nom: m.nomCourt, nomComplet: m.nom }, {
      chargee: this.affichees.has(id),
      isolee: this.c3d.isole === id,
      opacite: this.c3d.modeles.get(id)?.opacite,
      definirOpacite: (v) => this.c3d.definir(id, { opacite: v }),
      basculer3d: () => this.basculer3d(id),
      zoomer: () => this.zoomer(id),
      isoler: () => this.isoler(id),
      recalculer: () => this.recalculer(id),
      planLocaux: () => this.planLocaux(m),
      complements: this.complementsFiche(m),
    }, () => this.deselectionner());
  }

  async ouvrirObjet(cible) {
    this.c3d.selectionner(cible);
    const m = this.maquettes.get(cible.id);
    this.surSelection();
    let fiche;
    try {
      fiche = await lire(`maquettes/${cible.id}/objets/${encodeURIComponent(cible.guid)}`);
    } catch (e) {
      fiche = { erreur: e.message };
    }
    if (this.c3d.selection?.guid !== cible.guid) return;   // autre clic entre-temps
    afficherObjetIfc(fiche, m && { ...m, nom: m.nomCourt }, () => this.c3d.deselectionner(),
      { zoomer: () => this.zoomerObjet() });
  }

  /** Cadre la vue sur l'objet sélectionné, centre de la vue à sa hauteur (sous-sols compris). */
  zoomerObjet() {
    const b = this.c3d.boiteSelection();
    if (b) cadrer(this.carte, b);
    return Boolean(b);
  }

  deselectionner() {
    if (this.ficheOuverte) this.carte.setFeatureState({ source: 'sig-maquettes', id: this.ficheOuverte }, { choisie: false });
    this.ficheOuverte = null;
    this.c3d.deselectionner();
    this._rendre();
  }

  /** Hauteurs (m / terrain) couvertes par les maquettes chargées, pour la coupe horizontale. */
  /** Emprises (GeoJSON WGS84) des maquettes affichées en 3D dans la zone : masquage des bâtiments BD TOPO. */
  emprises3d() {
    return (this.emprisesZone?.features || [])
      .filter((f) => this.affichees.has(f.properties.id) && this.c3d.estCharge(f.properties.id)
        && this.c3d.modeles.get(f.properties.id)?.visible !== false)
      .map((f) => f.geometry);
  }

  hauteurs() {
    let bas = Infinity;
    let haut = -Infinity;
    for (const id of this.affichees) {
      const h = this.maquettes.get(id)?.positionnement?.hauteurs_affichees;
      if (h && this.c3d.estCharge(id)) { bas = Math.min(bas, h[0]); haut = Math.max(haut, h[1]); }
    }
    return Number.isFinite(bas) ? [Math.floor(bas), Math.ceil(haut)] : null;
  }

  // ------------------------------------------------------------ arborescence

  _arbre(maquettes) {
    const racine = { cle: '', nom: '', enfants: new Map(), maquettes: [] };
    for (const m of maquettes) {
      let noeud = racine;
      const parties = m.dossier === '.' ? [] : m.dossier.split('/');
      parties.forEach((p, i) => {
        if (!noeud.enfants.has(p)) noeud.enfants.set(p, { cle: parties.slice(0, i + 1).join('/'), nom: p, enfants: new Map(), maquettes: [] });
        noeud = noeud.enfants.get(p);
      });
      noeud.maquettes.push(m);
    }
    // dossiers chargés : sous-groupes par lot (ARC, SGO, CIF…)
    const regrouper = (noeud) => {
      for (const e of noeud.enfants.values()) regrouper(e);
      if (noeud.maquettes.length > SEUIL_SOUS_GROUPES) {
        const lots = new Map();
        for (const m of noeud.maquettes) {
          const l = lot(m.nomCourt);
          if (!lots.has(l)) lots.set(l, { cle: `${noeud.cle}#${l}`, nom: l, enfants: new Map(), maquettes: [], lot: true });
          lots.get(l).maquettes.push(m);
        }
        noeud.maquettes = [];
        const ordre = [...lots.entries()].sort(([a], [b]) => (a === 'Autres') - (b === 'Autres') || a.localeCompare(b));
        for (const [l, g] of ordre) noeud.enfants.set(`#${l}`, g);
      }
    };
    regrouper(racine);
    return racine;
  }

  _toutes(noeud) {
    return [...noeud.maquettes, ...[...noeud.enfants.values()].flatMap((e) => this._toutes(e))];
  }

  _ligneMaquette(m) {
    const pos = m.positionnement;
    const e = m.etat;
    const charge = this.affichees.has(m.id);
    const classes = ['sig-arbre__feuille'];
    if (this.ficheOuverte === m.id) classes.push('sig-arbre__feuille--choisie');
    if (this.c3d.isole === m.id) classes.push('sig-maquette--isolee');
    const li = el('li', { className: classes.join(' ') });
    li.dataset.maquette = m.id;

    let oeil;
    if (pos?.locaux_seuls) {
      oeil = el('button', { type: 'button', className: 'sig-bouton-icone', title: 'Plan des locaux' }, icone('plan', 16));
      oeil.addEventListener('click', () => this.planLocaux(m));
    } else if (pos?.statut === 'positionnee') {
      oeil = el('button', { type: 'button', className: `sig-bouton-icone${charge ? ' sig-bouton-icone--actif' : ''}`,
        title: charge ? 'Masquer la 3D' : `Afficher en 3D (${pos.affichage.taille_mo} Mo)` }, icone('maquettes', 16));
      oeil.addEventListener('click', () => this.basculer3d(m.id));
    } else {
      oeil = el('span', { className: 'sig-arbre__vide' });
    }
    const nom = el('button', { type: 'button', className: 'sig-arbre__nom', textContent: m.nomCourt, title: `${m.nom}\n${m.fichier}` });
    nom.addEventListener('click', () => this.ouvrirFiche(m.id, pos?.statut === 'positionnee', false));

    let etiquette = null;
    if (e.phase === 'en_cours') etiquette = el('span', { className: 'sig-etiquette sig-etiquette--cours', textContent: `${e.progression ?? 0} %` });
    else if (e.phase === 'en_attente') etiquette = el('span', { className: 'sig-etiquette', textContent: 'en attente' });
    else if (e.phase === 'erreur') etiquette = el('span', { className: 'sig-etiquette sig-etiquette--erreur', textContent: 'erreur', title: e.message });
    else if (pos?.statut !== 'positionnee') etiquette = el('span', { className: 'sig-etiquette sig-etiquette--erreur', textContent: 'non positionnée', title: pos?.raison || '' });
    else if (pos.precision === 'approchée') etiquette = el('span', { className: 'sig-etiquette sig-etiquette--alerte', textContent: 'approchée', title: pos.libelle });

    const infos = [];
    if (m.locaux?.phase === 'pret' && m.locaux.nb) infos.push(`${m.locaux.nb} locaux`);
    if (pos?.affichage && !pos.affichage.chargement_automatique && !charge) infos.push(`${pos.affichage.taille_mo} Mo`);
    li.append(el('div', { className: 'sig-arbre__ligne' }, oeil, nom,
      infos.length ? el('span', { className: 'sig-arbre__info', textContent: infos.join(' · ') }) : null, etiquette));
    const prog = this.progression.get(m.id);
    if (prog) {
      const z = el('div', { className: 'sig-arbre__progression', textContent: prog });
      z.dataset.progression = m.id;
      li.append(z);
    }
    if (e.phase === 'en_cours') {
      const barre = el('div', { className: 'sig-progression' }, el('span'));
      barre.firstChild.style.width = `${e.progression ?? 0}%`;
      li.append(barre);
    }
    return li;
  }

  _noeud(noeud, profondeur, forceOuvert) {
    const toutes = this._toutes(noeud);
    const ouvert = forceOuvert || this.ouverts.has(noeud.cle);
    const positionnees = toutes.filter((m) => m.positionnement?.statut === 'positionnee');
    const en3d = positionnees.filter((m) => this.affichees.has(m.id)).length;
    const enCours = toutes.filter((m) => m.etat.phase === 'en_cours' || m.etat.phase === 'en_attente').length;

    const basculer = () => {
      if (this.ouverts.has(noeud.cle)) this.ouverts.delete(noeud.cle); else this.ouverts.add(noeud.cle);
      this._rendre();
    };
    const chevron = el('button', { type: 'button', className: `sig-arbre__chevron${ouvert ? ' sig-arbre__chevron--ouvert' : ''}`,
      ariaExpanded: String(ouvert), title: ouvert ? 'Replier' : 'Déplier' }, icone('bas', 14));
    chevron.addEventListener('click', basculer);
    const nom = el('button', { type: 'button', className: `sig-arbre__groupe${noeud.lot ? ' sig-arbre__groupe--lot' : ''}`, textContent: noeud.nom });
    nom.addEventListener('click', basculer);
    const compte = el('span', { className: 'sig-arbre__compte', textContent: `${toutes.length}${en3d ? ` · ${en3d} en 3D` : ''}`,
      title: `${toutes.length} maquette(s), ${positionnees.length} positionnée(s), ${en3d} affichée(s) en 3D` });
    const actions = el('span', { className: 'sig-arbre__actions' });
    if (positionnees.length) {
      const legeres = positionnees.filter((m) => m.positionnement.affichage.chargement_automatique && !m.positionnement.locaux_seuls);
      const tout = en3d > 0;
      const titre = tout ? 'Masquer la 3D du groupe'
        : `Afficher la 3D du groupe (${legeres.length} maquette(s) légère(s) ; les lourdes se chargent une à une)`;
      const b = bouton('maquettes', titre, () => {
        if (tout) positionnees.forEach((m) => this.affichees.has(m.id) && this.masquer3d(m.id));
        else legeres.forEach((m) => this.afficher3d(m.id));
      });
      if (tout) b.classList.add('sig-bouton-icone--actif');
      actions.append(b, bouton('zoom', 'Zoomer sur le groupe', () => this.zoomer(positionnees.map((m) => m.id))));
    }
    const tete = el('div', { className: 'sig-arbre__tete' }, chevron, nom, compte,
      enCours ? el('span', { className: 'sig-etiquette sig-etiquette--cours', textContent: `${enCours} en cours` }) : null, actions);
    tete.style.paddingLeft = `${profondeur * 12}px`;
    const li = el('li', { className: 'sig-arbre__noeud' }, tete);
    if (ouvert) {
      const ul = el('ul', { className: 'sig-arbre' });
      for (const e of noeud.enfants.values()) ul.append(this._noeud(e, profondeur + 1, forceOuvert));
      const tri = [...noeud.maquettes].sort((a, b) => a.nomCourt.localeCompare(b.nomCourt, 'fr'));
      const limite = this.complets.has(noeud.cle) ? tri.length : MAX_LIGNES_GROUPE;
      for (const m of tri.slice(0, limite)) {
        const f = this._ligneMaquette(m);
        f.style.paddingLeft = `${(profondeur + 1) * 12}px`;
        ul.append(f);
      }
      if (tri.length > limite) {
        const plus = el('button', { type: 'button', className: 'sig-lien', textContent: `Afficher les ${tri.length - limite} autres maquettes` });
        plus.addEventListener('click', () => { this.complets.add(noeud.cle); this._rendre(); });
        const li2 = el('li', { className: 'sig-arbre__feuille' }, plus);
        li2.style.paddingLeft = `${(profondeur + 1) * 12}px`;
        ul.append(li2);
      }
      li.append(ul);
    }
    return li;
  }

  _rendre() {
    const inv = this.inventaire;
    if (!inv) return;
    const toutes = [...this.maquettes.values()];
    const positionnees = toutes.filter((m) => m.positionnement?.statut === 'positionnee').length;
    const traitees = toutes.filter((m) => m.etat.phase === 'pret').length;
    const f = this.filtre.trim().toLowerCase();
    const visibles = toutes.filter((m) => !f || m.fichier.toLowerCase().includes(f) || m.nomCourt.toLowerCase().includes(f));

    const saisie = this._saisie || (this._saisie = (() => {
      const s = el('input', { type: 'search', className: 'sig-filtre', placeholder: 'Filtrer (ouvrage, lot, nom…)' });
      s.addEventListener('input', () => { this.filtre = s.value; this._rendre(); });
      return s;
    })());
    const resume = el('p', { className: 'sig-resume' },
      `${toutes.length} maquette(s) · ${traitees} traitée(s) · ${positionnees} positionnée(s) · ${this.affichees.size} en 3D`
      + (inv.en_attente ? ` · ${inv.en_attente} en attente de traitement` : ''));
    const tout = (ouvrir) => () => {
      if (ouvrir) {
        for (const m of toutes) {
          const p = m.dossier.split('/');
          for (let i = 1; i <= p.length; i += 1) this.ouverts.add(p.slice(0, i).join('/'));
          this.ouverts.add(`${m.dossier}#${lot(m.nomCourt)}`);
        }
      } else {
        this.ouverts.clear();
      }
      this._rendre();
    };
    const outils = el('div', { className: 'sig-volet__outils' }, saisie,
      bouton('plein', 'Tout déplier', tout(true)), bouton('moins', 'Tout replier', tout(false)),
      bouton('actualiser', 'Actualiser la liste (nouvelles maquettes)', () => this.actualiser(true)));

    const arbre = this._arbre(visibles);
    const ul = el('ul', { className: 'sig-arbre sig-arbre--racine' });
    for (const e of arbre.enfants.values()) ul.append(this._noeud(e, 0, Boolean(f)));
    for (const m of arbre.maquettes) ul.append(this._ligneMaquette(m));
    if (!visibles.length) ul.append(el('li', { className: 'sig-info', textContent: f ? 'Aucune maquette ne correspond au filtre.' : 'Aucune maquette.' }));

    const defilement = this.panneau.querySelector('.sig-defilement')?.scrollTop;
    const avaitFocus = document.activeElement === saisie;
    const liste = el('div', { className: 'sig-defilement' }, ul);
    this.panneau.replaceChildren(resume, outils, liste);
    if (defilement) liste.scrollTop = defilement;
    if (avaitFocus) saisie.focus();
    this._rendreAnomalies(toutes);
  }

  _rendreAnomalies(toutes) {
    const erreurs = toutes.filter((m) => m.etat.phase === 'erreur');
    const nonPos = toutes.filter((m) => m.positionnement?.statut === 'non_positionnee');
    const avert = toutes.filter((m) => m.positionnement?.statut === 'positionnee' && m.positionnement.avertissements?.length);
    const ligne = (m, texte) => {
      const b = el('button', { type: 'button', className: 'sig-lien', textContent: `${m.ouvrage} › ${m.nomCourt}`, title: m.fichier });
      b.addEventListener('click', () => this.ouvrirFiche(m.id, true));
      return el('li', { className: 'sig-anomalie' }, b, el('span', { textContent: texte }));
    };
    this.panneauAnomalies.replaceChildren(el('section', { className: 'sig-section' },
      el('h3', { textContent: `Maquettes non positionnées (${nonPos.length})` }),
      nonPos.length ? el('ul', { className: 'sig-anomalies sig-anomalies--maquettes' }, ...nonPos.map((m) => ligne(m, m.positionnement.raison)))
        : el('p', { className: 'sig-info', textContent: 'Aucune.' }),
      erreurs.length ? el('h3', { textContent: `Maquettes en erreur de traitement (${erreurs.length})` }) : null,
      erreurs.length ? el('ul', { className: 'sig-anomalies sig-anomalies--maquettes' }, ...erreurs.map((m) => ligne(m, m.etat.message))) : null,
      avert.length ? el('h3', { textContent: `Avertissements de positionnement (${avert.length})` }) : null,
      avert.length ? el('ul', { className: 'sig-anomalies sig-anomalies--maquettes' },
        ...avert.flatMap((m) => m.positionnement.avertissements.map((t) => ligne(m, t)))) : null,
    ));
    this.nbAnomalies = nonPos.length + erreurs.length;
  }
}

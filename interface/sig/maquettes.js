// Maquettes IFC : liste (état du traitement, positionnement), emprises au sol sur la carte, modèles 3D
// dans la carte (vue3d.js), fiche d'une maquette et de ses objets. Données : /api/sig/maquettes.
import { envoyer, lire } from './api.js';
import { afficherMaquette, afficherObjetIfc } from './fiche.js';
import { bouton, el, icone } from './ui.js';

const ATTENTE_SUIVI_MS = 3000;     // rafraîchissement pendant les traitements
const COULEUR_EMPRISE = '#0079c1';
const METHODES_COURTES = {
  ifc_map_conversion: 'IfcMapConversion',
  ifc_site_projete: 'IfcSite projeté',
  ifc_site_latlon: 'Lat/Long approchée',
  manuel: 'Saisie manuelle',
};

export class GestionnaireMaquettes {
  constructor(carte, couche3d, { panneau, anomalies, surEtat, surSelection }) {
    this.carte = carte;
    this.c3d = couche3d;
    this.panneau = panneau;
    this.panneauAnomalies = anomalies;
    this.surEtat = surEtat || (() => {});
    this.surSelection = surSelection || (() => {});
    this.maquettes = new Map();   // id -> données serveur
    this.affichees = new Set();   // ids dont la 3D est demandée
    this.progression = new Map(); // id -> texte de chargement 3D
    this.filtre = '';
    this.ficheOuverte = null;
    this._minuteur = null;
  }

  async demarrer() {
    this.carte.addSource('sig-maquettes', { type: 'geojson', data: { type: 'FeatureCollection', features: [] }, promoteId: 'id' });
    this.carte.addLayer({ id: 'sig-maquettes-fond', type: 'fill', source: 'sig-maquettes',
      paint: { 'fill-color': COULEUR_EMPRISE, 'fill-opacity': ['case', ['boolean', ['feature-state', 'charge'], false], 0.04, 0.18] } });
    this.carte.addLayer({ id: 'sig-maquettes-contour', type: 'line', source: 'sig-maquettes',
      paint: { 'line-color': COULEUR_EMPRISE, 'line-width': ['case', ['boolean', ['feature-state', 'choisie'], false], 3.5, 1.6],
               'line-dasharray': [3, 1.5] } });
    this.carte.addLayer({ id: 'sig-maquettes-nom', type: 'symbol', source: 'sig-maquettes', minzoom: 15,
      layout: { 'text-field': ['get', 'nom'], 'text-size': 11, 'text-font': ['Noto Sans Regular'], 'text-optional': true },
      paint: { 'text-color': '#003a5d', 'text-halo-color': '#fff', 'text-halo-width': 1.5 } });
    // Les étiquettes exigent une police (glyphs) : sans glyphes configurés, le calque est retiré sans erreur.
    if (!this.carte.getStyle().glyphs) this.carte.removeLayer('sig-maquettes-nom');
    this.carte.addLayer(this.c3d);
    this.panneau.replaceChildren(el('p', { className: 'sig-info', textContent: 'Inventaire des maquettes…' }));
    await this.actualiser();
  }

  // ------------------------------------------------------------ données

  async actualiser() {
    clearTimeout(this._minuteur);
    let inv;
    try {
      inv = await lire('maquettes');
    } catch (e) {
      this.panneau.replaceChildren(el('p', { className: 'sig-erreur', textContent: `Maquettes indisponibles : ${e.message}` }));
      this._minuteur = setTimeout(() => this.actualiser(), ATTENTE_SUIVI_MS * 5);
      return;
    }
    this.inventaire = inv;
    const nouvelles = [];
    for (const m of inv.maquettes) {
      const avant = this.maquettes.get(m.id);
      this.maquettes.set(m.id, m);
      const pos = m.positionnement;
      if (pos?.statut === 'positionnee' && pos.affichage.chargement_automatique && avant?.etat.phase !== 'pret'
          && !this.affichees.has(m.id) && !this._masqueesParUtilisateur?.has(m.id)) {
        nouvelles.push(m.id);
      }
    }
    this._majEmprises();
    this._rendre();
    for (const id of nouvelles) this.afficher3d(id, true);
    if (this.ficheOuverte && this.maquettes.has(this.ficheOuverte)) this.ouvrirFiche(this.ficheOuverte, false);
    const enCours = inv.maquettes.filter((m) => m.etat.phase === 'en_cours' || m.etat.phase === 'en_attente').length;
    this.surEtat({ total: inv.maquettes.length, enCours, courant: inv.maquettes.find((m) => m.etat.phase === 'en_cours') });
    if (enCours) this._minuteur = setTimeout(() => this.actualiser(), ATTENTE_SUIVI_MS);
  }

  _majEmprises() {
    const features = [];
    for (const m of this.maquettes.values()) {
      const pos = m.positionnement;
      if (pos?.statut !== 'positionnee') continue;
      features.push({ type: 'Feature', geometry: pos.emprise_geojson, properties: { id: m.id, nom: m.nom } });
    }
    this.carte.getSource('sig-maquettes').setData({ type: 'FeatureCollection', features });
    for (const id of this.affichees) {
      this.carte.setFeatureState({ source: 'sig-maquettes', id }, { charge: this.c3d.estCharge(id) });
    }
  }

  // ------------------------------------------------------------ 3D

  async afficher3d(id, automatique = false) {
    const m = this.maquettes.get(id);
    const pos = m?.positionnement;
    if (pos?.statut !== 'positionnee') return;
    this.affichees.add(id);
    this._masqueesParUtilisateur?.delete(id);
    this.progression.set(id, 'chargement 3D…');
    this._rendre();
    try {
      await this.c3d.charger(id, `api/sig/maquettes/${id}/modele.glb`, pos.affichage, (p) => {
        const texte = p != null ? `chargement 3D ${Math.round(p * 100)} %` : 'chargement 3D…';
        if (this.progression.get(id) !== texte) {
          this.progression.set(id, texte);
          const zone = this.panneau.querySelector(`[data-progression="${id}"]`);
          if (zone) zone.textContent = texte;
        }
      });
      this.progression.delete(id);
      this.c3d.definir(id, { visible: true });
    } catch (e) {
      this.affichees.delete(id);
      this.progression.set(id, `3D indisponible : ${e.message || e}`);
    }
    this.carte.setFeatureState({ source: 'sig-maquettes', id }, { charge: this.c3d.estCharge(id) });
    this._rendre();
    if (!automatique && this.ficheOuverte === id) this.ouvrirFiche(id, false);
  }

  masquer3d(id) {
    this.affichees.delete(id);
    (this._masqueesParUtilisateur ||= new Set()).add(id);
    this.c3d.retirer(id);
    this.progression.delete(id);
    this.carte.setFeatureState({ source: 'sig-maquettes', id }, { charge: false });
    this._rendre();
    if (this.ficheOuverte === id) this.ouvrirFiche(id, false);
  }

  basculer3d(id) {
    if (this.affichees.has(id)) this.masquer3d(id);
    else this.afficher3d(id);
  }

  isoler(id) {
    this.c3d.isoler(this.c3d.isole === id ? null : id);
    this._rendre();
    if (this.ficheOuverte === id) this.ouvrirFiche(id, false);
  }

  zoomer(id) {
    const pos = this.maquettes.get(id)?.positionnement;
    if (!pos?.emprise_wgs84) return;
    const [x1, y1, x2, y2] = pos.emprise_wgs84;
    this.carte.fitBounds([[x1, y1], [x2, y2]], { padding: 80, maxZoom: 19, pitch: Math.max(this.carte.getPitch(), 55),
      bearing: this.carte.getBearing() });
  }

  async recalculer(id) {
    this.masquer3d(id);
    this._masqueesParUtilisateur.delete(id);
    try {
      await envoyer(`maquettes/${id}/recalculer`);
    } catch (e) {
      this.progression.set(id, `relance impossible : ${e.message}`);
    }
    await this.actualiser();
  }

  // ------------------------------------------------------------ sélection et fiches

  /** Emprise de maquette sous le point écran (id) ou null. */
  empriseSous(point) {
    const f = this.carte.queryRenderedFeatures([[point.x - 3, point.y - 3], [point.x + 3, point.y + 3]],
      { layers: ['sig-maquettes-fond'] })[0];
    return f ? f.properties.id : null;
  }

  ouvrirFiche(id, zoomer = false) {
    const m = this.maquettes.get(id);
    if (!m) return;
    if (this.ficheOuverte && this.ficheOuverte !== id) {
      this.carte.setFeatureState({ source: 'sig-maquettes', id: this.ficheOuverte }, { choisie: false });
    }
    this.ficheOuverte = id;
    if (m.positionnement?.statut === 'positionnee') {
      this.carte.setFeatureState({ source: 'sig-maquettes', id }, { choisie: true });
    }
    if (zoomer) this.zoomer(id);
    afficherMaquette(m, {
      chargee: this.affichees.has(id),
      isolee: this.c3d.isole === id,
      basculer3d: () => this.basculer3d(id),
      zoomer: () => this.zoomer(id),
      isoler: () => this.isoler(id),
      recalculer: () => this.recalculer(id),
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
    afficherObjetIfc(fiche, m, () => this.c3d.deselectionner());
  }

  deselectionner() {
    if (this.ficheOuverte) this.carte.setFeatureState({ source: 'sig-maquettes', id: this.ficheOuverte }, { choisie: false });
    this.ficheOuverte = null;
    this.c3d.deselectionner();
  }

  /** Hauteurs (m / terrain) couvertes par les maquettes chargées, pour la coupe horizontale. */
  hauteurs() {
    let bas = Infinity;
    let haut = -Infinity;
    for (const id of this.affichees) {
      const h = this.maquettes.get(id)?.positionnement?.hauteurs_affichees;
      if (h && this.c3d.estCharge(id)) { bas = Math.min(bas, h[0]); haut = Math.max(haut, h[1]); }
    }
    return Number.isFinite(bas) ? [Math.floor(bas), Math.ceil(haut)] : null;
  }

  // ------------------------------------------------------------ volet

  _ligne(m) {
    const pos = m.positionnement;
    const e = m.etat;
    const charge = this.affichees.has(m.id);
    const li = el('li', { className: 'sig-maquette' });
    const nom = el('button', { type: 'button', className: 'sig-maquette__nom', textContent: m.nom, title: m.fichier });
    nom.addEventListener('click', () => this.ouvrirFiche(m.id, pos?.statut === 'positionnee'));

    let etiquette;
    if (e.phase === 'pret' && pos) {
      etiquette = pos.statut === 'positionnee'
        ? el('span', { className: `sig-etiquette sig-etiquette--${pos.precision === 'approchée' ? 'alerte' : 'ok'}`,
          textContent: METHODES_COURTES[pos.methode] || pos.methode, title: `${pos.libelle}${pos.systeme ? ` — ${pos.systeme}` : ''}` })
        : el('span', { className: 'sig-etiquette sig-etiquette--erreur', textContent: 'Non positionnée', title: pos.raison || '' });
    } else if (e.phase === 'erreur') {
      etiquette = el('span', { className: 'sig-etiquette sig-etiquette--erreur', textContent: 'Erreur', title: e.message });
    } else if (e.phase === 'en_cours') {
      etiquette = el('span', { className: 'sig-etiquette sig-etiquette--cours', textContent: `${e.progression ?? 0} %` });
    } else {
      etiquette = el('span', { className: 'sig-etiquette', textContent: e.position ? `En attente (${e.position})` : 'En attente' });
    }

    const outils = el('div', { className: 'sig-maquette__outils' });
    if (pos?.statut === 'positionnee') {
      const oeil = el('button', { type: 'button', className: `sig-bouton-icone${charge ? ' sig-bouton-icone--actif' : ''}`,
        title: charge ? 'Masquer la 3D' : `Afficher en 3D (${pos.affichage.taille_mo} Mo)` }, icone('maquettes', 18));
      oeil.addEventListener('click', () => this.basculer3d(m.id));
      outils.append(oeil, bouton('zoom', 'Zoomer sur la maquette', () => this.zoomer(m.id)));
      if (charge) {
        const op = el('input', { type: 'range', min: 0.1, max: 1, step: 0.05, value: this.c3d.modeles.get(m.id)?.opacite ?? 1,
          title: 'Opacité de la maquette' });
        op.addEventListener('input', () => this.c3d.definir(m.id, { opacite: Number(op.value) }));
        outils.append(op);
      }
    }
    li.append(el('div', { className: 'sig-maquette__tete' }, nom, etiquette), outils);
    const details = [];
    if (e.phase === 'en_cours') {
      const barre = el('div', { className: 'sig-progression' }, el('span'));
      barre.firstChild.style.width = `${e.progression ?? 0}%`;
      li.append(barre);
      details.push(e.message);
    }
    if (e.phase === 'pret' && pos?.statut !== 'positionnee' && pos?.raison) details.push(pos.raison);
    if (e.phase === 'erreur') details.push(e.message);
    li.append(el('div', { className: 'sig-maquette__info', textContent: details.join(' · ') }));
    const prog = el('div', { className: 'sig-maquette__info' });
    prog.dataset.progression = m.id;
    prog.textContent = this.progression.get(m.id) || '';
    li.append(prog);
    if (this.c3d.isole === m.id) li.classList.add('sig-maquette--isolee');
    return li;
  }

  _rendre() {
    const inv = this.inventaire;
    if (!inv) return;
    const toutes = [...this.maquettes.values()];
    const positionnees = toutes.filter((m) => m.positionnement?.statut === 'positionnee').length;
    const traitees = toutes.filter((m) => m.etat.phase === 'pret').length;
    const filtre = this.filtre.trim().toLowerCase();
    const visibles = toutes.filter((m) => !filtre || m.fichier.toLowerCase().includes(filtre));

    const groupes = new Map();
    for (const m of visibles) {
      const g = m.dossier.split('/').slice(-2).join(' › ') || 'Racine';
      if (!groupes.has(g)) groupes.set(g, []);
      groupes.get(g).push(m);
    }
    const blocs = [];
    for (const [g, ms] of groupes) {
      blocs.push(el('section', { className: 'sig-groupe' },
        el('h3', { className: 'sig-groupe__titre', textContent: `${g} (${ms.length})` }),
        el('ul', { className: 'sig-maquettes' }, ...ms.map((m) => this._ligne(m)))));
    }

    const saisie = this._saisie || (this._saisie = (() => {
      const s = el('input', { type: 'search', className: 'sig-filtre', placeholder: 'Filtrer les maquettes (nom, ouvrage…)' });
      s.addEventListener('input', () => { this.filtre = s.value; this._rendre(); });
      return s;
    })());
    const resume = el('p', { className: 'sig-resume' },
      `${toutes.length} maquette(s) · ${traitees} traitée(s) · ${positionnees} positionnée(s) · ${this.affichees.size} en 3D`);
    const entete = el('div', { className: 'sig-volet__outils' }, saisie,
      bouton('actualiser', 'Actualiser la liste', () => this.actualiser()));
    const defilement = this.panneau.querySelector('.sig-defilement')?.scrollTop;
    const avaitFocus = document.activeElement === saisie;
    const liste = el('div', { className: 'sig-defilement' }, ...blocs);
    this.panneau.replaceChildren(resume, entete, liste);
    if (defilement) liste.scrollTop = defilement;
    if (avaitFocus) saisie.focus();
    this._rendreAnomalies(toutes);
  }

  _rendreAnomalies(toutes) {
    const erreurs = toutes.filter((m) => m.etat.phase === 'erreur');
    const nonPos = toutes.filter((m) => m.positionnement?.statut === 'non_positionnee');
    const avert = toutes.filter((m) => m.positionnement?.statut === 'positionnee' && m.positionnement.avertissements?.length);
    const ligne = (m, texte) => {
      const b = el('button', { type: 'button', className: 'sig-lien', textContent: m.nom, title: m.fichier });
      b.addEventListener('click', () => this.ouvrirFiche(m.id, true));
      return el('li', {}, b, ` : ${texte}`);
    };
    this.panneauAnomalies.replaceChildren(el('section', { className: 'sig-section' },
      el('h3', { textContent: `Maquettes non positionnées (${nonPos.length})` }),
      nonPos.length ? el('ul', { className: 'sig-anomalies' }, ...nonPos.map((m) => ligne(m, m.positionnement.raison)))
        : el('p', { className: 'sig-info', textContent: 'Aucune.' }),
      erreurs.length ? el('h3', { textContent: `Maquettes en erreur de traitement (${erreurs.length})` }) : null,
      erreurs.length ? el('ul', { className: 'sig-anomalies' }, ...erreurs.map((m) => ligne(m, m.etat.message))) : null,
      avert.length ? el('h3', { textContent: 'Avertissements de positionnement' }) : null,
      avert.length ? el('ul', { className: 'sig-anomalies' },
        ...avert.flatMap((m) => m.positionnement.avertissements.map((t) => ligne(m, t)))) : null,
    ));
    this.nbAnomalies = nonPos.length + erreurs.length;
  }
}

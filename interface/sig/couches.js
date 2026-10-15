// Gestionnaire des couches (fichiers et services Esri) : affichage, ordre, opacité, zoom, filtre
// d'emprise, clic sur un objet (fiche), recherche texte, état et actualisation des services.
// Les couches viennent de /api/sig/couches.
import { dateCourte, envoyer, lire } from './api.js';
import { afficherFiche, fermerFiche } from './fiche.js';
import { bouton } from './ui.js';

const DELAI_EMPRISE_MS = 300; // attente après un déplacement avant de recharger une couche filtrée
const TOLERANCE_CLIC_PX = 4;
export { idsCalques };

const FILTRES = {
  pt: ['in', ['geometry-type'], ['literal', ['Point', 'MultiPoint']]],
  ln: ['in', ['geometry-type'], ['literal', ['LineString', 'MultiLineString']]],
  fl: ['in', ['geometry-type'], ['literal', ['Polygon', 'MultiPolygon']]],
};

function el(tag, attrs = {}, ...enfants) {
  const e = Object.assign(document.createElement(tag), attrs);
  for (const x of enfants) if (x != null) e.append(x);
  return e;
}

const autour = (p) => [[p.x - TOLERANCE_CLIC_PX, p.y - TOLERANCE_CLIC_PX], [p.x + TOLERANCE_CLIC_PX, p.y + TOLERANCE_CLIC_PX]];

function idsCalques(c) {
  return ['pt', 'ln', 'fl', 'ol'].map((s) => `sig-${c.id}-${s}`);
}

// Objets trouvés par une interrogation croisée : état « resultat » de l'entité, surligné en or.
const RESULTAT = ['boolean', ['feature-state', 'resultat'], false];
const OR = '#E8B400';
const siResultat = (oui, non) => ['case', RESULTAT, oui, non];

function calques(c) {
  const s = c.style;
  const src = `sig-src-${c.id}`;
  const op = (base) => base ?? 0.9;
  return [
    { id: `sig-${c.id}-fl`, type: 'fill', source: src, filter: FILTRES.fl,
      paint: { 'fill-color': siResultat(OR, s.couleur), 'fill-opacity': op(s.opacite) } },
    { id: `sig-${c.id}-ol`, type: 'line', source: src, filter: FILTRES.fl,
      paint: { 'line-color': siResultat(OR, s.contour || s.couleur), 'line-width': siResultat(3, s.epaisseur ?? 1), 'line-opacity': 1 } },
    { id: `sig-${c.id}-ln`, type: 'line', source: src, filter: FILTRES.ln,
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': siResultat(OR, s.couleur), 'line-width': siResultat((s.epaisseur ?? 2) + 3, s.epaisseur ?? 2),
               'line-opacity': op(s.opacite) } },
    { id: `sig-${c.id}-pt`, type: 'circle', source: src, filter: FILTRES.pt,
      paint: { 'circle-color': s.couleur, 'circle-radius': siResultat((s.rayon ?? 5) + 2, s.rayon ?? 5), 'circle-opacity': op(s.opacite),
               'circle-stroke-color': siResultat(OR, s.contour || '#ffffff'), 'circle-stroke-width': siResultat(3, s.epaisseur_contour ?? 1),
               'circle-stroke-opacity': op(s.opacite) } },
  ];
}

const SOURCES = {
  service: 'téléchargée du service',
  cache: 'cache local à jour',
  cache_perime: 'service indisponible : cache ancien',
  repli: 'service indisponible : export de repli',
};

export function origineService(s) {
  return `${SOURCES[s.source] || s.source} — données du ${dateCourte(s.date_maj)}`;
}

const degradee = (s) => s.source === 'cache_perime' || s.source === 'repli';

const PROPRIETES_OPACITE = {
  fill: ['fill-opacity'], line: ['line-opacity'], circle: ['circle-opacity', 'circle-stroke-opacity'],
};

export class GestionnaireCouches {
  // panneaux : { couches, recherche, anomalies } ; surAnomalies(n) : nombre d'anomalies (badge)
  // complementsFiche(couche, n) : sections ajoutées à la fiche d'un objet (interrogation croisée)
  constructor(carte, panneaux, styleSelection, { surAnomalies, complementsFiche } = {}) {
    this.carte = carte;
    this.complementsFiche = complementsFiche || (() => []);
    this.resultats = [];    // [{ source, id }] surlignés
    this.panneau = panneaux.couches;
    this.panneaux = panneaux;
    this.surAnomalies = surAnomalies || (() => {});
    this.styleSel = styleSelection;
    this.etats = new Map(); // id -> { couche, visible, opacite, charge, info, ligne }
    this.ordre = [];        // ids, du dessus vers le dessous
    this._minuteur = null;
    this._seq = new Map();  // id -> n° de la dernière requête (ignorer les réponses périmées)
  }

  async demarrer() {
    this.panneau.replaceChildren(el('p', { className: 'sig-info', textContent: 'Inventaire des couches…' }));
    let inv;
    try {
      inv = await lire('couches');
    } catch (e) {
      this.panneau.replaceChildren(el('p', { className: 'sig-erreur', textContent: `Inventaire impossible : ${e.message}. Si un ancien serveur tourne encore, fermez sa fenêtre et relancez « lancer ».` }));
      return;
    }
    this.inventaire = inv;
    this._preparerCarte();
    for (const c of inv.couches) {
      this.etats.set(c.id, { couche: c, visible: c.visible, opacite: 1, charge: false, info: '' });
      this.ordre.push(c.id);
      this.carte.addSource(`sig-src-${c.id}`, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
      for (const calque of calques(c)) this.carte.addLayer(calque, 'sig-sel-fl');
      this._appliquerVisibilite(c.id);
      this._appliquerCoupe(c.id);
    }
    this._rendre();
    // après l'inventaire : la liste des couches de la recherche doit être remplie
    this.panneaux.recherche.replaceChildren(this._blocRecherche());
    this._appliquerOrdre();
    this.carte.on('moveend', () => {
      clearTimeout(this._minuteur);
      this._minuteur = setTimeout(() => this._rechargerFiltrees(), DELAI_EMPRISE_MS);
    });
    for (const id of this.ordre) if (this.etats.get(id).visible) this._charger(id);
  }

  // ------------------------------------------------------------ carte

  _preparerCarte() {
    const s = this.styleSel;
    this.carte.addSource('sig-selection', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
    this.carte.addLayer({ id: 'sig-sel-fl', type: 'line', source: 'sig-selection', filter: FILTRES.fl,
      paint: { 'line-color': s.couleur, 'line-width': s.epaisseur ?? 4 } });
    this.carte.addLayer({ id: 'sig-sel-ln', type: 'line', source: 'sig-selection', filter: FILTRES.ln,
      paint: { 'line-color': s.couleur, 'line-width': s.epaisseur ?? 6, 'line-opacity': 0.8 } });
    this.carte.addLayer({ id: 'sig-sel-pt', type: 'circle', source: 'sig-selection', filter: FILTRES.pt,
      paint: { 'circle-color': 'rgba(0,0,0,0)', 'circle-radius': s.rayon ?? 9,
               'circle-stroke-color': s.couleur, 'circle-stroke-width': 3 } });

  }

  _cliquables() {
    return this.ordre.filter((id) => this.etats.get(id).visible).flatMap((id) => idsCalques(this.etats.get(id).couche));
  }

  /** Objet SIG sous le point écran : { id, n } ou null (le clic est réparti par carte.js). */
  objetSous(point) {
    if (!this.ordre.length) return null;
    const f = this.carte.queryRenderedFeatures(autour(point), { layers: this._cliquables() })[0];
    return f ? { id: f.layer.id.slice(4, -3), n: f.id } : null;
  }

  _appliquerVisibilite(id) {
    const v = this.etats.get(id).visible ? 'visible' : 'none';
    for (const l of idsCalques(this.etats.get(id).couche)) this.carte.setLayoutProperty(l, 'visibility', v);
  }

  _appliquerOpacite(id) {
    const { couche, opacite } = this.etats.get(id);
    for (const calque of calques(couche)) {
      for (const prop of PROPRIETES_OPACITE[calque.type]) {
        const base = calque.paint[prop] ?? 1;
        this.carte.setPaintProperty(calque.id, prop, base * opacite);
      }
    }
  }

  _appliquerOrdre() {
    // Du dessous vers le dessus, chaque couche est replacée juste sous la sélection.
    for (const id of [...this.ordre].reverse()) {
      for (const l of ['sig-%-fl', 'sig-%-ol', 'sig-%-ln', 'sig-%-pt']) this.carte.moveLayer(l.replace('%', id), 'sig-sel-fl');
    }
  }

  _bbox() {
    const b = this.carte.getBounds();
    const lim = (v, a) => Math.max(-a, Math.min(a, v));
    return [lim(b.getWest(), 180), lim(b.getSouth(), 90), lim(b.getEast(), 180), lim(b.getNorth(), 90)]
      .map((v) => v.toFixed(6)).join(',');
  }

  async _charger(id) {
    const etat = this.etats.get(id);
    const c = etat.couche;
    if (!c.filtre_emprise && etat.charge) return;
    const seq = (this._seq.get(id) || 0) + 1;
    this._seq.set(id, seq);
    this._info(id, 'chargement…');
    try {
      const fc = await lire(`couches/${c.id}/objets`, c.filtre_emprise ? { bbox: this._bbox() } : {});
      if (this._seq.get(id) !== seq) return;
      this.carte.getSource(`sig-src-${c.id}`).setData(fc);
      etat.charge = true;
      let info = '';
      if (fc.filtre_emprise) {
        info = fc.tronque
          ? `${fc.features.length} / ${fc.total_dans_emprise} dans l'emprise (zoomer pour tout voir)`
          : `${fc.features.length} dans l'emprise affichée`;
      }
      // géométries défectueuses réparées ou écartées par le serveur : la couche s'affiche, l'écart est signalé
      if (fc.avertissements?.length) info = [info, ...fc.avertissements.map((a) => `⚠ ${a}`)].filter(Boolean).join(' · ');
      this._info(id, info, fc.tronque || fc.avertissements?.length > 0);
    } catch (e) {
      if (this._seq.get(id) !== seq) return;
      this._info(id, `erreur : ${e.message}`, true);
    }
  }

  _rechargerFiltrees() {
    for (const id of this.ordre) {
      const etat = this.etats.get(id);
      if (etat.visible && etat.couche.filtre_emprise) this._charger(id);
    }
  }

  // ------------------------------------------------------------ objets

  async ouvrirObjet(id, n, zoomer = false) {
    const etat = this.etats.get(id);
    if (!etat) return;
    try {
      const fiche = await lire(`couches/${id}/objets/${n}`);
      this.carte.getSource('sig-selection').setData({ type: 'Feature', geometry: fiche.geometrie_wgs84, properties: {} });
      if (zoomer) this.zoomerSur(fiche.emprise_wgs84, 17);
      afficherFiche(fiche, etat.couche, () => this.deselectionner(false), this.complementsFiche(etat.couche, n));
    } catch (e) {
      afficherFiche({ erreur: e.message }, etat.couche, () => this.deselectionner(false));
    }
  }

  /** Identifiants des couches affichées (pour l'interrogation croisée). */
  /** Couches visibles imposées (vues thématiques) : celles dont le libellé ou le fichier contient l'un des motifs
   *  (insensible à la casse) ; motifs null : visibilité d'origine de l'inventaire. */
  definirVisibles(motifs) {
    const norm = (t) => (t || '').toLocaleLowerCase('fr');
    for (const [id, etat] of this.etats) {
      const c = etat.couche;
      const v = motifs == null ? c.visible : motifs.some((m) => norm(c.libelle).includes(norm(m)) || norm(c.fichier).includes(norm(m)));
      if (v === etat.visible) continue;
      etat.visible = v;
      this._appliquerVisibilite(id);
      if (v) this._charger(id);
    }
    this._rendre();
  }

  visibles() {
    return this.ordre.filter((id) => this.etats.get(id).visible);
  }

  libelle(id) {
    return this.etats.get(id)?.couche.libelle;
  }

  /** Surligne les objets trouvés ; affiche et charge leurs couches si besoin. */
  surlignerResultats(groupes) {
    this.effacerResultats();
    for (const g of groupes) {
      const etat = this.etats.get(g.couche);
      if (!etat) continue;
      if (!etat.visible) {
        etat.visible = true;
        this._appliquerVisibilite(g.couche);
        this._charger(g.couche);
        this._rendre();
      }
      for (const o of g.objets) {
        const cle = { source: `sig-src-${g.couche}`, id: o.n };
        this.carte.setFeatureState(cle, { resultat: true });
        this.resultats.push(cle);
      }
    }
  }

  effacerResultats() {
    for (const cle of this.resultats) {
      if (this.carte.getSource(cle.source)) this.carte.removeFeatureState(cle, 'resultat');
    }
    this.resultats = [];
  }

  deselectionner(fermer = true) {
    this.carte.getSource('sig-selection').setData({ type: 'FeatureCollection', features: [] });
    if (fermer) fermerFiche();
  }

  zoomerSur(emprise, zoomMax = 16) {
    if (!emprise) return;
    const [x1, y1, x2, y2] = emprise;
    if (x1 === x2 && y1 === y2) this.carte.flyTo({ center: [x1, y1], zoom: zoomMax });
    else this.carte.fitBounds([[x1, y1], [x2, y2]], { padding: 40, maxZoom: zoomMax });
  }

  // ------------------------------------------------------------ panneau

  /** Plan de coupe (coupe.js) : expression de filtre supplémentaire (objets conservés), ou null. */
  definirCoupe(expr) {
    this.filtreCoupe = expr;
    for (const id of this.etats.keys()) this._appliquerCoupe(id);
  }

  _appliquerCoupe(id) {
    for (const [suffixe, f] of [['fl', FILTRES.fl], ['ol', FILTRES.fl], ['ln', FILTRES.ln], ['pt', FILTRES.pt]]) {
      const calque = `sig-${id}-${suffixe}`;
      if (this.carte.getLayer(calque)) this.carte.setFilter(calque, this.filtreCoupe ? ['all', f, this.filtreCoupe] : f);
    }
  }

  _info(id, texte, alerte = false) {
    const etat = this.etats.get(id);
    etat.info = texte;
    const zone = this.panneau.querySelector(`[data-info="${id}"]`);
    if (zone) {
      zone.textContent = texte;
      zone.classList.toggle('sig-alerte', alerte);
    }
  }

  _deplacer(id, sens) {
    const i = this.ordre.indexOf(id);
    const j = i + sens;
    if (j < 0 || j >= this.ordre.length) return;
    [this.ordre[i], this.ordre[j]] = [this.ordre[j], this.ordre[i]];
    this._appliquerOrdre();
    this._rendre();
  }

  _ligne(id) {
    const etat = this.etats.get(id);
    const c = etat.couche;
    const coche = el('input', { type: 'checkbox', checked: etat.visible, title: 'Afficher / masquer' });
    coche.addEventListener('change', () => {
      etat.visible = coche.checked;
      this._appliquerVisibilite(id);
      if (etat.visible) this._charger(id);
    });
    const pastille = el('span', { className: `sig-pastille sig-pastille--${c.geometrie}` });
    pastille.style.setProperty('--c', c.style.couleur);
    const nom = el('span', { className: 'sig-couche__nom', textContent: c.libelle,
      title: c.service ? `${c.service.url}${c.service.couche ? `\ncouche : ${c.service.couche}` : ''}\nfiltre : ${c.service.where}` : `${c.fichier}${c.couche ? ' › ' + c.couche : ''}` });
    const alerte = c.avertissements.length
      ? el('span', { className: 'sig-avert', textContent: '⚠', title: c.avertissements.join('\n') }) : null;

    const opacite = el('input', { type: 'range', min: 0, max: 1, step: 0.05, value: etat.opacite, title: 'Opacité' });
    opacite.addEventListener('input', () => { etat.opacite = Number(opacite.value); this._appliquerOpacite(id); });

    const details = el('div', { className: 'sig-couche__details' },
      `${c.format} · ${c.nb_objets} objet(s) · ${c.systeme}${c.filtre_emprise ? ' · filtrée sur l\'emprise' : ''}`);
    if (c.service) {
      details.append(el('br'), el('span', { className: degradee(c.service) ? 'sig-alerte' : '', textContent: origineService(c.service) }));
    }
    return el('li', { className: 'sig-couche' },
      el('div', { className: 'sig-couche__tete' }, coche, pastille, nom, alerte),
      details,
      el('div', { className: 'sig-couche__outils' },
        bouton('haut', 'Monter', () => this._deplacer(id, -1)),
        bouton('bas', 'Descendre', () => this._deplacer(id, 1)),
        bouton('zoom', 'Zoomer sur la couche', () => this.zoomerSur(c.emprise_wgs84)),
        c.service ? bouton('actualiser', 'Actualiser depuis le service', () => this.actualiser(id)) : null,
        opacite),
      el('div', { className: 'sig-couche__info', textContent: etat.info }),
    );
  }

  _rendre() {
    const inv = this.inventaire;
    const liste = el('ul', { className: 'sig-couches' });
    for (const id of this.ordre) {
      const li = this._ligne(id);
      li.querySelector('.sig-couche__info').dataset.info = id;
      liste.append(li);
    }
    const vide = inv.couches.length ? null
      : el('p', { className: 'sig-info', textContent: `Aucune couche chargeable dans ${inv.dossier}.` });

    this.panneau.replaceChildren(
      el('p', { className: 'sig-resume', textContent: `${inv.couches.length} couche(s) · ordre d'affichage du dessus vers le dessous` }),
      ...[vide, liste, this._blocServices()].filter(Boolean));
    this.panneaux.anomalies.replaceChildren(this._blocAnomalies());
    this.surAnomalies(inv.anomalies.length);
  }

  // ------------------------------------------------------------ services distants (Esri, WFS, GeoJSON)

  async actualiser(id) {
    const etat = this.etats.get(id);
    this._info(id, 'actualisation depuis le service…');
    let inv;
    try {
      inv = await envoyer(`services/${id}/actualiser`);
    } catch (e) {
      this._info(id, `actualisation impossible : ${e.message}`, true);
      return;
    }
    this.inventaire.anomalies = inv.anomalies;
    const nouvelle = inv.couches.find((c) => c.id === id);
    if (nouvelle) {
      // le style reste celui attribué au démarrage (couleur stable)
      etat.couche = { ...nouvelle, style: etat.couche.style };
      const i = this.inventaire.couches.findIndex((c) => c.id === id);
      if (i >= 0) this.inventaire.couches[i] = etat.couche;
      etat.charge = false;
    }
    this._rendre();
    if (etat.visible) await this._charger(id);
    const s = etat.couche.service;
    this._info(id, s.source === 'service' ? `actualisée : ${etat.couche.nb_objets} objet(s)` : origineService(s),
      s.source !== 'service');
  }

  _blocServices() {
    const inv = this.inventaire;
    if (!inv.nb_services) return null;
    const services = inv.couches.filter((c) => c.service);
    const enAnomalie = inv.anomalies.filter((a) => a.service);
    const ul = el('ul', { className: 'sig-services' });
    for (const c of services) {
      const b = bouton('actualiser', 'Actualiser depuis le service', () => this.actualiser(c.id));
      ul.append(el('li', { title: c.service.url },
        el('strong', { textContent: c.libelle }), ' ', el('span', { className: 'sig-info', textContent: `(${c.service.type || 'Esri'})` }), ' ', b,
        el('div', { className: degradee(c.service) ? 'sig-info sig-alerte' : 'sig-info', textContent: origineService(c.service) }),
        c.service.copyright ? el('div', { className: 'sig-info', textContent: `Source : ${c.service.copyright}` }) : null));
    }
    for (const a of enAnomalie) {
      ul.append(el('li', { title: a.fichier }, el('strong', { textContent: a.couche }),
        el('div', { className: 'sig-erreur', textContent: `non affichée : ${a.message}` })));
    }
    return el('section', { className: 'sig-section' },
      el('h3', { textContent: `Services distants (${services.length}/${inv.nb_services})` }), ul);
  }

  _blocRecherche() {
    const choix = el('select', { title: 'Couche interrogée' });
    for (const id of this.ordre) choix.append(el('option', { value: id, textContent: this.etats.get(id).couche.libelle }));
    this._choixRecherche = choix;
    const saisie = el('input', { type: 'search', placeholder: 'Texte à chercher dans les attributs', minLength: 2 });
    const resultats = el('div', { className: 'sig-resultats' });
    const lancer = async () => {
      const id = choix.value;
      if (!id || saisie.value.trim().length < 2) { resultats.replaceChildren(); return; }
      resultats.replaceChildren(el('p', { className: 'sig-info', textContent: 'Recherche…' }));
      try {
        const r = await lire(`couches/${id}/recherche`, { q: saisie.value });
        const ul = el('ul', { className: 'sig-liste-resultats' });
        for (const x of r.resultats) {
          const b = el('button', { type: 'button', className: 'sig-resultat' },
            el('strong', { textContent: x.libelle ?? `Objet n° ${x.n}` }),
            x.champ ? el('span', { textContent: ` — ${x.champ} : ${x.valeur}` }) : null);
          b.addEventListener('click', () => {
            const etat = this.etats.get(id);
            if (!etat.visible) {
              etat.visible = true;
              this._appliquerVisibilite(id);
              this._charger(id);
              this._rendre();
            }
            this.ouvrirObjet(id, x.n, true);
          });
          ul.append(el('li', {}, b));
        }
        const total = r.total > r.resultats.length ? `${r.resultats.length} premiers sur ${r.total}` : `${r.total}`;
        resultats.replaceChildren(el('p', { className: 'sig-info', textContent: `${total} résultat(s)` }), ul);
      } catch (e) {
        resultats.replaceChildren(el('p', { className: 'sig-erreur', textContent: e.message }));
      }
    };
    const form = el('form', { className: 'sig-recherche' }, choix, saisie, el('button', { type: 'submit', className: 'sig-btn', textContent: 'Chercher' }));
    form.addEventListener('submit', (e) => { e.preventDefault(); lancer(); });
    return el('section', { className: 'sig-section' },
      el('p', { className: 'sig-info', textContent: 'Recherche texte dans les attributs d\'une couche (sans accents ni majuscules).' }),
      form, resultats);
  }

  _blocAnomalies() {
    const inv = this.inventaire;
    const avert = inv.couches.filter((c) => c.avertissements.length);
    const n = inv.anomalies.length;
    const ul = (items) => el('ul', { className: 'sig-anomalies' }, ...items.map((t) => el('li', { textContent: t })));
    return el('section', { className: 'sig-section' },
      el('h3', { textContent: `Couches SIG en anomalie (${n})` }),
      n ? ul(inv.anomalies.map((a) => (a.service ? `Service « ${a.couche} » : ${a.message}`
        : `${a.fichier}${a.couche ? ' › ' + a.couche : ''} : ${a.message}`)))
        : el('p', { className: 'sig-info', textContent: 'Aucune couche en anomalie.' }),
      avert.length ? el('h3', { textContent: 'Avertissements (couches affichées)' }) : null,
      avert.length ? ul(avert.flatMap((c) => c.avertissements.map((t) => `${c.libelle} : ${t}`))) : null,
      inv.ignores.length ? el('h3', { textContent: 'Fichiers non pris en compte' }) : null,
      inv.ignores.length ? ul(inv.ignores.map((i) => `${i.fichier} : ${i.message}`)) : null,
      el('p', { className: 'sig-info', textContent: `Dossier : ${inv.dossier} — inventaire du ${dateCourte(inv.date)}` }),
    );
  }
}

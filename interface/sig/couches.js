// Gestionnaire des couches fichiers : affichage, ordre, opacité, zoom, filtre d'emprise,
// clic sur un objet (fiche) et recherche texte. Les couches viennent de /api/sig/couches.
import { lire } from './api.js';
import { afficherFiche, fermerFiche } from './fiche.js';

const DELAI_EMPRISE_MS = 300; // attente après un déplacement avant de recharger une couche filtrée
const TOLERANCE_CLIC_PX = 4;

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

function idsCalques(c) {
  return ['pt', 'ln', 'fl', 'ol'].map((s) => `sig-${c.id}-${s}`);
}

function calques(c) {
  const s = c.style;
  const src = `sig-src-${c.id}`;
  const op = (base) => base ?? 0.9;
  return [
    { id: `sig-${c.id}-fl`, type: 'fill', source: src, filter: FILTRES.fl,
      paint: { 'fill-color': s.couleur, 'fill-opacity': op(s.opacite) } },
    { id: `sig-${c.id}-ol`, type: 'line', source: src, filter: FILTRES.fl,
      paint: { 'line-color': s.contour || s.couleur, 'line-width': s.epaisseur ?? 1, 'line-opacity': 1 } },
    { id: `sig-${c.id}-ln`, type: 'line', source: src, filter: FILTRES.ln,
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': s.couleur, 'line-width': s.epaisseur ?? 2, 'line-opacity': op(s.opacite) } },
    { id: `sig-${c.id}-pt`, type: 'circle', source: src, filter: FILTRES.pt,
      paint: { 'circle-color': s.couleur, 'circle-radius': s.rayon ?? 5, 'circle-opacity': op(s.opacite),
               'circle-stroke-color': s.contour || '#ffffff', 'circle-stroke-width': s.epaisseur_contour ?? 1,
               'circle-stroke-opacity': op(s.opacite) } },
  ];
}

const PROPRIETES_OPACITE = {
  fill: ['fill-opacity'], line: ['line-opacity'], circle: ['circle-opacity', 'circle-stroke-opacity'],
};

export class GestionnaireCouches {
  constructor(carte, panneau, styleSelection) {
    this.carte = carte;
    this.panneau = panneau;
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
    }
    this._rendre();
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

    const cliquables = () => this.ordre.filter((id) => this.etats.get(id).visible)
      .flatMap((id) => idsCalques(this.etats.get(id).couche));
    const autour = (p) => [[p.x - TOLERANCE_CLIC_PX, p.y - TOLERANCE_CLIC_PX], [p.x + TOLERANCE_CLIC_PX, p.y + TOLERANCE_CLIC_PX]];
    this.carte.on('click', (e) => {
      const f = this.carte.queryRenderedFeatures(autour(e.point), { layers: cliquables() })[0];
      if (!f) { this.deselectionner(); return; }
      const id = f.layer.id.slice(4, -3);
      this.ouvrirObjet(id, f.id);
    });
    this.carte.on('mousemove', (e) => {
      const touche = this.carte.queryRenderedFeatures(autour(e.point), { layers: cliquables() }).length > 0;
      this.carte.getCanvas().style.cursor = touche ? 'pointer' : '';
    });
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
      this._info(id, info, fc.tronque);
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
      afficherFiche(fiche, etat.couche, () => this.deselectionner(false));
    } catch (e) {
      afficherFiche({ erreur: e.message }, etat.couche, () => this.deselectionner(false));
    }
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
    const nom = el('span', { className: 'sig-couche__nom', textContent: c.libelle, title: `${c.fichier}${c.couche ? ' › ' + c.couche : ''}` });
    const alerte = c.avertissements.length
      ? el('span', { className: 'sig-avert', textContent: '⚠', title: c.avertissements.join('\n') }) : null;

    const btn = (texte, titre, action) => {
      const b = el('button', { type: 'button', className: 'sig-btn', textContent: texte, title: titre });
      b.addEventListener('click', action);
      return b;
    };
    const opacite = el('input', { type: 'range', min: 0, max: 1, step: 0.05, value: etat.opacite, title: 'Opacité' });
    opacite.addEventListener('input', () => { etat.opacite = Number(opacite.value); this._appliquerOpacite(id); });

    const details = el('div', { className: 'sig-couche__details' },
      `${c.format} · ${c.nb_objets} objet(s) · ${c.systeme}${c.filtre_emprise ? ' · filtrée sur l\'emprise' : ''}`);
    return el('li', { className: 'sig-couche' },
      el('div', { className: 'sig-couche__tete' }, coche, pastille, nom, alerte),
      details,
      el('div', { className: 'sig-couche__outils' },
        btn('▲', 'Monter', () => this._deplacer(id, -1)),
        btn('▼', 'Descendre', () => this._deplacer(id, 1)),
        btn('⌖', 'Zoomer sur la couche', () => this.zoomerSur(c.emprise_wgs84)),
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
      el('details', { className: 'sig-bloc', open: true },
        el('summary', {}, el('h2', { textContent: `Couches (${inv.couches.length})` })), vide, liste),
      this._blocRecherche(),
      this._blocAnomalies(),
    );
  }

  _blocRecherche() {
    const choix = el('select', { title: 'Couche interrogée' });
    for (const id of this.ordre) choix.append(el('option', { value: id, textContent: this.etats.get(id).couche.libelle }));
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
    return el('details', { className: 'sig-bloc' },
      el('summary', {}, el('h2', { textContent: 'Recherche' })), form, resultats);
  }

  _blocAnomalies() {
    const inv = this.inventaire;
    const avert = inv.couches.filter((c) => c.avertissements.length);
    const n = inv.anomalies.length;
    const ul = (items) => el('ul', { className: 'sig-anomalies' }, ...items.map((t) => el('li', { textContent: t })));
    return el('details', { className: 'sig-bloc', open: n > 0 },
      el('summary', {}, el('h2', { textContent: `Anomalies (${n})` })),
      n ? ul(inv.anomalies.map((a) => `${a.fichier}${a.couche ? ' › ' + a.couche : ''} : ${a.message}`))
        : el('p', { className: 'sig-info', textContent: 'Aucune couche en anomalie.' }),
      avert.length ? el('h3', { textContent: 'Avertissements (couches affichées)' }) : null,
      avert.length ? ul(avert.flatMap((c) => c.avertissements.map((t) => `${c.libelle} : ${t}`))) : null,
      inv.ignores.length ? el('h3', { textContent: 'Fichiers non pris en compte' }) : null,
      inv.ignores.length ? ul(inv.ignores.map((i) => `${i.fichier} : ${i.message}`)) : null,
      el('p', { className: 'sig-info', textContent: `Dossier : ${inv.dossier} — inventaire du ${inv.date.replace('T', ' à ')}` }),
    );
  }
}

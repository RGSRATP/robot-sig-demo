// Plan des locaux d'une maquette : niveau par niveau, emprises des IfcSpace colorées par typologie et posées
// à l'altitude de leur plancher dans la 3D, coupe au-dessus du niveau, liste filtrable, fiche d'un local.
// Données : /api/sig/maquettes/{id}/locaux.
import { cadrer } from './camera.js';
import { lire } from './api.js';
import { afficherLocal } from './fiche.js';
import { bouton, el } from './ui.js';

const TEINTES = ['#8ecae6', '#ffb703', '#90be6d', '#f4a261', '#b5838d', '#a8dadc', '#e9c46a', '#cdb4db',
  '#84a59d', '#f28482', '#bde0fe', '#d4a373', '#c9ada7', '#99d98c', '#ffafcc', '#9fb4c7'];
const OPACITE_ATTENUEE = 0.18;

function teinte(cle) {
  let h = 0;
  for (const c of String(cle || '')) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return TEINTES[h % TEINTES.length];
}

export const typologieDe = (l) => l.typologie || l.code_typologie || l.nom_long || 'Non renseignée';

export class GestionnaireLocaux {
  // hauteurCoupe : hauteur du plan de coupe au-dessus du plancher du niveau (convention des plans : 1,50 m)
  constructor(carte, c3d, { panneau, ouvrirVolet, surFermeture, zoomer, police, hauteurCoupe }) {
    this.hauteurCoupe = hauteurCoupe;
    this.carte = carte;
    this.c3d = c3d;
    this.panneau = panneau;
    this.ouvrirVolet = ouvrirVolet;
    this.surFermeture = surFermeture || (() => {});
    this.zoomer = zoomer;
    this.police = police;
    this.donnees = null;       // réponse du serveur
    this.maquette = null;
    this.niveau = null;
    this.filtre = '';
    this.couper = true;
    this.attenuer = true;
    this.selection = null;
    this._vide();
  }

  _vide() {
    this.panneau.replaceChildren(el('div', { className: 'sig-vide' },
      el('p', { textContent: 'Ouvrez la fiche d\'une maquette qui contient des locaux, puis « Plan des locaux ».' })));
  }

  /** Ouvre le plan des locaux d'une maquette (m : description serveur). */
  async ouvrir(m, guid = null) {
    this.panneau.replaceChildren(el('p', { className: 'sig-info', textContent: 'Chargement des locaux…' }));
    this.ouvrirVolet();
    let d;
    try {
      d = await lire(`maquettes/${m.id}/locaux`);
    } catch (e) {
      this.panneau.replaceChildren(el('p', { className: 'sig-erreur', textContent: e.message }));
      return;
    }
    this.maquette = m;
    this.donnees = d;
    const cible = guid && d.locaux.find((l) => l.guid === guid);
    const parDefaut = d.niveaux.find((n) => n.hauteurs && n.hauteurs[0] <= 0 && n.hauteurs[1] > 0) || d.niveaux[0];
    this.niveau = cible ? this._niveauDe(cible) : parDefaut?.niveau;
    this._appliquer();
    if (d.positionnee) {
      const [x1, y1, x2, y2] = m.positionnement.emprise_wgs84;
      this.carte.fitBounds([[x1, y1], [x2, y2]], { padding: 60, pitch: 0, bearing: this.carte.getBearing(), maxZoom: 20 });
    }
    if (cible) this.selectionner(cible.guid, true);
  }

  _niveauDe(l) {
    return l.niveau || l.intitule_niveau || 'Sans niveau';
  }

  _locauxDuNiveau() {
    return (this.donnees?.locaux || []).filter((l) => this._niveauDe(l) === this.niveau);
  }

  _infoNiveau() {
    return this.donnees?.niveaux.find((n) => n.niveau === this.niveau);
  }

  _appliquer() {
    const m = this.maquette;
    const d = this.donnees;
    if (!m || !d) return;
    const locaux = this._locauxDuNiveau();
    if (d.positionnee) {
      this.c3d.afficherPlan(m.id, m.positionnement.affichage, locaux, (l) => teinte(typologieDe(l)));
      const n = this._infoNiveau();
      // coupe à hauteur d'homme au-dessus du plancher, sans dépasser le plafond du niveau
      const coupe = n?.hauteurs ? Math.min(n.hauteurs[0] + this.hauteurCoupe, n.hauteurs[1]) : null;
      this.c3d.definirCoupe(this.couper ? coupe : null);
      this.c3d.attenuer(this.attenuer ? OPACITE_ATTENUEE : 1);
      this._etiquettes(locaux);
    }
    if (this.selection && !locaux.some((l) => l.guid === this.selection)) this.selection = null;
    this.c3d.montrerVolume(this.selection);
    this._rendre();
  }

  _etiquettes(locaux) {
    const donnees = { type: 'FeatureCollection', features: locaux.filter((l) => l.centre_wgs84).map((l) => ({
      type: 'Feature', geometry: { type: 'Point', coordinates: l.centre_wgs84 },
      properties: { texte: l.code || l.nom || '' } })) };
    const source = this.carte.getSource('sig-locaux-etiquettes');
    if (source) { source.setData(donnees); return; }
    if (!this.police || !this.carte.getStyle().glyphs) return;
    this.carte.addSource('sig-locaux-etiquettes', { type: 'geojson', data: donnees });
    this.carte.addLayer({ id: 'sig-locaux-etiquettes', type: 'symbol', source: 'sig-locaux-etiquettes', minzoom: 18,
      layout: { 'text-field': ['get', 'texte'], 'text-font': [this.police], 'text-size': 11, 'text-max-width': 8,
                'text-allow-overlap': false },
      paint: { 'text-color': '#1b1f23', 'text-halo-color': 'rgba(255,255,255,0.9)', 'text-halo-width': 1.4 } });
    // Les étiquettes sont posées au sol : elles ne sont lisibles qu'en vue de dessus.
    const maj = () => this.carte.getLayer('sig-locaux-etiquettes')
      && this.carte.setLayoutProperty('sig-locaux-etiquettes', 'visibility', this.carte.getPitch() < 15 ? 'visible' : 'none');
    this.carte.on('pitch', maj);
    maj();
  }

  choisirNiveau(nom) {
    this.niveau = nom;
    this._appliquer();
  }

  selectionner(guid, zoomer = false) {
    const l = this.donnees?.locaux.find((x) => x.guid === guid);
    if (!l) return;
    if (this._niveauDe(l) !== this.niveau) { this.niveau = this._niveauDe(l); this.selection = guid; this._appliquer(); }
    this.selection = guid;
    this.c3d.montrerVolume(guid);
    this._rendre();
    if (zoomer && l.emprise_wgs84) {
      // cadrage à la hauteur du local (sous-sol ou étage) : la rotation tourne autour de lui
      const [x1, y1, x2, y2] = l.emprise_wgs84;
      const metres = Math.max((x2 - x1) * 111320 * Math.cos((y1 * Math.PI) / 180), (y2 - y1) * 110540);
      cadrer(this.carte, { centre: l.centre_wgs84 || [(x1 + x2) / 2, (y1 + y2) / 2],
        hauteur: l.hauteurs ? l.hauteurs[0] : 0, taille: metres });
    }
    afficherLocal(l, this.maquette, {
      couleur: teinte(typologieDe(l)),
      chargerProprietes: () => lire(`maquettes/${this.maquette.id}/objets/${encodeURIComponent(l.guid)}`),
    }, () => this.deselectionner());
  }

  deselectionner() {
    this.selection = null;
    this.c3d.montrerVolume(null);
    this._rendre();
  }

  fermer() {
    this.c3d.attenuer(1);
    this.c3d.retirerPlan();
    this.c3d.definirCoupe(null);
    if (this.carte.getSource('sig-locaux-etiquettes')) this.carte.getSource('sig-locaux-etiquettes').setData({ type: 'FeatureCollection', features: [] });
    this.maquette = null;
    this.donnees = null;
    this.selection = null;
    this._vide();
    this.surFermeture();
  }

  // ------------------------------------------------------------ volet

  _rendre() {
    const d = this.donnees;
    const m = this.maquette;
    if (!d || !m) return;
    const defilement = this.panneau.querySelector('.sig-locaux__liste')?.scrollTop;
    const avaitFocus = this._saisie && document.activeElement === this._saisie;

    const tete = el('div', { className: 'sig-locaux__tete' },
      el('strong', { textContent: m.nomCourt || m.nom, title: m.fichier }),
      el('span', { className: 'sig-info', textContent: ` ${d.locaux.length} locaux · ${d.niveaux.length} niveaux` }));
    const outils = el('div', { className: 'sig-groupe-boutons' },
      el('a', { className: 'sig-bouton', href: `api/sig/maquettes/${m.id}/locaux?format=csv`, download: '', textContent: 'Exporter CSV' }),
      (() => { const b = el('button', { type: 'button', className: 'sig-bouton', textContent: 'Fermer le plan' });
        b.addEventListener('click', () => this.fermer()); return b; })());
    if (!d.positionnee) {
      this.panneau.replaceChildren(tete, outils, el('p', { className: 'sig-alerte', textContent: 'Maquette non positionnée : liste sans plan.' }), this._liste(d.locaux));
      return;
    }

    // niveaux, du plus haut au plus bas (comme un ascenseur)
    const niveaux = el('div', { className: 'sig-niveaux', role: 'listbox', ariaLabel: 'Niveaux' });
    for (const n of [...d.niveaux].reverse()) {
      const b = el('button', { type: 'button', className: `sig-niveau${n.niveau === this.niveau ? ' sig-niveau--actif' : ''}`,
        role: 'option', ariaSelected: String(n.niveau === this.niveau),
        title: n.hauteurs ? `de ${n.hauteurs[0].toLocaleString('fr-FR')} m à ${n.hauteurs[1].toLocaleString('fr-FR')} m par rapport au terrain` : '' },
        el('span', { className: 'sig-niveau__nom', textContent: n.niveau }),
        el('span', { className: 'sig-niveau__intitule', textContent: n.intitule && n.intitule !== n.niveau ? n.intitule : '' }),
        el('span', { className: 'sig-niveau__nb', textContent: String(n.nb_locaux) }));
      b.addEventListener('click', () => this.choisirNiveau(n.niveau));
      niveaux.append(b);
    }

    const option = (libelle, valeur, action) => {
      const c = el('input', { type: 'checkbox', checked: valeur });
      c.addEventListener('change', () => action(c.checked));
      return el('label', { className: 'sig-option' }, c, ` ${libelle}`);
    };
    const options = el('div', { className: 'sig-options' },
      option(`Coupe à ${this.hauteurCoupe.toLocaleString('fr-FR')} m du plancher`, this.couper, (v) => { this.couper = v; this._appliquer(); }),
      option('Atténuer les maquettes', this.attenuer, (v) => { this.attenuer = v; this._appliquer(); }),
      (() => { const b = el('button', { type: 'button', className: 'sig-lien', textContent: 'Vue en plan' });
        b.addEventListener('click', () => this.carte.easeTo({ pitch: 0, duration: 600 })); return b; })());

    const locaux = this._locauxDuNiveau();
    const typos = new Map();
    for (const l of locaux) typos.set(typologieDe(l), (typos.get(typologieDe(l)) || 0) + 1);
    const legende = el('ul', { className: 'sig-legende' },
      ...[...typos.entries()].sort((a, b) => b[1] - a[1]).map(([t, n]) => {
        const pastille = el('span', { className: 'sig-legende__pastille' });
        pastille.style.background = teinte(t);
        return el('li', {}, pastille, el('span', { textContent: t }), el('span', { className: 'sig-info', textContent: ` ${n}` }));
      }));
    if (!this.c3d.modeles.has(m.id)) {
      options.append(el('p', { className: 'sig-info', textContent: 'La 3D de cette maquette n\'est pas chargée : seul le plan est affiché.' }));
    }

    const saisie = this._saisie || (this._saisie = (() => {
      const s = el('input', { type: 'search', className: 'sig-filtre', placeholder: 'Filtrer (code, nom, typologie)…' });
      s.addEventListener('input', () => { this.filtre = s.value; this._rendre(); });
      return s;
    })());
    const liste = this._liste(locaux);
    this.panneau.replaceChildren(tete, outils, el('h3', { textContent: 'Niveaux' }), niveaux, options,
      el('h3', { textContent: `Typologies du niveau ${this.niveau}` }), legende,
      el('h3', { textContent: `Locaux du niveau (${locaux.length})` }), saisie, liste);
    if (defilement) liste.scrollTop = defilement;
    if (avaitFocus) saisie.focus();
  }

  _liste(locaux) {
    const f = this.filtre.trim().toLowerCase();
    const visibles = locaux.filter((l) => !f || [l.code, l.nom, l.nom_long, l.typologie].some((v) => String(v || '').toLowerCase().includes(f)));
    const ul = el('ul', { className: 'sig-locaux__liste' });
    for (const l of visibles) {
      const b = el('button', { type: 'button', className: `sig-local${l.guid === this.selection ? ' sig-local--actif' : ''}` },
        el('span', { className: 'sig-legende__pastille' }),
        el('span', { className: 'sig-local__code', textContent: l.code || l.nom || '—' }),
        el('span', { className: 'sig-local__nom', textContent: l.nom_long && l.nom_long !== 'Pièce' ? l.nom_long : (l.typologie || '') }),
        el('span', { className: 'sig-local__surface', textContent: l.surface != null ? `${l.surface.toLocaleString('fr-FR', { maximumFractionDigits: 1 })} m²` : '' }));
      b.firstChild.style.background = teinte(typologieDe(l));
      b.addEventListener('click', () => this.selectionner(l.guid, true));
      ul.append(el('li', {}, b));
    }
    if (!visibles.length) ul.append(el('li', { className: 'sig-info', textContent: 'Aucun local.' }));
    return ul;
  }
}

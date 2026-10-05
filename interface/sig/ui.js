// Structure de l'écran (inspirée des visionneuses cartographiques web) : bandeau, barres d'actions à gauche
// et à droite, volets repliables, zone carte, pied de page. Aucune logique métier ici.

const TRACES = {
  couches: 'M12 3 2 8l10 5 10-5-10-5Z M2 12.5l10 5 10-5 M2 17l10 5 10-5',
  maquettes: 'M12 2.5 3.5 7v10l8.5 4.5 8.5-4.5V7L12 2.5Z M3.5 7l8.5 4.5L20.5 7 M12 11.5v10',
  fonds: 'M3.5 3.5h7v7h-7z M13.5 3.5h7v7h-7z M3.5 13.5h7v7h-7z M13.5 13.5h7v7h-7z',
  recherche: 'M10.5 3.5a7 7 0 1 0 0 14 7 7 0 0 0 0-14Z M15.5 15.5 21 21',
  anomalies: 'M12 3.5 2.5 20.5h19L12 3.5Z M12 10v5 M12 17.5v.5',
  proprietes: 'M8.5 6h12 M8.5 12h12 M8.5 18h12 M3.5 6h1 M3.5 12h1 M3.5 18h1',
  vue3d: 'M4 21v-6 M4 11V3 M12 21v-8 M12 9V3 M20 21v-4 M20 13V3 M1.5 15h5 M9.5 9h5 M17.5 17h5',
  fermer: 'M6 6l12 12 M18 6 6 18',
  plus: 'M12 5v14 M5 12h14',
  moins: 'M5 12h14',
  accueil: 'M3.5 11 12 3.5l8.5 7.5v9.5h-5.5v-6h-6v6H3.5V11Z',
  boussole: 'M12 2.5 15.5 12 12 21.5 8.5 12 12 2.5Z',
  oeil: 'M2 12s3.5-6.5 10-6.5S22 12 22 12s-3.5 6.5-10 6.5S2 12 2 12Z M12 9.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5Z',
  zoom: 'M10.5 3.5a7 7 0 1 0 0 14 7 7 0 0 0 0-14Z M15.5 15.5 21 21 M10.5 7.5v6 M7.5 10.5h6',
  actualiser: 'M20 11a8 8 0 1 0-2.3 5.7 M20 4.5V11h-6.5',
  haut: 'M6 15l6-6 6 6',
  bas: 'M6 9l6 6 6-6',
  plein: 'M4 9V4h5 M20 9V4h-5 M4 15v5h5 M20 15v5h-5',
};

export function icone(nom, taille = 20) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('width', taille);
  svg.setAttribute('height', taille);
  svg.setAttribute('aria-hidden', 'true');
  svg.classList.add('sig-icone');
  const p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  p.setAttribute('d', TRACES[nom] || '');
  svg.append(p);
  if (nom === 'boussole') {
    const sud = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    sud.setAttribute('d', 'M12 2.5 15.5 12h-7L12 2.5Z');
    sud.classList.add('sig-icone__nord');
    svg.append(sud);
  }
  return svg;
}

export function el(tag, attrs = {}, ...enfants) {
  const e = Object.assign(document.createElement(tag), attrs);
  for (const x of enfants) if (x != null && x !== false) e.append(x);
  return e;
}

export function bouton(nomIcone, titre, action, classe = 'sig-bouton-icone') {
  const b = el('button', { type: 'button', className: classe, title: titre, ariaLabel: titre }, icone(nomIcone, 18));
  if (action) b.addEventListener('click', action);
  return b;
}

class Volet {
  constructor(cote, surChangement) {
    this.cote = cote;
    this.surChangement = surChangement;
    this.panneaux = new Map(); // id -> { titre, corps, bouton }
    this.actif = null;
    this.barre = el('nav', { className: `sig-actions sig-actions--${cote}`, ariaLabel: `Outils ${cote}` });
    this.titre = el('h2', { className: 'sig-volet__titre' });
    this.corps = el('div', { className: 'sig-volet__corps' });
    const fermer = bouton('fermer', 'Replier le volet', () => this.ouvrir(null));
    this.racine = el('section', { className: `sig-volet sig-volet--${cote}` },
      el('header', { className: 'sig-volet__tete' }, this.titre, fermer), this.corps);
    this.racine.hidden = true;
  }

  ajouter(id, titre, nomIcone) {
    const corps = el('div', { className: 'sig-volet__panneau' });
    corps.hidden = true;
    const badge = el('span', { className: 'sig-badge' });
    badge.hidden = true;
    const b = el('button', { type: 'button', className: 'sig-action', title: titre, ariaLabel: titre },
      icone(nomIcone, 22), el('span', { className: 'sig-action__libelle', textContent: titre }), badge);
    b.addEventListener('click', () => this.ouvrir(this.actif === id ? null : id));
    this.barre.append(b);
    this.corps.append(corps);
    this.panneaux.set(id, { titre, corps, bouton: b, badge });
    return corps;
  }

  ouvrir(id) {
    if (id && !this.panneaux.has(id)) return;
    this.actif = id;
    for (const [k, p] of this.panneaux) {
      p.corps.hidden = k !== id;
      p.bouton.classList.toggle('sig-action--active', k === id);
      p.bouton.setAttribute('aria-pressed', String(k === id));
    }
    this.racine.hidden = !id;
    if (id) this.titre.textContent = this.panneaux.get(id).titre;
    this.surChangement?.();
  }

  badge(id, n, alerte = false) {
    const p = this.panneaux.get(id);
    if (!p) return;
    p.badge.hidden = !n;
    p.badge.textContent = n > 99 ? '99+' : String(n);
    p.badge.classList.toggle('sig-badge--alerte', alerte);
  }
}

// Coque complète ; renvoie les points d'accroche des modules.
export function creerCoque(conteneur, { titre, sousTitre }) {
  let surRedimension = () => {};
  const gauche = new Volet('gauche', () => surRedimension());
  const droite = new Volet('droite', () => surRedimension());
  const etat = el('div', { className: 'sig-entete__etat' });
  const entete = el('header', { className: 'sig-entete' },
    el('div', { className: 'sig-entete__marque' },
      el('span', { className: 'sig-logo', ariaHidden: 'true' }),
      el('span', { className: 'sig-entete__titre', textContent: titre }),
      sousTitre ? el('span', { className: 'sig-entete__sous', textContent: sousTitre }) : null),
    etat);
  const zone = el('main', { className: 'sig-zone' });
  const pied = el('footer', { className: 'sig-pied' });
  const app = el('div', { className: 'sig-app' }, entete, gauche.barre, gauche.racine, zone, droite.racine, droite.barre, pied);
  conteneur.append(app);
  return {
    app, entete, etat, zone, pied, gauche, droite,
    surRedimension(f) { surRedimension = f; },
    pastille(texte, classe = '') {
      const p = el('span', { className: `sig-pastille-etat ${classe}`, textContent: texte });
      etat.append(p);
      return p;
    },
  };
}

// Filtres des objets des maquettes affichées en 3D : familles (génie civil, réseaux et équipements, second œuvre,
// déclarées dans config/georef_maquettes.yaml) à afficher, colorer ou isoler ; types IFC à masquer.
// Les filtres valent pour toutes les maquettes chargées, y compris celles qui se chargent ensuite.
import { el } from './ui.js';

const MAX_TYPES = 60;   // types listés (les plus nombreux), les autres par la recherche

export function sectionFiltres(c3d) {
  const f = { typesMasques: new Set(), famillesMasquees: new Set(), famillesColorees: new Set(), isolee: null };
  let recherche = '';
  const zone = el('div');
  const section = el('section', { className: 'sig-section' }, el('h3', { textContent: 'Objets affichés' }), zone);

  const appliquer = () => { c3d.definirFiltres(f); rendre(); };
  const basculer = (ens, cle) => { if (ens.has(cle)) ens.delete(cle); else ens.add(cle); appliquer(); };
  const nf = (n) => n.toLocaleString('fr-FR');

  function ligneFamille(i, nom, couleur, nb) {
    const visible = el('input', { type: 'checkbox', checked: !f.famillesMasquees.has(i) && (f.isolee == null || f.isolee === i),
      disabled: f.isolee != null, title: 'Afficher ou masquer la famille' });
    visible.addEventListener('change', () => basculer(f.famillesMasquees, i));
    const pastille = el('span', { className: 'sig-legende__pastille' });
    pastille.style.background = couleur || 'transparent';
    if (!couleur) pastille.style.border = '1px dashed #8a9199';
    const actions = el('span', { className: 'sig-filtre__actions' });
    if (i >= 0) {
      const colorer = el('button', { type: 'button', className: `sig-bouton sig-bouton--petit${f.famillesColorees.has(i) ? ' sig-bouton--actif' : ''}`,
        textContent: 'Colorer', ariaPressed: String(f.famillesColorees.has(i)), title: `Dessiner les objets « ${nom} » dans leur couleur` });
      colorer.addEventListener('click', () => basculer(f.famillesColorees, i));
      const isoler = el('button', { type: 'button', className: `sig-bouton sig-bouton--petit${f.isolee === i ? ' sig-bouton--actif' : ''}`,
        textContent: f.isolee === i ? 'Tout afficher' : 'Isoler', ariaPressed: String(f.isolee === i),
        title: f.isolee === i ? 'Réafficher les autres familles' : `N'afficher que « ${nom} »` });
      isoler.addEventListener('click', () => { f.isolee = f.isolee === i ? null : i; appliquer(); });
      actions.append(colorer, isoler);
    }
    return el('li', { className: 'sig-filtre' }, el('label', { className: 'sig-filtre__nom' }, visible, pastille,
      el('span', { textContent: nom }), el('span', { className: 'sig-arbre__compte', textContent: nf(nb) })), actions);
  }

  function rendre() {
    const r = c3d.recapitulatif();
    if (!r.types.length) {
      zone.replaceChildren(el('p', { className: 'sig-info', textContent: 'Affichez une maquette en 3D pour filtrer ses objets par famille ou par type IFC.' }));
      return;
    }
    const familles = el('ul', { className: 'sig-filtres' },
      ...r.familles.map((x, i) => ligneFamille(i, x.nom, x.couleur, x.nb)), ligneFamille(-1, 'Autres', null, r.autres));

    const saisie = el('input', { type: 'search', className: 'sig-filtre-saisie', placeholder: 'Type IFC (IfcWall, Slab…)', value: recherche });
    saisie.addEventListener('input', () => { recherche = saisie.value; rendre(); zone.querySelector('.sig-filtre-saisie')?.focus(); });
    const q = recherche.trim().toLowerCase();
    const choisis = r.types.filter(([t]) => !q || t.toLowerCase().includes(q));
    const types = el('ul', { className: 'sig-filtres sig-filtres--types' }, ...choisis.slice(0, MAX_TYPES).map(([t, nb]) => {
      const c = el('input', { type: 'checkbox', checked: !f.typesMasques.has(t) });
      c.addEventListener('change', () => basculer(f.typesMasques, t));
      return el('li', { className: 'sig-filtre' }, el('label', { className: 'sig-filtre__nom' }, c,
        el('span', { textContent: t.replace(/^Ifc/, '') }), el('span', { className: 'sig-arbre__compte', textContent: nf(nb) })));
    }));
    const tous = (afficher) => () => {
      for (const [t] of choisis) if (afficher) f.typesMasques.delete(t); else f.typesMasques.add(t);
      appliquer();
    };
    const boutons = el('div', { className: 'sig-groupe-boutons' });
    for (const [texte, fn] of [['Tout cocher', tous(true)], ['Tout décocher', tous(false)]]) {
      const b = el('button', { type: 'button', className: 'sig-bouton sig-bouton--petit', textContent: texte });
      b.addEventListener('click', fn);
      boutons.append(b);
    }
    const masques = f.typesMasques.size + f.famillesMasquees.size + (f.isolee != null ? 1 : 0);
    zone.replaceChildren(
      el('h4', { textContent: 'Familles' }), familles,
      el('h4', { textContent: `Types IFC (${r.types.length})${masques ? ` · ${masques} filtre(s) actif(s)` : ''}` }), saisie, boutons, types,
      choisis.length > MAX_TYPES ? el('p', { className: 'sig-info', textContent: `${choisis.length - MAX_TYPES} autres types : précisez la recherche.` }) : null,
    );
  }

  c3d.surTypes = rendre;
  rendre();
  return section;
}

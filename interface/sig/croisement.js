// Interrogation croisée SIG ↔ maquettes (lot 4) : sections ajoutées aux fiches, résultats cliquables,
// surlignés sur la carte, exportables en CSV. Données : /api/sig/maquettes/{id}/croisement et
// /api/sig/couches/{id}/objets/{n}/maquettes.
import { lire } from './api.js';
import { el } from './ui.js';

const nf = (v) => v.toLocaleString('fr-FR', { maximumFractionDigits: 1 });

function lienCsv(url) {
  return el('a', { className: 'sig-bouton', href: url, download: '', textContent: 'Exporter CSV' });
}

function section(titre, libelleBouton, lancer) {
  const zone = el('div', { className: 'sig-croisement__resultats' });
  const b = el('button', { type: 'button', className: 'sig-bouton', textContent: libelleBouton });
  b.addEventListener('click', async () => {
    b.disabled = true;
    zone.replaceChildren(el('p', { className: 'sig-info', textContent: 'Interrogation en cours…' }));
    try {
      await lancer(zone);
    } catch (e) {
      zone.replaceChildren(el('p', { className: 'sig-erreur', textContent: e.message }));
    }
    b.disabled = false;
  });
  return el('section', { className: 'sig-croisement' }, el('h4', { textContent: titre }), b, zone);
}

/** Maquette → objets SIG des couches affichées qui touchent son emprise. */
export function sectionMaquette(m, { couches, ouvrirObjetSig }) {
  if (m.positionnement?.statut !== 'positionnee') return [];
  return [section('Interrogation croisée', 'Objets SIG dans l\'emprise (couches affichées)', async (zone) => {
    const ids = couches.visibles().join(',');
    if (!ids) {
      zone.replaceChildren(el('p', { className: 'sig-info', textContent: 'Aucune couche affichée : cochez des couches dans le volet Couches.' }));
      return;
    }
    const r = await lire(`maquettes/${m.id}/croisement`, { couches: ids });
    couches.surlignerResultats(r.groupes);
    const entete = el('div', { className: 'sig-croisement__tete' },
      el('span', { textContent: `${r.total} objet(s) dans ${r.groupes.length} couche(s) — emprise ${nf(r.surface_emprise_m2)} m²${r.tampon_m ? `, marge ${nf(r.tampon_m)} m` : ''}` }),
      lienCsv(`api/sig/maquettes/${m.id}/croisement?couches=${encodeURIComponent(ids)}&format=csv`));
    const blocs = r.groupes.map((g) => el('details', { className: 'sig-pset', open: r.groupes.length <= 3 },
      el('summary', { textContent: `${g.libelle} (${g.total}${g.tronque ? `, ${g.objets.length} affichés` : ''})` }),
      el('ul', { className: 'sig-croisement__liste' }, ...g.objets.map((o) => {
        const b = el('button', { type: 'button', className: 'sig-lien', textContent: o.libelle ?? `Objet n° ${o.n}` });
        b.addEventListener('click', () => ouvrirObjetSig(g.couche, o.n));
        const mesure = o.mesure != null ? ` · ${nf(o.mesure)} ${o.unite} dans l'emprise` : '';
        return el('li', {}, b, el('span', { className: 'sig-info', textContent: ` ${o.relation}${mesure}` }));
      }))));
    const anomalies = r.anomalies.map((a) => el('p', { className: 'sig-alerte', textContent: `${a.couche} : ${a.message}` }));
    zone.replaceChildren(entete, ...(blocs.length ? blocs : [el('p', { className: 'sig-info', textContent: 'Aucun objet des couches affichées dans l\'emprise.' })]), ...anomalies);
  })];
}

/** Objet SIG → maquettes et locaux au droit de l'objet. */
export function sectionObjet(couche, n, { ouvrirMaquette, ouvrirLocal }) {
  return [section('Interrogation croisée', 'Maquettes et locaux au droit de l\'objet', async (zone) => {
    const r = await lire(`couches/${couche.id}/objets/${n}/maquettes`);
    const entete = el('div', { className: 'sig-croisement__tete' },
      el('span', { textContent: `${r.total} maquette(s), ${r.nb_locaux} local(aux)${r.tampon_m ? ` — marge ${nf(r.tampon_m)} m` : ''}` }),
      lienCsv(`api/sig/couches/${couche.id}/objets/${n}/maquettes?format=csv`));
    const blocs = r.maquettes.map((m) => {
      const b = el('button', { type: 'button', className: 'sig-lien', textContent: m.nom, title: m.fichier });
      b.addEventListener('click', () => ouvrirMaquette(m.id));
      const locaux = m.locaux.map((l) => {
        const bl = el('button', { type: 'button', className: 'sig-lien', textContent: l.code || l.nom });
        bl.addEventListener('click', () => ouvrirLocal(m.id, l.guid));
        return el('li', {}, bl, el('span', { className: 'sig-info', textContent: ` ${l.niveau || ''} · ${l.typologie || l.nom_long || ''}` }));
      });
      return el('details', { className: 'sig-pset', open: r.maquettes.length <= 3 },
        el('summary', {}, b, ` (${m.locaux.length} local(aux))`),
        locaux.length ? el('ul', { className: 'sig-croisement__liste' }, ...locaux)
          : el('p', { className: 'sig-info', textContent: 'Aucun local au droit de l\'objet.' }));
    });
    zone.replaceChildren(entete, ...(blocs.length ? blocs : [el('p', { className: 'sig-info', textContent: 'Aucune maquette positionnée au droit de cet objet.' })]));
  })];
}

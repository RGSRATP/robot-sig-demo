// Contexte d'une maquette (lot 5, croisements) : section de la fiche, données de /api/sig/maquettes/{id}/contexte
// (risques Géorisques, bâti BDNB autour de l'emprise, niveau du cours d'eau le plus proche en NGF), affichage sur la
// carte (bâti ancien en rouge, autres bâtiments en gris, points à risque) et export CSV du bâti.
import { dateCourte, lire } from './api.js';
import { el } from './ui.js';

const SOURCE_BATI = 'sig-contexte-bati';
const SOURCE_POINTS = 'sig-contexte-points';
const COULEURS_NIVEAU = { 'élevé': '#e03131', moyen: '#f08c00', 'à vérifier': '#f08c00', faible: '#2f9e44', info: '#868e96' };
const COULEURS_POINTS = { 'Installation classée': '#7048e8', 'Site potentiellement pollué': '#a61e4d',
  'Cavité souterraine': '#495057', 'Mouvement de terrain': '#e8590c' };
const nf = (v, d = 2) => Number(v).toLocaleString('fr-FR', { maximumFractionDigits: d });

function installer(carte, police) {
  if (carte.getSource(SOURCE_BATI)) return;
  const vide = { type: 'FeatureCollection', features: [] };
  carte.addSource(SOURCE_BATI, { type: 'geojson', data: vide });
  carte.addSource(SOURCE_POINTS, { type: 'geojson', data: vide });
  carte.addLayer({ id: `${SOURCE_BATI}-fond`, type: 'fill', source: SOURCE_BATI,
    paint: { 'fill-color': ['case', ['get', 'ancien'], '#e03131', '#868e96'], 'fill-opacity': ['case', ['get', 'au_droit'], 0.55, 0.3] } });
  carte.addLayer({ id: `${SOURCE_BATI}-contour`, type: 'line', source: SOURCE_BATI,
    paint: { 'line-color': ['case', ['get', 'ancien'], '#a51111', '#495057'], 'line-width': 1.2 } });
  carte.addLayer({ id: `${SOURCE_BATI}-annee`, type: 'symbol', source: SOURCE_BATI, minzoom: 16,
    layout: { 'text-field': ['to-string', ['coalesce', ['get', 'annee_construction'], '?']], 'text-font': [police], 'text-size': 11 },
    paint: { 'text-color': '#212529', 'text-halo-color': '#ffffff', 'text-halo-width': 1.5 } });
  const couleur = ['match', ['get', 'categorie']];
  for (const [k, c] of Object.entries(COULEURS_POINTS)) couleur.push(k, c);
  couleur.push('#868e96');
  carte.addLayer({ id: `${SOURCE_POINTS}-points`, type: 'circle', source: SOURCE_POINTS,
    paint: { 'circle-radius': 6, 'circle-color': couleur, 'circle-stroke-color': '#ffffff', 'circle-stroke-width': 1.5 } });
}

function montrerSurCarte(carte, d) {
  carte.getSource(SOURCE_BATI)?.setData(d ? d.batiments : { type: 'FeatureCollection', features: [] });
  carte.getSource(SOURCE_POINTS)?.setData(d ? d.points : { type: 'FeatureCollection', features: [] });
}

/** Section « Contexte » de la fiche d'une maquette positionnée. */
export function sectionContexte(m, { carte, police }) {
  if (m.positionnement?.statut !== 'positionnee') return [];
  const zone = el('div', { className: 'sig-croisement__resultats' });
  const b = el('button', { type: 'button', className: 'sig-bouton', textContent: 'Risques, bâti environnant, cours d’eau' });
  const charger = async (actualiser = false) => {
    b.disabled = true;
    zone.replaceChildren(el('p', { className: 'sig-info', textContent: 'Interrogation de Géorisques, de la BDNB et de Hub’Eau…' }));
    try {
      const d = await lire(`maquettes/${m.id}/contexte`, actualiser ? { actualiser: 'true' } : {});
      installer(carte, police);
      rendre(d);
    } catch (e) {
      zone.replaceChildren(el('p', { className: 'sig-erreur', textContent: e.message }));
    }
    b.disabled = false;
  };
  b.addEventListener('click', () => charger(false));

  function rendre(d) {
    // risques
    const tr = el('table', { className: 'sig-attributs' });
    for (const r of d.risques) {
      const ligne = tr.insertRow();
      ligne.insertCell().textContent = r.theme;
      const c = ligne.insertCell();
      c.append(el('span', { className: 'sig-niveau-risque', style: `background:${COULEURS_NIVEAU[r.niveau] || '#868e96'}`, textContent: r.niveau }),
        ` ${r.detail}`);
    }
    // bâti
    const s = d.bati;
    const anciens = d.batiments.features.map((f) => f.properties).filter((p) => p.ancien)
      .sort((p, q) => Number(q.au_droit) - Number(p.au_droit) || p.distance_m - q.distance_m);
    const bati = [
      el('p', { textContent: `${s.nb} bâtiment(s) dans une bande de ${nf(s.tampon_m)} m autour de l'emprise, dont ${s.au_droit} au droit de l'ouvrage. `
        + `Bâti ancien (avant ${s.annee_seuil}) : ${s.anciens}, dont ${s.anciens_au_droit} au droit. `
        + `${s.sans_annee} sans année connue${s.monuments ? ` · ${s.monuments} monument(s) historique(s)` : ''}.` }),
      anciens.length ? el('ul', { className: 'sig-croisement__liste' }, ...anciens.slice(0, 30).map((p) => el('li', {
        textContent: `${p.annee_construction} · ${p.nb_niveaux ?? '?'} niveau(x) · ${p.murs || 'murs ?'} · `
          + `${p.au_droit ? 'au droit' : `à ${nf(p.distance_m, 1)} m`}${p.alea_argile ? ` · argiles ${p.alea_argile}` : ''}` }))) : null,
    ];
    // cours d'eau
    const c = d.crue;
    const crue = c ? el('p', { textContent: `${c.cours_d_eau || c.station} (station à ${nf(c.distance_km)} km) : hauteur ${nf(c.hauteur_m)} m `
      + (c.niveau_ngf != null ? `soit ${nf(c.niveau_ngf)} m NGF le ${dateCourte(c.observation)}. ` : '(niveau NGF inconnu). ')
      + (c.marge_m != null ? `Point bas de la maquette : ${nf(c.point_bas_maquette_ngf)} m NGF, ${c.marge_m >= 0 ? `${nf(c.marge_m)} m au-dessus`
        : `${nf(-c.marge_m)} m sous`} du niveau actuel du cours d’eau${c.marge_m < 0 ? ' (ouvrage sous le niveau de l’eau : étanchéité, accès et ouvertures à examiner)' : ''}.` : '') })
      : el('p', { className: 'sig-info', textContent: 'Aucune station hydrométrique exploitable à proximité.' });
    const carteCase = el('input', { type: 'checkbox', checked: true });
    carteCase.addEventListener('change', () => montrerSurCarte(carte, carteCase.checked ? d : null));
    montrerSurCarte(carte, d);
    const actualiser = el('button', { type: 'button', className: 'sig-lien', textContent: 'Actualiser' });
    actualiser.addEventListener('click', () => charger(true));
    zone.replaceChildren(...[
      el('div', { className: 'sig-croisement__tete' },
        el('label', { className: 'sig-option' }, carteCase, ' Afficher sur la carte'),
        el('a', { className: 'sig-bouton', href: `api/sig/maquettes/${m.id}/contexte?format=csv`, download: '', textContent: 'Bâti en CSV' })),
      el('h4', { textContent: 'Risques (Géorisques)' }), tr,
      el('h4', { textContent: 'Bâti environnant (BDNB)' }), ...bati,
      el('h4', { textContent: 'Cours d’eau le plus proche (Hub’Eau)' }), crue,
      ...d.erreurs.map((e) => el('p', { className: 'sig-alerte', textContent: e })),
      el('p', { className: 'sig-info' }, `Données du ${dateCourte(d.date)} (cache 24 h) · `, actualiser,
        ' · Carte : bâti ancien en rouge (plus foncé au droit de l’ouvrage), autres bâtiments en gris, points à risque colorés par catégorie.'),
    ].filter(Boolean));
  }
  return [el('section', { className: 'sig-croisement' }, el('h4', { textContent: 'Contexte de l’ouvrage' }), b, zone)];
}

/** Section « Carbone » de la fiche d'une maquette : ordre de grandeur par matériau, fiabilité, export CSV. */
export function sectionCarbone(m) {
  if (m.etat?.phase !== 'pret') return [];
  const zone = el('div', { className: 'sig-croisement__resultats' });
  const b = el('button', { type: 'button', className: 'sig-bouton', textContent: 'Ordre de grandeur des émissions' });
  b.addEventListener('click', async () => {
    b.disabled = true;
    try {
      const r = await lire(`maquettes/${m.id}/carbone`);
      const table = el('table', { className: 'sig-attributs' });
      const tete = table.insertRow();
      for (const t of ['Matériau', 'Volume', 'Émissions', 'Matériau déclaré']) tete.insertCell().textContent = t;
      for (const x of r.materiaux) {
        const tr = table.insertRow();
        tr.insertCell().textContent = `${x.materiau} (${nf(x.facteur_kgco2e_m3, 0)} kg/m³)`;
        tr.insertCell().textContent = `${nf(x.volume_m3, 1)} m³`;
        tr.insertCell().textContent = `${nf(x.tco2e, 1)} t CO2e`;
        tr.insertCell().textContent = `${Math.round(x.part_declaree * 100)} %`;
      }
      const nonClasses = Object.entries(r.non_classes);
      zone.replaceChildren(...[
        el('div', { className: 'sig-croisement__tete' },
          el('strong', { textContent: `≈ ${nf(r.total_tco2e, 0)} t CO2e pour ${nf(r.volume_compte_m3, 0)} m³` }),
          el('a', { className: 'sig-bouton', href: `api/sig/maquettes/${m.id}/carbone?format=csv`, download: '', textContent: 'CSV' })),
        r.materiaux.length ? table : el('p', { className: 'sig-info', textContent: 'Aucun objet avec un volume et un matériau identifiable.' }),
        el('p', { className: 'sig-info', textContent: `${r.nb_comptes} objet(s) comptés sur ${r.nb_objets} ; ${r.nb_sans_volume} sans volume`
          + `${r.nb_volume_douteux ? ` ; ${r.nb_volume_douteux} au volume douteux écarté(s)` : ''}. Matériau déclaré pour `
          + `${Math.round(r.part_volume_materiau_declare * 100)} % du volume, sinon déduit du nom de l'objet ou supposé d'après son type IFC.` }),
        nonClasses.length ? el('p', { className: 'sig-alerte', textContent: `Non comptés faute de matériau identifiable : `
          + nonClasses.slice(0, 6).map(([t, n]) => `${t} (${n})`).join(', ') }) : null,
        el('p', { className: 'sig-info', textContent: 'Ordre de grandeur (production des matériaux) avec des facteurs génériques indicatifs '
          + '(config/georef_maquettes.yaml) : ce n’est pas un bilan carbone réglementaire. À remplacer par les données '
          + 'environnementales du projet (FDES / INIES).' }),
      ].filter(Boolean));
    } catch (e) {
      zone.replaceChildren(el('p', { className: 'sig-erreur', textContent: e.message }));
    }
    b.disabled = false;
  });
  return [el('section', { className: 'sig-croisement' }, el('h4', { textContent: 'Carbone des matériaux' }), b, zone)];
}

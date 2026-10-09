// Contexte d'une maquette (lot 5, croisements) : section de la fiche, données de /api/sig/maquettes/{id}/contexte
// (risques Géorisques, bâti BDNB autour de l'emprise, niveau du cours d'eau le plus proche en NGF), affichage sur la
// carte (bâti ancien en rouge, autres bâtiments en gris, points à risque) et export CSV du bâti.
import { dateCourte, lire } from './api.js';
import { el } from './ui.js';

const SOURCE_BATI = 'sig-contexte-bati';
const SOURCE_POINTS = 'sig-contexte-points';
const SOURCE_CALAGE = 'sig-contexte-calage';
const COULEURS_NIVEAU = { 'élevé': '#e03131', moyen: '#f08c00', 'à vérifier': '#f08c00', faible: '#2f9e44', info: '#868e96' };
const COULEURS_POINTS = { 'Installation classée': '#7048e8', 'Site potentiellement pollué': '#a61e4d',
  'Cavité souterraine': '#495057', 'Mouvement de terrain': '#e8590c' };
const COULEURS_VIGILANCE = { 1: '#2f9e44', 2: '#fcc419', 3: '#f76707', 4: '#e03131' };   // Vigicrues
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
    zone.replaceChildren(el('p', { className: 'sig-info', textContent: 'Interrogation de Géorisques, de la BDNB, de Hub’Eau et de Vigicrues… (la première fois, lecture des étages de la maquette : jusqu’à quelques minutes)' }));
    try {
      const d = await lire(`maquettes/${m.id}/contexte`, actualiser ? { actualiser: 'true' } : {});
      installer(carte, police);
      rendre(d);
      zone.append(sectionCourbe(m.id));
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
    const eau = niveauxFaceALEau(d.face_a_l_eau, c, m.id);
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
      el('h4', { textContent: 'Cours d’eau le plus proche (Hub’Eau, Vigicrues)' }), crue, ...eau,
      ...d.erreurs.map((e) => el('p', { className: 'sig-alerte', textContent: e })),
      el('p', { className: 'sig-info' }, `Données du ${dateCourte(d.date)} (cache 24 h) · `, actualiser,
        ' · Carte : bâti ancien en rouge (plus foncé au droit de l’ouvrage), autres bâtiments en gris, points à risque colorés par catégorie.'),
    ].filter(Boolean));
  }
  return [el('section', { className: 'sig-croisement' }, el('h4', { textContent: 'Contexte de l’ouvrage' }), b, zone)];
}

/** Niveaux de la maquette (plancher en m NGF) comparés au niveau actuel du cours d'eau et aux crues historiques. */
function niveauxFaceALEau(eau, c, id) {
  if (!eau) return [];
  const h = c?.historiques;
  const sortie = [h ? el('p', { textContent: `Crues historiques à ${h.station}${h.meme_cours_d_eau ? '' : ` (${h.cours_d_eau}, autre cours d’eau)`}, `
      + `à ${nf(h.distance_km)} km : ` + h.crues.map((x) => `${x.libelle.replace('Crue du ', '')} ${nf(x.niveau_ngf)} m NGF (${nf(x.hauteur_m)} m à l’échelle)`).join(' · ') })
    : el('p', { className: 'sig-info', textContent: 'Aucune crue historique publiée par Vigicrues pour les stations voisines.' })];
  if (!eau.niveaux.length) {
    sortie.push(el('p', { className: 'sig-info', textContent: `Niveaux de la maquette : ${eau.message || 'aucun'}.` }));
    return sortie;
  }
  if (!eau.references.length) return sortie;
  const table = el('table', { className: 'sig-attributs sig-niveaux-eau' });
  const tete = table.insertRow();
  for (const t of ['Niveau', 'Altitude NGF', ...eau.references.map((r) => `${r.libelle.replace('Crue du ', 'Crue ')} (${nf(r.niveau_ngf)})`)]) {
    tete.insertCell().textContent = t;
  }
  for (const n of [...eau.niveaux].reverse()) {   // du plus haut au plus bas, comme une coupe
    const tr = table.insertRow();
    tr.insertCell().textContent = n.nom;
    tr.insertCell().textContent = `${nf(n.altitude_ngf)} m`;
    for (const m of n.marges_m) {
      const td = tr.insertCell();
      td.textContent = `${m >= 0 ? '+' : '−'}${nf(Math.abs(m))} m`;
      td.style.color = m < 0 ? '#c92a2a' : '#2b8a3e';
      if (m < 0) td.style.fontWeight = '600';
    }
  }
  const sous = eau.niveaux.filter((n) => n.sous.length);
  sortie.push(
    el('div', { className: 'sig-croisement__tete' },
      el('strong', { textContent: sous.length ? `${sous.length} niveau(x) sur ${eau.niveaux.length} sous au moins une référence`
        : `Aucun des ${eau.niveaux.length} niveaux sous les références` }),
      el('a', { className: 'sig-bouton', href: `api/sig/maquettes/${id}/contexte?format=csv_niveaux`, download: '', textContent: 'CSV' })),
    el('div', { className: 'sig-tableau-defilant' }, table),
    el('p', { className: 'sig-info', textContent: `Altitudes : ${{ locaux: 'bas des locaux de chaque niveau', 'modèle 3D': 'points bas et haut du modèle 3D (ni étage ni local dans la maquette)' }[eau.source] || 'placement des étages IFC'}, `
      + 'en m NGF (terrain IGN + calage de la maquette). Marge = altitude − niveau de l’eau à la station (en rouge : plus bas que l’eau). '
      + 'Comparaison d’altitudes seulement : la ligne d’eau varie le long du cours d’eau, les protections, le cheminement de l’eau et '
      + 'l’étanchéité ne sont pas pris en compte ; les crues anciennes sont converties avec le zéro d’échelle actuel.' }),
  );
  return sortie;
}

// ---------------------------------------------------------------- courbe du niveau de l'eau

const SVG = 'http://www.w3.org/2000/svg';
function svg(nom, attrs = {}, ...enfants) {
  const n = document.createElementNS(SVG, nom);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  n.append(...enfants);
  return n;
}
const dateFr = (t) => new Date(t).toLocaleDateString('fr-FR', { day: 'numeric', month: 'short' });
const heureFr = (t) => new Date(t).toLocaleString('fr-FR', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });

/** Pas « rond » pour environ n graduations sur [a, b]. */
function pasRond(a, b, n) {
  const brut = Math.max((b - a) / n, 0.01);
  const p = 10 ** Math.floor(Math.log10(brut));
  return [1, 2, 2.5, 5, 10].map((k) => k * p).find((x) => x >= brut);
}

/** Étiquettes écartées d'au moins « ecart » pixels, du bas vers le haut. */
function ecarter(etiquettes, ecart) {
  const tri = [...etiquettes].sort((a, b) => b.y - a.y);
  for (let i = 1; i < tri.length; i += 1) tri[i].y = Math.min(tri[i].y, tri[i - 1].y - ecart);
  return tri;
}

/** Section « Niveau de l'eau » : courbe des derniers jours, prévisions, crues historiques, niveaux de l'ouvrage. */
function sectionCourbe(id) {
  const zone = el('div', { className: 'sig-courbe-eau' }, el('p', { className: 'sig-info', textContent: 'Courbe du niveau de l’eau…' }));
  lire(`maquettes/${id}/hydrogramme`).then((h) => zone.replaceChildren(...courbe(h)))
    .catch((e) => zone.replaceChildren(el('p', { className: 'sig-erreur', textContent: `Courbe du niveau de l’eau : ${e.message}` })));
  return zone;
}

export function courbe(h) {   // exportée pour l’aperçu et les essais
  const titre = el('h4', { textContent: `Niveau de l’eau — ${h.cours_d_eau || h.station || 'station'}` });
  const v = h.vigilance;
  const vig = v ? el('p', {}, el('span', { className: 'sig-niveau-risque', style: `background:${COULEURS_VIGILANCE[v.niveau] || '#868e96'}`,
    textContent: `vigilance ${String(v.vigilance).toLowerCase()}` }), ` Tronçon Vigicrues « ${v.troncon} », à ${nf(v.distance_km)} km.`) : null;
  const erreurs = (h.erreurs || []).map((e) => el('p', { className: 'sig-alerte', textContent: e }));
  if (!h.observations?.length) {
    return [titre, vig, el('p', { className: 'sig-info', textContent: 'Aucune hauteur d’eau observée récemment à la station.' }), ...erreurs].filter(Boolean);
  }
  const obs = h.observations.map(([t, z]) => [Date.parse(t), z]);
  const prev = (h.previsions || []).map(([t, z, lo, hi]) => [Date.parse(t), z, lo ?? z, hi ?? z]);
  const crues = h.crues || [];
  // niveaux de l'ouvrage : les plus bas (au plus 3) pour garder l'eau lisible ; les autres sont annoncés
  const niveaux = [...(h.niveaux || [])].sort((a, b) => a.altitude_ngf - b.altitude_ngf).slice(0, 3);
  const valeurs = [...obs.map((o) => o[1]), ...prev.flatMap((p) => [p[2], p[3]]), ...crues.map((c) => c.niveau_ngf),
    ...niveaux.map((n) => n.altitude_ngf)];
  const pas = pasRond(Math.min(...valeurs), Math.max(...valeurs), 5);
  const y0 = Math.floor((Math.min(...valeurs) - pas / 2) / pas) * pas;
  const y1 = Math.ceil((Math.max(...valeurs) + pas / 2) / pas) * pas;
  const t0 = obs[0][0];
  const t1 = Math.max(obs[obs.length - 1][0], ...prev.map((p) => p[0]));
  const L = 460; const H = 250; const m = { g: 40, d: 118, h: 22, b: 24 };
  const X = (t) => m.g + ((t - t0) / (t1 - t0 || 1)) * (L - m.g - m.d);
  const Y = (z) => m.h + ((y1 - z) / (y1 - y0)) * (H - m.h - m.b);
  const racine = svg('svg', { viewBox: `0 0 ${L} ${H}`, class: 'sig-courbe-eau__svg', role: 'img',
    'aria-label': `Niveau de ${h.cours_d_eau || 'l’eau'} en mètres NGF sur ${h.jours} jours, crues historiques et niveaux de l’ouvrage` });
  for (let z = y0; z <= y1 + 1e-9; z += pas) {   // grille et axe des altitudes
    racine.append(svg('line', { x1: m.g, x2: L - m.d, y1: Y(z), y2: Y(z), class: 'grille' }),
      svg('text', { x: m.g - 6, y: Y(z) + 4, 'text-anchor': 'end', class: 'axe' }, nf(z, 1)));
  }
  racine.append(svg('text', { x: 4, y: 11, class: 'axe' }, 'm NGF'));
  const jour = 86400000;   // axe du temps : une graduation par semaine (par jour si la période est courte)
  const pasT = (t1 - t0) > 10 * jour ? 7 * jour : jour;
  for (let t = Math.ceil(t0 / jour) * jour; t <= t1; t += pasT) {
    racine.append(svg('text', { x: X(t), y: H - 8, 'text-anchor': 'middle', class: 'axe' }, dateFr(t)));
  }
  const etiquettes = [];   // crues (tirets gris) et niveaux de l'ouvrage (orange), étiquetés à droite
  for (const c of crues) {
    racine.append(svg('line', { x1: m.g, x2: L - m.d, y1: Y(c.niveau_ngf), y2: Y(c.niveau_ngf), class: 'crue' }));
    etiquettes.push({ y: Y(c.niveau_ngf), texte: `Crue ${(c.libelle.match(/(\d{4})\s*$/) || [null, c.libelle])[1]} · ${nf(c.niveau_ngf)}`, classe: 'etiq-crue' });
  }
  for (const n of niveaux) {
    racine.append(svg('line', { x1: m.g, x2: L - m.d, y1: Y(n.altitude_ngf), y2: Y(n.altitude_ngf), class: 'niveau' }));
    etiquettes.push({ y: Y(n.altitude_ngf), texte: `${n.nom.replace(/ de la maquette$/, '')} · ${nf(n.altitude_ngf)}`, classe: 'etiq-niveau' });
  }
  for (const e of ecarter(etiquettes, 12)) {
    racine.append(svg('text', { x: L - m.d + 6, y: e.y + 4, class: e.classe }, e.texte.length > 22 ? `${e.texte.slice(0, 21)}…` : e.texte));
  }
  if (prev.length) {   // prévisions : fourchette et trait tireté
    const p0 = obs[obs.length - 1];
    const bande = [...prev.map((p) => `${X(p[0])},${Y(p[3])}`), ...[...prev].reverse().map((p) => `${X(p[0])},${Y(p[2])}`)].join(' ');
    racine.append(svg('polygon', { points: bande, class: 'prevision-bande' }),
      svg('polyline', { points: [p0, ...prev].map((p) => `${X(p[0])},${Y(p[1])}`).join(' '), class: 'prevision' }));
  }
  racine.append(svg('polyline', { points: obs.map((o) => `${X(o[0])},${Y(o[1])}`).join(' '), class: 'observe' }));
  const der = obs[obs.length - 1];
  racine.append(svg('circle', { cx: X(der[0]), cy: Y(der[1]), r: 4, class: 'point-actuel' }));
  // survol : réticule et bulle (date, niveau, marge au niveau le plus bas de l'ouvrage)
  const reticule = svg('line', { y1: m.h, y2: H - m.b, class: 'reticule', visibility: 'hidden' });
  const repere = svg('circle', { r: 4, class: 'point-actuel', visibility: 'hidden' });
  const cible = svg('rect', { x: m.g, y: m.h, width: L - m.g - m.d, height: H - m.h - m.b, fill: 'transparent' });
  racine.append(reticule, repere, cible);
  const bulle = el('div', { className: 'sig-courbe-eau__bulle', hidden: true });
  const serie = [...obs.map((o) => [o[0], o[1], 'observé']), ...prev.map((p) => [p[0], p[1], 'prévu'])];
  const bas = niveaux[0];
  cible.addEventListener('pointermove', (ev) => {
    const r = racine.getBoundingClientRect();
    const t = t0 + (((ev.clientX - r.left) * (L / r.width) - m.g) / (L - m.g - m.d)) * (t1 - t0);
    const p = serie.reduce((a, b) => (Math.abs(b[0] - t) < Math.abs(a[0] - t) ? b : a));
    reticule.setAttribute('x1', X(p[0]));
    reticule.setAttribute('x2', X(p[0]));
    repere.setAttribute('cx', X(p[0]));
    repere.setAttribute('cy', Y(p[1]));
    reticule.setAttribute('visibility', 'visible');
    repere.setAttribute('visibility', 'visible');
    const marge = bas ? bas.altitude_ngf - p[1] : null;
    bulle.replaceChildren(el('strong', { textContent: `${nf(p[1])} m NGF` }), ` ${p[2]}, ${heureFr(p[0])}`,
      ...(bas ? [el('div', { textContent: `${bas.nom} : ${marge >= 0 ? `${nf(marge)} m au-dessus` : `${nf(-marge)} m sous l’eau`}` })] : []));
    bulle.hidden = false;
    bulle.style.left = `${Math.min((X(p[0]) / L) * r.width + 10, r.width - 200)}px`;
    bulle.style.top = `${Math.max(0, (Y(p[1]) / H) * r.height - 48)}px`;
  });
  cible.addEventListener('pointerleave', () => {
    bulle.hidden = true;
    reticule.setAttribute('visibility', 'hidden');
    repere.setAttribute('visibility', 'hidden');
  });
  const traits = [['observe', `observé (${h.source})`], ...(prev.length ? [['prevision', 'prévu (Vigicrues)']] : []),
    ...(crues.length ? [['crue', 'crues historiques']] : []), ...(niveaux.length ? [['niveau', 'niveaux de l’ouvrage']] : [])];
  const legende = el('ul', { className: 'sig-legende sig-courbe-eau__legende' }, ...traits.map(([c, t]) => el('li', {},
    el('span', { className: `sig-courbe-eau__trait sig-courbe-eau__trait--${c}` }), el('span', { textContent: t }))));
  const masques = (h.niveaux || []).length - niveaux.length;
  return [titre, vig, legende, el('div', { className: 'sig-courbe-eau__cadre' }, racine, bulle),
    el('p', { className: 'sig-info', textContent: `Station ${h.station}, zéro d’échelle ${nf(h.zero_echelle_ngf)} m NGF, `
      + `${h.jours} derniers jours, une valeur par heure. `
      + (prev.length ? '' : 'Pas de prévision publiée (Vigicrues n’en publie qu’en période de crue). ')
      + (masques > 0 ? `Seuls les ${niveaux.length} niveaux les plus bas de l’ouvrage sont tracés (${masques} autres plus haut, voir le tableau). ` : '')
      + 'Le niveau à la station n’est pas celui au droit de l’ouvrage.' }),
    ...erreurs].filter(Boolean);
}

/** Section « Contrôle du calage » : terrain RGE ALTI (calage) comparé au MNT LiDAR HD sur l'emprise, constats,
 * points de contrôle sur la carte colorés par écart entre les deux sources. */
export function sectionCalage(m, { carte, police }) {
  if (m.positionnement?.statut !== 'positionnee') return [];
  const zone = el('div', { className: 'sig-croisement__resultats' });
  const b = el('button', { type: 'button', className: 'sig-bouton', textContent: 'Comparer au terrain LiDAR HD' });
  b.addEventListener('click', async () => {
    b.disabled = true;
    zone.replaceChildren(el('p', { className: 'sig-info', textContent: 'Interrogation du service d’altimétrie de l’IGN…' }));
    try {
      const d = await lire(`maquettes/${m.id}/calage`);
      montrerPointsCalage(carte, police, d);
      const val = (v) => (v == null ? '—' : `${nf(v)} m`);
      const st = (s) => (s ? `${nf(s.min)} à ${nf(s.max)} m (moy. ${nf(s.moyenne)})` : 'indisponible');
      const table = el('table', { className: 'sig-attributs' });
      for (const [k, v] of [
        ['Calage', d.calage || '—'],
        ['Terrain au centre', `${d.source_calage} ${val(d.centre.reference)} · ${d.source_comparaison} ${val(d.centre.comparaison)}`],
        [`${d.source_calage} sur l’emprise`, st(d.reference)],
        [`${d.source_comparaison} sur l’emprise`, st(d.comparaison)],
        ['Écart entre sources', d.ecarts ? `${nf(d.ecarts.min)} à ${nf(d.ecarts.max)} m sur ${d.ecarts.nb} points` : '—'],
      ]) {
        const tr = table.insertRow();
        tr.insertCell().textContent = k;
        tr.insertCell().textContent = v;
      }
      const caseCarte = el('input', { type: 'checkbox', checked: true });
      caseCarte.addEventListener('change', () => montrerPointsCalage(carte, police, caseCarte.checked ? d : null));
      zone.replaceChildren(
        el('ul', { className: 'sig-croisement__liste' }, ...d.constats.map((c) => el('li', {},
          el('span', { className: 'sig-niveau-risque', style: `background:${COULEURS_NIVEAU[c.niveau] || COULEURS_CONSTAT[c.niveau] || '#868e96'}`, textContent: c.niveau }),
          ` ${c.texte}`))),
        table,
        ...d.erreurs.map((e) => el('p', { className: 'sig-alerte', textContent: e })),
        el('label', { className: 'sig-option' }, caseCarte, ` Points de contrôle sur la carte (${d.nb_points})`),
        el('p', { className: 'sig-info', textContent: `Points verts : écart entre sources ≤ ${nf(d.ecart_tolere_m)} m ; orange : jusqu’à `
          + `${nf(d.amplitude_signalee_m)} m ; rouges : au-delà (terrain modifié entre les relevés ?). Le calage n’est pas modifié par ce contrôle.` }),
      );
    } catch (e) {
      zone.replaceChildren(el('p', { className: 'sig-erreur', textContent: e.message }));
    }
    b.disabled = false;
  });
  return [el('section', { className: 'sig-croisement' }, el('h4', { textContent: 'Contrôle du calage vertical' }), b, zone)];
}

const COULEURS_CONSTAT = { conforme: '#2f9e44' };

function montrerPointsCalage(carte, police, d) {
  if (!carte.getSource(SOURCE_CALAGE)) {
    carte.addSource(SOURCE_CALAGE, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
    carte.addLayer({ id: `${SOURCE_CALAGE}-points`, type: 'circle', source: SOURCE_CALAGE,
      paint: { 'circle-radius': 5, 'circle-color': ['get', 'couleur'], 'circle-stroke-color': '#ffffff', 'circle-stroke-width': 1.2 } });
    carte.addLayer({ id: `${SOURCE_CALAGE}-ecart`, type: 'symbol', source: SOURCE_CALAGE, minzoom: 17,
      layout: { 'text-field': ['get', 'etiquette'], 'text-font': [police], 'text-size': 10, 'text-offset': [0, 1.1] },
      paint: { 'text-color': '#212529', 'text-halo-color': '#ffffff', 'text-halo-width': 1.5 } });
  }
  const features = (d?.points || []).map((p) => {
    const e = p.reference != null && p.comparaison != null ? p.reference - p.comparaison : null;
    const a = e == null ? null : Math.abs(e);
    const couleur = a == null ? '#868e96' : a <= d.ecart_tolere_m ? '#2f9e44' : a <= d.amplitude_signalee_m ? '#f08c00' : '#e03131';
    return { type: 'Feature', geometry: { type: 'Point', coordinates: [p.lon, p.lat] },
      properties: { couleur, etiquette: e == null ? 'sans donnée' : `${e >= 0 ? '+' : '−'}${nf(Math.abs(e))} m` } };
  });
  carte.getSource(SOURCE_CALAGE).setData({ type: 'FeatureCollection', features });
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

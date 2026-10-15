// Volet « Propriétés » : fiche d'un objet SIG (attributs, coordonnées d'origine et Lambert 93),
// d'une maquette (positionnement) ou d'un objet de maquette (Psets IFC).
import { dateCourte } from './api.js';
import { bouton, el } from './ui.js';

let panneau = null;
let ouvrirVolet = () => {};
let fermerVolet = () => {};
let surFermeture = null;

export function monterFiche(conteneur, { ouvrir, fermer }) {
  panneau = conteneur;
  ouvrirVolet = ouvrir;
  fermerVolet = fermer;
  vider();
}

function vider() {
  panneau.replaceChildren(el('div', { className: 'sig-vide' },
    el('p', { textContent: 'Cliquez sur un objet de la carte ou d\'une maquette pour afficher ses propriétés.' })));
}

export function fermerFiche() {
  if (!panneau) return;
  vider();
  fermerVolet();
  const f = surFermeture;
  surFermeture = null;
  f?.();
}

function nombre(v) {
  return v.toLocaleString('fr-FR', { maximumFractionDigits: 8 });
}

function coordonnees(c) {
  if (c.point) return `X ${nombre(c.point[0])} · Y ${nombre(c.point[1])}`;
  const [x1, y1, x2, y2] = c.emprise;
  return `X ${nombre(x1)} → ${nombre(x2)} · Y ${nombre(y1)} → ${nombre(y2)}`;
}

function liste(paires) {
  const dl = el('dl', { className: 'sig-dl' });
  for (const [k, v] of paires) {
    if (v == null || v === '') continue;
    dl.append(el('dt', { textContent: k }), el('dd', { textContent: v }));
  }
  return dl;
}

function tableau(valeurs) {
  const table = el('table', { className: 'sig-attributs' });
  for (const [k, v] of Object.entries(valeurs)) {
    const tr = table.insertRow();
    tr.insertCell().textContent = k;
    tr.insertCell().textContent = v == null ? '' : (typeof v === 'object' ? JSON.stringify(v) : String(v));
  }
  return table;
}

function entete(titre, sousTitre, nature) {
  return el('header', { className: 'sig-fiche__tete' },
    nature ? el('span', { className: 'sig-fiche__nature', textContent: nature }) : null,
    el('h3', { className: 'sig-fiche__titre', textContent: titre }),
    sousTitre ? el('p', { className: 'sig-info', textContent: sousTitre }) : null);
}

// La sélection précédente est effacée par carte.js (répartition des clics), pas ici.
function montrer(noeuds, fermeture) {
  surFermeture = fermeture || null;
  panneau.replaceChildren(...noeuds.filter(Boolean));
  panneau.scrollTop = 0;
  ouvrirVolet();
}

// ---------------------------------------------------------------- objet SIG

export function afficherFiche(fiche, couche, fermeture, complements = []) {
  if (!panneau) return;
  const source = couche.service
    ? `${couche.libelle} — service ${couche.service.type || 'Esri'}, données du ${dateCourte(couche.service.date_maj)}`
    : couche.libelle;
  if (fiche.erreur) {
    montrer([entete('Objet indisponible', source, 'Objet SIG'), el('p', { className: 'sig-erreur', textContent: fiche.erreur })], fermeture);
    return;
  }
  const { origine, lambert93 } = fiche.coordonnees;
  const coords = [[`Lambert 93`, coordonnees(lambert93)]];
  if (origine.systeme !== lambert93.systeme) coords.push([`Origine (${origine.systeme})`, coordonnees(origine)]);
  coords.push(['Géométrie', fiche.type_geometrie]);
  montrer([
    entete(fiche.libelle ?? `Objet n° ${fiche.n}`, source, 'Objet SIG'),
    liste(coords),
    ...complements,
    el('h4', { textContent: `Attributs (${Object.keys(fiche.attributs).length})` }),
    tableau(fiche.attributs),
  ], fermeture);
}

// ---------------------------------------------------------------- point temps réel

const nf = (v, d = 2) => (typeof v === 'number' ? v.toLocaleString('fr-FR', { maximumFractionDigits: d }) : v);

/** Fiche d'un point temps réel (Vélib', Hub'Eau, météo, gare IDFM), datée. */
export function afficherTempsReel(p, source, fermeture) {
  if (!panneau) return;
  const date = `données du ${dateCourte(source.date_donnees || source.date_maj)}, lues le ${dateCourte(source.date_maj)}`;
  let titre = '';
  let paires = [];
  const extra = [];
  if (source.type === 'gbfs') {
    titre = p.nom;
    paires = [['Vélos disponibles', p.velos], ['dont mécaniques', p.velos_mecaniques], ['dont électriques', p.velos_electriques],
      ['Places libres', p.places_libres], ['Capacité', p.capacite], ['État', p.en_service ? 'en service' : 'fermée'],
      ['Dernier relevé', dateCourte(p.releve)]];
  } else if (source.type === 'hubeau_hydrometrie') {
    titre = p.station;
    paires = [["Cours d'eau", p.cours_d_eau], ['Hauteur', p.hauteur_m != null ? `${nf(p.hauteur_m, 3)} m` : null],
      ['Observation', dateCourte(p.observation)], ['Code station', p.code]];
    extra.push(el('p', { className: 'sig-info', textContent: "Hauteur à l'échelle de la station (zéro propre à chaque station, pas une altitude NGF)." }));
  } else if (source.type === 'vigicrues_vigilance') {
    titre = `${p.troncon} · vigilance ${String(p.vigilance).toLowerCase()}`;
    paires = [['Tronçon', p.troncon], ['Code', p.code], ['Vigilance', p.vigilance]];
    extra.push(el('p', { className: 'sig-info', textContent: 'Niveau de vigilance crues publié par Vigicrues pour ce tronçon. '
      + 'Détail et prévisions : vigicrues.gouv.fr. Courbe de hauteur face aux niveaux d’un ouvrage : fiche de la maquette, « Contexte de l’ouvrage ».' }));
  } else if (source.type === 'open_meteo') {
    titre = `Météo · ${p.temps}`;
    paires = [['Température', `${nf(p.temperature_c, 1)} °C`], ['Précipitations', `${nf(p.precipitation_mm, 1)} mm`],
      ['Vent', `${nf(p.vent_kmh, 0)} km/h, de ${nf(p.vent_direction_deg, 0)}°`], ['Heure (locale)', p.heure_locale]];
  } else {
    titre = p.gare;
    paires = [['Commune', p.commune], ['Zone d’arrêt IDFM', p.zone_arret], ['Erreur', p.erreur]];
    const passages = Array.isArray(p.passages) ? p.passages : [];
    if (passages.length) {
      const table = el('table', { className: 'sig-attributs' });
      const tete = table.insertRow();
      for (const t of ['Ligne', 'Destination', 'Départ', 'État']) tete.insertCell().textContent = t;
      for (const x of passages) {
        const tr = table.insertRow();
        tr.insertCell().textContent = x.ligne;
        tr.insertCell().textContent = x.destination || '';
        tr.insertCell().textContent = `${new Date(x.heure).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })} (${x.dans_min} min)`;
        tr.insertCell().textContent = { onTime: "à l'heure", delayed: 'retardé', cancelled: 'supprimé', early: 'en avance' }[x.statut] || x.statut || '';
      }
      extra.push(el('h4', { textContent: `Prochains passages (${passages.length})` }), table);
    } else if (!p.erreur) {
      extra.push(el('p', { className: 'sig-info', textContent: 'Aucun passage annoncé dans l’heure.' }));
    }
  }
  montrer([entete(titre || source.libelle, `${source.libelle} — ${date}`, 'Temps réel'),
    liste(paires.map(([k, v]) => [k, v == null ? null : String(v)])), ...extra], fermeture);
}

// ---------------------------------------------------------------- bâtiment BD TOPO

/** Fiche d'un bâtiment des tuiles BD TOPO : attributs choisis en tête (config « champs_fiche »), puis tous. */
export function afficherBatiment(proprietes, cfg, fermeture) {
  if (!panneau) return;
  const valeur = (v) => (typeof v === 'number' ? nombre(v) : String(v));
  const tete = cfg.champs_fiche.filter((k) => proprietes[k] != null && proprietes[k] !== '')
    .map((k) => [k.replace(/_([dl])_/g, ' $1’').replaceAll('_', ' ').replace(/^./, (c) => c.toUpperCase()), valeur(proprietes[k])]);
  const usage = proprietes[cfg.champ_couleur];
  montrer([
    entete(usage ? `Bâtiment · ${usage}` : 'Bâtiment', cfg.libelle, 'BD TOPO'),
    liste(tete),
    el('h4', { textContent: `Attributs (${Object.keys(proprietes).length})` }),
    tableau(proprietes),
  ], fermeture);
}

// ---------------------------------------------------------------- maquette

const PHASES = { pret: 'traitée', en_cours: 'traitement en cours', en_attente: 'en attente de traitement', erreur: 'erreur de traitement' };

export function afficherMaquette(m, actions, fermeture) {
  if (!panneau) return;
  const pos = m.positionnement;
  const meta = m.metadonnees || {};
  const noeuds = [entete(m.nom, m.dossier, 'Maquette IFC')];
  const outils = el('div', { className: 'sig-fiche__actions' });
  if (pos?.locaux_seuls) {
    noeuds.push(el('p', { className: 'sig-info', textContent: 'Maquette de locaux (IfcSpace) seuls : consultez-la avec le plan des locaux.' }));
  }
  if (pos?.statut === 'positionnee' && !pos.locaux_seuls) {
    const b3d = el('button', { type: 'button', className: 'sig-bouton sig-bouton--principal',
      textContent: actions.chargee ? 'Masquer la 3D' : `Afficher en 3D (${pos.affichage.taille_mo} Mo)` });
    b3d.addEventListener('click', actions.basculer3d);
    outils.append(b3d, bouton('zoom', 'Zoomer sur la maquette', actions.zoomer),
      bouton('oeil', actions.isolee ? 'Ne plus isoler' : 'Isoler (atténuer les autres maquettes)', actions.isoler));
  }
  outils.append(bouton('actualiser', 'Relancer le traitement', actions.recalculer));
  noeuds.push(outils);
  if (actions.chargee && actions.opacite != null) {
    const op = el('input', { type: 'range', min: 0.1, max: 1, step: 0.05, value: actions.opacite });
    op.addEventListener('input', () => actions.definirOpacite(Number(op.value)));
    noeuds.push(el('label', { className: 'sig-reglage' }, el('span', { className: 'sig-reglage__libelle', textContent: 'Opacité de la maquette' }), op));
  }
  const loc = m.locaux;
  if (loc?.phase === 'pret' && loc.nb > 0) {
    const b = el('button', { type: 'button', className: 'sig-bouton', textContent: `Plan des locaux (${loc.nb} · ${loc.niveaux} niveaux)` });
    b.addEventListener('click', actions.planLocaux);
    noeuds.push(el('div', { className: 'sig-fiche__actions' }, b));
  } else if (loc && loc.phase !== 'pret') {
    noeuds.push(el('p', { className: loc.phase === 'erreur' ? 'sig-alerte' : 'sig-info',
      textContent: loc.phase === 'erreur' ? `Locaux non extraits : ${loc.message}` : 'Extraction des locaux en cours…' }));
  }
  noeuds.push(...(actions.complements || []));

  if (pos) {
    const etat = pos.statut === 'positionnee'
      ? el('p', { className: `sig-etiquette sig-etiquette--${pos.precision === 'approchée' ? 'alerte' : 'ok'}`, textContent: pos.libelle })
      : el('p', { className: 'sig-etiquette sig-etiquette--erreur', textContent: 'Non positionnée' });
    noeuds.push(el('h4', { textContent: 'Positionnement' }), etat);
    if (pos.raison) noeuds.push(el('p', { className: 'sig-erreur', textContent: pos.raison }));
    noeuds.push(liste([
      ['Système', pos.systeme ? `${pos.systeme}${pos.systeme_source ? ` (${pos.systeme_source})` : ''}` : null],
      ['Centre (Lambert 93)', pos.centre_l93 ? `X ${nombre(pos.centre_l93[0])} · Y ${nombre(pos.centre_l93[1])}` : null],
      ['Rotation', pos.repere ? `${nombre(Math.round(pos.repere.rotation_deg * 1000) / 1000)}°` : null],
      ['Terrain (RGE ALTI)', pos.altitude_terrain != null ? `${nombre(pos.altitude_terrain)} m NGF` : null],
      ['Hauteurs / terrain', pos.hauteurs_affichees ? `${nombre(pos.hauteurs_affichees[0])} m → ${nombre(pos.hauteurs_affichees[1])} m` : null],
      ['Calage vertical', pos.calage_vertical],
    ]));
    if (pos.avertissements?.length) {
      noeuds.push(el('ul', { className: 'sig-avertissements' }, ...pos.avertissements.map((t) => el('li', { textContent: t }))));
    }
  } else {
    const e = m.etat;
    noeuds.push(el('p', { className: e.phase === 'erreur' ? 'sig-erreur' : 'sig-info',
      textContent: e.phase === 'erreur' ? e.message : `${PHASES[e.phase]}${e.progression != null ? ` (${e.progression} %)` : ''}` }));
  }
  noeuds.push(el('h4', { textContent: 'Fichier' }), liste([
    ['Fichier', m.fichier],
    ['Taille', `${nombre(m.taille_mo)} Mo`],
    ['Schéma', meta.schema],
    ['Projet', meta.projet],
    ['Application', meta.application],
    ['Date', meta.date ? dateCourte(meta.date) : null],
    ['Unité', meta.unite_longueur],
    ['Objets 3D', objets3d(m)],
    ['Modèle 3D', m.positionnement?.affichage ? tailleModele(m.positionnement.affichage) : null],
    ['Locaux (IfcSpace)', meta.nb_locaux != null ? nombre(meta.nb_locaux) : null],
    ['Traitement', m.duree_traitement_s != null ? `${nombre(m.duree_traitement_s)} s` : null],
  ]));
  montrer(noeuds, fermeture);
}

function objets3d(m) {
  if (m.nb_objets == null) return null;
  const manquants = Object.entries(m.non_convertis || {});
  if (!manquants.length) return nombre(m.nb_objets);
  const detail = manquants.map(([type, n]) => `${nombre(n)} ${type}`).join(', ');
  return `${nombre(m.nb_objets)} — non convertis : ${detail}`;
}

function tailleModele(a) {
  const mtri = (n) => `${nombre(Math.round(n / 1e5) / 10)} M triangles`;
  return a.detail
    ? `${nombre(a.taille_mo)} Mo (${mtri(a.triangles)}) + ${nombre(a.taille_detail_mo)} Mo de petits éléments (${mtri(a.triangles_detail)})`
    : `${nombre(a.taille_mo)} Mo (${mtri(a.triangles)})`;
}

// ---------------------------------------------------------------- objet de maquette

export function afficherObjetIfc(fiche, maquette, fermeture, { zoomer } = {}) {
  if (!panneau) return;
  if (fiche.erreur) {
    montrer([entete('Objet indisponible', maquette?.nom, 'Objet IFC'), el('p', { className: 'sig-erreur', textContent: fiche.erreur })], fermeture);
    return;
  }
  const noeuds = [
    entete(fiche.nom || fiche.type_ifc, maquette?.nom, fiche.type_ifc),
    liste([['Type', fiche.type_objet], ['Étage', fiche.etage], ['Description', fiche.description], ['GlobalId', fiche.guid]]),
  ];
  if (zoomer) {
    const b = el('button', { type: 'button', className: 'sig-bouton', textContent: 'Zoomer sur l\'objet',
      title: 'Centre la vue sur l\'objet, à sa hauteur (sous-sol ou étage) : la rotation tourne autour de lui. Double-clic : idem.' });
    b.addEventListener('click', zoomer);
    noeuds.push(el('div', { className: 'sig-fiche__actions' }, b));
  }
  const psets = Object.entries(fiche.psets || {}).sort(([a], [b]) => a.localeCompare(b, 'fr'));
  noeuds.push(el('h4', { textContent: `Jeux de propriétés (${psets.length})` }));
  for (const [nom, valeurs] of psets) {
    const d = el('details', { className: 'sig-pset', open: psets.length <= 3 || /identification|SGP_/i.test(nom) },
      el('summary', { textContent: `${nom} (${Object.keys(valeurs).length})` }), tableau(valeurs));
    noeuds.push(d);
  }
  if (fiche.anomalie) noeuds.push(el('p', { className: 'sig-alerte', textContent: fiche.anomalie }));
  montrer(noeuds, fermeture);
}

// ---------------------------------------------------------------- local (IfcSpace)

const LIBELLES_CODES = {
  SGP_DI_Sous_Ouvrage: 'Code local', SGP_DI_Intitule_Typologie_Espace: 'Typologie', SGP_DI_Typologie_Espace: 'Code typologie',
  SGP_DI_Niveau: 'Code niveau', SGP_DI_Intitule_Niveau: 'Niveau', SGP_DI_Ouvrage: 'Ouvrage', SGP_DI_Intitule_Ouvrage: 'Intitulé ouvrage',
  SGP_DI_Batiment: 'Bâtiment', SGP_DI_Intitule_Batiment: 'Intitulé bâtiment', SGP_DI_Incrementation: 'Numéro',
};

export function afficherLocal(l, maquette, { couleur, chargerProprietes }, fermeture) {
  if (!panneau) return;
  const pastille = el('span', { className: 'sig-legende__pastille' });
  pastille.style.background = couleur;
  const nf = (v) => v.toLocaleString('fr-FR', { maximumFractionDigits: 2 });
  const codes = Object.entries(l.codes || {}).map(([k, v]) => [LIBELLES_CODES[k] || k, v]);
  const zone = el('div');
  const b = el('button', { type: 'button', className: 'sig-bouton', textContent: 'Propriétés IFC complètes' });
  b.addEventListener('click', async () => {
    b.disabled = true;
    zone.replaceChildren(el('p', { className: 'sig-info', textContent: 'Chargement…' }));
    try {
      const f = await chargerProprietes();
      const psets = Object.entries(f.psets || {}).sort(([x], [y]) => x.localeCompare(y, 'fr'));
      zone.replaceChildren(...psets.map(([nom, valeurs]) => el('details', { className: 'sig-pset' },
        el('summary', { textContent: `${nom} (${Object.keys(valeurs).length})` }), tableau(valeurs))));
    } catch (e) {
      zone.replaceChildren(el('p', { className: 'sig-erreur', textContent: e.message }));
    }
  });
  montrer([
    entete(l.code || l.nom || 'Local', maquette?.nomCourt || maquette?.nom, 'Local (IfcSpace)'),
    el('p', { className: 'sig-local__typologie' }, pastille, ` ${l.typologie || l.nom_long || ''}`),
    liste([
      ['Nom', l.nom], ['Nom long', l.nom_long], ['Niveau', [l.niveau, l.intitule_niveau].filter(Boolean).join(' · ')],
      ['Surface', l.surface != null ? `${nf(l.surface)} m² (${l.surface_source})` : null],
      ['Hauteurs / terrain', l.hauteurs ? `${nf(l.hauteurs[0])} m → ${nf(l.hauteurs[1])} m` : null],
      ['Centre (Lambert 93)', l.x_l93 != null ? `X ${nf(l.x_l93)} · Y ${nf(l.y_l93)}` : null],
      ['GlobalId', l.guid],
    ]),
    el('h4', { textContent: `Données d'identification (${codes.length})` }),
    codes.length ? liste(codes) : el('p', { className: 'sig-info', textContent: 'Aucun code SGP_DI_*.' }),
    el('div', { className: 'sig-fiche__actions' }, b), zone,
  ], fermeture);
}

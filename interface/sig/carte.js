// Carte SIG + BIM : MapLibre GL sur les fonds IGN déclarés dans config/sig.yaml (servis par /api/sig/config/carte),
// couches SIG (fichiers, services Esri), maquettes IFC en 3D dans la carte, volets d'outils et de propriétés.
// Aucune URL ni coordonnée en dur : tout vient de la configuration du serveur.
import { Map as CarteMapLibre, ScaleControl } from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import './carte.css';
import { lire } from './api.js';
import { GestionnaireCouches } from './couches.js';
import { sectionMaquette, sectionObjet } from './croisement.js';
import { fermerFiche, monterFiche } from './fiche.js';
import { versLambert93 } from './lambert93.js';
import { GestionnaireLocaux } from './locaux.js';
import { GestionnaireMaquettes } from './maquettes.js';
import { auSol } from './camera.js';
import { GestionnaireRelief, idsRelief, styleRelief } from './relief.js';
import { sectionFiltres } from './filtres.js';
import { bouton, creerCoque, el, icone } from './ui.js';
import { CoucheMaquettes3D } from './vue3d.js';

const SEUIL_ECHECS_FOND = 3;   // tuiles en échec d'affilée avant d'annoncer le fond indisponible
const INCLINAISON_3D = 60;     // inclinaison (degrés) du bouton « 3D »
const INCLINAISON_MAX = 85;

function bandeau(racine) {
  const b = el('div', { className: 'sig-bandeau', role: 'status' });
  b.hidden = true;
  racine.append(b);
  return {
    montrer(texte, erreur = false) {
      b.textContent = texte;
      b.classList.toggle('sig-bandeau--erreur', erreur);
      b.hidden = false;
    },
    cacher() { b.hidden = true; },
  };
}

function styleInitial(cfg) {
  const sources = {};
  const layers = [{ id: 'sig-vide', type: 'background', paint: { 'background-color': '#eef0f2' } }];
  for (const f of cfg.fonds) {
    sources[`fond-${f.id}`] = { type: 'raster', tiles: [f.url], tileSize: 256, attribution: f.attribution, maxzoom: 19 };
    layers.push({ id: `fond-${f.id}`, type: 'raster', source: `fond-${f.id}`,
      layout: { visibility: f.id === cfg.fond_defaut ? 'visible' : 'none' } });
  }
  const style = { version: 8, sources, layers };
  styleRelief(cfg.relief, style);   // altitudes et ombrage, sous les couches SIG
  if (cfg.glyphes) style.glyphs = cfg.glyphes;   // police des étiquettes (codes des locaux, noms des maquettes)
  return style;
}

// ---------------------------------------------------------------- volets simples

function voletFonds(conteneur, cfg, choisir) {
  const groupe = el('div', { className: 'sig-galerie' });
  for (const f of [...cfg.fonds, { id: '', libelle: 'Aucun fond' }]) {
    const radio = el('input', { type: 'radio', name: 'sig-fond', value: f.id, checked: f.id === cfg.fond_defaut });
    radio.addEventListener('change', () => choisir(f.id));
    groupe.append(el('label', { className: 'sig-galerie__choix' }, radio,
      el('span', { className: `sig-galerie__vignette sig-galerie__vignette--${f.id || 'aucun'}` }),
      el('span', { textContent: f.libelle })));
  }
  conteneur.append(groupe, el('p', { className: 'sig-info', textContent: 'Fonds de la Géoplateforme IGN, appelés par le navigateur.' }));
}

function voletVue3d(conteneur, carte, c3d, maquettes, appliquerTransparence) {
  const ligne = (libelle, ...controles) => el('div', { className: 'sig-reglage' }, el('span', { className: 'sig-reglage__libelle', textContent: libelle }), ...controles);

  const transparence = el('input', { type: 'range', min: 0, max: 0.9, step: 0.05, value: 0 });
  const valT = el('output', { textContent: '0 %' });
  transparence.addEventListener('input', () => {
    appliquerTransparence(Number(transparence.value));
    valT.textContent = `${Math.round(Number(transparence.value) * 100)} %`;
  });

  const coupeActive = el('input', { type: 'checkbox' });
  const coupe = el('input', { type: 'range', min: -40, max: 40, step: 0.5, value: 0, disabled: true });
  const valC = el('output', { textContent: '—' });
  const majBornes = () => {
    const h = maquettes.hauteurs();
    if (h) { coupe.min = h[0]; coupe.max = h[1]; }
  };
  const appliquerCoupe = () => {
    coupe.disabled = !coupeActive.checked;
    c3d.definirCoupe(coupeActive.checked ? Number(coupe.value) : null);
    valC.textContent = coupeActive.checked ? `${Number(coupe.value).toLocaleString('fr-FR')} m` : '—';
  };
  coupeActive.addEventListener('change', () => { majBornes(); appliquerCoupe(); });
  coupe.addEventListener('input', appliquerCoupe);

  const vues = el('div', { className: 'sig-groupe-boutons' });
  const vue = (texte, options) => {
    const b = el('button', { type: 'button', className: 'sig-bouton', textContent: texte });
    b.addEventListener('click', () => carte.easeTo({ ...options, duration: 800 }));
    vues.append(b);
  };
  vue('Dessus', { pitch: 0, bearing: 0 });
  vue('Oblique', { pitch: INCLINAISON_3D });
  vue('Rasante', { pitch: 80 });

  conteneur.append(
    el('section', { className: 'sig-section' }, el('h3', { textContent: 'Point de vue' }), vues,
      el('p', { className: 'sig-info', textContent: 'Clic droit + glisser (ou Ctrl + glisser) pour incliner et tourner la vue.' })),
    el('section', { className: 'sig-section' }, el('h3', { textContent: 'Sous-sol' }),
      ligne('Transparence du fond de carte', transparence, valT),
      el('p', { className: 'sig-info', textContent: 'Rend le fond translucide pour lire les ouvrages enterrés sous la ville.' })),
    el('section', { className: 'sig-section' }, el('h3', { textContent: 'Coupe horizontale' }),
      ligne('Couper au-dessus de', coupeActive, coupe, valC),
      el('p', { className: 'sig-info', textContent: 'Hauteur par rapport au terrain naturel (RGE ALTI). Les objets au-dessus sont masqués.' })),

    sectionFiltres(c3d),
  );
  return { majBornes };
}

// ---------------------------------------------------------------- contrôles de carte

function controles(zone, carte, cfg) {
  const boussole = bouton('boussole', 'Réorienter au nord', () => carte.easeTo({ bearing: 0, duration: 500 }));
  const b3d = el('button', { type: 'button', className: 'sig-bouton-icone sig-bouton-icone--texte', title: 'Basculer vue de dessus / vue 3D', textContent: '3D' });
  b3d.addEventListener('click', () => carte.easeTo({ pitch: carte.getPitch() > 5 ? 0 : INCLINAISON_3D, duration: 700 }));
  const groupe = el('div', { className: 'sig-controles' },
    el('div', { className: 'sig-controles__groupe' },
      bouton('plus', 'Zoom avant', () => carte.zoomIn()),
      bouton('moins', 'Zoom arrière', () => carte.zoomOut())),
    el('div', { className: 'sig-controles__groupe' }, boussole, b3d),
    el('div', { className: 'sig-controles__groupe' },
      bouton('accueil', 'Vue initiale', () => {
        auSol(carte);
        carte.flyTo({ center: cfg.centre_wgs84, zoom: cfg.zoom_initial, pitch: 0, bearing: 0 });
      })));
  zone.append(groupe);
  const maj = () => {
    boussole.firstChild.style.transform = `rotate(${-carte.getBearing()}deg)`;
    b3d.textContent = carte.getPitch() > 5 ? '2D' : '3D';
  };
  carte.on('rotate', maj);
  carte.on('pitch', maj);
  maj();
}

function piedDePage(pied, carte) {
  const coords = el('span', { className: 'sig-pied__coords', textContent: 'Lambert 93 —' });
  const vue = el('span', { className: 'sig-pied__vue' });
  pied.append(coords, vue);
  const nf = (v, d = 2) => v.toLocaleString('fr-FR', { minimumFractionDigits: d, maximumFractionDigits: d });
  carte.on('mousemove', (e) => {
    const [x, y] = versLambert93(e.lngLat.lng, e.lngLat.lat);
    const z = carte.getTerrain() ? carte.queryTerrainElevation(e.lngLat) : null;
    coords.textContent = `Lambert 93  X ${nf(x)}  Y ${nf(y)}   ·   WGS84 ${nf(e.lngLat.lat, 6)}° N ${nf(e.lngLat.lng, 6)}° E`
      + (z != null ? `   ·   Terrain ${nf(z, 1)} m NGF` : '');
  });
  const majVue = () => {
    vue.textContent = `Zoom ${nf(carte.getZoom(), 1)} · Inclinaison ${Math.round(carte.getPitch())}° · Orientation ${Math.round((carte.getBearing() + 360) % 360)}°`;
  };
  carte.on('move', majVue);
  majVue();
}

// ---------------------------------------------------------------- montage

export async function monterCarte(conteneur) {
  const coque = creerCoque(conteneur, { titre: 'Démonstrateur SIG · BIM', sousTitre: 'Carte, couches et maquettes IFC' });
  const message = bandeau(coque.zone);
  const g = coque.gauche;
  const d = coque.droite;
  const pCouches = g.ajouter('couches', 'Couches', 'couches');
  const pMaquettes = g.ajouter('maquettes', 'Maquettes', 'maquettes');
  const pLocaux = g.ajouter('locaux', 'Plan des locaux', 'plan');
  const pFonds = g.ajouter('fonds', 'Fonds de carte', 'fonds');
  const pRecherche = g.ajouter('recherche', 'Recherche', 'recherche');
  const pAnomalies = g.ajouter('anomalies', 'Anomalies', 'anomalies');
  const anomaliesCouches = el('div');
  const anomaliesMaquettes = el('div');
  pAnomalies.append(anomaliesMaquettes, anomaliesCouches);
  const pProprietes = d.ajouter('proprietes', 'Propriétés', 'proprietes');
  const pVue3d = d.ajouter('vue3d', 'Vue 3D', 'vue3d');
  monterFiche(pProprietes, { ouvrir: () => d.ouvrir('proprietes'), fermer: () => { if (d.actif === 'proprietes') d.ouvrir(null); } });

  let cfg;
  try {
    cfg = await lire('config/carte');
  } catch (e) {
    message.montrer(`Configuration de la carte indisponible : ${e.message}. Vérifiez que le serveur est lancé et config/sig.yaml.`, true);
    return null;
  }

  const divCarte = el('div', { className: 'sig-carte' });
  coque.zone.prepend(divCarte);
  const carte = new CarteMapLibre({
    container: divCarte,
    style: styleInitial(cfg),
    center: cfg.centre_wgs84,
    zoom: cfg.zoom_initial,
    maxPitch: INCLINAISON_MAX,
    centerClampedToGround: false,   // centre de la vue à la hauteur d'un objet en sous-sol ou en étage (camera.js)
    attributionControl: { compact: true },
    canvasContextAttributes: { antialias: true },
  });
  carte.addControl(new ScaleControl({ unit: 'metric' }), 'bottom-left');
  controles(coque.zone, carte, cfg);
  piedDePage(coque.pied, carte);
  coque.surRedimension(() => requestAnimationFrame(() => carte.resize()));

  // Fond indisponible : la carte reste utilisable, un message l'indique.
  let fondActif = cfg.fond_defaut;
  let echecs = 0;
  // Relief indisponible : la carte reste plate là où les altitudes manquent, un message l'indique.
  const sourcesRelief = new Set((cfg.relief?.sources || []).flatMap((s) => Object.values(idsRelief(s.id))));
  let echecsRelief = 0;
  carte.on('error', (e) => {
    if (e.sourceId && sourcesRelief.has(e.sourceId)) {
      echecsRelief += 1;
      if (echecsRelief === SEUIL_ECHECS_FOND) {
        console.warn('Relief :', e.error?.message);
        message.montrer(`Relief IGN indisponible (${e.error?.message || "service d'altitude injoignable"}). Le terrain reste plat là où les altitudes manquent.`);
      }
      return;
    }
    if (!e.sourceId || e.sourceId !== `fond-${fondActif}`) return;
    echecs += 1;
    if (echecs >= SEUIL_ECHECS_FOND) {
      message.montrer('Fond IGN indisponible (data.geopf.fr injoignable depuis ce poste ?). La carte reste utilisable sans fond.');
    }
  });
  carte.on('data', (e) => {
    if (sourcesRelief.has(e.sourceId) && e.tile?.state === 'loaded') echecsRelief = 0;
    if (e.sourceId === `fond-${fondActif}` && e.tile && e.tile.state === 'loaded') {
      echecs = 0;
      message.cacher();
    }
  });
  let transparenceSol = 0;
  const appliquerTransparence = (t) => {
    transparenceSol = t;
    for (const f of cfg.fonds) carte.setPaintProperty(`fond-${f.id}`, 'raster-opacity', 1 - t);
    carte.setPaintProperty('sig-vide', 'background-opacity', 1 - t);
  };
  voletFonds(pFonds, cfg, (id) => {
    for (const f of cfg.fonds) carte.setLayoutProperty(`fond-${f.id}`, 'visibility', f.id === id ? 'visible' : 'none');
    fondActif = id;
    echecs = 0;
    message.cacher();
    appliquerTransparence(transparenceSol);
  });

  const pastilleMaquettes = coque.pastille('Maquettes…');
  let maquettes = null;
  let locaux = null;
  const couches = new GestionnaireCouches(carte, { couches: pCouches, recherche: pRecherche, anomalies: anomaliesCouches },
    cfg.style_selection, {
      surAnomalies: () => majBadge(),
      complementsFiche: (couche, n) => sectionObjet(couche, n, {
        ouvrirMaquette: (id) => maquettes.ouvrirFiche(id, true),
        ouvrirLocal: (id, guid) => locaux.ouvrir(maquettes.maquettes.get(id), guid),
        nomDe: (m) => { const x = maquettes.maquettes.get(m.id); return x ? `${x.ouvrage} › ${x.nomCourt}` : m.nom; },
      }),
    });
  const c3d = new CoucheMaquettes3D();
  locaux = new GestionnaireLocaux(carte, c3d, {
    panneau: pLocaux,
    ouvrirVolet: () => g.ouvrir('locaux'),
    police: cfg.police,
    hauteurCoupe: cfg.hauteur_coupe_plan_m,
  });
  maquettes = new GestionnaireMaquettes(carte, c3d, {
    panneau: pMaquettes,
    anomalies: anomaliesMaquettes,
    complementsFiche: (m) => sectionMaquette(m, { couches, ouvrirObjetSig: (id, n) => couches.ouvrirObjet(id, n, true) }),
    planLocaux: (m) => locaux.ouvrir(m),
    surSelection: () => couches.deselectionner(false),
    surEtat: ({ total, enCours, courant }) => {
      pastilleMaquettes.textContent = enCours
        ? `Maquettes : ${total - enCours}/${total} traitée(s)${courant ? ` (en cours ${courant.etat.progression ?? 0} %)` : ''}`
        : `Maquettes : ${total} traitée(s)`;
      pastilleMaquettes.classList.toggle('sig-pastille-etat--actif', enCours > 0);
      g.badge('maquettes', enCours, false);
      majBadge();
    },
  });
  const vue3d = voletVue3d(pVue3d, carte, c3d, maquettes, appliquerTransparence);
  // Relief : maquettes en altitude NGF quand il est affiché, centre de la vue ramené au sol
  const relief = cfg.relief && new GestionnaireRelief(carte, cfg.relief, {
    surChangement: (actif) => { c3d.definirRelief(actif); auSol(carte); },
  });
  if (relief) {
    pFonds.append(relief.section({ aTravers: c3d.aTravers, surATravers: (v) => c3d.definirATravers(v) }));
    relief.actif = cfg.relief.actif;
  }
  function majBadge() {
    const n = (couches.inventaire?.anomalies.length || 0) + (maquettes.nbAnomalies || 0);
    g.badge('anomalies', n, true);
  }
  d.panneaux.get('vue3d').bouton.addEventListener('click', () => vue3d.majBornes());

  // Répartition des clics : local du plan affiché, objet de maquette 3D, objet SIG, emprise de maquette.
  carte.on('click', async (e) => {
    if (await maquettes.deplierGroupe(e.point)) return;   // groupe de maquettes (vue éloignée) : zoom
    const local = c3d.localSous(e.point);
    if (local) {
      couches.deselectionner(false);
      c3d.deselectionner();
      locaux.selectionner(local.guid);
      return;
    }
    const cible3d = c3d.objetSous(e.point);
    if (cible3d) {
      couches.deselectionner(false);
      maquettes.ouvrirObjet(cible3d);
      return;
    }
    const sig = couches.objetSous(e.point);
    if (sig) {
      maquettes.deselectionner();
      couches.ouvrirObjet(sig.id, sig.n);
      return;
    }
    const emprise = maquettes.empriseSous(e.point);
    if (emprise) {
      couches.deselectionner(false);
      maquettes.ouvrirFiche(emprise);
      return;
    }
    couches.deselectionner(false);
    couches.effacerResultats();
    maquettes.deselectionner();
    locaux.deselectionner();
    fermerFiche();
  });
  // Double-clic sur un objet de maquette : vue centrée sur lui, à sa hauteur (au lieu du zoom de la carte).
  carte.on('dblclick', (e) => {
    const cible3d = c3d.objetSous(e.point);
    if (!cible3d) return;
    e.preventDefault();
    if (c3d.selection?.guid !== cible3d.guid) maquettes.ouvrirObjet(cible3d);
    maquettes.zoomerObjet();
  });
  carte.on('mousemove', (e) => {
    const touche = couches.objetSous(e.point) || maquettes.empriseSous(e.point)
      || carte.queryRenderedFeatures(e.point, { layers: ['sig-maquettes-groupes'].filter((c) => carte.getLayer(c)) }).length;
    carte.getCanvas().style.cursor = touche ? 'pointer' : '';
  });

  const demarrer = async () => {
    if (relief) { relief.appliquer(); relief.majCase(); }
    await couches.demarrer();
    await maquettes.demarrer(cfg.police);
  };
  if (carte.loaded()) demarrer();
  else carte.once('load', demarrer);
  g.ouvrir('couches');
  return carte;
}

// Carte SIG : MapLibre GL sur les fonds IGN déclarés dans config/sig.yaml (servis par /api/sig/config/carte),
// panneau latéral (fonds, couches, recherche, anomalies) et fiche des objets.
// Aucune URL ni coordonnée en dur : tout vient de la configuration du serveur.
import { Map as CarteMapLibre, NavigationControl, ScaleControl } from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import './carte.css';
import { lire } from './api.js';
import { GestionnaireCouches } from './couches.js';
import { monterFiche } from './fiche.js';

const SEUIL_ECHECS_FOND = 3; // tuiles en échec d'affilée avant d'annoncer le fond indisponible

function bandeau(racine) {
  const el = document.createElement('div');
  el.className = 'sig-bandeau';
  el.hidden = true;
  racine.appendChild(el);
  return {
    montrer(texte, erreur = false) {
      el.textContent = texte;
      el.classList.toggle('sig-bandeau--erreur', erreur);
      el.hidden = false;
    },
    cacher() { el.hidden = true; },
  };
}

function styleInitial(cfg) {
  const sources = {};
  const layers = [{ id: 'sig-vide', type: 'background', paint: { 'background-color': '#f2efe9' } }];
  for (const f of cfg.fonds) {
    sources[`fond-${f.id}`] = { type: 'raster', tiles: [f.url], tileSize: 256, attribution: f.attribution };
    layers.push({
      id: `fond-${f.id}`,
      type: 'raster',
      source: `fond-${f.id}`,
      layout: { visibility: f.id === cfg.fond_defaut ? 'visible' : 'none' },
    });
  }
  return { version: 8, sources, layers };
}

function selecteurFonds(cfg, choisir) {
  const boite = document.createElement('details');
  boite.className = 'sig-bloc';
  boite.open = true;
  boite.innerHTML = '<summary><h2>Fond de carte</h2></summary>';
  const groupe = document.createElement('div');
  groupe.className = 'sig-fonds';
  for (const f of [...cfg.fonds, { id: '', libelle: 'Aucun' }]) {
    const label = document.createElement('label');
    const radio = Object.assign(document.createElement('input'), {
      type: 'radio', name: 'sig-fond', value: f.id, checked: f.id === cfg.fond_defaut,
    });
    radio.addEventListener('change', () => choisir(f.id));
    label.append(radio, ` ${f.libelle}`);
    groupe.appendChild(label);
  }
  boite.appendChild(groupe);
  return boite;
}

export async function monterCarte(conteneur) {
  const racine = document.createElement('div');
  racine.className = 'sig-racine';
  const cote = document.createElement('aside');
  cote.className = 'sig-panneau';
  cote.innerHTML = '<header class="sig-panneau__tete"><h1>Carte SIG</h1></header>';
  const zone = document.createElement('div');
  zone.className = 'sig-zone';
  racine.append(cote, zone);
  conteneur.appendChild(racine);
  const message = bandeau(zone);

  let cfg;
  try {
    cfg = await lire('config/carte');
  } catch (e) {
    message.montrer(`Configuration de la carte indisponible : ${e.message}. Vérifiez que le serveur est lancé et config/sig.yaml.`, true);
    return null;
  }

  const divCarte = document.createElement('div');
  divCarte.className = 'sig-carte';
  zone.prepend(divCarte);
  monterFiche(zone);

  const carte = new CarteMapLibre({
    container: divCarte,
    style: styleInitial(cfg),
    center: cfg.centre_wgs84,
    zoom: cfg.zoom_initial,
    attributionControl: { compact: false },
  });
  carte.addControl(new NavigationControl(), 'top-right');
  carte.addControl(new ScaleControl({ unit: 'metric' }), 'bottom-left');

  // Fond indisponible : la carte reste utilisable, un message l'indique.
  let fondActif = cfg.fond_defaut;
  let echecs = 0;
  carte.on('error', (e) => {
    if (!e.sourceId || e.sourceId !== `fond-${fondActif}`) return;
    echecs += 1;
    if (echecs >= SEUIL_ECHECS_FOND) {
      message.montrer('Fond IGN indisponible (data.geopf.fr injoignable depuis ce poste ?). La carte reste utilisable sans fond.');
    }
  });
  carte.on('data', (e) => {
    if (e.sourceId === `fond-${fondActif}` && e.tile && e.tile.state === 'loaded') {
      echecs = 0;
      message.cacher();
    }
  });

  const panneauCouches = document.createElement('div');
  cote.append(selecteurFonds(cfg, (id) => {
    for (const f of cfg.fonds) {
      carte.setLayoutProperty(`fond-${f.id}`, 'visibility', f.id === id ? 'visible' : 'none');
    }
    fondActif = id;
    echecs = 0;
    message.cacher();
  }), panneauCouches);

  const gestionnaire = new GestionnaireCouches(carte, panneauCouches, cfg.style_selection);
  if (carte.loaded()) gestionnaire.demarrer();
  else carte.once('load', () => gestionnaire.demarrer());

  return carte;
}

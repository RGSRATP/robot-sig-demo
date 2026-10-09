// Données en temps réel (config/sig.yaml, section « temps_reel ») : le serveur interroge les services (Vélib',
// Hub'Eau, Open-Meteo, IDFM) et garde la dernière réponse ; l'interface la redemande à chaque période, l'affiche en
// points sur la carte et montre la date des données. Une source en panne garde sa dernière réponse, avec le message.
import { dateCourte, lire } from './api.js';
import { el } from './ui.js';

const ICI = (id) => `sig-tr-${id}`;

/** Couches MapLibre d'une source selon son type : pastilles colorées et étiquette de la valeur. */
function couches(s, police) {
  const src = ICI(s.id);
  // étiquette sous la pastille, ou dessus (centre) pour la météo
  const texte = (expr, minzoom, centre = false) => ({ id: `${src}-texte`, type: 'symbol', source: src, minzoom,
    layout: { 'text-field': expr, 'text-font': [police], 'text-size': 11, 'text-offset': centre ? [0, 0] : [0, 1.3],
      'text-allow-overlap': centre },
    paint: { 'text-color': '#1f2328', 'text-halo-color': '#ffffff', 'text-halo-width': 1.5 } });
  const cercle = (paint) => ({ id: `${src}-points`, type: 'circle', source: src,
    paint: { 'circle-stroke-color': '#ffffff', 'circle-stroke-width': 1.5, ...paint } });
  if (s.type === 'vigicrues_vigilance') {   // tronçons de cours d'eau colorés par niveau de vigilance
    return [{ id: `${src}-points`, type: 'line', source: src, layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': ['match', ['get', 'niveau'], 1, COULEURS_VIGILANCE[0], 2, COULEURS_VIGILANCE[1], 3,
        COULEURS_VIGILANCE[2], 4, COULEURS_VIGILANCE[3], '#adb5bd'],
      'line-width': ['interpolate', ['linear'], ['zoom'], 8, ['+', 2, ['get', 'niveau']], 14, ['+', 5, ['*', 2, ['get', 'niveau']]]],
      'line-opacity': 0.8 } },
    { id: `${src}-texte`, type: 'symbol', source: src, minzoom: 9,
      layout: { 'symbol-placement': 'line', 'text-field': ['concat', ['get', 'troncon'], ' · ', ['get', 'vigilance']],
        'text-font': [police], 'text-size': 11 },
      paint: { 'text-color': '#1f2328', 'text-halo-color': '#ffffff', 'text-halo-width': 1.5 } }];
  }
  if (s.type === 'gbfs') {
    return [cercle({
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 11, 2.5, 16, 7],
      'circle-color': ['case', ['!', ['get', 'en_service']], '#adb5bd',
        ['step', ['coalesce', ['get', 'velos'], 0], '#e03131', 1, '#f59f00', 5, '#2f9e44']],
    }), texte(['to-string', ['get', 'velos']], 15)];
  }
  if (s.type === 'hubeau_hydrometrie') {
    return [cercle({ 'circle-radius': 6, 'circle-color': '#1c7ed6' }),
      texte(['concat', ['number-format', ['get', 'hauteur_m'], { 'max-fraction-digits': 2, locale: 'fr-FR' }], ' m'], 10)];
  }
  if (s.type === 'open_meteo') {
    return [cercle({
      'circle-radius': 13, 'circle-opacity': 0.85,
      'circle-color': ['interpolate', ['linear'], ['coalesce', ['get', 'temperature_c'], 10],
        -5, '#4dabf7', 5, '#a5d8ff', 12, '#ffe066', 20, '#ffa94d', 30, '#e03131'],
    }), texte(['concat', ['to-string', ['round', ['coalesce', ['get', 'temperature_c'], 0]]], '°'], 0, true)];
  }
  // IDFM : prochains passages en gare
  return [cercle({ 'circle-radius': 9, 'circle-color': ['case', ['has', 'erreur'], '#adb5bd', '#ae3ec9'] }),
    texte(['case', ['has', 'prochain_min'], ['concat', ['get', 'gare'], ' · ', ['to-string', ['get', 'prochain_min']], ' min'], ['get', 'gare']], 11)];
}

const COULEURS_VIGILANCE = ['#2f9e44', '#fcc419', '#f76707', '#e03131'];   // vert, jaune, orange, rouge (Vigicrues)
const LEGENDES = {
  vigicrues_vigilance: [[COULEURS_VIGILANCE[0], 'vert : pas de vigilance'], [COULEURS_VIGILANCE[1], 'jaune : risque de crue'],
    [COULEURS_VIGILANCE[2], 'orange : crue importante'], [COULEURS_VIGILANCE[3], 'rouge : crue majeure']],
  gbfs: [['#2f9e44', '5 vélos ou plus'], ['#f59f00', '1 à 4 vélos'], ['#e03131', 'aucun vélo'], ['#adb5bd', 'station fermée']],
  hubeau_hydrometrie: [['#1c7ed6', "hauteur d'eau (m)"]],
  open_meteo: [['#4dabf7', '≤ 0 °C'], ['#ffe066', '12 °C'], ['#e03131', '≥ 30 °C']],
  idfm_prochains_passages: [['#ae3ec9', 'départ dans … min'], ['#adb5bd', 'gare en erreur']],
};

export class GestionnaireTempsReel {
  constructor(carte, { panneau, police }) {
    this.carte = carte;
    this.panneau = panneau;
    this.police = police;
    this.sources = new Map();   // id -> { cfg, visible, donnees, minuterie, zone }
  }

  async demarrer() {
    let r;
    try {
      r = await lire('temps-reel');
    } catch (e) {
      this.panneau.replaceChildren(el('p', { className: 'sig-erreur', textContent: `Temps réel indisponible : ${e.message}` }));
      return;
    }
    for (const s of r.sources) {
      this.carte.addSource(ICI(s.id), { type: 'geojson', data: { type: 'FeatureCollection', features: [] }, attribution: s.attribution });
      for (const c of couches(s, this.police)) this.carte.addLayer({ ...c, layout: { ...(c.layout || {}), visibility: 'none' } });
      this.sources.set(s.id, { cfg: s, visible: false, donnees: null, minuterie: null });
    }
    this._rendre();
    for (const s of r.sources) if (s.visible) this.basculer(s.id, true);
    setInterval(() => { for (const id of this.sources.keys()) this._majEtat(id); }, 30000);   // « il y a … min »
  }

  basculer(id, visible) {
    const s = this.sources.get(id);
    s.visible = visible;
    for (const suffixe of ['points', 'texte']) this.carte.setLayoutProperty(`${ICI(id)}-${suffixe}`, 'visibility', visible ? 'visible' : 'none');
    clearInterval(s.minuterie);
    if (visible) {
      this.actualiser(id);
      s.minuterie = setInterval(() => this.actualiser(id), s.cfg.periode_s * 1000);
    }
    this._majEtat(id);
  }

  async actualiser(id) {
    const s = this.sources.get(id);
    s.chargement = true;
    this._majEtat(id);
    try {
      s.donnees = await lire(`temps-reel/${id}`);
      this.carte.getSource(ICI(id)).setData(s.donnees);
      s.erreurLecture = null;
    } catch (e) {
      s.erreurLecture = e.message;
    }
    s.chargement = false;
    this._majEtat(id);
  }

  /** Point temps réel sous le point écran : { source, proprietes } ou null. */
  objetSous(point) {
    const couchesVisibles = [...this.sources.values()].filter((s) => s.visible).map((s) => `${ICI(s.cfg.id)}-points`);
    if (!couchesVisibles.length) return null;
    const f = this.carte.queryRenderedFeatures(point, { layers: couchesVisibles })[0];
    if (!f) return null;
    const s = this.sources.get(f.source.slice('sig-tr-'.length));
    // MapLibre rend les tableaux des propriétés sous forme de texte JSON
    const proprietes = Object.fromEntries(Object.entries(f.properties).map(([k, v]) => {
      if (typeof v === 'string' && /^[[{]/.test(v)) { try { return [k, JSON.parse(v)]; } catch { /* texte */ } }
      return [k, v];
    }));
    return { source: { ...s.cfg, date_donnees: s.donnees?.date_donnees, date_maj: s.donnees?.date_maj }, proprietes };
  }

  _majEtat(id) {
    const s = this.sources.get(id);
    const zone = s?.zone;
    if (!zone) return;
    const d = s.donnees;
    const lignes = [];
    if (s.visible) {
      if (s.chargement && !d) lignes.push('chargement…');
      if (d && (d.features.length || !d.erreur)) {
        const age = d.date_maj ? Math.round((Date.now() - new Date(d.date_maj)) / 60000) : null;
        lignes.push(`${d.features.length} ${s.cfg.type === 'vigicrues_vigilance' ? 'tronçon(s)' : 'point(s)'} · données du ${dateCourte(d.date_donnees || d.date_maj)}`
          + (age != null ? ` · lu il y a ${age < 1 ? "moins d'une" : age} min` : ''));
      }
    }
    const erreur = s.erreurLecture || d?.erreur;
    if (erreur) lignes.push(`⚠ ${erreur}`);
    for (const a of d?.avertissements || []) lignes.push(`⚠ ${a}`);
    zone.textContent = lignes.join(' · ');
    zone.classList.toggle('sig-alerte', Boolean(erreur));
  }

  _rendre() {
    const blocs = [...this.sources.values()].map((s) => {
      const c = s.cfg;
      const coche = el('input', { type: 'checkbox', checked: s.visible });
      coche.addEventListener('change', () => this.basculer(c.id, coche.checked));
      s.zone = el('p', { className: 'sig-info' });
      const cle = c.cle && !c.cle.presente
        ? el('p', { className: 'sig-alerte', textContent: `Clé absente : ajoutez la ligne ${c.cle.variable}=votre_clé dans le fichier .env `
          + 'à la racine du projet (voir DEMARRAGE.md).' })
        : null;
      return el('section', { className: 'sig-section' },
        el('label', { className: 'sig-option' }, coche, ` ${c.libelle}`),
        el('ul', { className: 'sig-legende' }, ...(LEGENDES[c.type] || []).map(([coul, t]) => el('li', {},
          el('span', { className: 'sig-legende__pastille', style: `background:${coul}` }), el('span', { textContent: t })))),
        cle, s.zone,
        el('p', { className: 'sig-info', textContent: `Source : ${c.attribution || '—'} · actualisé toutes les ${c.periode_s >= 120 ? `${Math.round(c.periode_s / 60)} min` : `${c.periode_s} s`}` }));
    });
    this.panneau.replaceChildren(
      el('p', { className: 'sig-info', textContent: 'Données ouvertes interrogées par le serveur à travers le proxy du poste, '
        + 'puis actualisées automatiquement tant que la case est cochée. Clic sur un point : sa fiche.' }), ...blocs);
    for (const id of this.sources.keys()) this._majEtat(id);
  }
}

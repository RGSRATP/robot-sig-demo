// Vues thématiques (config/sig.yaml, section « vues ») : onglets du bandeau qui règlent d'un coup les couches SIG, le
// temps réel, le relief, les bâtiments, le nuage LiDAR, le cadrage et la couleur des maquettes selon un thème
// (altitude du point bas, carbone, catégorie d'ouvrage), avec une légende sur la carte. Adresse directe : #vue=<id>.
import { lire } from './api.js';
import { el } from './ui.js';

const GRIS = '#adb5bd';
// Suites d'une seule teinte, du clair au foncé (5 classes) ; catégories : 3 premières couleurs d'une palette validée
// pour les daltonismes (méthode dataviz, toutes paires).
const SUITE_BLEUE = ['#08306b', '#2171b5', '#4f9bd1', '#9ecae1', '#d4e6f4'];   // du plus bas (foncé) au plus haut
const SUITE_CARBONE = ['#f3e3ee', '#e2b6d3', '#c97aa9', '#a2457e', '#6b1d52'];   // du plus faible au plus fort
const CATEGORIES = ['#2a78d6', '#eb6834', '#1baf7a', '#4a3aa7', '#e34948'];
const RELANCE_CARBONE_MS = 4000;

const nf = (v, d = 0) => Number(v).toLocaleString('fr-FR', { maximumFractionDigits: d });

/** Motif de chemin (« * » : n'importe quelle suite de caractères), insensible à la casse. */
function motif(m) {
  const re = m.toLowerCase().replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*').replace(/\?/g, '.');
  return new RegExp(`^${re}$`);
}

/** Arrondi à deux chiffres significatifs (bornes lisibles : 120, 3 700…). */
function arrondi(x) {
  if (!x) return 0;
  const p = 10 ** (Math.floor(Math.log10(Math.abs(x))) - 1);
  return Math.round(x / p) * p;
}

/** Bornes de 5 classes d'effectifs égaux (quantiles), arrondies à deux chiffres significatifs. */
function bornes(valeurs) {
  const v = [...valeurs].sort((a, b) => a - b);
  if (!v.length) return [];
  const b = [1, 2, 3, 4].map((k) => arrondi(v[Math.floor((k * v.length) / 5)]));
  return [...new Set(b)].filter((x, i, t) => i === 0 || x > t[i - 1]);
}

function expressionSuite(b, couleurs) {
  if (!b.length) return ['case', ['has', 'v'], couleurs[2], GRIS];
  const step = ['step', ['get', 'v'], couleurs[0]];
  b.forEach((x, i) => step.push(x, couleurs[i + 1]));
  return ['case', ['has', 'v'], step, GRIS];
}

function classesLegende(b, couleurs, unite, d = 0) {
  const lignes = [];
  for (let i = 0; i <= b.length; i += 1) {
    const texte = i === 0 ? `moins de ${nf(b[0], d)} ${unite}` : i === b.length ? `${nf(b[i - 1], d)} ${unite} et plus`
      : `${nf(b[i - 1], d)} à ${nf(b[i], d)} ${unite}`;
    lignes.push([couleurs[i], texte]);
  }
  return lignes;
}

export class GestionnaireVues {
  /** reglages : { carte, coque, gauche, couches, tempsReel, relief, batiments, lidar, maquettes } */
  constructor(vues, reglages) {
    this.vues = vues;
    this.r = reglages;
    this.active = null;
    this._jeton = 0;
    // les onglets sont montrés dès l'ouverture ; une vue n'est appliquée qu'une fois couches et maquettes chargées
    this.pret = new Promise((r) => { this.signalerPret = () => { this._pret = true; r(); }; });
  }

  installer() {
    if (!this.vues.length) return;
    this.onglets = new Map(this.vues.map((v) => {
      const b = el('button', { type: 'button', className: 'sig-vues__onglet', textContent: v.libelle, title: v.description || v.libelle });
      b.addEventListener('click', () => { window.location.hash = `vue=${v.id}`; });
      return [v.id, b];
    }));
    const nav = el('nav', { className: 'sig-vues', ariaLabel: 'Vues thématiques' }, ...this.onglets.values());
    this.r.coque.entete.querySelector('.sig-entete__marque').after(nav);
    this.r.coque.entete.classList.add('sig-entete--vues');
    this.legende = el('aside', { className: 'sig-legende-vue', hidden: true });
    this.r.coque.zone.append(this.legende);
    window.addEventListener('hashchange', () => this._depuisAdresse());
    const v = this._vueAdresse();
    this._marquer(v ? v.id : this.vues[0].id);   // à l'ouverture sans #vue= : réglages d'origine, sans les imposer
    if (v) this.appliquer(v);
  }

  _vueAdresse() {
    const id = new URLSearchParams(window.location.hash.slice(1)).get('vue');
    return this.vues.find((x) => x.id === id) || null;
  }

  _depuisAdresse() {
    const v = this._vueAdresse();
    if (v) this.appliquer(v);
  }

  _marquer(id) {
    this.active = id;
    for (const [i, b] of this.onglets) {
      b.classList.toggle('sig-vues__onglet--active', i === id);
      b.setAttribute('aria-current', i === id ? 'page' : 'false');
    }
  }

  async appliquer(v) {
    const r = this.r;
    const jeton = (this._jeton += 1);
    this._marquer(v.id);
    if (!this._pret) {
      this.legende.hidden = false;
      this.legende.replaceChildren(el('h3', { className: 'sig-legende-vue__titre', textContent: v.libelle }),
        el('p', { className: 'sig-legende-vue__texte', textContent: 'Chargement des couches et des maquettes…' }));
      await this.pret;
      if (jeton !== this._jeton) return;
    }
    if ('couches' in v) r.couches.definirVisibles(v.couches === 'toutes' ? null : v.couches);
    if ('temps_reel' in v) r.tempsReel.definirVisibles(v.temps_reel === 'toutes' ? null : v.temps_reel);
    if ('relief' in v && r.relief) r.relief.definirActif(v.relief);
    if ('batiments' in v && r.batiments) r.batiments.definirVisible(v.batiments);
    if ('lidar' in v && r.lidar && r.lidar.actif !== v.lidar) r.lidar.activer(v.lidar);
    if (v.camera && v.camera !== 'maquettes') {
      const c = v.camera;
      r.carte.flyTo({ center: c.centre_wgs84, zoom: c.zoom, pitch: c.inclinaison ?? 0, bearing: c.orientation ?? 0, duration: 1200 });
    }
    r.gauche.ouvrir(v.theme ? null : 'couches');   // vue thématique : la carte en grand, la légende explique
    if (!v.theme) {
      r.maquettes.definirTheme(null);
      this.legende.hidden = true;
      return;
    }
    await this._rafraichirTheme(v, jeton);
  }

  async _rafraichirTheme(v, jeton) {
    const theme = await this._theme(v, jeton);
    if (jeton !== this._jeton) return;   // autre vue choisie entre-temps
    const premier = this.r.maquettes.theme?.vue !== v.id;
    if (theme.theme) theme.theme.vue = v.id;
    this.r.maquettes.definirTheme(theme.theme);
    this._legende(v, theme);
    if (premier && v.camera === 'maquettes') this._cadrer(theme.theme);
  }

  /** Cadrage sur les maquettes gardées par le thème, en laissant la place de la légende. */
  _cadrer(theme) {
    const pts = [...this.r.maquettes.maquettes.values()]
      .filter((m) => m.positionnement?.statut === 'positionnee' && (!theme || theme.garder(m))).map((m) => m.positionnement.centre_wgs84);
    if (!pts.length) return;
    const lon = pts.map((p) => p[0]);
    const lat = pts.map((p) => p[1]);
    const largeur = this.legende.hidden ? 0 : this.legende.offsetWidth + 20;
    this.r.carte.fitBounds([[Math.min(...lon), Math.min(...lat)], [Math.max(...lon), Math.max(...lat)]],
      { padding: { top: 40, bottom: 40, right: 60, left: 40 + largeur }, pitch: 0, bearing: 0, duration: 1200, maxZoom: 14 });
  }

  async _theme(v, jeton) {
    const ms = [...this.r.maquettes.maquettes.values()].filter((m) => m.positionnement?.statut === 'positionnee');
    if (v.theme === 'point_bas') {
      const valeur = (m) => {
        const p = m.positionnement;
        return p.altitude_terrain != null && p.hauteurs_affichees ? Math.round((p.altitude_terrain + p.hauteurs_affichees[0]) * 100) / 100 : null;
      };
      const b = bornes(ms.map(valeur).filter((x) => x != null));
      return { theme: { garder: () => true, valeur, couleur: expressionSuite(b, SUITE_BLEUE) },
        titre: 'Altitude du point bas (m NGF)', classes: classesLegende(b, SUITE_BLEUE, 'm'), gris: 'altitude du terrain inconnue',
        note: 'Plus foncé = plus bas. Comparer au niveau des crues : fiche de l’ouvrage, « Contexte ».' };
    }
    if (v.theme === 'carbone') {
      let r;
      try {
        r = await lire('themes/carbone');
      } catch (e) {
        return { theme: null, titre: 'Carbone', classes: [], erreur: e.message };
      }
      if (!r.complet) setTimeout(() => { if (jeton === this._jeton) this._rafraichirTheme(v, jeton); }, RELANCE_CARBONE_MS);
      const valeur = (m) => (r.valeurs[m.id]?.tco2e > 0 ? r.valeurs[m.id].tco2e : null);
      const b = bornes(ms.map(valeur).filter((x) => x != null));
      const estimees = ms.filter((m) => valeur(m) != null);
      const total = estimees.reduce((s, m) => s + valeur(m), 0);
      return { theme: { garder: () => true, valeur, couleur: expressionSuite(b, SUITE_CARBONE) },
        titre: 'Émissions de production des matériaux (t CO2e)', classes: classesLegende(b, SUITE_CARBONE, 't'),
        gris: 'non estimé (ni volume ni matériau exploitable)',
        note: `${estimees.length} maquette(s) estimée(s) sur ${ms.length}, ≈ ${nf(total)} t CO2e au total. Ordre de grandeur, facteurs génériques indicatifs.`
          + (r.complet ? '' : ` Calcul en cours : ${r.calcules}/${r.total}…`) };
    }
    // categories : couleur par catégorie de chemin, les autres maquettes masquées
    const cats = v.categories.map((c, i) => ({ ...c, couleur: c.couleur || CATEGORIES[i % CATEGORIES.length], re: c.motifs.map(motif) }));
    const categorie = (m) => cats.find((c) => c.re.some((re) => re.test(String(m.fichier || '').toLowerCase())));
    const couleur = ['match', ['get', 'v'], ...cats.flatMap((c) => [c.libelle, c.couleur]), GRIS];
    const nb = new Map(cats.map((c) => [c.libelle, 0]));
    for (const m of ms) { const c = categorie(m); if (c) nb.set(c.libelle, nb.get(c.libelle) + 1); }
    return { theme: { garder: (m) => Boolean(categorie(m)), valeur: (m) => categorie(m)?.libelle ?? null, couleur },
      titre: 'Ouvrages', classes: cats.map((c) => [c.couleur, `${c.libelle} (${nb.get(c.libelle)})`]),
      note: 'Selon le dossier de la maquette ; les autres maquettes sont masquées.' };
  }

  _legende(v, t) {
    this.legende.hidden = false;
    this.legende.replaceChildren(
      el('h3', { className: 'sig-legende-vue__titre', textContent: v.libelle }),
      v.description ? el('p', { className: 'sig-legende-vue__texte', textContent: v.description }) : '',
      el('h4', { className: 'sig-legende-vue__sous', textContent: t.titre }),
      el('ul', { className: 'sig-legende' }, ...t.classes.map(([c, texte]) => el('li', {},
        el('span', { className: 'sig-legende__pastille', style: `background:${c}` }), el('span', { textContent: texte }))),
        ...(t.gris ? [el('li', {}, el('span', { className: 'sig-legende__pastille', style: `background:${GRIS}` }),
          el('span', { textContent: t.gris }))] : [])),
      t.note ? el('p', { className: 'sig-legende-vue__texte', textContent: t.note }) : '',
      t.erreur ? el('p', { className: 'sig-erreur', textContent: t.erreur }) : '',
      el('p', { className: 'sig-legende-vue__texte', textContent: 'Maquettes : points en vue éloignée, emprises en vue rapprochée.' }),
    );
  }
}

// Import d'un périmètre de crise (lot C3) : fichier GeoJSON exporté par la carte de crise (aller-retour exact :
// cercles, polygones, couloirs) ou dessiné ailleurs (préfecture, autre SIG : polygones et lignes).
// GeoJSON en WGS84 (longitude, latitude) seulement : un autre système est refusé, jamais supposé.

const SYSTEMES_WGS84 = /CRS84|EPSG:+4326|EPSG::4326/i;

function coordonneesDe(geom) {
  if (!geom) return [];
  if (geom.type === 'Point') return [geom.coordinates];
  if (geom.type === 'LineString' || geom.type === 'MultiPoint') return geom.coordinates;
  if (geom.type === 'Polygon' || geom.type === 'MultiLineString') return geom.coordinates.flat();
  if (geom.type === 'MultiPolygon') return geom.coordinates.flat(2);
  if (geom.type === 'GeometryCollection') return geom.geometries.flatMap(coordonneesDe);
  return [];
}

const ouvert = (anneau) => {
  const a = anneau[0];
  const b = anneau[anneau.length - 1];
  return a && b && a[0] === b[0] && a[1] === b[1] ? anneau.slice(0, -1) : anneau;
};

/**
 * Zones lues dans le texte d'un fichier GeoJSON.
 * options : { zones: [{ id, libelle }] (rôles déclarés), roleDefaut, centre (point actuel ou null), tamponDefaut }.
 * Retour : { centre, zones, notes } ; erreur explicite si le fichier ne convient pas.
 */
export function lirePerimetre(texte, { zones: roles, roleDefaut, centre = null, tamponDefaut = 100 }) {
  let d;
  try {
    d = JSON.parse(texte);
  } catch {
    throw new Error('fichier illisible : un GeoJSON est attendu');
  }
  const nom = d?.crs?.properties?.name;
  if (nom && !SYSTEMES_WGS84.test(nom)) throw new Error(`système de coordonnées « ${nom} » : un GeoJSON en WGS84 (longitude, latitude) est attendu`);
  const features = d?.type === 'FeatureCollection' ? d.features : d?.type === 'Feature' ? [d] : d?.type ? [{ type: 'Feature', geometry: d, properties: {} }] : null;
  if (!Array.isArray(features)) throw new Error('GeoJSON attendu (FeatureCollection, Feature ou géométrie)');
  const tout = features.flatMap((f) => coordonneesDe(f?.geometry));
  if (tout.some((c) => !Array.isArray(c) || !Number.isFinite(c[0]) || !Number.isFinite(c[1]))) throw new Error('coordonnées illisibles dans le fichier');
  if (tout.some(([x, y]) => Math.abs(x) > 180 || Math.abs(y) > 90)) {
    throw new Error('coordonnées en mètres (Lambert 93 ?) : exportez le périmètre en WGS84 (longitude, latitude)');
  }
  const ids = new Set(roles.map((r) => r.id));
  const parLibelle = new Map(roles.map((r) => [r.libelle.toLowerCase(), r.id]));
  const notes = [];
  let parDefaut = 0;
  const role = (p) => {
    if (ids.has(p?.role)) return p.role;
    const l = String(p?.libelle || p?.zone || '').toLowerCase();
    if (parLibelle.has(l)) return parLibelle.get(l);
    parDefaut += 1;
    return roleDefaut;
  };
  const centreFichier = features.find((f) => f?.geometry?.type === 'Point' && f.properties?.nature === 'evenement')?.geometry.coordinates;
  const centreFinal = centre || centreFichier || null;
  const memeCentre = centreFichier && centreFinal && Math.abs(centreFichier[0] - centreFinal[0]) < 1e-5 && Math.abs(centreFichier[1] - centreFinal[1]) < 1e-5;
  const sortie = [];
  let trous = 0;
  let ignores = 0;
  for (const f of features) {
    const g = f?.geometry;
    const p = f?.properties || {};
    if (!g || g.type === 'Point') {
      if (p.nature !== 'evenement') ignores += 1;
      continue;
    }
    if (p.forme === 'cercle' && p.rayon_m > 0 && memeCentre) {
      sortie.push({ role: role(p), forme: 'cercle', rayon_m: Number(p.rayon_m) });
    } else if (p.forme === 'couloir' && Array.isArray(p.axe) && p.axe.length >= 2 && p.tampon_m > 0) {
      sortie.push({ role: role(p), forme: 'couloir', coordonnees: p.axe, tampon_m: Number(p.tampon_m) });
    } else if (g.type === 'Polygon' || g.type === 'MultiPolygon') {
      const polys = g.type === 'Polygon' ? [g.coordinates] : g.coordinates;
      const r = role(p);
      for (const poly of polys) {
        trous += poly.length - 1;
        const anneau = ouvert(poly[0]);
        if (anneau.length >= 3) sortie.push({ role: r, forme: 'polygone', coordonnees: anneau });
      }
      if (p.forme === 'cercle' && !memeCentre) notes.push('cercle importé en polygone (point de l’événement différent)');
    } else if (g.type === 'LineString' || g.type === 'MultiLineString') {
      const lignes = g.type === 'LineString' ? [g.coordinates] : g.coordinates;
      const t = p.tampon_m > 0 ? Number(p.tampon_m) : tamponDefaut;
      if (!(p.tampon_m > 0)) notes.push(`ligne importée en couloir de ${tamponDefaut} m de part et d’autre (modifiable)`);
      for (const l of lignes) if (l.length >= 2) sortie.push({ role: role(p), forme: 'couloir', coordonnees: l, tampon_m: t });
    } else {
      ignores += 1;
    }
  }
  if (!sortie.length) throw new Error('aucune zone (polygone ou ligne) dans le fichier');
  if (parDefaut) notes.push(`${parDefaut} zone(s) sans rôle reconnu : rôle choisi appliqué`);
  if (trous) notes.push(`${trous} trou(s) de polygone ignoré(s)`);
  if (ignores) notes.push(`${ignores} objet(s) ignoré(s) (points ou géométries non surfaciques)`);
  let c = centreFinal;
  if (!c) {   // ni point actuel ni point dans le fichier : centre des sommets
    const pts = sortie.flatMap((z) => z.coordonnees || []);
    c = [pts.reduce((a, q) => a + q[0], 0) / pts.length, pts.reduce((a, q) => a + q[1], 0) / pts.length];
    notes.push('pas de point d’événement dans le fichier : placé au centre du périmètre (déplaçable)');
  }
  return { centre: c, zones: sortie, notes: [...new Set(notes)] };
}

// Import d'un périmètre de crise (lot C3) : aller-retour avec l'export, polygones et lignes d'un autre SIG,
// systèmes refusés. Lancé par pytest (test_crise_c3.py) ou seul : node --test tests/sig/test_crise_import.mjs
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { lirePerimetre } from '../../interface/sig/crise_import.js';

const ZONES = [{ id: 'evacuation', libelle: 'Évacuation' }, { id: 'fermeture', libelle: 'Fermeture' }, { id: 'vigilance', libelle: 'Vigilance' }];
const OPTS = { zones: ZONES, roleDefaut: 'vigilance', centre: null, tamponDefaut: 100 };
const C = [2.3691, 48.8532];
const carre = (d) => [[[C[0] - d, C[1] - d], [C[0] + d, C[1] - d], [C[0] + d, C[1] + d], [C[0] - d, C[1] + d], [C[0] - d, C[1] - d]]];
const fc = (...features) => JSON.stringify({ type: 'FeatureCollection', features });
const evenement = { type: 'Feature', geometry: { type: 'Point', coordinates: C }, properties: { nature: 'evenement', titre: 'Essai' } };

test('aller-retour avec l’export : cercle, polygone et couloir restitués', () => {
  const r = lirePerimetre(fc(evenement,
    { type: 'Feature', geometry: { type: 'Polygon', coordinates: carre(0.005) }, properties: { nature: 'zone', role: 'evacuation', forme: 'cercle', rayon_m: 500 } },
    { type: 'Feature', geometry: { type: 'Polygon', coordinates: carre(0.002) }, properties: { nature: 'zone', role: 'fermeture', forme: 'polygone' } },
    { type: 'Feature', geometry: { type: 'Polygon', coordinates: carre(0.003) }, properties: { nature: 'zone', role: 'vigilance', forme: 'couloir', tampon_m: 80, axe: [C, [C[0] + 0.01, C[1]]] } },
  ), OPTS);
  assert.deepEqual(r.centre, C);
  assert.deepEqual(r.zones[0], { role: 'evacuation', forme: 'cercle', rayon_m: 500 });
  assert.equal(r.zones[1].forme, 'polygone');
  assert.equal(r.zones[1].coordonnees.length, 4);   // anneau rouvert
  assert.deepEqual(r.zones[2], { role: 'vigilance', forme: 'couloir', coordonnees: [C, [C[0] + 0.01, C[1]]], tampon_m: 80 });
  assert.deepEqual(r.notes, []);
});

test('cercle d’un autre événement : importé en polygone', () => {
  const r = lirePerimetre(fc(evenement,
    { type: 'Feature', geometry: { type: 'Polygon', coordinates: carre(0.005) }, properties: { role: 'evacuation', forme: 'cercle', rayon_m: 500 } },
  ), { ...OPTS, centre: [2.3, 48.8] });
  assert.equal(r.zones[0].forme, 'polygone');
  assert.deepEqual(r.centre, [2.3, 48.8]);
  assert.ok(r.notes.some((n) => n.includes('polygone')));
});

test('périmètre d’un autre SIG : rôle par libellé, rôle par défaut, multipolygone, ligne en couloir', () => {
  const r = lirePerimetre(fc(
    { type: 'Feature', geometry: { type: 'MultiPolygon', coordinates: [carre(0.001), carre(0.002)] }, properties: { libelle: 'fermeture' } },
    { type: 'Feature', geometry: { type: 'Polygon', coordinates: [...carre(0.004), carre(0.001)[0]] }, properties: { nom: 'zone préfecture' } },
    { type: 'Feature', geometry: { type: 'LineString', coordinates: [C, [C[0], C[1] + 0.01]] }, properties: {} },
  ), OPTS);
  assert.deepEqual(r.zones.map((z) => [z.role, z.forme]), [['fermeture', 'polygone'], ['fermeture', 'polygone'], ['vigilance', 'polygone'], ['vigilance', 'couloir']]);
  assert.equal(r.zones[3].tampon_m, 100);
  assert.ok(r.notes.some((n) => n.includes('rôle choisi')));
  assert.ok(r.notes.some((n) => n.includes('trou')));
  assert.ok(r.notes.some((n) => n.includes('centre du périmètre')));   // pas de point d'événement
  assert.ok(Math.abs(r.centre[0] - C[0]) < 0.003 && Math.abs(r.centre[1] - C[1]) < 0.004);
});

test('géométrie seule acceptée', () => {
  const r = lirePerimetre(JSON.stringify({ type: 'Polygon', coordinates: carre(0.001) }), OPTS);
  assert.equal(r.zones.length, 1);
});

test('systèmes et fichiers refusés, jamais supposés', () => {
  assert.throws(() => lirePerimetre('pas du json', OPTS), /illisible/);
  assert.throws(() => lirePerimetre(JSON.stringify({ type: 'FeatureCollection', crs: { type: 'name', properties: { name: 'urn:ogc:def:crs:EPSG::2154' } }, features: [] }), OPTS), /WGS84/);
  const l93 = [[[652000, 6862000], [652100, 6862000], [652100, 6862100], [652000, 6862000]]];
  assert.throws(() => lirePerimetre(fc({ type: 'Feature', geometry: { type: 'Polygon', coordinates: l93 }, properties: {} }), OPTS), /Lambert 93/);
  assert.throws(() => lirePerimetre(fc(evenement), OPTS), /aucune zone/);
  // CRS84 explicite : accepté
  const ok = JSON.parse(fc({ type: 'Feature', geometry: { type: 'Polygon', coordinates: carre(0.001) }, properties: {} }));
  ok.crs = { type: 'name', properties: { name: 'urn:ogc:def:crs:OGC:1.3:CRS84' } };
  assert.equal(lirePerimetre(JSON.stringify(ok), OPTS).zones.length, 1);
});

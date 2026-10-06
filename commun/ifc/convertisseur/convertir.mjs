// Conversion d'une maquette IFC en modèles 3D allégés (GLB) et empreintes au sol, avec web-ifc.
//
//   node convertir.mjs <fichier.ifc> <dossier_sortie> <parametres.json>
//
// Paramètres (JSON) : exclure (types IFC exclus, sous-types compris), petits_elements_m (plus grande dimension
// en dessous de laquelle un morceau de géométrie va dans le modèle « détail »), simplification_3d_m (écart maximal,
// en mètres, toléré en retirant des triangles : les surfaces courbes sont découpées très finement par web-ifc).
//
// Sorties dans le dossier :
//   modele.glb        objets regroupés par couleur (quelques dizaines de maillages au lieu d'un par objet),
//                     positions quantifiées (KHR_mesh_quantization) et compressées (EXT_meshopt_compression) ;
//   detail.glb        même format, petits éléments (boulons, platines…), chargés seulement en vue rapprochée ;
//   geometrie.json    origine du repère des GLB, empreinte (enveloppe convexe 2D) de chaque objet, altitudes.
// L'avancement est écrit sur la sortie standard (« progression N »), le résultat en dernière ligne (« fin {…} »).
//
// Repère : coordonnées IFC monde (mètres) moins `origine`, axe Y vertical (convention glTF : x, z, -y).
// Les GLB n'ont pas de nœud par objet : chaque primitive porte dans ses `extras.plages` la liste
// [premier triangle, indice d'objet, …] et la scène la table `extras.guids` (indice → GlobalId).
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { createRequire } from 'node:module';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { MeshoptEncoder, MeshoptSimplifier } from 'meshoptimizer';

const require = createRequire(import.meta.url);
const WebIFC = require('web-ifc');

const TRIANGLES_PAR_MAILLAGE = 300_000;   // technique : au-delà, nouveau maillage (culling, sélection, précision)
const PRECISION_QUANTIFICATION_M = 0.002; // technique : en deçà, positions en flottants plutôt qu'en entiers 16 bits

// ---------------------------------------------------------------- regroupement des triangles

class Maillage {
  constructor(couleur) {
    this.couleur = couleur;
    this.positions = [];   // morceaux Float32Array (x, y, z en repère glTF)
    this.indices = [];     // morceaux Uint32Array
    this.nbSommets = 0;
    this.nbTriangles = 0;
    this.plages = [];      // [premier triangle, indice d'objet, …]
    this.dernierObjet = -1;
    this.min = [Infinity, Infinity, Infinity];
    this.max = [-Infinity, -Infinity, -Infinity];
  }

  ajouter(objet, pos, idx, bmin, bmax) {
    if (objet !== this.dernierObjet) {
      this.plages.push(this.nbTriangles, objet);
      this.dernierObjet = objet;
    }
    const base = this.nbSommets;
    const decale = new Uint32Array(idx.length);
    for (let i = 0; i < idx.length; i += 1) decale[i] = idx[i] + base;
    this.positions.push(pos);
    this.indices.push(decale);
    this.nbSommets += pos.length / 3;
    this.nbTriangles += idx.length / 3;
    for (let k = 0; k < 3; k += 1) {
      if (bmin[k] < this.min[k]) this.min[k] = bmin[k];
      if (bmax[k] > this.max[k]) this.max[k] = bmax[k];
    }
  }
}

class Regroupement {
  constructor() {
    this.ouverts = new Map();   // clé couleur -> Maillage en cours
    this.fermes = [];
  }

  maillage(couleur, cle) {
    let m = this.ouverts.get(cle);
    if (m && m.nbTriangles >= TRIANGLES_PAR_MAILLAGE) {
      this.fermes.push(m);
      m = null;
    }
    if (!m) {
      m = new Maillage(couleur);
      this.ouverts.set(cle, m);
    }
    return m;
  }

  tous() {
    return [...this.fermes, ...this.ouverts.values()].filter((m) => m.nbTriangles > 0);
  }
}

// ---------------------------------------------------------------- écriture GLB

function concat(morceaux, Type, n) {
  const sortie = new Type(n);
  let o = 0;
  for (const m of morceaux) { sortie.set(m, o); o += m.length; }
  return sortie;
}

function aligner4(n) { return (n + 3) & ~3; }

function ecrireGlb(chemin, maillages, guids) {
  const materiaux = new Map();
  const json = {
    asset: { version: '2.0', generator: 'commun/ifc/convertisseur (web-ifc)' },
    extensionsUsed: ['EXT_meshopt_compression'],
    extensionsRequired: ['EXT_meshopt_compression'],
    scene: 0,
    scenes: [{ nodes: [], extras: { guids } }],
    nodes: [], meshes: [], materials: [], accessors: [], bufferViews: [],
    buffers: [],
  };
  const compresse = [];
  let tailleCompressee = 0;
  let tailleBrute = 0;
  let quantifie = false;

  function vue(donnees, count, taille, mode, cible) {
    const brut = new Uint8Array(donnees.buffer, donnees.byteOffset, donnees.byteLength);
    const c = MeshoptEncoder.encodeGltfBuffer(brut, count, taille, mode);
    const v = {
      buffer: 1, byteOffset: tailleBrute, byteLength: brut.byteLength,
      extensions: { EXT_meshopt_compression: { buffer: 0, byteOffset: tailleCompressee, byteLength: c.byteLength,
        byteStride: taille, mode, count } },
    };
    if (mode === 'ATTRIBUTES') v.byteStride = taille;
    if (cible) v.target = cible;
    compresse.push({ octets: c, decalage: tailleCompressee });
    tailleCompressee = aligner4(tailleCompressee + c.byteLength);
    tailleBrute = aligner4(tailleBrute + brut.byteLength);
    json.bufferViews.push(v);
    return json.bufferViews.length - 1;
  }

  for (const m of maillages) {
    const cle = m.couleur.join(',');
    if (!materiaux.has(cle)) {
      const [r, g, b, a] = m.couleur;
      const mat = { pbrMetallicRoughness: { baseColorFactor: [r, g, b, a], metallicFactor: 0, roughnessFactor: 0.8 },
        doubleSided: true };
      if (a < 0.99) mat.alphaMode = 'BLEND';
      json.materials.push(mat);
      materiaux.set(cle, json.materials.length - 1);
    }
    const pos = concat(m.positions, Float32Array, m.nbSommets * 3);
    const idx = concat(m.indices, Uint32Array, m.nbTriangles * 3);
    const etendue = Math.max(m.max[0] - m.min[0], m.max[1] - m.min[1], m.max[2] - m.min[2]);
    const noeud = { mesh: json.meshes.length };
    let accesseurPos;
    if (etendue / 65535 <= PRECISION_QUANTIFICATION_M) {
      // entiers 16 bits sur l'étendue du maillage ; le nœud porte l'échelle et la translation
      quantifie = true;
      const pas = etendue > 0 ? etendue / 65535 : 1;
      const q = new Uint16Array(m.nbSommets * 4);
      const qmin = [65535, 65535, 65535];
      const qmax = [0, 0, 0];
      for (let i = 0; i < m.nbSommets; i += 1) {
        for (let k = 0; k < 3; k += 1) {
          const v = Math.min(65535, Math.max(0, Math.round((pos[i * 3 + k] - m.min[k]) / pas)));
          q[i * 4 + k] = v;
          if (v < qmin[k]) qmin[k] = v;
          if (v > qmax[k]) qmax[k] = v;
        }
      }
      json.accessors.push({ bufferView: vue(q, m.nbSommets, 8, 'ATTRIBUTES', 34962), componentType: 5123,
        count: m.nbSommets, type: 'VEC3', min: qmin, max: qmax });
      noeud.translation = m.min;
      noeud.scale = [pas, pas, pas];
    } else {
      json.accessors.push({ bufferView: vue(pos, m.nbSommets, 12, 'ATTRIBUTES', 34962), componentType: 5126,
        count: m.nbSommets, type: 'VEC3', min: m.min, max: m.max });
    }
    accesseurPos = json.accessors.length - 1;
    json.accessors.push({ bufferView: vue(idx, idx.length, 4, 'TRIANGLES', 34963), componentType: 5125,
      count: idx.length, type: 'SCALAR' });
    json.meshes.push({ primitives: [{ attributes: { POSITION: accesseurPos }, indices: json.accessors.length - 1,
      material: materiaux.get(cle), extras: { plages: m.plages } }] });
    json.nodes.push(noeud);
    json.scenes[0].nodes.push(json.nodes.length - 1);
  }
  if (quantifie) {
    json.extensionsUsed.push('KHR_mesh_quantization');
    json.extensionsRequired.push('KHR_mesh_quantization');
  }
  json.buffers.push({ byteLength: tailleCompressee });
  json.buffers.push({ byteLength: tailleBrute, extensions: { EXT_meshopt_compression: { fallback: true } } });

  const bin = new Uint8Array(tailleCompressee);
  for (const { octets, decalage } of compresse) bin.set(octets, decalage);
  const texte = Buffer.from(JSON.stringify(json), 'utf8');
  const tJson = aligner4(texte.length);
  const total = 12 + 8 + tJson + 8 + bin.byteLength;
  const glb = Buffer.alloc(total, 0);
  glb.writeUInt32LE(0x46546c67, 0);
  glb.writeUInt32LE(2, 4);
  glb.writeUInt32LE(total, 8);
  glb.writeUInt32LE(tJson, 12);
  glb.writeUInt32LE(0x4e4f534a, 16);
  texte.copy(glb, 20);
  glb.fill(0x20, 20 + texte.length, 20 + tJson);
  glb.writeUInt32LE(bin.byteLength, 20 + tJson);
  glb.writeUInt32LE(0x004e4942, 24 + tJson);
  Buffer.from(bin.buffer, bin.byteOffset, bin.byteLength).copy(glb, 28 + tJson);
  writeFileSync(chemin, glb);
  return total;
}

// ---------------------------------------------------------------- empreintes

/** Enveloppe convexe 2D (chaîne monotone) d'une liste plate [x0, y0, x1, y1, …]. */
function enveloppe(plat) {
  const pts = [];
  for (let i = 0; i < plat.length; i += 2) pts.push([plat[i], plat[i + 1]]);
  pts.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  if (pts.length < 3) return pts;
  const croix = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const bas = [];
  for (const p of pts) {
    while (bas.length >= 2 && croix(bas[bas.length - 2], bas[bas.length - 1], p) <= 0) bas.pop();
    bas.push(p);
  }
  const haut = [];
  for (let i = pts.length - 1; i >= 0; i -= 1) {
    const p = pts[i];
    while (haut.length >= 2 && croix(haut[haut.length - 2], haut[haut.length - 1], p) <= 0) haut.pop();
    haut.push(p);
  }
  return bas.slice(0, -1).concat(haut.slice(0, -1));
}

// ---------------------------------------------------------------- conversion

function typesExclus(api, noms) {
  const codes = new Set();
  for (const nom of noms) {
    const code = api.GetTypeCodeFromName(nom.toUpperCase());
    if (!code) continue;
    codes.add(code);
    for (const schema of Object.values(WebIFC.InheritanceDef)) for (const t of schema[code] || []) codes.add(t);
  }
  return codes;
}

function typesProduits(api, exclus) {
  const produit = api.GetTypeCodeFromName('IFCPRODUCT');
  const codes = new Set();
  for (const schema of Object.values(WebIFC.InheritanceDef)) for (const t of schema[produit] || []) codes.add(t);
  return [...codes].filter((t) => !exclus.has(t));
}

export async function convertir(fichier, sortie, params) {
  const debut = Date.now();
  mkdirSync(sortie, { recursive: true });
  await MeshoptEncoder.ready;
  await MeshoptSimplifier.ready;
  const api = new WebIFC.IfcAPI();
  await api.Init();
  api.SetLogLevel(WebIFC.LogLevel.LOG_LEVEL_OFF);
  const donnees = readFileSync(fichier);
  const modele = api.OpenModel(new Uint8Array(donnees.buffer, donnees.byteOffset, donnees.byteLength),
    { COORDINATE_TO_ORIGIN: false });
  if (modele < 0) throw new Error('fichier IFC illisible par web-ifc');
  const exclus = typesExclus(api, params.exclure || []);
  const seuil = Number(params.petits_elements_m) || 0;
  const tolerance = Number(params.simplification_3d_m) || 0;
  let trianglesAvant = 0;
  let trianglesApres = 0;

  const groupes = { principal: new Regroupement(), detail: new Regroupement() };
  const guids = [];                  // indice d'objet -> GlobalId
  const empreintes = [];             // [indice d'objet, [[x, y], …]] en repère IFC local
  const cacheGeo = new Map();        // geometryExpressID -> { pos (Float32Array x,y,z), idx (Uint32Array) }
  let origine = null;                // [x, y, z] repère Y vertical de web-ifc
  let zmin = Infinity;
  let zmax = -Infinity;
  let nbMorceaux = 0;
  let nbPetits = 0;
  let derniere = -1;

  // échelle = unités de la géométrie → mètres (première colonne de la matrice de placement)
  function geometrie(id, echelle = 1) {
    let g = cacheGeo.get(id);
    if (g) return g;
    const geo = api.GetGeometry(modele, id);
    const v = api.GetVertexArray(geo.GetVertexData(), geo.GetVertexDataSize());
    const idx = api.GetIndexArray(geo.GetIndexData(), geo.GetIndexDataSize());
    // web-ifc duplique les sommets de chaque face (normales à facettes) : sans normales, on les fusionne
    const n = v.length / 6;
    const remap = new Uint32Array(n);
    const vus = new Map();
    const pos = [];
    for (let i = 0; i < n; i += 1) {
      const x = v[i * 6]; const y = v[i * 6 + 1]; const z = v[i * 6 + 2];
      const cle = `${x},${y},${z}`;
      let j = vus.get(cle);
      if (j === undefined) { j = pos.length / 3; vus.set(cle, j); pos.push(x, y, z); }
      remap[i] = j;
    }
    const indices = new Uint32Array(idx.length);
    for (let i = 0; i < idx.length; i += 1) indices[i] = remap[idx[i]];
    geo.delete();
    if (!indices.length) {
      g = { pos: new Float64Array(0), idx: indices };
      cacheGeo.set(id, g);
      return g;
    }
    // simplification : triangles retirés tant que l'écart à la forme reste sous la tolérance (en mètres)
    let tri = indices;
    trianglesAvant += indices.length / 3;
    if (tolerance > 0 && indices.length > 3 * 12) {
      const [simple] = MeshoptSimplifier.simplify(indices, Float32Array.from(pos), 3, 0, tolerance / echelle,
        ['ErrorAbsolute', 'Prune']);
      if (simple.length >= 3) tri = simple;
    }
    trianglesApres += tri.length / 3;
    // ordre des triangles et des sommets favorable au GPU et à la compression (à l'intérieur du morceau)
    const [ordre, nb] = MeshoptEncoder.reorderMesh(tri, true, false);
    const pos2 = new Float64Array(nb * 3);
    for (let i = 0; i < ordre.length; i += 1) {
      const d = ordre[i];
      if (d === 0xffffffff) continue;
      pos2[d * 3] = pos[i * 3]; pos2[d * 3 + 1] = pos[i * 3 + 1]; pos2[d * 3 + 2] = pos[i * 3 + 2];
    }
    g = { pos: pos2, idx: tri };
    cacheGeo.set(id, g);
    return g;
  }

  api.StreamAllMeshesWithTypes(modele, typesProduits(api, exclus), (mesh, index, total) => {
    const p = total ? Math.floor((index * 100) / total) : 0;
    if (p !== derniere) { process.stdout.write(`progression ${p}\n`); derniere = p; }
    const pieces = mesh.geometries;
    let objet = -1;
    const contour = [];   // enveloppes des morceaux de l'objet (liste plate x, y)
    for (let i = 0; i < pieces.size(); i += 1) {
      const piece = pieces.get(i);
      const m = piece.flatTransformation;   // 4x4, colonnes
      const g = geometrie(piece.geometryExpressID, Math.hypot(m[0], m[1], m[2]) || 1);
      if (!g.idx.length) continue;
      if (!origine) origine = [Math.round(m[12]), Math.round(m[13]), Math.round(m[14])];
      const n = g.pos.length / 3;
      const out = new Float32Array(n * 3);
      const bmin = [Infinity, Infinity, Infinity];
      const bmax = [-Infinity, -Infinity, -Infinity];
      const plat = new Float64Array(n * 2);
      for (let k = 0; k < n; k += 1) {
        const x = g.pos[k * 3]; const y = g.pos[k * 3 + 1]; const z = g.pos[k * 3 + 2];
        const wx = m[0] * x + m[4] * y + m[8] * z + m[12] - origine[0];
        const wy = m[1] * x + m[5] * y + m[9] * z + m[13] - origine[1];
        const wz = m[2] * x + m[6] * y + m[10] * z + m[14] - origine[2];
        out[k * 3] = wx; out[k * 3 + 1] = wy; out[k * 3 + 2] = wz;
        if (wx < bmin[0]) bmin[0] = wx; if (wx > bmax[0]) bmax[0] = wx;
        if (wy < bmin[1]) bmin[1] = wy; if (wy > bmax[1]) bmax[1] = wy;
        if (wz < bmin[2]) bmin[2] = wz; if (wz > bmax[2]) bmax[2] = wz;
        plat[k * 2] = wx; plat[k * 2 + 1] = -wz;   // plan IFC : x, y = -z (repère Y vertical)
      }
      if (!Number.isFinite(bmin[0])) continue;
      if (objet < 0) {
        objet = guids.length;
        let guid = null;
        try { guid = api.GetLine(modele, mesh.expressID, false).GlobalId?.value ?? null; } catch { guid = null; }
        guids.push(guid || `#${mesh.expressID}`);
      }
      for (const [x, y] of enveloppe(plat)) contour.push(x, y);
      if (bmin[1] < zmin) zmin = bmin[1];
      if (bmax[1] > zmax) zmax = bmax[1];
      const taille = Math.max(bmax[0] - bmin[0], bmax[1] - bmin[1], bmax[2] - bmin[2]);
      const petit = taille < seuil;
      nbMorceaux += 1;
      if (petit) nbPetits += 1;
      const c = piece.color;
      const couleur = [c.x, c.y, c.z, c.w].map((v) => Math.round(Math.min(1, Math.max(0, v)) * 255) / 255);
      const cle = couleur.join(',');
      (petit ? groupes.detail : groupes.principal).maillage(couleur, cle).ajouter(objet, out, g.idx, bmin, bmax);
    }
    mesh.delete?.();
    if (objet >= 0 && contour.length >= 6) {
      empreintes.push([objet, enveloppe(contour).map(([x, y]) => [Math.round(x * 1000) / 1000, Math.round(y * 1000) / 1000])]);
    }
  });
  api.CloseModel(modele);

  const tailles = {};
  const triangles = {};
  for (const [nom, fichierGlb] of [['principal', 'modele.glb'], ['detail', 'detail.glb']]) {
    const maillages = groupes[nom].tous();
    tailles[nom] = maillages.length ? ecrireGlb(join(sortie, fichierGlb), maillages, guids) : 0;
    triangles[nom] = maillages.reduce((t, m) => t + m.nbTriangles, 0);
  }
  const resultat = {
    moteur: `web-ifc ${api.GetVersion?.() ?? ''}`.trim(),
    // origine du repère des GLB en coordonnées IFC monde (x, y, z ; z vertical)
    origine: origine ? [origine[0], -origine[2], origine[1]] : [0, 0, 0],
    nb_elements: guids.length,
    nb_morceaux: nbMorceaux,
    nb_petits_morceaux: nbPetits,
    // triangles des géométries distinctes avant / après simplification
    triangles_avant_simplification: trianglesAvant,
    triangles_apres_simplification: trianglesApres,
    // altitudes relatives à l'origine
    z_min: Number.isFinite(zmin) ? zmin : null,
    z_max: Number.isFinite(zmax) ? zmax : null,
    taille_glb: tailles.principal,
    taille_detail_glb: tailles.detail,
    // triangles à dessiner (instances comprises) : la charge réelle pour la carte graphique
    triangles: triangles.principal,
    triangles_detail: triangles.detail,
    duree_secondes: Math.round((Date.now() - debut) / 100) / 10,
    guids,
    empreintes,
  };
  writeFileSync(join(sortie, 'geometrie.json'), JSON.stringify(resultat));
  return resultat;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  const [fichier, sortie, parametres] = process.argv.slice(2);
  try {
    const r = await convertir(fichier, sortie, JSON.parse(readFileSync(parametres, 'utf8')));
    const { guids, empreintes, ...resume } = r;
    process.stdout.write(`fin ${JSON.stringify(resume)}\n`);
  } catch (e) {
    process.stderr.write(`erreur ${e?.message || e}\n`);
    process.exit(1);
  }
}

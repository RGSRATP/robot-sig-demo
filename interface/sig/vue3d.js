// Maquettes 3D dans la carte : calque MapLibre personnalisé dessiné avec Three.js dans le contexte WebGL
// de la carte. Les maquettes partagent la caméra de la carte (zoom, rotation, inclinaison) et se superposent
// aux couches SIG à leur position géoréférencée.
//
// Repère : les modèles GLB sont en mètres « locaux » (origine du site IFC, axe Y vertical glTF). Le serveur
// fournit trois points de contrôle (origine, +100 m en X, +100 m en Y) en WGS84 et le décalage vertical
// par rapport au terrain ; la matrice locale → Mercator en est déduite ici, en double précision.
// Carte plate : le terrain est à 0, la scène est en hauteurs par rapport au terrain. Relief activé : la scène est
// en altitudes NGF (hauteur + altitude du terrain au centre de la maquette, `altitude_terrain`), comme le terrain. La scène
// Three.js est exprimée en mètres autour d'un point de référence commun (unités Mercator ramenées au mètre) :
// les nombres restent petits pour le GPU et l'éclairage à facettes reste précis.
//
// Modèles (convertisseur web-ifc de commun/ifc) : objets regroupés par couleur en quelques maillages compressés
// (meshopt). Chaque maillage porte `plages` = [premier triangle, indice d'objet, …] et la scène du GLB la table
// `guids` : le clic retrouve l'objet par le triangle touché. Les petits éléments sont dans un second GLB (détail).
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { MeshoptDecoder } from 'three/examples/jsm/libs/meshopt_decoder.module.js';
import { MercatorCoordinate } from 'maplibre-gl';

const COULEUR_SELECTION = 0xe8b400;
const COULEUR_CONTOUR_PLAN = 0x2b3137;
const ZOOM_ETIQUETTES = 17;        // étiquettes des locaux à partir de ce zoom
const ETIQUETTE_HAUTEUR_PX = 16;   // encombrement d'une étiquette (évitement des chevauchements)
const ETIQUETTE_CHASSE_PX = 6.6;   // largeur moyenne d'un caractère
const ARETES_PAQUET = 40000;       // triangles traités entre deux images pour le calcul des arêtes

/** Point d'ancrage d'une étiquette : barycentre du plus grand polygone de l'emprise (repère local). */
function ancrage(geojson) {
  const polys = geojson.type === 'Polygon' ? [geojson.coordinates] : geojson.coordinates;
  let meilleur = null;
  for (const anneaux of polys) {
    const a = anneaux[0];
    let aire = 0; let cx = 0; let cy = 0;
    for (let i = 0, j = a.length - 1; i < a.length; j = i, i += 1) {
      const f = a[j][0] * a[i][1] - a[i][0] * a[j][1];
      aire += f; cx += (a[j][0] + a[i][0]) * f; cy += (a[j][1] + a[i][1]) * f;
    }
    if (Math.abs(aire) < 1e-9) continue;
    if (!meilleur || Math.abs(aire) > meilleur.aire) meilleur = { aire: Math.abs(aire), x: cx / (3 * aire), y: cy / (3 * aire) };
  }
  if (meilleur) return meilleur;
  const p = polys[0]?.[0]?.[0];
  return p ? { aire: 0, x: p[0], y: p[1] } : null;
}

/** Indice d'objet du triangle `face` d'après les plages [premier triangle, objet, …] (recherche dichotomique). */
function objetDuTriangle(plages, face) {
  let bas = 0;
  let haut = plages.length / 2 - 1;
  while (bas < haut) {
    const milieu = (bas + haut + 1) >> 1;
    if (plages[milieu * 2] <= face) bas = milieu; else haut = milieu - 1;
  }
  return plages[bas * 2 + 1];
}

/** Paramètres d'affichage 3D d'une maquette positionnée, avec l'altitude du terrain (relief). */
export const affichage3d = (pos) => ({ ...pos.affichage, altitude_terrain: pos.altitude_terrain });

export class CoucheMaquettes3D {
  constructor() {
    this.id = 'sig-maquettes-3d';
    this.type = 'custom';
    this.renderingMode = '3d';
    this.modeles = new Map();   // id -> { groupe, opacite, visible, chargement }
    this.reference = null;      // MercatorCoordinate commune
    this.coupe = null;          // hauteur (m au-dessus du terrain) au-dessus de laquelle on coupe
    this.relief = false;        // relief affiché : scène en altitudes NGF
    this.aTravers = true;       // ouvrages dessinés à travers le terrain et les bâtiments 3D (sous-sols visibles)
    this.batiments = false;     // bâtiments 3D affichés (ils occultent, comme le terrain)
    this.selection = null;      // { id, guid, meshes, materiaux }
    this.isole = null;          // id de la maquette isolée
    this.attenuation = 1;       // facteur d'opacité commun (plan des locaux : maquettes atténuées)
    this.matSelection = new THREE.MeshStandardMaterial({
      color: COULEUR_SELECTION, emissive: COULEUR_SELECTION, emissiveIntensity: 0.45,
      roughness: 0.6, side: THREE.DoubleSide, flatShading: true,
    });
    this.chargeur = new GLTFLoader();
    this.chargeur.setMeshoptDecoder(MeshoptDecoder);
    this.plan = null;           // { id, groupe, volume } : plan des locaux d'un niveau
    this.matContour = new THREE.LineBasicMaterial({ color: COULEUR_CONTOUR_PLAN, transparent: true, opacity: 0.9 });
    this.matVolume = new THREE.MeshBasicMaterial({ color: COULEUR_SELECTION, transparent: true, opacity: 0.38,
      depthWrite: false, side: THREE.DoubleSide });
    this.matVolumeArete = new THREE.LineBasicMaterial({ color: 0x8a6a00 });
    // Filtres d'objets (types IFC, familles : génie civil…), communs à toutes les maquettes affichées
    this.types = new Map();     // id -> { types, objets, familles, famille } (serveur : /maquettes/{id}/types)
    this.filtres = { typesMasques: new Set(), famillesMasquees: new Set(), famillesColorees: new Set(), isolee: null };
    this.matsFamilles = new Map();
    this.surTypes = () => {};
    this.nuages = new THREE.Group();   // nuages de points LiDAR (lidar.js), un groupe par dalle ; ajouté à la scène dans onAdd
    this.aides = new THREE.Group();    // repères (mesures, plan de coupe) : seconde passe, jamais coupés
    this.plansCoupe = [];              // plans de coupe quelconques (coupe.js), en plus de la coupe horizontale
    // Rendu (config/georef_maquettes.yaml, affichage_3d.rendu ; réglable dans le volet Vue 3D)
    this.rendu = { aretes: true, angle_aretes_deg: 30, aretes_max_triangles_millions: 1.5, couleur_aretes: '#1f2328',
      opacite_aretes: 0.4, exposition: 1, contraste_lumiere: 1, zoom_aretes: 17 };
    this.matAretes = new THREE.LineBasicMaterial({ color: this.rendu.couleur_aretes, transparent: true,
      opacity: this.rendu.opacite_aretes, depthWrite: false });
    this.surRendu = () => {};
  }

  onAdd(carte, gl) {
    this.carte = carte;
    this.camera = new THREE.Camera();
    this.scene = new THREE.Scene();
    // Éclairage : ciel et sol (ambiance), lumière principale venant du nord-ouest en hauteur, lumière d'appoint du
    // sud-est (faces à l'ombre lisibles). Repère de la scène : y vers le sud.
    this.lumCiel = new THREE.HemisphereLight(0xf1f5ff, 0x7a7266, 1);
    this.lumCle = new THREE.DirectionalLight(0xfff8ee, 2.2);
    this.lumCle.position.set(-0.5, -0.9, 1.3);
    this.lumAppoint = new THREE.DirectionalLight(0xdde8ff, 0.6);
    this.lumAppoint.position.set(0.8, 0.6, 0.35);
    this.scene.add(this.lumCiel, this.lumCle, this.lumAppoint);
    this.renderer = new THREE.WebGLRenderer({ canvas: carte.getCanvas(), context: gl, antialias: true });
    this.renderer.autoClear = false;
    this.renderer.localClippingEnabled = true;
    this.renderer.toneMapping = THREE.NeutralToneMapping;   // correction de tons respectueuse des couleurs IFC
    this._appliquerLumieres();
    this.planCoupe = new THREE.Plane(new THREE.Vector3(0, 0, -1), 0);
    this.scene.add(this.nuages);
    this.sceneAides = new THREE.Scene();
    this.sceneAides.add(this.aides);
  }

  onRemove() {
    for (const id of [...this.modeles.keys()]) this.retirer(id);
    this.renderer?.dispose();
  }

  render(gl, args) {
    if (!this.reference || (!this.modeles.size && !this.plan && !this.nuages.children.length && !this.aides.children.length)) return;
    const principale = new THREE.Matrix4().fromArray(args.defaultProjectionData.mainMatrix);
    const r = this.reference;
    const s = this.echelleRef;
    this.camera.projectionMatrix = principale
      .multiply(new THREE.Matrix4().makeTranslation(r.x, r.y, 0))
      .multiply(new THREE.Matrix4().makeScale(s, s, s));
    this.camera.projectionMatrixInverse.copy(this.camera.projectionMatrix).invert();
    this.coupeScene = this.coupe != null ? this.coupe + this._solCoupe() : null;
    if (this.coupeScene != null) this.planCoupe.constant = this.coupeScene;
    this.renderer.clippingPlanes = [...(this.coupeScene != null ? [this.planCoupe] : []), ...this.plansCoupe];
    this.matAretes.visible = this.carte.getZoom() >= this.rendu.zoom_aretes;   // de loin, pas d'arêtes
    this.renderer.resetState();
    // terrain et bâtiments 3D sont dans le tampon de profondeur et cacheraient les ouvrages enterrés
    if (this.aTravers && (this.relief || this.batiments)) this.renderer.clearDepth();
    this.renderer.render(this.scene, this.camera);
    if (this.aides.children.length) {   // repères : sans coupe, par-dessus (profondeur selon chaque matériau)
      this.renderer.clippingPlanes = [];
      this.renderer.render(this.sceneAides, this.camera);
    }
    this._placerEtiquettes();
  }

  // ---------------------------------------------------------------- étiquettes des locaux

  /** Étiquettes HTML posées sur la carte, ancrées dans la 3D à l'altitude du plancher de leur local :
   *  lisibles quelle que soit l'inclinaison, à la bonne hauteur pour les sous-sols et les étages. */
  _calqueEtiquettes() {
    if (!this.etiquettes) {
      this.etiquettes = { div: document.createElement('div'), liste: [] };
      this.etiquettes.div.className = 'sig-etiquettes-3d';
      this.carte.getCanvasContainer().append(this.etiquettes.div);
    }
    return this.etiquettes;
  }

  _placerEtiquettes() {
    const e = this.etiquettes;
    if (!e?.liste.length) return;
    const visible = this.carte.getZoom() >= ZOOM_ETIQUETTES;
    e.div.hidden = !visible;
    if (!visible) return;
    const canvas = this.carte.getCanvas();
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    const v = new THREE.Vector4();
    const prises = [];
    for (const et of e.liste) {   // les plus grands locaux d'abord : ils gardent leur étiquette
      v.set(et.point.x, et.point.y, et.point.z, 1).applyMatrix4(this.camera.projectionMatrix);
      let montre = v.w > 0;
      let x = 0; let y = 0;
      if (montre) {
        x = ((v.x / v.w + 1) / 2) * w;
        y = ((1 - v.y / v.w) / 2) * h;
        montre = x > -50 && x < w + 50 && y > -20 && y < h + 20;
      }
      if (montre) {
        const lx = et.largeur / 2;
        const ly = ETIQUETTE_HAUTEUR_PX / 2;
        montre = !prises.some((r) => Math.abs(r.x - x) < r.lx + lx && Math.abs(r.y - y) < r.ly + ly);
        if (montre) prises.push({ x, y, lx, ly });
      }
      et.el.hidden = !montre;
      if (montre) et.el.style.transform = `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px) translate(-50%, -50%)`;
    }
  }

  _retirerEtiquettes() {
    if (!this.etiquettes) return;
    this.etiquettes.div.replaceChildren();
    this.etiquettes.liste = [];
  }

  // ---------------------------------------------------------------- modèles

  // Matrice locale (mètres, Z vertical) → scène. gltf : le modèle glTF a l'axe Y vertical et sa propre origine
  // (affichage.origine_modele, en coordonnées locales).
  _matrice(affichage, gltf = true) {
    const o = MercatorCoordinate.fromLngLat(affichage.origine);
    const ax = MercatorCoordinate.fromLngLat(affichage.axe_x);
    const ay = MercatorCoordinate.fromLngLat(affichage.axe_y);
    if (!this.reference) {
      this.reference = o;
      this.echelleRef = o.meterInMercatorCoordinateUnits();
    }
    const r = this.reference;
    const k = 1 / this.echelleRef;   // unités Mercator → mètres de la scène
    const p = affichage.pas_m;
    const sz = o.meterInMercatorCoordinateUnits();
    const m = new THREE.Matrix4().set(
      k * (ax.x - o.x) / p, k * (ay.x - o.x) / p, 0, k * (o.x - r.x),
      k * (ax.y - o.y) / p, k * (ay.y - o.y) / p, 0, k * (o.y - r.y),
      0, 0, k * sz, k * (affichage.decalage_z_m + this._sol(affichage)) * sz,
      0, 0, 0, 1,
    );
    if (!gltf) return m;
    // glTF : Y vertical → Z vertical, puis origine du modèle (x, y, z local → x, z, -y glTF)
    const [ox, oy, oz] = affichage.origine_modele || [0, 0, 0];
    return m.multiply(new THREE.Matrix4().makeRotationX(Math.PI / 2))
      .multiply(new THREE.Matrix4().makeTranslation(ox, oz, -oy));
  }

  /** Altitude du terrain ajoutée à la scène pour une maquette : 0 sur la carte plate ; en relief, altitude
   *  du terrain au centre de la maquette (serveur), à défaut celle du relief affiché à son origine. */
  _sol(affichage) {
    // nuage LiDAR : altitudes NGF dans le fichier ; carte plate : abaissé de l'altitude moyenne du sol de la dalle
    if (affichage.nuage) return this.relief ? 0 : -(affichage.sol || 0);
    if (!this.relief) return 0;
    if (affichage.altitude_terrain != null) return affichage.altitude_terrain;
    return this.carte?.queryTerrainElevation(affichage.origine) ?? 0;
  }

  /** Hauteur par rapport au terrain → hauteur dans la scène (altitude NGF en relief). */
  versScene(affichage, hauteur) {
    return hauteur + this._sol(affichage);
  }

  /** Terrain de référence de la coupe (relief) : maquette du plan affiché, sinon la plus proche du centre. */
  _solCoupe() {
    if (!this.relief) return 0;
    if (this.plan) return this._sol(this.plan.affichage);
    const c = this.carte.getCenter();
    let proche = null;
    let d = Infinity;
    for (const m of this.modeles.values()) {
      if (!m.groupe) continue;
      const [x, y] = m.affichage.origine;
      const e = (x - c.lng) ** 2 + (y - c.lat) ** 2;
      if (e < d) { d = e; proche = m.affichage; }
    }
    return proche ? this._sol(proche) : 0;
  }

  /** Relief affiché ou non : maquettes, plan des locaux et étiquettes replacés en altitude. */
  definirRelief(actif) {
    this.relief = actif;
    for (const m of this.modeles.values()) {
      if (!m.groupe) continue;
      m.groupe.matrix.copy(this._matrice(m.affichage));
      m.groupe.updateMatrixWorld(true);
    }
    for (const g of this.nuages.children) {
      g.matrix.copy(this.matriceNuage(g.userData.affichage));
      g.updateMatrixWorld(true);
    }
    if (this.plan) {
      this.plan.groupe.matrix.copy(this._matrice(this.plan.affichage, false));
      this.plan.groupe.updateMatrixWorld(true);
      for (const et of this.etiquettes?.liste || []) et.point.copy(et.local).applyMatrix4(this.plan.groupe.matrix);
    }
    this.carte?.triggerRepaint();
  }

  // ---------------------------------------------------------------- repère de la scène

  /** Point de référence de la scène (premier objet 3D, ou point donné si la scène est encore vide). */
  assurerReference(lngLat) {
    if (this.reference) return;
    this.reference = MercatorCoordinate.fromLngLat(lngLat);
    this.echelleRef = this.reference.meterInMercatorCoordinateUnits();
  }

  /** [lon, lat] et hauteur de la scène (m) → point de la scène. */
  versPointScene([lon, lat], z = 0) {
    this.assurerReference([lon, lat]);
    const m = MercatorCoordinate.fromLngLat([lon, lat]);
    return new THREE.Vector3((m.x - this.reference.x) / this.echelleRef, (m.y - this.reference.y) / this.echelleRef, z);
  }

  /** Point de la scène → { lon, lat, z } (z : altitude NGF en relief, hauteur / terrain sur la carte plate). */
  depuisScene(v) {
    const r = this.reference;
    const ll = new MercatorCoordinate(r.x + v.x * this.echelleRef, r.y + v.y * this.echelleRef, 0).toLngLat();
    return { lon: ll.lng, lat: ll.lat, z: v.z };
  }

  /** Vrai si le point de la scène est retiré par une coupe (horizontale ou plan quelconque). */
  estCoupe(v) {
    if (this.coupeScene != null && v.z > this.coupeScene + 1e-3) return true;
    return this.plansCoupe.some((p) => p.distanceToPoint(v) < -1e-3);
  }

  /** Point 3D visible sous le point écran (maquette, plan des locaux, nuage LiDAR) : { point, nature } ou null. */
  pointSous(point, toleranceM = 0.5) {
    if (!this.camera || !this.reference) return null;
    const rayon = this._rayon(point);
    rayon.params.Points = { threshold: toleranceM };
    const cibles = [...this.modeles.entries()].filter(([k, m]) => m.groupe && m.visible && (!this.isole || this.isole === k))
      .map(([, m]) => m.groupe);
    if (this.plan) cibles.push(this.plan.groupe);
    for (const g of this.nuages.children) cibles.push(g);
    for (const hit of rayon.intersectObjects(cibles, true)) {
      if (!hit.object.visible || this.estCoupe(hit.point)) continue;
      let o = hit.object;
      while (o && o.userData.idModele == null && !o.userData.affichage) o = o.parent;
      const nature = hit.object.isPoints ? 'nuage LiDAR' : o?.userData.idModele != null ? 'maquette' : 'objet 3D';
      return { point: hit.point.clone(), nature };
    }
    return null;
  }

  /** Plans de coupe quelconques (THREE.Plane, côté positif conservé). */
  definirPlansCoupe(plans) {
    this.plansCoupe = plans;
    this.carte?.triggerRepaint();
  }

  /** Matrice d'une dalle LiDAR (mètres depuis son coin sud-ouest, altitudes NGF) → scène. */
  matriceNuage(affichage) {
    return this._matrice(affichage, false);
  }

  ajouterNuage(groupe) {
    groupe.matrix.copy(this.matriceNuage(groupe.userData.affichage));
    groupe.updateMatrixWorld(true);
    this.nuages.add(groupe);
    this.carte?.triggerRepaint();
  }

  retirerNuage(groupe) {
    this.nuages.remove(groupe);
    this.carte?.triggerRepaint();
  }

  /** Bâtiments 3D affichés ou non (occultation des ouvrages). */
  definirBatiments(visibles) {
    this.batiments = visibles;
    this.carte?.triggerRepaint();
  }

  /** Ouvrages dessinés à travers le terrain et les bâtiments (true) ou masqués par eux. */
  definirATravers(actif) {
    this.aTravers = actif;
    this.carte?.triggerRepaint();
  }

  // ---------------------------------------------------------------- rendu

  /** Réglages du rendu (configuration, puis volet Vue 3D) : lumières, exposition, arêtes. */
  definirRendu(r) {
    const avant = this.rendu;
    this.rendu = { ...this.rendu, ...r };
    this.matAretes.color.set(this.rendu.couleur_aretes);
    this.matAretes.opacity = this.rendu.opacite_aretes;
    this._appliquerLumieres();
    if (avant.aretes !== this.rendu.aretes || avant.angle_aretes_deg !== this.rendu.angle_aretes_deg) {
      for (const id of this.modeles.keys()) this._aretes(id);
    }
    this.surRendu();
    this.carte?.triggerRepaint();
  }

  _appliquerLumieres() {
    if (!this.lumCiel) return;
    const c = this.rendu.contraste_lumiere;
    this.lumCiel.intensity = Math.max(0.2, 1.55 - 0.55 * c);
    this.lumCle.intensity = 2.2 * c;
    this.lumAppoint.intensity = 0.35 + 0.25 * c;
    if (this.renderer) this.renderer.toneMappingExposure = this.rendu.exposition;
  }

  /** Arêtes vives des objets visibles d'une maquette (trait fin), calculées maillage par maillage sans figer
   *  l'interface ; recalculées quand les filtres changent. Pas d'arêtes au-delà du plafond de triangles. */
  async _aretes(id) {
    const m = this.modeles.get(id);
    if (!m?.groupe) return;
    const jeton = (m.jetonAretes = (m.jetonAretes || 0) + 1);
    const maillages = [];
    m.groupe.traverse((o) => { if (o.isMesh && o.geometry.userData.plages) maillages.push(o); });
    for (const o of maillages) for (const a of o.children.filter((x) => x.userData.aretes)) { o.remove(a); a.geometry.dispose(); }
    const triangles = maillages.reduce((t, o) => t + (o.geometry.userData.indexOrigine || o.geometry.index).count / 3, 0);
    if (!this.rendu.aretes || triangles > this.rendu.aretes_max_triangles_millions * 1e6) { this.carte?.triggerRepaint(); return; }
    for (const o of maillages) {
      const g = o.geometry;
      const n = Math.min(g.index.count, Number.isFinite(g.drawRange.count) ? g.drawRange.count : g.index.count);
      if (!n) continue;
      // paquets d'objets entiers (≤ ARETES_PAQUET triangles) : une arête n'est jamais coupée entre deux paquets,
      // et la page reste fluide (une image entre deux paquets)
      const plages = g.userData.plagesVisibles || g.userData.plages;
      const nbTri = n / 3;
      const morceaux = [];
      let debut = 0;
      for (let i = 2; i <= plages.length; i += 2) {
        const fin = i < plages.length ? plages[i] : nbTri;
        if (fin - debut >= ARETES_PAQUET || i === plages.length) {
          await new Promise((r) => { requestAnimationFrame(r); });
          if (m.jetonAretes !== jeton || !this.modeles.has(id)) return;
          const tmp = new THREE.BufferGeometry();
          tmp.setAttribute('position', g.getAttribute('position'));
          tmp.setIndex(new THREE.BufferAttribute(g.index.array.subarray(debut * 3, fin * 3), 1));
          morceaux.push(new THREE.EdgesGeometry(tmp, this.rendu.angle_aretes_deg).getAttribute('position').array);
          debut = fin;
        }
      }
      const tout = new Float32Array(morceaux.reduce((t, x) => t + x.length, 0));
      let k = 0;
      for (const x of morceaux) { tout.set(x, k); k += x.length; }
      const geoAretes = new THREE.BufferGeometry();
      geoAretes.setAttribute('position', new THREE.BufferAttribute(tout, 3));
      const lignes = new THREE.LineSegments(geoAretes, this.matAretes);
      lignes.userData.aretes = true;
      lignes.raycast = () => {};   // ni clic ni mesure sur les arêtes
      lignes.renderOrder = 1;
      o.add(lignes);
      lignes.updateMatrixWorld(true);
    }
    this._appliquer(id);
  }

  /** Lit un GLB et prépare ses matériaux (ombrage à facettes, mats, deux faces). */
  _lireGlb(url, progression) {
    return new Promise((resolve, reject) => {
      this.chargeur.load(url, (gltf) => {
        gltf.scene.traverse((o) => {
          if (!o.isMesh) return;
          for (const mat of [o.material].flat()) {
            // pas de normales dans les modèles : normales calculées dans le shader
            mat.flatShading = true;
            if (mat.isMeshStandardMaterial) { mat.roughness = Math.max(mat.roughness, 0.75); mat.metalness = 0; }
            mat.side = THREE.DoubleSide;
            // faces légèrement reculées : les arêtes tracées par-dessus ne scintillent pas
            mat.polygonOffset = true;
            mat.polygonOffsetFactor = 1;
            mat.polygonOffsetUnits = 1;
            mat.userData.opaciteOrigine = mat.opacity;
            mat.userData.transparentOrigine = mat.transparent;
          }
        });
        resolve(gltf.scene);
      }, (e) => progression?.(e.total ? e.loaded / e.total : null), reject);
    });
  }

  charger(id, url, affichage, progression) {
    if (this.modeles.has(id)) return this.modeles.get(id).chargement;
    const entree = { groupe: null, opacite: 1, visible: true, detail: null, voirDetail: false, affichage };
    entree.chargement = this._lireGlb(url, progression).then((scene) => {
      const groupe = new THREE.Group();
      groupe.matrixAutoUpdate = false;
      groupe.matrix.copy(this._matrice(affichage));
      groupe.add(scene);
      groupe.userData.idModele = id;
      groupe.updateMatrixWorld(true);
      entree.groupe = groupe;
      if (this.modeles.get(id) !== entree) return groupe;   // retiré pendant le chargement
      this.scene.add(groupe);
      this._appliquer(id);
      this._filtrer(id);
      this._aretes(id);
      return groupe;
    }, (err) => {
      if (this.modeles.get(id) === entree) this.modeles.delete(id);
      throw err;
    });
    this.modeles.set(id, entree);
    return entree.chargement;
  }

  /** Petits éléments (second GLB) : chargés une fois, puis affichés ou masqués selon le zoom. */
  async montrerDetail(id, url, visible) {
    const m = this.modeles.get(id);
    if (!m) return;
    m.voirDetail = visible;
    if (visible && !m.detail) {
      m.detail = 'chargement';
      try {
        await m.chargement;
        const scene = await this._lireGlb(url);
        if (this.modeles.get(id) !== m) return;
        m.groupe.add(scene);
        m.groupe.updateMatrixWorld(true);
        m.detail = scene;
        this._filtrer(id);
        this._aretes(id);
      } catch (e) {
        m.detail = null;
        return;
      }
    }
    if (m.detail && m.detail !== 'chargement') {
      m.detail.visible = m.voirDetail;
      this._appliquer(id);
    }
  }

  retirer(id) {
    const m = this.modeles.get(id);
    if (!m) return;
    if (this.selection?.id === id) this.deselectionner();
    if (m.groupe) {
      this.scene.remove(m.groupe);
      m.groupe.traverse((o) => {
        if (o.isMesh) {
          o.geometry.dispose();
          for (const mat of [o.material].flat()) if (!mat.userData.famille) mat.dispose();
        }
      });
    }
    this.modeles.delete(id);
    if (this.types.delete(id)) this.surTypes();
    this.carte?.triggerRepaint();
  }

  // ---------------------------------------------------------------- filtres d'objets

  /** Types IFC et familles des objets d'une maquette chargée (indices alignés sur sa table d'objets). */
  definirTypes(id, donnees) {
    if (!this.modeles.has(id)) return;
    this.types.set(id, donnees);
    this._filtrer(id);
    if (this._masques(donnees)) this._aretes(id);   // filtres déjà actifs : arêtes des seuls objets visibles
    this.surTypes();
  }

  /** Filtres : { typesMasques: Set(type IFC), famillesMasquees: Set(indice, -1 = autres),
   *  famillesColorees: Set(indice), isolee: indice de famille ou null }. */
  definirFiltres(filtres) {
    this.filtres = { ...this.filtres, ...filtres };
    for (const id of this.modeles.keys()) { this._filtrer(id); this._aretes(id); }
  }

  /** Récapitulatif des maquettes chargées : familles (nom, couleur, nombre d'objets) et types IFC (nombre). */
  recapitulatif() {
    const familles = [];
    const types = new Map();
    let autres = 0;
    for (const t of this.types.values()) {
      t.familles.forEach((f, i) => {
        if (!familles[i]) familles[i] = { nom: f.nom, couleur: f.couleur, nb: 0 };
        familles[i].nb += f.nb;
      });
      for (const f of t.famille) if (f < 0) autres += 1;
      for (const k of t.objets) types.set(t.types[k], (types.get(t.types[k]) || 0) + 1);
    }
    return { familles, autres, types: [...types.entries()].sort((a, b) => b[1] - a[1]) };
  }

  _matFamille(couleur) {
    if (!this.matsFamilles.has(couleur)) {
      this.matsFamilles.set(couleur, new THREE.MeshStandardMaterial({
        color: couleur, roughness: 0.8, metalness: 0, side: THREE.DoubleSide, flatShading: true,
        polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1 }));
      this.matsFamilles.get(couleur).userData.famille = true;   // partagé entre maquettes
    }
    return this.matsFamilles.get(couleur);
  }

  /** Objets masqués (1) d'une maquette d'après les filtres, ou null si tout est visible. */
  _masques(t) {
    const f = this.filtres;
    if (!t || (!f.typesMasques.size && !f.famillesMasquees.size && f.isolee == null)) return null;
    const masque = new Uint8Array(t.objets.length);
    let n = 0;
    for (let i = 0; i < masque.length; i += 1) {
      const fam = t.famille[i];
      if (f.typesMasques.has(t.types[t.objets[i]]) || f.famillesMasquees.has(fam) || (f.isolee != null && fam !== f.isolee)) {
        masque[i] = 1;
        n += 1;
      }
    }
    return n ? masque : null;
  }

  /** Applique les filtres à une maquette. Objets masqués : triangles retirés d'un index filtré (alloué une fois
   *  par maillage, réutilisé avec une plage de dessin : pas de nouveau tampon graphique à chaque changement) ;
   *  l'index d'origine reste pour la sélection et le retour sans filtre. Familles colorées : redessinées par-dessus
   *  dans leur couleur (surcouches partageant les sommets du maillage). */
  _filtrer(id) {
    const m = this.modeles.get(id);
    if (!m?.groupe) return;
    const t = this.types.get(id);
    const masque = this._masques(t);
    const colorees = t && this.filtres.famillesColorees.size ? this.filtres.famillesColorees : null;
    m.groupe.traverse((o) => {
      const g = o.isMesh && o.geometry;
      const plages = g?.userData.plages;
      if (!plages) return;
      const origine = g.userData.indexOrigine || (g.userData.indexOrigine = g.index);
      const index = origine.array;
      const nbTri = index.length / 3;
      const remplir = (cible, garder) => {   // copie dans cible les triangles des objets gardés ; plages visibles
        const visibles = [];
        let n = 0;
        for (let i = 0; i < plages.length; i += 2) {
          if (!garder(plages[i + 1])) continue;
          const fin = i + 2 < plages.length ? plages[i + 2] : nbTri;
          visibles.push(n, plages[i + 1]);
          cible.array.set(index.subarray(plages[i] * 3, fin * 3), n * 3);
          n += fin - plages[i];
        }
        cible.needsUpdate = true;
        return { n, visibles };
      };
      if (masque) {
        if (!g.userData.indexFiltre) {
          g.userData.indexFiltre = new THREE.BufferAttribute(new (index.constructor)(index.length), 1)
            .setUsage(THREE.DynamicDrawUsage);
        }
        const { n, visibles } = remplir(g.userData.indexFiltre, (objet) => !masque[objet]);
        if (g.index !== g.userData.indexFiltre) g.setIndex(g.userData.indexFiltre);
        g.setDrawRange(0, n * 3);
        g.userData.plagesVisibles = visibles;   // le clic retrouve l'objet dans l'index filtré
      } else if (g.index !== origine) {
        g.setIndex(origine);
        g.setDrawRange(0, Infinity);
        g.userData.plagesVisibles = null;
      }
      o.userData.surcouches ||= new Map();   // famille -> maillage
      for (const [fam, s] of o.userData.surcouches) s.visible = Boolean(colorees?.has(fam));
      if (!colorees) return;
      for (const fam of colorees) {
        let s = o.userData.surcouches.get(fam);
        if (!s) {
          const geo = new THREE.BufferGeometry();
          geo.setAttribute('position', g.getAttribute('position'));   // partagé avec le maillage
          geo.setIndex(new THREE.BufferAttribute(new (index.constructor)(index.length), 1).setUsage(THREE.DynamicDrawUsage));
          s = new THREE.Mesh(geo, this._matFamille(t.familles[fam]?.couleur || '#888888'));
          s.renderOrder = 1;
          o.add(s);
          s.updateMatrixWorld(true);
          o.userData.surcouches.set(fam, s);
        }
        const { n } = remplir(s.geometry.index, (objet) => t.famille[objet] === fam && !(masque && masque[objet]));
        s.geometry.setDrawRange(0, n * 3);
        s.visible = n > 0;
      }
    });
    this.carte?.triggerRepaint();
  }

  estCharge(id) {
    return Boolean(this.modeles.get(id)?.groupe);
  }

  definir(id, { visible, opacite } = {}) {
    const m = this.modeles.get(id);
    if (!m) return;
    if (visible != null) m.visible = visible;
    if (opacite != null) m.opacite = opacite;
    this._appliquer(id);
  }

  isoler(id) {
    this.isole = id;
    for (const k of this.modeles.keys()) this._appliquer(k);
  }

  /** Atténue toutes les maquettes (1 : opacité normale). */
  attenuer(facteur) {
    this.attenuation = facteur;
    for (const k of this.modeles.keys()) this._appliquer(k);
  }

  _appliquer(id) {
    const m = this.modeles.get(id);
    if (!m?.groupe) return;
    m.groupe.visible = m.visible;
    const attenue = this.isole && this.isole !== id;
    const op = m.opacite * (attenue ? 0.12 : 1) * this.attenuation;
    m.groupe.traverse((o) => {
      if (o.userData.aretes) o.visible = op >= 0.99;   // maquette atténuée : pas d'arêtes (lisibilité)
      if (!o.isMesh) return;
      for (const mat of [o.material].flat()) {
        if (mat === this.matSelection || mat.userData.famille) continue;
        const base = mat.userData.opaciteOrigine ?? 1;
        mat.opacity = base * op;
        mat.transparent = mat.userData.transparentOrigine || op < 1;
        mat.depthWrite = op >= 1 || !mat.transparent;
        mat.needsUpdate = true;
      }
    });
    this.carte?.triggerRepaint();
  }

  definirCoupe(hauteur) {
    this.coupe = hauteur;
    this.carte?.triggerRepaint();
  }

  // ---------------------------------------------------------------- plan des locaux

  static _formes(geojson) {
    const polys = geojson.type === 'Polygon' ? [geojson.coordinates] : geojson.coordinates;
    return polys.map((anneaux) => {
      const forme = new THREE.Shape(anneaux[0].map(([x, y]) => new THREE.Vector2(x, y)));
      for (const trou of anneaux.slice(1)) forme.holes.push(new THREE.Path(trou.map(([x, y]) => new THREE.Vector2(x, y))));
      return { forme, contour: anneaux[0] };
    });
  }

  /** Plan d'un niveau : emprises des locaux, colorées, posées à l'altitude de leur plancher. */
  afficherPlan(id, affichage, locaux, couleur) {
    this.retirerPlan();
    const groupe = new THREE.Group();
    groupe.matrixAutoUpdate = false;
    groupe.matrix.copy(this._matrice(affichage, false));
    groupe.userData.idModele = id;
    for (const l of locaux) {
      if (!l.emprise_locale || l.z_min == null) continue;
      const noeud = new THREE.Group();
      noeud.name = l.guid;
      noeud.userData.local = l;
      const mat = new THREE.MeshBasicMaterial({ color: couleur(l), transparent: true, opacity: 0.62,
        side: THREE.DoubleSide, depthWrite: false });
      for (const { forme, contour } of CoucheMaquettes3D._formes(l.emprise_locale)) {
        const geo = new THREE.ShapeGeometry(forme);
        geo.translate(0, 0, l.z_min + 0.03);
        const surface = new THREE.Mesh(geo, mat);
        surface.renderOrder = 2;
        noeud.add(surface);
        const pts = contour.map(([x, y]) => new THREE.Vector3(x, y, l.z_min + 0.05));
        const ligne = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), this.matContour);
        ligne.renderOrder = 3;
        noeud.add(ligne);
      }
      groupe.add(noeud);
    }
    groupe.updateMatrixWorld(true);
    this.plan = { id, groupe, volume: null, affichage };
    this.scene.add(groupe);
    // étiquettes : code du local, ancrées au plancher, les plus grands locaux d'abord
    const calque = this._calqueEtiquettes();
    this._retirerEtiquettes();
    // préfixe commun aux codes du niveau (ouvrage, bâtiment, niveau : « 49MAS-B-S01- ») retiré des étiquettes
    const codes = locaux.map((l) => l.code).filter(Boolean);
    let prefixe = codes[0] || '';
    for (const c of codes) while (prefixe && !c.startsWith(prefixe)) prefixe = prefixe.slice(0, -1);
    prefixe = codes.length > 1 ? prefixe.slice(0, prefixe.lastIndexOf('-') + 1) : '';
    for (const l of locaux) {
      const texte = l.code ? l.code.slice(prefixe.length) || l.code : l.nom;
      const a = texte && l.emprise_locale && l.z_min != null && ancrage(l.emprise_locale);
      if (!a) continue;
      const el = document.createElement('span');
      el.className = 'sig-etiquette-3d';
      el.textContent = texte;
      el.title = [l.code, l.nom_long || l.nom, l.typologie].filter(Boolean).join(' · ');
      calque.div.append(el);
      const local = new THREE.Vector3(a.x, a.y, l.z_min + 0.05);
      calque.liste.push({ el, aire: a.aire, largeur: texte.length * ETIQUETTE_CHASSE_PX + 8,
        local, point: local.clone().applyMatrix4(groupe.matrix) });
    }
    calque.liste.sort((p, q) => q.aire - p.aire);
    this.carte.triggerRepaint();
  }

  retirerPlan() {
    this._retirerEtiquettes();
    if (!this.plan) return;
    this.scene.remove(this.plan.groupe);
    this.plan.groupe.traverse((o) => {
      o.geometry?.dispose();
      if (o.material && o.material !== this.matContour && o.material !== this.matVolume && o.material !== this.matVolumeArete) o.material.dispose();
    });
    this.plan = null;
    this.carte?.triggerRepaint();
  }

  /** Volume doré du local choisi (extrusion de son emprise entre ses altitudes basse et haute). */
  montrerVolume(guid) {
    if (!this.plan) return;
    if (this.plan.volume) {
      this.plan.groupe.remove(this.plan.volume);
      this.plan.volume.traverse((o) => o.geometry?.dispose());
      this.plan.volume = null;
    }
    const noeud = guid && this.plan.groupe.children.find((n) => n.name === guid);
    if (noeud) {
      const l = noeud.userData.local;
      const volume = new THREE.Group();
      for (const { forme } of CoucheMaquettes3D._formes(l.emprise_locale)) {
        const geo = new THREE.ExtrudeGeometry(forme, { depth: Math.max(0.1, l.z_max - l.z_min), bevelEnabled: false });
        geo.translate(0, 0, l.z_min);
        const m = new THREE.Mesh(geo, this.matVolume);
        m.renderOrder = 4;
        volume.add(m, new THREE.LineSegments(new THREE.EdgesGeometry(geo), this.matVolumeArete));
      }
      this.plan.groupe.add(volume);
      volume.updateMatrixWorld(true);
      this.plan.volume = volume;
    }
    this.carte.triggerRepaint();
  }

  // ---------------------------------------------------------------- sélection

  _rayon(point) {
    const canvas = this.carte.getCanvas();
    const x = (point.x / canvas.clientWidth) * 2 - 1;
    const y = 1 - (point.y / canvas.clientHeight) * 2;
    const inv = this.camera.projectionMatrixInverse;
    const a = new THREE.Vector3(x, y, -1).applyMatrix4(inv);
    const b = new THREE.Vector3(x, y, 1).applyMatrix4(inv);
    return new THREE.Raycaster(a, b.sub(a).normalize());
  }

  /** Local du plan affiché sous le point écran : { id, guid, local } ou null. */
  localSous(point) {
    if (!this.plan || !this.camera || !this.reference) return null;
    for (const hit of this._rayon(point).intersectObject(this.plan.groupe, true)) {
      if (!hit.object.isMesh || hit.object.parent === this.plan.volume) continue;
      if (this.coupeScene != null && hit.point.z > this.coupeScene + 0.5) continue;
      if (this.plansCoupe.some((p) => p.distanceToPoint(hit.point) < 0)) continue;
      const noeud = hit.object.parent;
      if (noeud?.userData.local) return { id: this.plan.id, guid: noeud.name, local: noeud.userData.local };
    }
    return null;
  }

  /** Objet de maquette sous le point écran { x, y } (pixels CSS) : { id, guid, groupe }, ou null. */
  objetSous(point) {
    if (!this.modeles.size || !this.camera || !this.reference) return null;
    const rayon = this._rayon(point);
    const cibles = [...this.modeles.entries()]
      .filter(([k, m]) => m.groupe && m.visible && (!this.isole || this.isole === k)).map(([, m]) => m.groupe);
    for (const hit of rayon.intersectObjects(cibles, true)) {
      if (this.estCoupe(hit.point)) continue;
      const ud = hit.object.geometry.userData;
      const plages = ud.plagesVisibles || ud.plages;
      if (!plages || hit.faceIndex == null) continue;   // surbrillance, surcouche (sans plages) ou objet étranger
      let scene = hit.object;
      while (scene && !scene.userData.guids) scene = scene.parent;
      let racine = scene;
      while (racine && racine.userData.idModele == null) racine = racine.parent;
      if (!scene || !racine) continue;
      const objet = objetDuTriangle(plages, hit.faceIndex);
      return { id: racine.userData.idModele, guid: scene.userData.guids[objet], groupe: racine };
    }
    return null;
  }

  /** Surbrillance d'un objet : ses triangles (dans chaque maillage et chaque GLB) redessinés en doré. */
  selectionner(cible) {
    this.deselectionner();
    if (!cible) return;
    const ajouts = [];
    cible.groupe.traverse((scene) => {
      const guids = scene.userData.guids;
      if (!guids) return;
      const objet = guids.indexOf(cible.guid);
      if (objet < 0) return;
      scene.traverse((o) => {
        const plages = o.isMesh && o.geometry.userData.plages;
        if (!plages) return;
        const index = (o.geometry.userData.indexOrigine || o.geometry.index).array;
        const morceaux = [];
        for (let i = 0; i < plages.length; i += 2) {
          if (plages[i + 1] !== objet) continue;
          const fin = i + 2 < plages.length ? plages[i + 2] : index.length / 3;
          morceaux.push(index.subarray(plages[i] * 3, fin * 3));
        }
        if (!morceaux.length) return;
        const sousIndex = new Uint32Array(morceaux.reduce((t, m) => t + m.length, 0));
        let k = 0;
        for (const m of morceaux) { sousIndex.set(m, k); k += m.length; }
        const geo = new THREE.BufferGeometry();
        geo.setAttribute('position', o.geometry.getAttribute('position'));   // partagé avec le maillage
        geo.setIndex(new THREE.BufferAttribute(sousIndex, 1));
        const surbrillance = new THREE.Mesh(geo, this.matSelection);
        surbrillance.renderOrder = 1;
        o.add(surbrillance);
        surbrillance.updateMatrixWorld(true);
        ajouts.push(surbrillance);
      });
    });
    this.selection = { id: cible.id, guid: cible.guid, ajouts };
    this.carte.triggerRepaint();
  }

  /** Objet sélectionné : centre [lon, lat], hauteur du centre dans la scène (m par rapport au terrain, altitude
   *  NGF en relief : c'est l'élévation attendue par la caméra dans les deux cas) et plus grande
   *  dimension (m), d'après ses triangles. null sans sélection. */
  boiteSelection() {
    if (!this.selection?.ajouts.length || !this.reference) return null;
    const boite = new THREE.Box3();
    const v = new THREE.Vector3();
    for (const m of this.selection.ajouts) {
      const pos = m.geometry.getAttribute('position');
      const index = m.geometry.index.array;
      m.updateMatrixWorld(true);
      for (let i = 0; i < index.length; i += 1) boite.expandByPoint(v.fromBufferAttribute(pos, index[i]).applyMatrix4(m.matrixWorld));
    }
    if (boite.isEmpty()) return null;
    const c = boite.getCenter(new THREE.Vector3());
    const t = boite.getSize(new THREE.Vector3());
    // scène : mètres autour du point de référence (x vers l'est, y vers le sud en Mercator, z vers le haut)
    const r = this.reference;
    const ll = new MercatorCoordinate(r.x + c.x * this.echelleRef, r.y + c.y * this.echelleRef, 0).toLngLat();
    return { centre: [ll.lng, ll.lat], hauteur: c.z, taille: Math.max(t.x, t.y, t.z) };
  }

  deselectionner() {
    if (!this.selection) return;
    // la surbrillance partage les positions du maillage : pas de dispose() (il les retirerait aussi du GPU)
    for (const m of this.selection.ajouts) m.removeFromParent();
    this.selection = null;
    this.carte?.triggerRepaint();
  }
}

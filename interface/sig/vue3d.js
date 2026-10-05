// Maquettes 3D dans la carte : calque MapLibre personnalisé dessiné avec Three.js dans le contexte WebGL
// de la carte. Les maquettes partagent la caméra de la carte (zoom, rotation, inclinaison) et se superposent
// aux couches SIG à leur position géoréférencée.
//
// Repère : les modèles GLB sont en mètres « locaux » (origine du site IFC, axe Y vertical glTF). Le serveur
// fournit trois points de contrôle (origine, +100 m en X, +100 m en Y) en WGS84 et le décalage vertical
// par rapport au terrain ; la matrice locale → Mercator en est déduite ici, en double précision. La scène
// Three.js est exprimée en mètres autour d'un point de référence commun (unités Mercator ramenées au mètre) :
// les nombres restent petits pour le GPU et l'éclairage à facettes reste précis.
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { MercatorCoordinate } from 'maplibre-gl';

const COULEUR_SELECTION = 0xe8b400;
const MOTIF_GUID = /^[0-9A-Za-z_$]{22}$/;

export class CoucheMaquettes3D {
  constructor() {
    this.id = 'sig-maquettes-3d';
    this.type = 'custom';
    this.renderingMode = '3d';
    this.modeles = new Map();   // id -> { groupe, opacite, visible, chargement }
    this.reference = null;      // MercatorCoordinate commune
    this.coupe = null;          // hauteur (m au-dessus du terrain) au-dessus de laquelle on coupe
    this.selection = null;      // { id, guid, meshes, materiaux }
    this.isole = null;          // id de la maquette isolée
    this.matSelection = new THREE.MeshStandardMaterial({
      color: COULEUR_SELECTION, emissive: COULEUR_SELECTION, emissiveIntensity: 0.45,
      roughness: 0.6, side: THREE.DoubleSide, flatShading: true,
    });
    this.chargeur = new GLTFLoader();
  }

  onAdd(carte, gl) {
    this.carte = carte;
    this.camera = new THREE.Camera();
    this.scene = new THREE.Scene();
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x8a8f96, 1.4));
    const soleil = new THREE.DirectionalLight(0xffffff, 2.2);
    soleil.position.set(-0.6, -1.0, 1.4);   // venant du nord-ouest, en hauteur (repère de la scène : y vers le sud)
    this.scene.add(soleil);
    this.renderer = new THREE.WebGLRenderer({ canvas: carte.getCanvas(), context: gl, antialias: true });
    this.renderer.autoClear = false;
    this.renderer.localClippingEnabled = true;
    this.plan = new THREE.Plane(new THREE.Vector3(0, 0, -1), 0);
  }

  onRemove() {
    for (const id of [...this.modeles.keys()]) this.retirer(id);
    this.renderer?.dispose();
  }

  render(gl, args) {
    if (!this.reference || !this.modeles.size) return;
    const principale = new THREE.Matrix4().fromArray(args.defaultProjectionData.mainMatrix);
    const r = this.reference;
    const s = this.echelleRef;
    this.camera.projectionMatrix = principale
      .multiply(new THREE.Matrix4().makeTranslation(r.x, r.y, 0))
      .multiply(new THREE.Matrix4().makeScale(s, s, s));
    this.camera.projectionMatrixInverse.copy(this.camera.projectionMatrix).invert();
    if (this.coupe != null) {
      this.plan.constant = this.coupe;
      this.renderer.clippingPlanes = [this.plan];
    } else {
      this.renderer.clippingPlanes = [];
    }
    this.renderer.resetState();
    this.renderer.render(this.scene, this.camera);
  }

  // ---------------------------------------------------------------- modèles

  _matrice(affichage) {
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
      0, 0, k * sz, k * affichage.decalage_z_m * sz,
      0, 0, 0, 1,
    );
    // glTF : Y vertical → Z vertical
    return m.multiply(new THREE.Matrix4().makeRotationX(Math.PI / 2));
  }

  charger(id, url, affichage, progression) {
    if (this.modeles.has(id)) return this.modeles.get(id).chargement;
    const entree = { groupe: null, opacite: 1, visible: true };
    entree.chargement = new Promise((resolve, reject) => {
      this.chargeur.load(url, (gltf) => {
        const groupe = new THREE.Group();
        groupe.matrixAutoUpdate = false;
        groupe.matrix.copy(this._matrice(affichage));
        groupe.add(gltf.scene);
        groupe.userData.idModele = id;
        gltf.scene.traverse((o) => {
          if (o.isMesh) {
            const mats = Array.isArray(o.material) ? o.material : [o.material];
            for (const mat of mats) {
              // Les GLB d'IfcOpenShell n'ont pas de normales : ombrage à facettes (normales calculées dans le shader),
              // matériaux mats (les rugosités exportées sont souvent quasi nulles).
              mat.flatShading = true;
              if (mat.isMeshStandardMaterial) { mat.roughness = Math.max(mat.roughness, 0.75); mat.metalness = 0; }
              mat.side = THREE.DoubleSide;
              mat.userData.opaciteOrigine = mat.opacity;
              mat.userData.transparentOrigine = mat.transparent;
            }
          }
        });
        groupe.updateMatrixWorld(true);
        entree.groupe = groupe;
        if (!this.modeles.has(id)) return;   // retiré pendant le chargement
        this.scene.add(groupe);
        this._appliquer(id);
        this.carte.triggerRepaint();
        resolve(groupe);
      }, (e) => progression?.(e.total ? e.loaded / e.total : null), (err) => {
        this.modeles.delete(id);
        reject(err);
      });
    });
    this.modeles.set(id, entree);
    return entree.chargement;
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
          for (const mat of [o.material].flat()) mat.dispose();
        }
      });
    }
    this.modeles.delete(id);
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

  _appliquer(id) {
    const m = this.modeles.get(id);
    if (!m?.groupe) return;
    m.groupe.visible = m.visible;
    const attenue = this.isole && this.isole !== id;
    const op = m.opacite * (attenue ? 0.12 : 1);
    m.groupe.traverse((o) => {
      if (!o.isMesh) return;
      for (const mat of [o.material].flat()) {
        if (mat === this.matSelection) continue;
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

  // ---------------------------------------------------------------- sélection

  /** Objet de maquette sous le point écran { x, y } (pixels CSS), ou null. */
  objetSous(point) {
    if (!this.modeles.size || !this.camera) return null;
    const canvas = this.carte.getCanvas();
    const x = (point.x / canvas.clientWidth) * 2 - 1;
    const y = 1 - (point.y / canvas.clientHeight) * 2;
    const inv = this.camera.projectionMatrixInverse;
    const a = new THREE.Vector3(x, y, -1).applyMatrix4(inv);
    const b = new THREE.Vector3(x, y, 1).applyMatrix4(inv);
    const rayon = new THREE.Raycaster(a, b.sub(a).normalize());
    const cibles = [...this.modeles.entries()]
      .filter(([k, m]) => m.groupe && m.visible && (!this.isole || this.isole === k)).map(([, m]) => m.groupe);
    for (const hit of rayon.intersectObjects(cibles, true)) {
      if (this.coupe != null && hit.point.z > this.coupe) continue;
      let o = hit.object;
      while (o && !MOTIF_GUID.test(o.name || '')) o = o.parent;
      let racine = hit.object;
      while (racine && racine.userData.idModele == null) racine = racine.parent;
      if (o && racine) return { id: racine.userData.idModele, guid: o.name, noeud: o };
    }
    return null;
  }

  selectionner(cible) {
    this.deselectionner();
    if (!cible) return;
    const meshes = [];
    cible.noeud.traverse((o) => { if (o.isMesh) meshes.push(o); });
    this.selection = { id: cible.id, guid: cible.guid, meshes, materiaux: meshes.map((m) => m.material) };
    for (const m of meshes) m.material = this.matSelection;
    this.carte.triggerRepaint();
  }

  deselectionner() {
    if (!this.selection) return;
    this.selection.meshes.forEach((m, i) => { m.material = this.selection.materiaux[i]; });
    this.selection = null;
    this.carte?.triggerRepaint();
  }
}

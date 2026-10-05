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
const COULEUR_CONTOUR_PLAN = 0x2b3137;
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
    this.attenuation = 1;       // facteur d'opacité commun (plan des locaux : maquettes atténuées)
    this.matSelection = new THREE.MeshStandardMaterial({
      color: COULEUR_SELECTION, emissive: COULEUR_SELECTION, emissiveIntensity: 0.45,
      roughness: 0.6, side: THREE.DoubleSide, flatShading: true,
    });
    this.chargeur = new GLTFLoader();
    this.plan = null;           // { id, groupe, volume } : plan des locaux d'un niveau
    this.matContour = new THREE.LineBasicMaterial({ color: COULEUR_CONTOUR_PLAN, transparent: true, opacity: 0.9 });
    this.matVolume = new THREE.MeshBasicMaterial({ color: COULEUR_SELECTION, transparent: true, opacity: 0.38,
      depthWrite: false, side: THREE.DoubleSide });
    this.matVolumeArete = new THREE.LineBasicMaterial({ color: 0x8a6a00 });
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
    this.planCoupe = new THREE.Plane(new THREE.Vector3(0, 0, -1), 0);
  }

  onRemove() {
    for (const id of [...this.modeles.keys()]) this.retirer(id);
    this.renderer?.dispose();
  }

  render(gl, args) {
    if (!this.reference || (!this.modeles.size && !this.plan)) return;
    const principale = new THREE.Matrix4().fromArray(args.defaultProjectionData.mainMatrix);
    const r = this.reference;
    const s = this.echelleRef;
    this.camera.projectionMatrix = principale
      .multiply(new THREE.Matrix4().makeTranslation(r.x, r.y, 0))
      .multiply(new THREE.Matrix4().makeScale(s, s, s));
    this.camera.projectionMatrixInverse.copy(this.camera.projectionMatrix).invert();
    if (this.coupe != null) {
      this.planCoupe.constant = this.coupe;
      this.renderer.clippingPlanes = [this.planCoupe];
    } else {
      this.renderer.clippingPlanes = [];
    }
    this.renderer.resetState();
    this.renderer.render(this.scene, this.camera);
  }

  // ---------------------------------------------------------------- modèles

  // Matrice locale (mètres, Z vertical) → scène. gltf : le modèle glTF a l'axe Y vertical.
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
      0, 0, k * sz, k * affichage.decalage_z_m * sz,
      0, 0, 0, 1,
    );
    // glTF : Y vertical → Z vertical
    return gltf ? m.multiply(new THREE.Matrix4().makeRotationX(Math.PI / 2)) : m;
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
    this.plan = { id, groupe, volume: null };
    this.scene.add(groupe);
    this.carte.triggerRepaint();
  }

  retirerPlan() {
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
      if (this.coupe != null && hit.point.z > this.coupe + 0.5) continue;
      const noeud = hit.object.parent;
      if (noeud?.userData.local) return { id: this.plan.id, guid: noeud.name, local: noeud.userData.local };
    }
    return null;
  }

  /** Objet de maquette sous le point écran { x, y } (pixels CSS), ou null. */
  objetSous(point) {
    if (!this.modeles.size || !this.camera || !this.reference) return null;
    const rayon = this._rayon(point);
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

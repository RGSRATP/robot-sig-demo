// Cadrage de la caméra autour d'un point en hauteur : objet ou local en sous-sol, en étage. Le centre de la vue
// n'est pas collé au sol (carte créée avec centerClampedToGround: false) : il est placé à la hauteur de l'objet,
// et la rotation, l'inclinaison et le zoom au clavier tournent autour de lui.
// Relief : les hauteurs sont des altitudes NGF ; hors cadrage sur un objet, le centre de la vue suit le terrain
// (centerClampedToGround activé par auSol, désactivé par cadrer et hauteurVue).

const MARGE_SOUS_SOL_M = 0.5;    // centre de la vue plus bas que le sol de cette marge : caméra libre sous le sol
const M_PAR_PX_Z0 = 78271.517;   // mètres par pixel CSS au zoom 0, à l'équateur (tuiles de 512 px)
const PART_DE_LA_VUE = 3;        // l'objet occupe environ un tiers de la vue
const ZOOM_MIN_CADRAGE = 15;

/** Zoom auquel `taille` mètres occupent 1/PART_DE_LA_VUE de la plus petite dimension de la carte. */
export function zoomPourTaille(carte, latitude, taille) {
  const c = carte.getCanvas();
  const px = Math.min(c.clientWidth, c.clientHeight) || 600;
  const metres = Math.max(taille, 1.5) * PART_DE_LA_VUE;
  const zoom = Math.log2((M_PAR_PX_Z0 * Math.cos((latitude * Math.PI) / 180) * px) / metres);
  return Math.min(Math.max(zoom, ZOOM_MIN_CADRAGE), carte.getMaxZoom());
}

/** Centre la vue sur `centre` ([lon, lat]) à `hauteur` mètres par rapport au terrain, cadré sur `taille` mètres.
 *  Animation faite ici (jumpTo image par image) : easeTo de MapLibre 6 ignore l'option `elevation`. */
export function cadrer(carte, { centre, hauteur = 0, taille = 10 }, { pitch, duree = 900 } = {}) {
  const depart = { c: carte.getCenter(), z: carte.getZoom(), h: carte.getCenterElevation(), p: carte.getPitch() };
  carte.setCenterClampedToGround(false);
  const arrivee = { z: zoomPourTaille(carte, centre[1], taille), p: pitch ?? Math.max(carte.getPitch(), 50) };
  const lisse = (t) => (t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2);
  const debut = performance.now();
  carte.stop();
  const pas = (maintenant) => {
    const t = Math.min(1, (maintenant - debut) / duree);
    const k = lisse(t);
    carte.jumpTo({
      center: [depart.c.lng + (centre[0] - depart.c.lng) * k, depart.c.lat + (centre[1] - depart.c.lat) * k],
      zoom: depart.z + (arrivee.z - depart.z) * k,
      elevation: depart.h + (hauteur - depart.h) * k,
      pitch: depart.p + (arrivee.p - depart.p) * k,
    });
    if (t < 1) requestAnimationFrame(pas);
    else carte.fire('moveend');   // chargements à la vue, petits éléments
  };
  requestAnimationFrame(pas);
}

/** Amène le centre de la vue à `hauteur` mètres (plancher d'un niveau), sans déplacer ni zoomer. */
export function hauteurVue(carte, hauteur, duree = 500) {
  const depart = carte.getCenterElevation();
  carte.setCenterClampedToGround(false);
  if (Math.abs(depart - hauteur) < 0.01) return;
  const debut = performance.now();
  const pas = (maintenant) => {
    const t = Math.min(1, (maintenant - debut) / duree);
    carte.setCenterElevation(depart + (hauteur - depart) * t * (2 - t));
    if (t < 1) requestAnimationFrame(pas);
  };
  requestAnimationFrame(pas);
}

/** Ramène le centre de la vue au niveau du sol (avant un cadrage sur une emprise au sol) : 0 sur la carte plate,
 *  le terrain en relief (le centre le suit ensuite). */
export function auSol(carte) {
  if (carte.getTerrain()) {
    carte.setCenterClampedToGround(true);
    const sol = carte.queryTerrainElevation(carte.getCenter());
    if (sol != null) carte.setCenterElevation(sol);
    return;
  }
  carte.setCenterClampedToGround(false);
  if (carte.getCenterElevation() !== 0) carte.setCenterElevation(0);
}

/** Autorise la caméra sous le sol quand le centre de la vue y est (objet en sous-sol, tunnel) : MapLibre remonte
 *  sinon la caméra au-dessus du terrain, ou de l'altitude 0 sur la carte plate, après chaque mouvement, en réduisant
 *  zoom et inclinaison (on ne pouvait ni s'approcher ni tourner autour d'un objet enterré : vue « bloquée au RDC »).
 *  Remplace la vérification interne _elevateCameraIfInsideTerrain de la caméra (MapLibre 6.12.0) ; hors sous-sol,
 *  la vérification d'origine s'applique. Sans effet si la version de MapLibre ne la possède plus. */
export function libererSousSol(carte) {
  const camera = carte._camera;
  const origine = camera?._elevateCameraIfInsideTerrain;
  if (typeof origine !== 'function') {
    console.warn('Caméra sous le sol : vérification interne de MapLibre introuvable (version changée ?)');
    return false;
  }
  camera._elevateCameraIfInsideTerrain = function verifier(tr) {
    if (!carte.getCenterClampedToGround()) {
      const sol = carte.terrain ? carte.terrain.getElevationForLngLat(tr.center, tr) : 0;
      if (tr.elevation < sol - MARGE_SOUS_SOL_M) return {};
    }
    return origine.call(this, tr);
  };
  return true;
}

/** Molette vers l'avant sur un objet 3D enterré (maquette, local, point LiDAR sous le sol) : le centre de la vue,
 *  pivot du zoom et de la rotation, est placé sur ce point, à sa profondeur, caméra immobile. Sans cela le pivot
 *  restait au niveau du sol et l'on ne pouvait pas tourner autour d'un objet en sous-sol. */
export function viserSousSol(carte, c3d, zoomMin = ZOOM_MIN_CADRAGE) {
  carte.getCanvasContainer().addEventListener('wheel', (e) => {
    if (e.deltaY >= 0 || carte.getZoom() < zoomMin) return;
    const r = carte.getCanvas().getBoundingClientRect();
    const p3 = c3d.pointSous({ x: e.clientX - r.left, y: e.clientY - r.top });
    if (!p3) return;
    const { lon, lat, z } = c3d.depuisScene(p3.point);
    const sol = carte.getTerrain() ? (carte.queryTerrainElevation([lon, lat]) ?? 0) : 0;
    if (z >= sol - MARGE_SOUS_SOL_M) return;   // objet au-dessus du sol : zoom habituel
    if (!carte.getCenterClampedToGround() && Math.abs(carte.getCenterElevation() - z) < 1) return;   // déjà à sa profondeur
    const tr = carte._camera?.transform;
    if (!tr) return;
    let vise;
    try {
      vise = carte.calculateCameraOptionsFromTo(tr.getCameraLngLat(), tr.getCameraAltitude(), [lon, lat], z);
    } catch {
      return;   // caméra sur le point visé
    }
    carte.setCenterClampedToGround(false);
    carte.jumpTo({ center: [lon, lat], elevation: z, zoom: vise.zoom, pitch: vise.pitch, bearing: vise.bearing });
  }, { capture: true });
}

// Cadrage de la caméra autour d'un point en hauteur : objet ou local en sous-sol, en étage. Le centre de la vue
// n'est pas collé au sol (carte créée avec centerClampedToGround: false) : il est placé à la hauteur de l'objet,
// et la rotation, l'inclinaison et le zoom au clavier tournent autour de lui.

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
  if (Math.abs(depart - hauteur) < 0.01) return;
  const debut = performance.now();
  const pas = (maintenant) => {
    const t = Math.min(1, (maintenant - debut) / duree);
    carte.setCenterElevation(depart + (hauteur - depart) * t * (2 - t));
    if (t < 1) requestAnimationFrame(pas);
  };
  requestAnimationFrame(pas);
}

/** Ramène le centre de la vue au niveau du sol (avant un cadrage sur une emprise au sol). */
export function auSol(carte) {
  if (carte.getCenterElevation() !== 0) carte.setCenterElevation(0);
}

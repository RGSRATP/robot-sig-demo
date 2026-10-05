// Conversion WGS84 → Lambert 93 (EPSG:2154, conique conforme sécante, ellipsoïde GRS80), pour afficher
// les coordonnées du curseur. Formules IGN (NTG_71) ; écart avec pyproj inférieur au centimètre.
const A = 6378137;
const F = 1 / 298.257222101;
const E = Math.sqrt(2 * F - F * F);
const RAD = Math.PI / 180;
const PHI0 = 46.5 * RAD;
const PHI1 = 44 * RAD;
const PHI2 = 49 * RAD;
const LAMBDA0 = 3 * RAD;
const X0 = 700000;
const Y0 = 6600000;

const m = (phi) => Math.cos(phi) / Math.sqrt(1 - (E * Math.sin(phi)) ** 2);
const t = (phi) => Math.tan(Math.PI / 4 - phi / 2) / ((1 - E * Math.sin(phi)) / (1 + E * Math.sin(phi))) ** (E / 2);

const N = Math.log(m(PHI1) / m(PHI2)) / Math.log(t(PHI1) / t(PHI2));
const AF = (A * m(PHI1)) / (N * t(PHI1) ** N);
const RHO0 = AF * t(PHI0) ** N;

export function versLambert93(lon, lat) {
  const rho = AF * t(lat * RAD) ** N;
  const theta = N * (lon * RAD - LAMBDA0);
  return [X0 + rho * Math.sin(theta), Y0 + RHO0 - rho * Math.cos(theta)];
}

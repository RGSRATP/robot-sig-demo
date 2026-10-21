// Exports de la carte de crise (lot C2) : PDF (carte affichée, légende, chiffres, décisions, main courante) et
// GeoJSON du périmètre. Fichiers remis à l'utilisateur, jamais envoyés. L'export Excel est fait par le serveur.
import { jsPDF } from 'jspdf';
import { depuisLambert93, versLambert93 } from './lambert93.js';

const POINTS_CERCLE = 180;
const BLEU_EXERCICE = [25, 113, 194];
const ROUGE_REEL = [201, 42, 42];
const ENCRE = [27, 30, 36];
const GRIS = [106, 106, 106];

export const dateHeure = (iso) => (iso ? new Date(iso).toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' }) : '');
const nf = (v, d = 0) => Number(v).toLocaleString('fr-FR', { maximumFractionDigits: d }).replace(/ | /g, ' ');
const rgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
const nomFichier = (t) => String(t || 'evenement').replace(/[^\p{L}\p{N}_-]+/gu, '_').slice(0, 80);

export function telecharger(blob, nom) {
  const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: nom });
  document.body.append(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
}

/** Anneau [lon, lat] d'un vrai cercle au sol (Lambert 93). */
export function cercle([lon, lat], rayon, n = POINTS_CERCLE) {
  const [cx, cy] = versLambert93(lon, lat);
  const anneau = [];
  for (let i = 0; i <= n; i += 1) {
    const a = (2 * Math.PI * i) / n;
    anneau.push(depuisLambert93(cx + rayon * Math.sin(a), cy + rayon * Math.cos(a)));
  }
  return anneau;
}

/** GeoJSON du périmètre : point de l'événement et zones (cercles en vrais cercles au sol, polygones). */
export function geojsonPerimetre(evt, zonesCfg) {
  const features = [{ type: 'Feature', geometry: { type: 'Point', coordinates: evt.centre },
    properties: { nature: 'evenement', titre: evt.titre, type: evt.type, mode: evt.mode, debut: evt.debut } }];
  for (const z of evt.zones) {
    const zc = zonesCfg.get(z.role);
    const anneau = z.forme === 'cercle' ? cercle(evt.centre, z.rayon_m) : [...z.coordonnees, z.coordonnees[0]];
    features.push({ type: 'Feature', geometry: { type: 'Polygon', coordinates: [anneau] },
      properties: { nature: 'zone', role: z.role, libelle: zc.libelle, couleur: zc.couleur, forme: z.forme,
        rayon_m: z.forme === 'cercle' ? z.rayon_m : null } });
  }
  return { type: 'FeatureCollection', name: evt.titre, features };
}

/** Image de la carte telle qu'affichée (lue pendant un rendu : pas besoin de garder le tampon WebGL). */
export function imageCarte(carte) {
  return new Promise((resolve) => {
    carte.once('render', () => {
      const c = carte.getCanvas();
      resolve({ url: c.toDataURL('image/jpeg', 0.9), largeur: c.width, hauteur: c.height });
    });
    carte.triggerRepaint();
  });
}

/** Largeur au sol de la carte affichée (m), mesurée en Lambert 93 au milieu de la vue. */
function largeurSol(carte) {
  const b = carte.getBounds();
  const lat = (b.getNorth() + b.getSouth()) / 2;
  const [x1] = versLambert93(b.getWest(), lat);
  const [x2] = versLambert93(b.getEast(), lat);
  return Math.abs(x2 - x1);
}

/**
 * PDF de l'événement. donnees : { evt, zonesCfg, statuts (Map id -> statut), categories (analyse avec la zone
 * vivante de chaque élément : { libelle, elements: [{ libelle, zone, statut, decision }] }), sources, anomalies,
 * avertissement, carte, format ('a4' | 'a3') }.
 */
export async function exporterPdf(d) {
  const { evt, zonesCfg, carte } = d;
  const img = await imageCarte(carte);
  const doc = new jsPDF({ orientation: 'landscape', unit: 'mm', format: d.format || 'a4' });
  const W = doc.internal.pageSize.getWidth();
  const H = doc.internal.pageSize.getHeight();
  const M = 8;
  const exercice = evt.mode === 'exercice';
  const echelle = d.format === 'a3' ? 1.25 : 1;
  const taille = (t) => doc.setFontSize(t * echelle);

  const entete = (sousTitre) => {
    doc.setFillColor(...(exercice ? BLEU_EXERCICE : ROUGE_REEL));
    doc.rect(0, 0, W, 13 * echelle, 'F');
    doc.setTextColor(255, 255, 255);
    doc.setFont('helvetica', 'bold');
    taille(12);
    doc.text(`${exercice ? 'EXERCICE' : 'ÉVÉNEMENT RÉEL'} — ${evt.titre}`, M, 8.5 * echelle);
    doc.setFont('helvetica', 'normal');
    taille(8.5);
    doc.text(`${evt.type || ''} · depuis ${dateHeure(evt.debut)} · ${sousTitre}`, W - M, 8.5 * echelle, { align: 'right' });
    doc.setTextColor(...ENCRE);
  };
  const pied = (page) => {
    doc.setTextColor(...GRIS);
    taille(7);
    doc.text(`Document de travail — aide à la décision, à confirmer sur place. Usage interne RATP. Édité le ${dateHeure(new Date().toISOString())}`
      + `${evt.auteur ? ` par ${evt.auteur}` : ''}.`, M, H - 4);
    doc.text(`Page ${page}`, W - M, H - 4, { align: 'right' });
    doc.setTextColor(...ENCRE);
  };

  // ---- page 1 : carte, légende, chiffres
  entete(`carte au ${dateHeure(new Date().toISOString())}`);
  const haut = 13 * echelle + 4;
  const colonne = 92 * echelle;
  const zoneL = W - 2 * M - colonne - 5;
  const zoneH = H - haut - 16;
  const ratio = Math.min(zoneL / img.largeur, zoneH / img.hauteur);
  const lI = img.largeur * ratio;
  const hI = img.hauteur * ratio;
  doc.addImage(img.url, 'JPEG', M, haut, lI, hI);
  doc.setDrawColor(...GRIS);
  doc.rect(M, haut, lI, hI);
  taille(7.5);
  doc.setTextColor(...GRIS);
  doc.text(`Largeur de la carte : environ ${nf(largeurSol(carte) / 1000, 2)} km au sol · nord en haut si orientation 0° · fond © IGN Géoplateforme`,
    M, haut + hI + 4);
  doc.setTextColor(...ENCRE);

  let x = M + lI + 5;
  let y = haut + 3;
  const titre = (t) => { doc.setFont('helvetica', 'bold'); taille(10); doc.text(t, x, y); doc.setFont('helvetica', 'normal'); y += 5 * echelle; };
  titre('Zones');
  taille(8.5);
  for (const z of evt.zones) {
    const zc = zonesCfg.get(z.role);
    doc.setFillColor(...rgb(zc.couleur));
    doc.rect(x, y - 3, 4, 4, 'F');
    doc.text(`${zc.libelle} — ${z.forme === 'cercle' ? `${nf(z.rayon_m)} m` : 'polygone dessiné'}`, x + 6, y);
    y += 5 * echelle;
  }
  y += 2;
  titre('Éléments touchés');
  const roles = [...zonesCfg.keys()].filter((r) => evt.zones.some((z) => z.role === r));
  const col = (colonne - 44 * echelle) / Math.max(roles.length + 1, 1);
  taille(7.5);
  doc.setTextColor(...GRIS);
  roles.forEach((r, i) => doc.text(zonesCfg.get(r).libelle.split(/[ /]/)[0], x + 44 * echelle + col * i, y, { maxWidth: col - 1 }));
  doc.text('décidés', x + 44 * echelle + col * roles.length, y);
  doc.setTextColor(...ENCRE);
  y += 4.5 * echelle;
  taille(8);
  for (const k of d.categories) {
    const n = roles.map((r) => k.elements.filter((e) => e.zone === r).length);
    const decides = k.elements.filter((e) => e.zone && e.decision).length;
    if (!n.some(Boolean) && !decides) continue;
    doc.text(doc.splitTextToSize(k.libelle, 42 * echelle)[0], x, y);
    n.forEach((v, i) => doc.text(v ? String(v) : '—', x + 44 * echelle + col * i, y));
    doc.text(decides ? String(decides) : '—', x + 44 * echelle + col * roles.length, y);
    y += 4.4 * echelle;
  }
  if (d.secours?.length) {
    y += 3;
    titre('Secours les plus proches');
    taille(8);
    for (const sec of d.secours) {
      doc.setFillColor(...rgb(sec.couleur));
      doc.circle(M + lI + 6.5, y - 1.2, 1.3, 'F');
      doc.text(doc.splitTextToSize(`${sec.categorie} : ${sec.libelle} — ${nf(sec.distance_m)} m ${sec.direction}`, colonne - 5)[0], M + lI + 9, y);
      y += 4.4 * echelle;
    }
  }
  y += 3;
  titre('Décisions');
  taille(8);
  const parStatut = new Map();
  for (const k of d.categories) for (const e of k.elements) if (e.decision) parStatut.set(e.decision.statut, (parStatut.get(e.decision.statut) || 0) + 1);
  if (!parStatut.size) { doc.text('Aucune décision enregistrée.', x, y); y += 4.4 * echelle; }
  for (const [s, n] of parStatut) { doc.text(`${d.statuts.get(s)?.libelle || s} : ${n}`, x, y); y += 4.4 * echelle; }
  y += 3;
  titre('Sources');
  taille(6.8);
  doc.setTextColor(...GRIS);
  const sources = [...new Set(d.sources.map((s) => `${s.libelle}${s.date ? ` (${dateHeure(s.date)})` : ''}`))].join(' ; ');
  for (const l of doc.splitTextToSize(`${sources}. ${d.avertissement || ''}`, colonne)) {
    if (y > H - 12) break;
    doc.text(l, x, y);
    y += 3.2 * echelle;
  }
  doc.setTextColor(...ENCRE);
  pied(1);

  // ---- pages suivantes : décisions détaillées puis main courante
  let page = 1;
  const nouvellePage = (t) => { doc.addPage(); page += 1; entete(t); y = 13 * echelle + 9; x = M; };
  const ligne = (texte, retrait = 0, gras = false) => {
    doc.setFont('helvetica', gras ? 'bold' : 'normal');
    for (const l of doc.splitTextToSize(texte, W - 2 * M - retrait)) {
      if (y > H - 10) { pied(page); nouvellePage('suite'); }
      doc.text(l, x + retrait, y);
      y += 4.2 * echelle;
    }
  };
  nouvellePage('synthèse des décisions');
  taille(9);
  let vide = true;
  for (const k of d.categories) {
    const decides = k.elements.filter((e) => e.decision);
    if (!decides.length) continue;
    vide = false;
    ligne(`${k.libelle} (${decides.length})`, 0, true);
    for (const e of decides) {
      const zone = e.zone ? zonesCfg.get(e.zone).libelle : 'hors périmètre';
      ligne(`${d.statuts.get(e.decision.statut)?.libelle || e.decision.statut} — ${e.libelle} · ${zone} · ${dateHeure(e.decision.heure)}${e.decision.auteur ? ` · ${e.decision.auteur}` : ''}`, 4);
    }
    y += 1.5;
  }
  if (vide) ligne('Aucune décision enregistrée.');
  y += 3;
  ligne('Main courante', 0, true);
  taille(8);
  for (const j of evt.journal || []) ligne(`${dateHeure(j.heure)}  ${j.auteur || ''}  [${j.type}]  ${j.texte}`, 2);
  pied(page);
  doc.save(`crise_${nomFichier(evt.titre)}.pdf`);
}

export function exporterGeojson(evt, zonesCfg) {
  const blob = new Blob([JSON.stringify(geojsonPerimetre(evt, zonesCfg), null, 1)], { type: 'application/geo+json' });
  telecharger(blob, `perimetre_${nomFichier(evt.titre)}.geojson`);
}

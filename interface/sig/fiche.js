// Panneau « fiche » d'un objet SIG : attributs et coordonnées (système d'origine et Lambert 93).
import { dateCourte } from './api.js';
let panneau = null;

export function monterFiche(racine) {
  panneau = document.createElement('aside');
  panneau.className = 'sig-fiche';
  panneau.hidden = true;
  racine.appendChild(panneau);
}

export function fermerFiche() {
  if (panneau) panneau.hidden = true;
}

function nombre(v) {
  return v.toLocaleString('fr-FR', { maximumFractionDigits: 8 });
}

function coordonnees(c) {
  if (c.point) return `X ${nombre(c.point[0])} · Y ${nombre(c.point[1])}`;
  const [x1, y1, x2, y2] = c.emprise;
  return `emprise X ${nombre(x1)} → ${nombre(x2)} · Y ${nombre(y1)} → ${nombre(y2)}`;
}

export function afficherFiche(fiche, couche, surFermeture) {
  if (!panneau) return;
  const fermer = document.createElement('button');
  Object.assign(fermer, { type: 'button', className: 'sig-btn sig-fiche__fermer', textContent: '×', title: 'Fermer' });
  fermer.addEventListener('click', () => { fermerFiche(); surFermeture?.(); });

  const titre = document.createElement('h2');
  const sous = document.createElement('p');
  sous.className = 'sig-info';
  sous.textContent = couche.service
    ? `${couche.libelle} — service Esri, données du ${dateCourte(couche.service.date_maj)}`
    : couche.libelle;
  panneau.replaceChildren(fermer, titre, sous);

  if (fiche.erreur) {
    titre.textContent = 'Objet indisponible';
    const p = document.createElement('p');
    p.className = 'sig-erreur';
    p.textContent = fiche.erreur;
    panneau.append(p);
    panneau.hidden = false;
    return;
  }
  titre.textContent = fiche.libelle ?? `Objet n° ${fiche.n}`;

  const coords = document.createElement('dl');
  coords.className = 'sig-coords';
  const { origine, lambert93 } = fiche.coordonnees;
  const ajouter = (dt, dd) => {
    coords.append(Object.assign(document.createElement('dt'), { textContent: dt }),
                  Object.assign(document.createElement('dd'), { textContent: dd }));
  };
  ajouter(`Lambert 93 (${lambert93.systeme})`, coordonnees(lambert93));
  if (origine.systeme !== lambert93.systeme) ajouter(`Origine (${origine.systeme})`, coordonnees(origine));
  ajouter('Géométrie', fiche.type_geometrie);

  const table = document.createElement('table');
  table.className = 'sig-attributs';
  for (const [k, v] of Object.entries(fiche.attributs)) {
    const tr = table.insertRow();
    tr.insertCell().textContent = k;
    tr.insertCell().textContent = v ?? '';
  }
  const h3 = Object.assign(document.createElement('h3'), { textContent: `Attributs (${Object.keys(fiche.attributs).length})` });
  const enveloppe = document.createElement('div');
  enveloppe.className = 'sig-attributs__defilement';
  enveloppe.append(table);
  panneau.append(coords, h3, enveloppe);
  panneau.hidden = false;
}

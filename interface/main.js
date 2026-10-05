// Enveloppe du démonstrateur autonome. Après la fusion, l'onglet « Carte » du
// demonstrateur-patrimoine appellera directement monterCarte() depuis sig/.
import { monterCarte } from './sig/carte.js';

monterCarte(document.getElementById('carte-sig'));

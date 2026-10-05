// Enveloppe du démonstrateur autonome. Après la fusion, l'onglet « Carte » du
// demonstrateur-patrimoine appellera directement monterCarte() depuis sig/.
import { monterCarte } from './sig/carte.js';

// La carte est exposée pour le diagnostic depuis la console du navigateur.
monterCarte(document.getElementById('carte-sig')).then((carte) => { window.carteSig = carte; });

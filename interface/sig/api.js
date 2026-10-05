// Accès à l'API du module SIG (/api/sig). Les erreurs portent le message du serveur.
const API = 'api/sig';

export async function lire(chemin, params, methode = 'GET') {
  const url = new URL(`${API}/${chemin}`, document.baseURI);
  for (const [k, v] of Object.entries(params || {})) url.searchParams.set(k, v);
  const r = await fetch(url, { method: methode });
  const corps = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(corps.detail || `réponse ${r.status}`);
  return corps;
}

export const envoyer = (chemin, params) => lire(chemin, params, 'POST');

export function dateCourte(iso) {
  if (!iso) return '?';
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' });
}

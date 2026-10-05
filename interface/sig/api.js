// Accès à l'API du module SIG (/api/sig). Les erreurs portent le message du serveur.
const API = 'api/sig';

export async function lire(chemin, params) {
  const url = new URL(`${API}/${chemin}`, document.baseURI);
  for (const [k, v] of Object.entries(params || {})) url.searchParams.set(k, v);
  const r = await fetch(url);
  const corps = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(corps.detail || `réponse ${r.status}`);
  return corps;
}

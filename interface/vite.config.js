import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';

// MapLibre 6 charge son worker depuis un fichier voisin de son code (maplibre-gl-worker.mjs, qui importe
// maplibre-gl-shared.mjs). Au build, on dépose ces deux fichiers tels quels à côté du bundle (assets/).
const DIST_MAPLIBRE = fileURLToPath(new URL('./node_modules/maplibre-gl/dist/', import.meta.url));
function workerMapLibre() {
  return {
    name: 'worker-maplibre',
    apply: 'build',
    generateBundle() {
      for (const f of ['maplibre-gl-worker.mjs', 'maplibre-gl-shared.mjs']) {
        this.emitFile({ type: 'asset', fileName: `assets/${f}`, source: readFileSync(DIST_MAPLIBRE + f) });
      }
    },
  };
}

export default defineConfig({
  // Chemins relatifs : dist/ peut être servi par FastAPI ou déposé sur n'importe quel serveur web.
  base: './',
  plugins: [workerMapLibre()],
  // En développement, MapLibre est servi depuis node_modules : son worker y est trouvé à côté de lui.
  optimizeDeps: { exclude: ['maplibre-gl'] },
  server: {
    port: 5173,
    open: true,
    // L'API FastAPI (python -m uvicorn serveur.sig.app:app --port 8000) est relayée sous /api.
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
  build: { chunkSizeWarningLimit: 2000 }, // MapLibre seul dépasse le seuil par défaut
});

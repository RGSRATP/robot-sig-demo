import { defineConfig } from 'vite';

export default defineConfig({
  // Chemins relatifs : dist/ peut être servi par FastAPI ou déposé sur n'importe quel serveur web.
  base: './',
  server: {
    port: 5173,
    open: true,
    // L'API FastAPI (python -m uvicorn serveur.sig.app:app --port 8000) est relayée sous /api.
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
  build: { chunkSizeWarningLimit: 2000 }, // MapLibre seul dépasse le seuil par défaut
});

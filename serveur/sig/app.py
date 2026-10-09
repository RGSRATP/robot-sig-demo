r"""Application autonome du démonstrateur SIG (inutile après la fusion : seul `routes.routeur` sera repris).

Lancement : .venv\Scripts\python -m uvicorn serveur.sig.app:app --port 8000
Sert aussi l'interface construite (interface/dist) si elle existe.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from .config import RACINE
from .routes import VERSION, routeur

app = FastAPI(title="Synergie-SIG-BDM-POC", version=VERSION)
app.include_router(routeur)
# listes de maquettes et emprises : texte JSON très compressible (à reprendre dans l'application du patrimoine)
app.add_middleware(GZipMiddleware, minimum_size=2000)

_DIST = RACINE / "interface" / "dist"
if (_DIST / "index.html").is_file():
    app.mount("/", StaticFiles(directory=_DIST, html=True), name="interface")

"""Routes HTTP du module SIG, toutes sous /api/sig.

Le routeur est autonome : le demonstrateur-patrimoine l'inclura tel quel (app.include_router).
"""
from __future__ import annotations

import platform
from importlib.metadata import PackageNotFoundError, version

from fastapi import APIRouter, Depends, HTTPException

from . import config as cfg_sig

VERSION = "0.1.0 (lot 0)"

routeur = APIRouter(prefix="/api/sig", tags=["sig"])


def _config_ou_erreur() -> dict:
    """Configuration chargée, ou {"erreur": message} : une configuration invalide ne bloque pas le serveur."""
    try:
        return cfg_sig.config()
    except cfg_sig.ConfigInvalide as e:
        return {"erreur": str(e)}


def _version(paquet: str) -> str | None:
    try:
        return version(paquet)
    except PackageNotFoundError:
        return None


def _compter_fichiers(dossier) -> int | None:
    if not dossier.is_dir():
        return None
    return sum(1 for p in dossier.rglob("*") if p.is_file())


@routeur.get("/sante")
def sante(cfg: dict = Depends(_config_ou_erreur)):
    anomalies = []
    if "erreur" in cfg:
        anomalies.append(f"Configuration : {cfg['erreur']}")

    donnees = {}
    for sous in ("sig", "ifc", "esri"):
        n = _compter_fichiers(cfg_sig.DOSSIER_DONNEES / sous)
        donnees[sous] = n
        if n is None:
            anomalies.append(f"Dossier donnees/{sous}/ absent")

    ifc = _version("ifcopenshell")
    if ifc is None:
        anomalies.append("IfcOpenShell n'est pas installé")

    return {
        "statut": "degrade" if anomalies else "ok",
        "version": VERSION,
        "python": platform.python_version(),
        "ifcopenshell": ifc,
        "configuration_chargee": "erreur" not in cfg,
        "donnees": donnees,
        "anomalies": anomalies,
    }


@routeur.get("/config/carte")
def config_carte(cfg: dict = Depends(_config_ou_erreur)):
    if "erreur" in cfg:
        raise HTTPException(status_code=503, detail=cfg["erreur"])
    return cfg_sig.config_carte(cfg)

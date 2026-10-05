"""Routes HTTP du module SIG, toutes sous /api/sig.

Le routeur est autonome : le demonstrateur-patrimoine l'inclura tel quel (app.include_router).
"""
from __future__ import annotations

import platform
from importlib.metadata import PackageNotFoundError, version

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from . import config as cfg_sig
from . import esri as esri_sig
from . import inventaire as inv_sig
from . import maquettes as maq_sig
from . import objets as obj_sig
from commun.ifc import proprietes as proprietes_ifc

VERSION = "0.4.0 (lot 3)"

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


def _config_valide(cfg: dict = Depends(_config_ou_erreur)) -> dict:
    if "erreur" in cfg:
        raise HTTPException(status_code=503, detail=cfg["erreur"])
    return cfg


@routeur.get("/config/carte")
def config_carte(cfg: dict = Depends(_config_valide)):
    return {**cfg_sig.config_carte(cfg), "style_selection": cfg["style_selection"]}


def _inventaire_complet(cfg: dict, forcer: str | None = None) -> dict:
    inv = inv_sig.inventaire(cfg).publique()
    couches, anomalies = esri_sig.couches_services(cfg, rang0=len(inv["couches"]), forcer=forcer)
    return {
        **inv,
        "couches": inv["couches"] + [c.publique() for c in couches],
        "anomalies": inv["anomalies"] + anomalies,
        "nb_services": len(cfg["services_esri"]),
        "systeme_attendu": cfg["carte"].get("systeme_donnees_attendu"),
        "seuil_objets_filtre_emprise": cfg["chargement"]["seuil_objets_filtre_emprise"],
    }


@routeur.get("/couches")
def couches(cfg: dict = Depends(_config_valide)):
    """Inventaire : couches fichiers et couches de services Esri, anomalies et fichiers ignorés."""
    return _inventaire_complet(cfg)


def _service(id_couche: str, cfg: dict) -> dict | None:
    return next((s for s in cfg["services_esri"] if esri_sig.identifiant(s) == id_couche), None)


def _couche(id_couche: str, cfg: dict) -> inv_sig.Couche:
    couche = inv_sig.inventaire(cfg).couche(id_couche)
    if couche is not None:
        return couche
    service = _service(id_couche, cfg)
    if service is None:
        raise HTTPException(status_code=404, detail=f"couche inconnue : {id_couche}")
    try:
        return esri_sig.couche_service(cfg, service, rang=0)
    except inv_sig.AnomalieCouche as e:
        raise HTTPException(status_code=422, detail=f"{service['libelle']} : {e}")


@routeur.post("/services/{id_couche}/actualiser")
def actualiser(id_couche: str, cfg: dict = Depends(_config_valide)):
    """Nouveau téléchargement d'une couche de service, même si son cache est à jour."""
    if _service(id_couche, cfg) is None:
        raise HTTPException(status_code=404, detail=f"service inconnu : {id_couche}")
    return _inventaire_complet(cfg, forcer=id_couche)


def _anomalie(e: inv_sig.AnomalieCouche, couche: inv_sig.Couche):
    return HTTPException(status_code=422, detail=f"{couche.fichier} : {e}")


@routeur.get("/couches/{id_couche}/objets")
def objets(
    id_couche: str,
    bbox: str | None = Query(None, description="minLon,minLat,maxLon,maxLat (WGS84)"),
    cfg: dict = Depends(_config_valide),
):
    couche = _couche(id_couche, cfg)
    emprise = None
    if bbox:
        try:
            emprise = [float(v) for v in bbox.split(",")]
            assert len(emprise) == 4 and emprise[0] <= emprise[2] and emprise[1] <= emprise[3]
        except (ValueError, AssertionError):
            raise HTTPException(status_code=400, detail="bbox attendu : minLon,minLat,maxLon,maxLat")
    try:
        return obj_sig.objets(couche, emprise, cfg["chargement"]["seuil_objets_filtre_emprise"])
    except inv_sig.AnomalieCouche as e:
        raise _anomalie(e, couche)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@routeur.get("/couches/{id_couche}/objets/{n}")
def fiche(id_couche: str, n: int, cfg: dict = Depends(_config_valide)):
    couche = _couche(id_couche, cfg)
    try:
        f = obj_sig.fiche(couche, n)
    except inv_sig.AnomalieCouche as e:
        raise _anomalie(e, couche)
    if f is None:
        raise HTTPException(status_code=404, detail=f"objet n° {n} absent de la couche")
    return f


@routeur.get("/couches/{id_couche}/recherche")
def recherche(id_couche: str, q: str = Query(..., min_length=1), cfg: dict = Depends(_config_valide)):
    couche = _couche(id_couche, cfg)
    try:
        return obj_sig.rechercher(couche, q, int(cfg["chargement"]["max_resultats_recherche"]))
    except inv_sig.AnomalieCouche as e:
        raise _anomalie(e, couche)


# ---------------------------------------------------------------- maquettes IFC (lot 3)

def _config_maquettes() -> dict:
    try:
        return maq_sig.config()
    except maq_sig.ConfigMaquettesInvalide as e:
        raise HTTPException(status_code=503, detail=str(e))


@routeur.get("/maquettes")
def maquettes(cfg: dict = Depends(_config_valide), cfgm: dict = Depends(_config_maquettes)):
    """Maquettes du dossier IFC : état du traitement, métadonnées, positionnement (méthode, emprise, calage)."""
    return maq_sig.inventaire(cfg, cfgm)


@routeur.get("/maquettes/emprises")
def maquettes_emprises(cfg: dict = Depends(_config_valide), cfgm: dict = Depends(_config_maquettes)):
    return maq_sig.emprises(maq_sig.inventaire(cfg, cfgm))


def _maquette_traitee(id_maquette: str, cfgm: dict) -> tuple[str, dict]:
    trouve = maq_sig.chemin_maquette(cfgm, id_maquette)
    if trouve is None:
        raise HTTPException(status_code=404, detail=f"maquette inconnue : {id_maquette}")
    rel, chemin = trouve
    if maq_sig.resultat(cfgm, id_maquette, maq_sig.cle_traitement(chemin, cfgm)) is None:
        raise HTTPException(status_code=409, detail=f"{rel} : traitement non terminé")
    return rel, chemin


@routeur.get("/maquettes/{id_maquette}/modele.glb")
def maquette_modele(id_maquette: str, cfgm: dict = Depends(_config_maquettes)):
    _maquette_traitee(id_maquette, cfgm)
    return FileResponse(maq_sig.dossier_cache(cfgm, id_maquette) / "modele.glb", media_type="model/gltf-binary")


@routeur.get("/maquettes/{id_maquette}/objets/{guid}")
def maquette_objet(id_maquette: str, guid: str, cfgm: dict = Depends(_config_maquettes)):
    rel, _ = _maquette_traitee(id_maquette, cfgm)
    fiche = proprietes_ifc.lire_fiche(maq_sig.dossier_cache(cfgm, id_maquette) / "proprietes.sqlite", guid)
    if fiche is None:
        raise HTTPException(status_code=404, detail=f"objet {guid} absent de {rel}")
    return fiche


@routeur.post("/maquettes/{id_maquette}/recalculer")
def maquette_recalculer(id_maquette: str, cfg: dict = Depends(_config_valide),
                        cfgm: dict = Depends(_config_maquettes)):
    trouve = maq_sig.chemin_maquette(cfgm, id_maquette)
    if trouve is None:
        raise HTTPException(status_code=404, detail=f"maquette inconnue : {id_maquette}")
    maq_sig.traitements.relancer(cfgm, id_maquette, trouve[1])
    return maq_sig.inventaire(cfg, cfgm)

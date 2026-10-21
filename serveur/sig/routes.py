"""Routes HTTP du module SIG, toutes sous /api/sig.

Le routeur est autonome : le demonstrateur-patrimoine l'inclura tel quel (app.include_router).
"""
from __future__ import annotations

import json
from pathlib import Path
import platform
from importlib.metadata import PackageNotFoundError, version

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response

from . import config as cfg_sig
from . import carbone as carb_sig
from . import calage as cal_sig
from . import contexte as ctx_sig
from . import crise as crise_sig
from . import croisement as crois_sig
from . import esri as esri_sig
from . import inventaire as inv_sig
from . import maquettes as maq_sig
from . import objets as obj_sig
from . import temps_reel as tr_sig
from . import themes as th_sig
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
        "nb_services": len(esri_sig.services_declares(cfg)),
        "systeme_attendu": cfg["carte"].get("systeme_donnees_attendu"),
        "seuil_objets_filtre_emprise": cfg["chargement"]["seuil_objets_filtre_emprise"],
    }


@routeur.get("/couches")
def couches(cfg: dict = Depends(_config_valide)):
    """Inventaire : couches fichiers et couches de services Esri, anomalies et fichiers ignorés."""
    return _inventaire_complet(cfg)


def _service(id_couche: str, cfg: dict):
    """(service, connecteur) d'une couche de service distant (Esri, WFS, GeoJSON), sinon None."""
    return esri_sig.trouver_service(cfg, id_couche)


def _couche(id_couche: str, cfg: dict) -> inv_sig.Couche:
    couche = inv_sig.inventaire(cfg).couche(id_couche)
    if couche is not None:
        return couche
    trouve = _service(id_couche, cfg)
    if trouve is None:
        raise HTTPException(status_code=404, detail=f"couche inconnue : {id_couche}")
    service, connecteur = trouve
    try:
        return esri_sig.couche_service(cfg, service, rang=0, connecteur=connecteur)
    except inv_sig.AnomalieCouche as e:
        raise HTTPException(status_code=422, detail=f"{service['libelle']} : {e}")


# ---------------------------------------------------------------- PLAI (plans locaux d'aide à l'intervention)

def _service_plai(cfg: dict) -> dict:
    if not cfg.get("services_plai"):
        raise HTTPException(status_code=404, detail="aucun PLAI déclaré (section « services_plai »)")
    return cfg["services_plai"][0]


@routeur.get("/plai/icones/{type_}.png")
def plai_icone(type_: str, cfg: dict = Depends(_config_valide)):
    """Pictogramme de la légende des PLAI (symbole de la carte), découpé à la lecture des plans."""
    from . import plai as plai_sig
    _service_plai(cfg)
    if type_ not in plai_sig.TYPES:
        raise HTTPException(status_code=404, detail=f"pictogramme inconnu : {type_}")
    chemin = plai_sig.dossier_cache(cfg) / f"icone_{type_}.png"
    if not chemin.is_file():
        raise HTTPException(status_code=404, detail="pictogramme pas encore lu (lecture des PLAI en cours)")
    return FileResponse(chemin, media_type="image/png", headers={"Cache-Control": "max-age=3600"})


@routeur.get("/plai/pdf/{fichier}")
def plai_pdf(fichier: str, cfg: dict = Depends(_config_valide)):
    """Le PLAI d'une station, ouvert dans le navigateur (dossier en lecture seule ; nom contrôlé)."""
    from . import plai as plai_sig
    dossier = plai_sig.dossier_plans(_service_plai(cfg))
    chemin = next((p for p in dossier.glob("*.pdf") if p.name == fichier), None) if dossier.is_dir() else None
    if chemin is None:
        raise HTTPException(status_code=404, detail=f"PLAI introuvable : {fichier}")
    return FileResponse(chemin, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{chemin.name}"'})


@routeur.get("/plai/controle")
def plai_controle(cfg: dict = Depends(_config_valide)):
    """Tableau de contrôle de la lecture des PLAI (une ligne par plan), en CSV pour Excel."""
    import csv
    import io
    from . import plai as plai_sig
    _service_plai(cfg)
    chemin = plai_sig.dossier_cache(cfg) / "controle.json"
    if not chemin.is_file():
        raise HTTPException(status_code=404, detail="tableau pas encore produit (lecture des PLAI en cours)")
    lignes = json.loads(chemin.read_text(encoding="utf-8"))
    sortie = io.StringIO()
    if lignes:
        w = csv.DictWriter(sortie, fieldnames=list(lignes[0]), delimiter=cfg["export"]["separateur"])
        w.writeheader()
        w.writerows(lignes)
    return Response(sortie.getvalue().encode(cfg["export"]["encodage"]), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="controle_plai.csv"'})


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


def _json(donnees) -> Response:
    """Réponse JSON sérialisée directement (listes de milliers de maquettes : bien plus rapide que l'encodeur
    générique de FastAPI)."""
    return Response(content=json.dumps(donnees, ensure_ascii=False, separators=(",", ":")),
                    media_type="application/json")


@routeur.get("/maquettes")
def maquettes(depuis: str | None = Query(None, description="jeton du dernier appel : seules les maquettes modifiées"),
              actualiser: bool = Query(False, description="reparcourir tout le dossier (nouvelles maquettes)"),
              cfg: dict = Depends(_config_valide), cfgm: dict = Depends(_config_maquettes)):
    """Liste allégée des maquettes : état du traitement, positionnement (méthode, rectangle d'emprise, affichage 3D).

    Avec « depuis », seules les maquettes modifiées (et les supprimées) sont renvoyées. Les emprises dessinées
    sont demandées par zone (/maquettes/emprises), la description complète par maquette (/maquettes/{id}).
    """
    maq_sig.suivi.actualiser(cfg, cfgm, forcer=actualiser)
    return _json(maq_sig.suivi.depuis(cfgm, depuis))


@routeur.get("/maquettes/emprises")
def maquettes_emprises(bbox: str | None = Query(None, description="ouest,sud,est,nord (WGS84) ; vide : toutes"),
                       cfg: dict = Depends(_config_valide), cfgm: dict = Depends(_config_maquettes)):
    """Emprises simplifiées (dessin) des maquettes positionnées de la zone, les plus proches du centre d'abord."""
    try:
        zone = [float(v) for v in bbox.split(",")] if bbox else [-180.0, -90.0, 180.0, 90.0]
    except ValueError:
        zone = []
    if len(zone) != 4:
        raise HTTPException(status_code=422, detail="bbox attendue : ouest,sud,est,nord")
    maq_sig.suivi.actualiser(cfg, cfgm)
    return Response(content=maq_sig.emprises_zone(cfgm, zone), media_type="application/json")


@routeur.get("/maquettes/{id_maquette}")
def maquette(id_maquette: str, cfg: dict = Depends(_config_valide), cfgm: dict = Depends(_config_maquettes)):
    """Description complète d'une maquette (fiche) : métadonnées, positionnement détaillé (repère, calage)."""
    m = maq_sig.une_maquette(cfg, cfgm, id_maquette)
    if m is None:
        raise HTTPException(status_code=404, detail=f"maquette inconnue : {id_maquette}")
    for cle in ("emprise_geojson", "emprise_affichage"):
        (m.get("positionnement") or {}).pop(cle, None)
    return _json(m)


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
    return _glb(maq_sig.dossier_cache(cfgm, id_maquette) / "modele.glb")


@routeur.get("/maquettes/{id_maquette}/detail.glb")
def maquette_detail(id_maquette: str, cfgm: dict = Depends(_config_maquettes)):
    """Petits éléments de la maquette (boulons, platines…), chargés en vue rapprochée."""
    _maquette_traitee(id_maquette, cfgm)
    return _glb(maq_sig.dossier_cache(cfgm, id_maquette) / "detail.glb")


def _glb(chemin: Path) -> FileResponse:
    if not chemin.is_file():
        raise HTTPException(status_code=404, detail="aucun modèle 3D pour cette maquette")
    return FileResponse(chemin, media_type="model/gltf-binary")


@routeur.get("/maquettes/{id_maquette}/types")
def maquette_types(id_maquette: str, cfgm: dict = Depends(_config_maquettes)):
    """Type IFC et famille (génie civil, réseaux…) de chaque objet du modèle 3D, dans l'ordre de sa table d'objets :
    filtres de la vue 3D (masquer, colorer, isoler)."""
    rel, chemin = _maquette_traitee(id_maquette, cfgm)
    return _json(maq_sig.types_objets(cfgm, id_maquette, rel, chemin))


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
    maq_sig.suivi.oublier_balayage()
    return {"relancee": id_maquette}


# ---------------------------------------------------------------- interrogation croisée et locaux (lot 4)

def _csv(cfg: dict, colonnes, lignes, nom: str) -> Response:
    e = cfg["export"]
    texte = crois_sig.csv_texte(colonnes, lignes, e["separateur"])
    nom_fichier = "".join(c if c.isalnum() or c in "-_." else "_" for c in nom)[:120] + ".csv"
    return Response(content=texte.encode(e["encodage"]), media_type=f"text/csv; charset={e['encodage']}",
                    headers={"Content-Disposition": f'attachment; filename="{nom_fichier}"'})


def _maquette_decrite(id_maquette: str, cfg: dict, cfgm: dict) -> dict:
    m = maq_sig.une_maquette(cfg, cfgm, id_maquette)
    if m is None:
        raise HTTPException(status_code=404, detail=f"maquette inconnue : {id_maquette}")
    if m["etat"]["phase"] != "pret":
        raise HTTPException(status_code=409, detail=f"{m['fichier']} : traitement non terminé")
    return m


@routeur.get("/maquettes/{id_maquette}/locaux")
def maquette_locaux(id_maquette: str, format: str = Query("json", pattern="^(json|csv)$"),
                    cfg: dict = Depends(_config_valide), cfgm: dict = Depends(_config_maquettes)):
    """Locaux (IfcSpace) : codes SGP_DI, niveau, surface, emprise au sol et hauteurs par rapport au terrain."""
    m = _maquette_decrite(id_maquette, cfg, cfgm)
    d = maq_sig.locaux_affichables(cfg, cfgm, m)
    if d is None:
        etat = (m.get("locaux") or {}).get("phase", "en_attente")
        message = (m.get("locaux") or {}).get("message") if etat == "erreur" else "extraction des locaux en cours"
        raise HTTPException(status_code=409, detail=f"{m['nom']} : {message}")
    if format == "csv":
        return _csv(cfg, crois_sig.COLONNES_LOCAUX, d["locaux"], f"locaux_{m['nom']}")
    return d


def _tampon(cfg: dict, tampon: float | None) -> float:
    return float(cfg["croisement"]["tampon_m"] if tampon is None else tampon)


@routeur.get("/maquettes/{id_maquette}/croisement")
def maquette_croisement(id_maquette: str,
                        couches: str = Query("", description="identifiants des couches, séparés par des virgules (vide : toutes)"),
                        tampon: float | None = Query(None, ge=0, le=1000), format: str = Query("json", pattern="^(json|csv)$"),
                        cfg: dict = Depends(_config_valide), cfgm: dict = Depends(_config_maquettes)):
    """Objets SIG des couches demandées qui touchent l'emprise de la maquette (Lambert 93)."""
    m = _maquette_decrite(id_maquette, cfg, cfgm)
    pos = m.get("positionnement") or {}
    if pos.get("statut") != "positionnee":
        raise HTTPException(status_code=422, detail=f"{m['nom']} : maquette non positionnée, pas d'emprise à croiser")
    ids = [c for c in couches.split(",") if c]
    toutes = inv_sig.inventaire(cfg).couches + esri_sig.couches_services(cfg)[0]
    choisies = [c for c in toutes if not ids or c.id in ids]
    t = _tampon(cfg, tampon)
    zone = crois_sig.geometrie_l93(pos["emprise_geojson"])
    zone = zone.buffer(t) if t else zone
    r = crois_sig.maquette_vers_sig(choisies, zone, int(cfg["croisement"]["max_objets_par_couche"]))
    r.update({"maquette": {"id": m["id"], "nom": m["nom"]}, "tampon_m": t,
              "surface_emprise_m2": round(zone.area, 1), "nb_couches_interrogees": len(choisies)})
    if format == "csv":
        return _csv(cfg, crois_sig.COLONNES_SIG, crois_sig.lignes_sig(r), f"objets_sig_{m['nom']}")
    return r


@routeur.get("/couches/{id_couche}/objets/{n}/maquettes")
def objet_maquettes(id_couche: str, n: int, tampon: float | None = Query(None, ge=0, le=1000),
                    format: str = Query("json", pattern="^(json|csv)$"),
                    cfg: dict = Depends(_config_valide), cfgm: dict = Depends(_config_maquettes)):
    """Maquettes positionnées (et leurs locaux) au droit d'un objet SIG."""
    couche = _couche(id_couche, cfg)
    try:
        charge = obj_sig.charger(couche)
    except inv_sig.AnomalieCouche as e:
        raise _anomalie(e, couche)
    if n not in charge.l93.index:
        raise HTTPException(status_code=404, detail=f"objet n° {n} absent de la couche")
    geom = charge.l93.geometry.loc[n]
    # présélection par rectangle d'emprise (liste allégée), puis description complète des seules candidates
    maq_sig.suivi.actualiser(cfg, cfgm)
    o, s, e, n_ = charge.wgs84.geometry.loc[n].bounds
    marge = _tampon(cfg, tampon) / 50000 + 1e-4   # degrés, largement au-dessus du tampon en mètres
    candidates = maq_sig.suivi.dans_emprise([o - marge, s - marge, e + marge, n_ + marge])
    positionnees = [d for d in (maq_sig.une_maquette(cfg, cfgm, m["id"]) for m in candidates)
                    if d and (d.get("positionnement") or {}).get("statut") == "positionnee"]
    locaux = {}
    for m in positionnees:
        d = maq_sig.locaux_affichables(cfg, cfgm, m)
        if d:
            locaux[m["id"]] = d["locaux"]
    r = crois_sig.sig_vers_maquettes(geom, positionnees, locaux, _tampon(cfg, tampon))
    lib = couche.champ_libelle
    libelle = obj_sig.valeur_json(charge.origine.loc[n, lib]) if lib else None
    r.update({"couche": couche.libelle, "n": n, "libelle": libelle, "tampon_m": _tampon(cfg, tampon)})
    if format == "csv":
        return _csv(cfg, crois_sig.COLONNES_MAQUETTES, crois_sig.lignes_maquettes(r),
                    f"maquettes_{couche.libelle}_{libelle or n}")
    return r


# ---------------------------------------------------------------- temps réel (lot 5)

@routeur.get("/temps-reel")
def temps_reel_sources(cfg: dict = Depends(_config_valide)):
    """Sources temps réel déclarées (présence de la clé éventuelle, jamais sa valeur)."""
    return {"sources": tr_sig.sources_publiques(cfg)}


@routeur.get("/temps-reel/{id_source}")
def temps_reel_donnees(id_source: str, cfg: dict = Depends(_config_valide)):
    """Dernières données de la source (GeoJSON WGS84), date des données et de la lecture, message d'erreur."""
    try:
        return tr_sig.donnees(cfg, id_source)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"source temps réel inconnue : {id_source}")


# ---------------------------------------------------------------- contexte d'une maquette (lot 5, croisements)

@routeur.get("/maquettes/{id_maquette}/contexte")
def maquette_contexte(id_maquette: str, actualiser: bool = False,
                      format: str = Query("json", pattern="^(json|csv|csv_niveaux)$"),
                      cfg: dict = Depends(_config_valide), cfgm: dict = Depends(_config_maquettes)):
    """Risques (Géorisques), bâti environnant (BDNB), niveau du cours d'eau le plus proche (Hub'Eau) et crues
    historiques (Vigicrues) comparés aux niveaux de la maquette. CSV : bâti (« csv ») ou niveaux (« csv_niveaux »)."""
    if not cfg.get("contexte"):
        raise HTTPException(status_code=404, detail="section « contexte » absente de config/sig.yaml")
    m = _maquette_decrite(id_maquette, cfg, cfgm)
    if (m.get("positionnement") or {}).get("statut") != "positionnee":
        raise HTTPException(status_code=422, detail=f"{m['nom']} : maquette non positionnée")
    d = ctx_sig.contexte(cfg, m, actualiser=actualiser)
    if format == "csv":
        return _csv(cfg, ctx_sig.COLONNES_BATI, ctx_sig.lignes_bati(d), f"bati_{m['nom']}")
    niveaux = maq_sig.niveaux_ngf(cfgm, m, delai_s=float(cfg["contexte"].get("delai_etages_s", 300)))
    eau = ctx_sig.face_a_l_eau(d["crue"], niveaux)
    if format == "csv_niveaux":
        colonnes, lignes = ctx_sig.tableau_niveaux(eau)
        return _csv(cfg, colonnes, lignes, f"niveaux_eau_{m['nom']}")
    return {**{k: v for k, v in d.items() if not k.startswith("_")}, "face_a_l_eau": eau}


@routeur.get("/themes/carbone")
def theme_carbone(cfgm: dict = Depends(_config_maquettes)):
    """Carbone (t CO2e) de toutes les maquettes traitées, pour la vue « Carbone » ; calcul en arrière-plan, l'interface
    redemande tant que « complet » est faux."""
    if not cfgm.get("carbone"):
        raise HTTPException(status_code=404, detail="section « carbone » absente de config/georef_maquettes.yaml")
    return th_sig.carbone(cfgm)


@routeur.get("/maquettes/{id_maquette}/hydrogramme")
def maquette_hydrogramme(id_maquette: str, cfg: dict = Depends(_config_valide), cfgm: dict = Depends(_config_maquettes)):
    """Niveau du cours d'eau (m NGF) sur les derniers jours et prévisions Vigicrues, crues historiques, niveaux de la
    maquette, vigilance du tronçon le plus proche : données de la courbe de la fiche."""
    if not cfg.get("contexte"):
        raise HTTPException(status_code=404, detail="section « contexte » absente de config/sig.yaml")
    m = _maquette_decrite(id_maquette, cfg, cfgm)
    pos = m.get("positionnement") or {}
    if pos.get("statut") != "positionnee":
        raise HTTPException(status_code=422, detail=f"{m['nom']} : maquette non positionnée")
    cx = cfg["contexte"]
    d = ctx_sig.contexte(cfg, m)
    niveaux = maq_sig.niveaux_ngf(cfgm, m, delai_s=float(cx.get("delai_etages_s", 300)))
    h = ctx_sig.hydrogramme(cfg, d["crue"], niveaux)
    h["vigilance"] = None
    if cx.get("source_vigilance") and tr_sig.source(cfg, cx["source_vigilance"]):
        v = tr_sig.donnees(cfg, cx["source_vigilance"])
        h["vigilance"] = ctx_sig.vigilance_proche(v["features"], *pos["centre_wgs84"], cx.get("rayon_vigilance_km", 5))
        if v.get("erreur"):
            h["erreurs"].append(f"vigilance crues : {v['erreur']}")
    return {**h, "maquette": {"id": m["id"], "nom": m["nom"]}}


@routeur.get("/maquettes/{id_maquette}/calage")
def maquette_calage(id_maquette: str, cfg: dict = Depends(_config_valide), cfgm: dict = Depends(_config_maquettes)):
    """Contrôle du calage vertical : terrain RGE ALTI (calage) comparé au MNT LiDAR HD sur l'emprise, niveau de la
    maquette le plus proche du terrain."""
    if not (cfgm.get("altimetrie") or {}).get("controle"):
        raise HTTPException(status_code=404, detail="section « altimetrie.controle » absente de config/georef_maquettes.yaml")
    m = _maquette_decrite(id_maquette, cfg, cfgm)
    if (m.get("positionnement") or {}).get("statut") != "positionnee":
        raise HTTPException(status_code=422, detail=f"{m['nom']} : maquette non positionnée")
    return cal_sig.controler(cfg, cfgm, m, maq_sig.niveaux_ngf(cfgm, m))


@routeur.get("/maquettes/{id_maquette}/carbone")
def maquette_carbone(id_maquette: str, format: str = Query("json", pattern="^(json|csv)$"),
                     cfg: dict = Depends(_config_valide), cfgm: dict = Depends(_config_maquettes)):
    """Ordre de grandeur des émissions de production des matériaux de la maquette (index des propriétés)."""
    if not cfgm.get("carbone"):
        raise HTTPException(status_code=404, detail="section « carbone » absente de config/georef_maquettes.yaml")
    rel, _ = _maquette_traitee(id_maquette, cfgm)
    index = maq_sig.dossier_cache(cfgm, id_maquette) / "proprietes.sqlite"
    if not index.is_file():
        raise HTTPException(status_code=409, detail=f"{rel} : index des propriétés absent (maquette à retraiter)")
    b = carb_sig.bilan(index, cfgm["carbone"])
    if format == "csv":
        return _csv(cfg, carb_sig.COLONNES, b["materiaux"], f"carbone_{Path(rel).stem}")
    return {**b, "maquette": {"id": id_maquette, "fichier": rel}}


# ---------------------------------------------------------------- carte de gestion de crise (lot C1)

def _crise(cfg: dict = Depends(_config_valide)) -> dict:
    if not cfg.get("crise"):
        raise HTTPException(status_code=404, detail="section « crise » absente de config/sig.yaml")
    return cfg


@routeur.post("/crise/analyse")
def crise_analyse(demande: dict = Body(..., examples=[{"centre": [2.3691, 48.8532],
                                                        "zones": [{"role": "evacuation", "forme": "cercle", "rayon_m": 500}]}]),
                  format: str = Query("json", pattern="^(json|csv)$"), cfg: dict = Depends(_crise)):
    """Éléments touchés autour du point, par catégorie et par zone (la plus grave l'emporte), et tous les objets
    jusqu'à « rayon_max_m » avec leur distance (l'interface recalcule les cercles en direct)."""
    try:
        r = crise_sig.analyser(cfg, demande.get("centre"), demande.get("zones") or [])
    except crise_sig.ErreurCrise as e:
        raise HTTPException(status_code=422, detail=str(e))
    if format == "csv":
        titre = str(demande.get("titre") or "evenement")
        return _csv(cfg, crise_sig.COLONNES, crise_sig.lignes_csv(cfg, r), f"crise_{titre}")
    return _json(r)


@routeur.get("/crise/lieux")
def crise_lieux(q: str = Query(..., min_length=2, max_length=80), cfg: dict = Depends(_crise)):
    """Stations et accès dont le nom contient le texte (placement de l'événement)."""
    return {"resultats": crise_sig.lieux(cfg, q)}


@routeur.get("/crise/evenements")
def crise_evenements(cfg: dict = Depends(_crise)):
    """Événements enregistrés sur le poste, du plus récent au plus ancien."""
    return {"evenements": crise_sig.evenements(cfg)}


@routeur.get("/crise/evenements/{id_evenement}")
def crise_evenement(id_evenement: str, cfg: dict = Depends(_crise)):
    e = crise_sig.evenement(cfg, id_evenement)
    if e is None:
        raise HTTPException(status_code=404, detail=f"événement inconnu : {id_evenement}")
    return e


@routeur.post("/crise/evenements")
def crise_enregistrer(evt: dict = Body(...), cfg: dict = Depends(_crise)):
    """Enregistre l'événement (description, point, zones) dans le dossier de travail du poste."""
    try:
        return crise_sig.enregistrer(cfg, evt)
    except crise_sig.ErreurCrise as e:
        raise HTTPException(status_code=422, detail=str(e))

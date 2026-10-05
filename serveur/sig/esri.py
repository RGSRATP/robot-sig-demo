"""Connecteur des services Esri (ArcGIS REST, FeatureServer / MapServer) déclarés dans config/sig.yaml.

Chaque couche de service est téléchargée (requêtes « query » paginées, Esri JSON dans le système du
service), puis écrite dans un cache local (GeoPackage + fiche de métadonnées). Elle devient alors une
couche comme les autres : objets, fiche, recherche et filtre d'emprise du lot 1 s'appliquent tels quels.

Ne jamais bloquer : service injoignable ou en erreur → cache précédent, sinon export de repli déclaré
(dossier des exports), sinon anomalie. Le jeton éventuel est lu dans l'environnement ou dans `.env`,
envoyé dans le corps des requêtes (jamais dans l'URL) et n'apparaît ni dans les messages ni dans le cache.
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import geopandas as gpd
import httpx
from pyproj import CRS
from pyproj.exceptions import CRSError
from shapely.geometry import LineString, MultiLineString, MultiPoint, MultiPolygon, Point, Polygon

from . import inventaire as inv_sig
from . import reseau
from .config import RACINE

MOTIF_URL = re.compile(r"^https?://.+/(FeatureServer|MapServer)/\d+/?$", re.IGNORECASE)
COUCHE_CACHE = "objets"


class ErreurService(Exception):
    """Service injoignable ou réponse en erreur ; le message est destiné à l'utilisateur (sans jeton)."""


# ---------------------------------------------------------------- paramètres

def _chemin(cfg_chemin: str) -> Path:
    p = Path(cfg_chemin)
    return p if p.is_absolute() else RACINE / p


def dossier_cache(cfg: dict) -> Path:
    return _chemin(cfg["esri"]["dossier_cache"])


def dossier_exports(cfg: dict) -> Path:
    return _chemin(cfg["esri"]["dossier_exports"])


def identifiant(service: dict) -> str:
    return inv_sig.identifiant(f"esri:{service['url'].rstrip('/')}", service.get("where") or "1=1")


def _lire_env(nom: str) -> str | None:
    """Variable d'environnement, sinon ligne NOM=valeur du fichier .env à la racine du projet."""
    if os.environ.get(nom):
        return os.environ[nom]
    env = RACINE / ".env"
    if env.is_file():
        for ligne in env.read_text(encoding="utf-8").splitlines():
            cle, sep, valeur = ligne.partition("=")
            if sep and cle.strip() == nom:
                return valeur.strip().strip('"').strip("'") or None
    return None


def fabrique_client(cfg: dict) -> httpx.Client:
    """Client HTTP du connecteur (remplacé par un faux serveur dans les tests)."""
    return reseau.client(cfg)


# ---------------------------------------------------------------- dialogue avec le service

class _Session:
    def __init__(self, client: httpx.Client, url: str, jeton: str | None):
        self.client, self.url, self.jeton = client, url.rstrip("/"), jeton

    def appeler(self, suffixe: str, **params) -> dict:
        donnees = {"f": "json", **{k: v for k, v in params.items() if v is not None}}
        if self.jeton:
            donnees["token"] = self.jeton
        try:
            r = self.client.post(self.url + suffixe, data=donnees)
        except httpx.HTTPError as e:
            raise ErreurService(f"service {reseau.message_erreur(e)}") from None
        if r.status_code in (401, 403):
            raise ErreurService(f"accès refusé (HTTP {r.status_code}) : jeton absent, invalide ou expiré")
        if r.status_code >= 400:
            raise ErreurService(f"réponse HTTP {r.status_code} du service")
        try:
            corps = r.json()
        except ValueError:
            raise ErreurService("réponse du service illisible (ce n'est pas du JSON : URL ou portail à vérifier)") from None
        err = corps.get("error") if isinstance(corps, dict) else None
        if err:
            code = err.get("code")
            message = err.get("message") or "erreur sans message"
            if code in (498, 499):
                message = f"jeton invalide, expiré ou requis ({message})"
            details = "; ".join(str(d) for d in err.get("details") or [] if d)
            raise ErreurService(f"erreur du service ({code}) : {message}{' — ' + details if details else ''}")
        if not isinstance(corps, dict):
            raise ErreurService("réponse du service inattendue")
        return corps


def systeme_esri(sr: dict | None) -> CRS:
    """Système d'une référence spatiale Esri (latestWkid, wkid, puis WKT). Jamais supposé."""
    if not sr:
        raise ErreurService("le service ne déclare pas de système de coordonnées")
    for code in (sr.get("latestWkid"), sr.get("wkid")):
        if code:
            for autorite in ("EPSG", "ESRI"):
                try:
                    return CRS.from_authority(autorite, int(code))
                except (CRSError, ValueError):
                    pass
    if sr.get("wkt"):
        try:
            return CRS.from_wkt(sr["wkt"])
        except CRSError:
            pass
    raise ErreurService(f"système de coordonnées du service non reconnu ({sr})")


# ---------------------------------------------------------------- Esri JSON → géométries

def _aire_signee(anneau) -> float:
    return sum(x1 * y2 - x2 * y1 for (x1, y1, *_), (x2, y2, *_) in zip(anneau, anneau[1:] + anneau[:1])) / 2


def geometrie(g: dict | None):
    """Géométrie Shapely d'une géométrie Esri JSON (point, multipoint, polyligne, polygone), ou None."""
    if not g:
        return None
    if "x" in g:
        x, y = g.get("x"), g.get("y")
        if x is None or y is None or (isinstance(x, float) and math.isnan(x)):
            return None
        return Point(x, y)
    if "points" in g:
        pts = [p[:2] for p in g["points"]]
        return MultiPoint(pts) if pts else None
    if "paths" in g:
        lignes = [LineString([p[:2] for p in chemin]) for chemin in g["paths"] if len(chemin) >= 2]
        if not lignes:
            return None
        return lignes[0] if len(lignes) == 1 else MultiLineString(lignes)
    if "rings" in g:
        # Esri : anneaux extérieurs dans le sens horaire (aire signée négative), trous dans l'autre sens.
        exterieurs, trous = [], []
        for anneau in g["rings"]:
            pts = [p[:2] for p in anneau]
            if len(pts) < 4:
                continue
            (exterieurs if _aire_signee(pts) <= 0 else trous).append(pts)
        polys = [[Polygon(e), []] for e in exterieurs]
        for t in trous:
            interieur = Polygon(t)
            hotes = [p for p in polys if p[0].contains(interieur.representative_point())]
            if hotes:
                min(hotes, key=lambda p: p[0].area)[1].append(t)
            else:  # trou orphelin : sens non conforme, traité comme un anneau extérieur
                polys.append([Polygon(t), []])
        geoms = [Polygon(p[0].exterior.coords, p[1]) for p in polys]
        if not geoms:
            return None
        return geoms[0] if len(geoms) == 1 else MultiPolygon(geoms)
    if "curvePaths" in g or "curveRings" in g:
        raise ErreurService("géométries courbes non gérées (le service doit les densifier)")
    return None


def _valeur(v, type_champ: str | None):
    if v is not None and type_champ == "esriFieldTypeDate" and isinstance(v, (int, float)):
        return (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=v)).isoformat(timespec="seconds")
    return v


# ---------------------------------------------------------------- téléchargement

def telecharger(service: dict, cfg: dict, client: httpx.Client) -> tuple[gpd.GeoDataFrame, dict]:
    """Objets de la couche de service (système du service) et métadonnées du téléchargement."""
    jeton = None
    if service.get("jeton_env"):
        jeton = _lire_env(service["jeton_env"])
        if not jeton:
            raise ErreurService(f"jeton requis : variable « {service['jeton_env']} » absente de l'environnement et de .env")
    s = _Session(client, service["url"], jeton)
    debut = time.monotonic()

    info = s.appeler("")
    if not info.get("geometryType"):
        nature = f" (type « {info['type']} »)" if info.get("type") else ""
        raise ErreurService(f"la ressource n'est pas une couche d'objets géographiques{nature}")
    capacites = (info.get("capabilities") or "Query").lower()
    if "query" not in capacites and "data" not in capacites:
        raise ErreurService("le service n'autorise pas l'interrogation (capacité « Query » absente)")

    sr_service = (info.get("extent") or {}).get("spatialReference") or info.get("sourceSpatialReference")
    out_sr = None
    if sr_service:
        out_sr = sr_service.get("latestWkid") or sr_service.get("wkid")
        if not out_sr and sr_service.get("wkt"):
            out_sr = json.dumps({"wkt": sr_service["wkt"]})

    where = service.get("where") or "1=1"
    champs = service.get("champs") or "*"
    id_champ = info.get("objectIdField") or next(
        (f["name"] for f in info.get("fields") or [] if f.get("type") == "esriFieldTypeOID"), None)
    taille = int(service.get("taille_page") or info.get("maxRecordCount") or 1000)
    if info.get("maxRecordCount"):
        taille = min(taille, int(info["maxRecordCount"]))
    max_objets = int(service.get("max_objets") or cfg["esri"]["max_objets"])
    pagination = bool((info.get("advancedQueryCapabilities") or {}).get("supportsPagination"))
    commun = dict(where=where, outFields=champs, returnGeometry="true", outSR=out_sr, returnZ="false", returnM="false")

    total = s.appeler("/query", where=where, returnCountOnly="true").get("count")
    features, reponse_sr, types = [], None, {}

    def ajouter(page: dict) -> int:
        nonlocal reponse_sr
        reponse_sr = reponse_sr or page.get("spatialReference")
        for f in page.get("fields") or []:
            types.setdefault(f["name"], f.get("type"))
        lot = page.get("features") or []
        features.extend(lot)
        return len(lot)

    if pagination and id_champ:
        decalage = 0
        while len(features) < max_objets:
            page = s.appeler("/query", **commun, orderByFields=id_champ, resultOffset=decalage,
                             resultRecordCount=min(taille, max_objets - len(features)))
            n = ajouter(page)
            decalage += n
            if n == 0 or (n < taille and not page.get("exceededTransferLimit")):
                break
            if isinstance(total, int) and len(features) >= total:
                break
    elif id_champ:
        ids = sorted(s.appeler("/query", where=where, returnIdsOnly="true").get("objectIds") or [])
        for i in range(0, min(len(ids), max_objets), taille):
            bloc = ids[i:min(i + taille, max_objets)]
            ajouter(s.appeler("/query", **commun, objectIds=",".join(str(v) for v in bloc)))
    else:
        page = s.appeler("/query", **commun)
        ajouter(page)
        if page.get("exceededTransferLimit"):
            features = features[:max_objets]

    if service.get("systeme"):
        crs = inv_sig._lire_systeme(str(service["systeme"]))
    else:
        crs = systeme_esri(reponse_sr or sr_service)

    for f in info.get("fields") or []:
        types.setdefault(f["name"], f.get("type"))
    lignes = [{k: _valeur(v, types.get(k)) for k, v in (f.get("attributes") or {}).items()} for f in features]
    geoms = [geometrie(f.get("geometry")) for f in features]
    gdf = gpd.GeoDataFrame(lignes, geometry=geoms, crs=crs)
    # « fid » est réservé par le GeoPackage
    gdf = gdf.rename(columns={c: f"{c}_esri" for c in gdf.columns if str(c).lower() == "fid"})

    tronque = total is not None and len(gdf) < total
    meta = {
        "url": service["url"],
        "where": where,
        "nom_service": info.get("name"),
        "copyright": info.get("copyrightText") or "",
        "date_maj": datetime.now().isoformat(timespec="seconds"),
        "nb_objets": len(gdf),
        "nb_objets_service": total,
        "tronque": tronque,
        "max_objets": max_objets,
        "taille_page": taille,
        "pagination": "decalage" if pagination and id_champ else ("identifiants" if id_champ else "aucune"),
        "duree_secondes": round(time.monotonic() - debut, 1),
    }
    return gdf, meta


# ---------------------------------------------------------------- cache local

def _fichiers_cache(cfg: dict, id_: str) -> tuple[Path, Path]:
    d = dossier_cache(cfg)
    return d / f"{id_}.gpkg", d / f"{id_}.json"


def lire_meta(cfg: dict, id_: str) -> dict | None:
    gpkg, meta = _fichiers_cache(cfg, id_)
    if not (gpkg.is_file() and meta.is_file()):
        return None
    try:
        return json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def ecrire_cache(cfg: dict, id_: str, gdf: gpd.GeoDataFrame, meta: dict) -> None:
    gpkg, fiche = _fichiers_cache(cfg, id_)
    gpkg.parent.mkdir(parents=True, exist_ok=True)
    tmp = gpkg.with_suffix(".tmp.gpkg")
    tmp.unlink(missing_ok=True)
    if gdf.empty:
        raise ErreurService("le service n'a renvoyé aucun objet (filtre « where » à vérifier)")
    gdf.to_file(tmp, layer=COUCHE_CACHE, driver="GPKG", engine="pyogrio")
    os.replace(tmp, gpkg)
    fiche.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _age_heures(meta: dict) -> float:
    try:
        return (datetime.now() - datetime.fromisoformat(meta["date_maj"])).total_seconds() / 3600
    except (KeyError, ValueError):
        return math.inf


# ---------------------------------------------------------------- couches de service

_verrou = threading.Lock()
_echecs: dict[str, tuple[float, str]] = {}  # id -> (instant, message) du dernier échec de téléchargement


def reinitialiser() -> None:
    with _verrou:
        _echecs.clear()


def _couche_cache(cfg, service, id_, meta, rang, source, avert) -> inv_sig.Couche:
    gpkg, _ = _fichiers_cache(cfg, id_)
    decl = {k: v for k, v in service.items() if k in ("style", "visible", "champ_libelle")}
    c = inv_sig._couche(cfg, dossier_cache(cfg), gpkg, COUCHE_CACHE, decl, rang)
    c.id, c.couche, c.fichier = id_, None, service["url"]
    c.libelle = service["libelle"]
    c.format = "Service Esri"
    c.systeme_source = "service" if not service.get("systeme") else "configuration"
    c.avertissements = avert + c.avertissements
    if meta.get("tronque"):
        c.avertissements.append(
            f"{meta['nb_objets']} objets téléchargés sur {meta['nb_objets_service']} (limite « max_objets »)")
    c.service = {
        "url": service["url"], "where": meta.get("where"), "source": source,
        "date_maj": meta.get("date_maj"), "copyright": meta.get("copyright", ""),
        "nb_objets_service": meta.get("nb_objets_service"), "pagination": meta.get("pagination"),
    }
    return c


def _couche_repli(cfg, service, id_, rang, avert) -> inv_sig.Couche:
    export = service["export"]
    chemin = dossier_exports(cfg) / export
    if not chemin.is_file():
        raise inv_sig.AnomalieCouche(f"export de repli introuvable : {export} dans {cfg['esri']['dossier_exports']}")
    if chemin.suffix.lower() not in inv_sig.FORMATS:
        raise inv_sig.AnomalieCouche(f"export de repli {export} : format non géré")
    decl = {k: v for k, v in service.items() if k not in ("libelle", "url", "export", "export_couche")}
    c = inv_sig._couche(cfg, dossier_exports(cfg), chemin, service.get("export_couche"), decl, rang)
    c.id = id_
    c.libelle = service["libelle"]
    c.format = f"Service Esri (export {c.format})"
    c.avertissements = avert + c.avertissements
    c.service = {"url": service["url"], "where": service.get("where") or "1=1", "source": "repli",
                 "date_maj": datetime.fromtimestamp(chemin.stat().st_mtime).isoformat(timespec="seconds"),
                 "export": export}
    return c


def couche_service(cfg: dict, service: dict, rang: int, forcer: bool = False,
                   client: httpx.Client | None = None) -> inv_sig.Couche:
    """Couche d'un service : cache à jour, sinon téléchargement, sinon cache ancien, sinon export de repli.

    Lève AnomalieCouche si rien n'est disponible.
    """
    id_ = identifiant(service)
    meta = lire_meta(cfg, id_)
    duree = float(service.get("cache_heures", cfg["esri"]["cache_heures_defaut"]))
    if meta and not forcer and _age_heures(meta) < duree:
        return _couche_cache(cfg, service, id_, meta, rang, "cache", [])

    attente = float(cfg["esri"]["nouvel_essai_minutes"]) * 60
    with _verrou:
        echec = _echecs.get(id_)
    if echec and not forcer and time.monotonic() - echec[0] < attente:
        message = echec[1]
    else:
        try:
            propre = client is None
            client = client or fabrique_client(cfg)
            try:
                gdf, nouvelle = telecharger(service, cfg, client)
            finally:
                if propre:
                    client.close()
            ecrire_cache(cfg, id_, gdf, nouvelle)
            with _verrou:
                _echecs.pop(id_, None)
            return _couche_cache(cfg, service, id_, nouvelle, rang, "service", [])
        except (ErreurService, inv_sig.AnomalieCouche) as e:
            message = str(e)
        except Exception as e:  # garde-fou : un service ne doit jamais interrompre l'inventaire
            message = f"erreur inattendue ({type(e).__name__} : {e})"
        with _verrou:
            _echecs[id_] = (time.monotonic(), message)

    if meta:
        date = meta.get("date_maj", "?").replace("T", " à ")
        return _couche_cache(cfg, service, id_, meta, rang, "cache_perime",
                             [f"service indisponible ({message}) : données du cache du {date}"])
    if service.get("export"):
        return _couche_repli(cfg, service, id_, rang,
                             [f"service indisponible ({message}) : repli sur l'export {service['export']}"])
    raise inv_sig.AnomalieCouche(f"{message} ; ni cache local ni export de repli (clé « export »)")


def couches_services(cfg: dict, rang0: int = 0, client: httpx.Client | None = None,
                     forcer: str | None = None) -> tuple[list[inv_sig.Couche], list[dict]]:
    """Couches des services déclarés et anomalies (une anomalie n'empêche pas les autres services)."""
    couches, anomalies = [], []
    for service in cfg.get("services_esri") or []:
        id_ = identifiant(service)
        try:
            couches.append(couche_service(cfg, service, rang0 + len(couches), forcer == id_, client))
        except inv_sig.AnomalieCouche as e:
            anomalies.append({"fichier": service["url"], "couche": service["libelle"], "message": str(e), "service": True})
        except Exception as e:
            anomalies.append({"fichier": service["url"], "couche": service["libelle"],
                              "message": f"erreur inattendue ({type(e).__name__} : {e})", "service": True})
    return couches, anomalies

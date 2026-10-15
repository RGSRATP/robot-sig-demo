"""Connecteurs WFS 2.0 et « GeoJSON par URL » (lot 5, catalogue Open Data), déclarés dans config/sig.yaml.

Même chemin que les services Esri (`esri.py`) : la couche est téléchargée par le serveur, écrite dans le cache
local (GeoPackage + fiche), puis traitée comme une couche fichier ; service indisponible → cache précédent,
sinon export de repli, sinon anomalie. Seul le téléchargement diffère :

- WFS 2.0 (`services_wfs`) : GetFeature en GeoJSON, paginé (COUNT / STARTINDEX), limité à une emprise
  obligatoire (les services WFS publics sont nationaux : jamais de téléchargement complet), dans le système
  demandé (`systeme`, Lambert 93 par défaut) ;
- GeoJSON par URL (`services_geojson`) : un seul appel (export d'une API Explore Opendatasoft, fichier publié…),
  taille plafonnée, système déclaré (GeoJSON : WGS84 par définition), filtre d'emprise facultatif après lecture.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime

import geopandas as gpd
import httpx
from pyproj import Transformer
from shapely.geometry import box, shape

from . import inventaire as inv_sig
from . import reseau
from .esri import ErreurService

MOTIF_URL = re.compile(r"^https?://\S+$", re.IGNORECASE)
SYSTEME_WFS_DEFAUT = "EPSG:2154"
SYSTEME_GEOJSON_DEFAUT = "EPSG:4326"
TAILLE_PAGE_WFS = 1000
MAX_MO_GEOJSON = 50


def _emprise_texte(service: dict) -> str:
    e = service.get("emprise_wgs84")
    return ",".join(f"{v:g}" for v in e) if e else ""


def identifiant_wfs(service: dict) -> str:
    return inv_sig.identifiant(f"wfs:{service['url'].rstrip('/?')}|{service['couche']}", _emprise_texte(service))


def identifiant_geojson(service: dict) -> str:
    return inv_sig.identifiant(f"geojson:{service['url']}", _emprise_texte(service))


# ---------------------------------------------------------------- lecture des réponses

def _get(client: httpx.Client, url: str, params: dict | None = None) -> httpx.Response:
    try:
        r = client.get(url, params=params)
    except httpx.HTTPError as e:
        raise ErreurService(f"service {reseau.message_erreur(e)}") from None
    if r.status_code in (401, 403):
        raise ErreurService(f"accès refusé (HTTP {r.status_code})")
    if r.status_code >= 400:
        raise ErreurService(f"réponse HTTP {r.status_code} du service{_exception_ows(r.text)}")
    return r


def _exception_ows(texte: str) -> str:
    """Message d'une ExceptionReport OGC (WFS), s'il y en a une."""
    m = re.search(r"<(?:\w+:)?ExceptionText>(.*?)</(?:\w+:)?ExceptionText>", texte or "", re.S)
    return f" : {re.sub(r'\s+', ' ', m.group(1)).strip()[:300]}" if m else ""


def _collection(r: httpx.Response) -> dict:
    """FeatureCollection GeoJSON ; sinon erreur explicite (exception OGC, page HTML, JSON inattendu)."""
    try:
        corps = r.json()
    except ValueError:
        exc = _exception_ows(r.text)
        raise ErreurService(f"réponse du service en erreur{exc}" if exc
                            else "réponse illisible (ce n'est pas du GeoJSON : URL ou nom de couche à vérifier)") from None
    return _verifier_collection(corps)


def _verifier_collection(corps) -> dict:
    if not isinstance(corps, dict) or corps.get("type") != "FeatureCollection" or not isinstance(corps.get("features"), list):
        raise ErreurService("réponse inattendue : une collection d'objets GeoJSON (FeatureCollection) est attendue")
    return corps


def _valeur(v):
    """Valeurs imbriquées (listes, objets des API Explore) gardées en texte JSON : le GeoPackage ne les stocke pas."""
    return json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v


def _vers_gdf(features: list, crs) -> tuple[gpd.GeoDataFrame, int]:
    """GeoDataFrame des objets ; ceux sans géométrie lisible sont écartés et comptés."""
    lignes, geoms, ecartes = [], [], 0
    for f in features:
        try:
            g = shape(f["geometry"]) if f.get("geometry") else None
        except Exception:
            g = None
        if g is None or g.is_empty:
            ecartes += 1
            continue
        lignes.append({k: _valeur(v) for k, v in (f.get("properties") or {}).items()})
        geoms.append(g)
    gdf = gpd.GeoDataFrame(lignes, geometry=geoms, crs=crs)
    # « fid » est réservé par le GeoPackage
    gdf = gdf.rename(columns={c: f"{c}_source" for c in gdf.columns if str(c).lower() == "fid"})
    return gdf, ecartes


def _crs(service: dict, defaut: str):
    systeme = service.get("systeme") or defaut
    try:
        return inv_sig._lire_systeme(str(systeme))
    except Exception:
        raise ErreurService(f"système « {systeme} » inconnu") from None


# ---------------------------------------------------------------- WFS 2.0

def telecharger_wfs(service: dict, cfg: dict, client: httpx.Client) -> tuple[gpd.GeoDataFrame, dict]:
    debut = time.monotonic()
    systeme = str(service.get("systeme") or SYSTEME_WFS_DEFAUT)
    crs = _crs(service, SYSTEME_WFS_DEFAUT)
    ouest, sud, est, nord = service["emprise_wgs84"]
    taille = int(service.get("taille_page") or TAILLE_PAGE_WFS)
    max_objets = int(service.get("max_objets") or cfg["esri"]["max_objets"])
    base = {
        "SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature", "TYPENAMES": service["couche"],
        # MapServer (Sandre, BRGM, Géorisques) : « application/json; subtype=geojson », déclaré dans « format_sortie »
        "OUTPUTFORMAT": service.get("format_sortie") or "application/json", "SRSNAME": systeme,
        # WFS 2.0 : axes de EPSG:4326 dans l'ordre latitude, longitude
        "BBOX": f"{sud},{ouest},{nord},{est},urn:ogc:def:crs:EPSG::4326",
    }
    features, total, decalage = [], None, 0
    while len(features) < max_objets:
        n_demande = min(taille, max_objets - len(features))
        params = {**base, "COUNT": n_demande}
        # Décalage seulement à partir de la 2e page : GeoServer (Airparif) refuse un décalage sans ordre de tri
        # quand la couche n'a pas de clé primaire ; « tri » (SORTBY) le fournit pour les couches à plusieurs pages.
        if decalage:
            params["STARTINDEX"] = decalage
        if service.get("tri"):
            params["SORTBY"] = service["tri"]
        page = _collection(_get(client, service["url"], params))
        if total is None:
            total = page.get("numberMatched") if isinstance(page.get("numberMatched"), int) else page.get("totalFeatures")
            total = total if isinstance(total, int) else None
        lot = page["features"]
        features.extend(lot)
        decalage += len(lot)
        if len(lot) < n_demande or (total is not None and decalage >= total):
            break

    gdf, ecartes = _vers_gdf(features, crs)
    tronque = (total is not None and len(features) < total) or (total is None and len(features) >= max_objets)
    meta = {
        "url": service["url"], "couche": service["couche"], "where": f"emprise {_emprise_texte(service)}",
        "copyright": service.get("attribution", ""), "date_maj": datetime.now().isoformat(timespec="seconds"),
        "nb_objets": len(gdf), "nb_objets_service": total, "sans_geometrie": ecartes, "tronque": tronque,
        "max_objets": max_objets, "taille_page": taille, "pagination": "decalage",
        "duree_secondes": round(time.monotonic() - debut, 1),
    }
    if gdf.empty:
        raise ErreurService("aucun objet dans l'emprise déclarée (« emprise_wgs84 » ou nom de couche à vérifier)")
    return gdf, meta


# ---------------------------------------------------------------- GeoJSON par URL

def _lire_borne(client: httpx.Client, url: str, max_octets: int) -> dict:
    """Lecture plafonnée (contenu décompressé) : un export trop gros est refusé au lieu de saturer la mémoire."""
    try:
        with client.stream("GET", url) as r:
            if r.status_code in (401, 403):
                raise ErreurService(f"accès refusé (HTTP {r.status_code})")
            if r.status_code >= 400:
                raise ErreurService(f"réponse HTTP {r.status_code} du service")
            contenu = bytearray()
            for morceau in r.iter_bytes():      # déjà décompressé (gzip des API Explore)
                contenu += morceau
                if len(contenu) > max_octets:
                    raise ErreurService(f"fichier de plus de {max_octets // 1_000_000} Mo (« max_mo ») : "
                                        "filtrer à la source (paramètre « where » d'une API Explore) ou déposer un extrait")
    except httpx.HTTPError as e:
        raise ErreurService(f"service {reseau.message_erreur(e)}") from None
    try:
        corps = json.loads(bytes(contenu).decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError):
        raise ErreurService("réponse illisible (ce n'est pas du GeoJSON : URL à vérifier)") from None
    return _verifier_collection(corps)


def telecharger_geojson(service: dict, cfg: dict, client: httpx.Client) -> tuple[gpd.GeoDataFrame, dict]:
    debut = time.monotonic()
    crs = _crs(service, SYSTEME_GEOJSON_DEFAUT)
    max_octets = int(float(service.get("max_mo", MAX_MO_GEOJSON)) * 1_000_000)
    max_objets = int(service.get("max_objets") or cfg["esri"]["max_objets"])
    page = _lire_borne(client, service["url"], max_octets)
    gdf, ecartes = _vers_gdf(page["features"], crs)
    nb_lus = len(gdf)

    if service.get("emprise_wgs84") and not gdf.empty:
        ouest, sud, est, nord = service["emprise_wgs84"]
        vers = Transformer.from_crs("EPSG:4326", gdf.crs, always_xy=True)
        x0, y0 = vers.transform(ouest, sud)
        x1, y1 = vers.transform(est, nord)
        gdf = gdf[gdf.intersects(box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))]
    tronque = len(gdf) > max_objets
    gdf = gdf.iloc[:max_objets]
    meta = {
        "url": service["url"], "where": f"emprise {_emprise_texte(service)}" if service.get("emprise_wgs84") else "tout le fichier",
        "copyright": service.get("attribution", ""), "date_maj": datetime.now().isoformat(timespec="seconds"),
        "nb_objets": len(gdf), "nb_objets_service": nb_lus, "sans_geometrie": ecartes, "tronque": tronque,
        "max_objets": max_objets, "pagination": "aucune", "duree_secondes": round(time.monotonic() - debut, 1),
    }
    if gdf.empty:
        raise ErreurService("aucun objet géolocalisé" + (" dans l'emprise déclarée" if service.get("emprise_wgs84") else ""))
    return gdf.reset_index(drop=True), meta

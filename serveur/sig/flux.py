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
from shapely.prepared import prep

from . import inventaire as inv_sig
from . import reseau
from .esri import ErreurService

MOTIF_URL = re.compile(r"^https?://\S+$", re.IGNORECASE)
SYSTEME_WFS_DEFAUT = "EPSG:2154"
SYSTEME_GEOJSON_DEFAUT = "EPSG:4326"
TAILLE_PAGE_WFS = 1000
MAX_MO_GEOJSON = 50


def _emprise_texte(service: dict) -> str:
    if service.get("le_long_de"):
        d = service["le_long_de"]
        return f"le long de {d['couche']} ({d['tampon_m']:g} m)"
    e = service.get("emprise_wgs84")
    return ",".join(f"{v:g}" for v in e) if e else ""


def identifiant_wfs(service: dict) -> str:
    filtre = f"|{service['filtre_cql']}" if service.get("filtre_cql") else ""   # même couche, filtres différents
    return inv_sig.identifiant(f"wfs:{service['url'].rstrip('/?')}|{service['couche']}{filtre}", _emprise_texte(service))


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


# ---------------------------------------------------------------- bande le long d'une couche (lignes du GPE…)

PAS_CARREAUX_M = 2000


def zone_le_long_de(decl: dict, cfg: dict):
    """Bande de `tampon_m` mètres autour de la couche fichier de référence (libellé exact), en Lambert 93,
    et carreaux de `pas_m` mètres qui la recouvrent (emprises WGS84 [ouest, sud, est, nord] à interroger)."""
    from . import objets   # import local : objets dépend de l'inventaire, comme ce module
    couches = [c for c in inv_sig.inventaire(cfg).couches if c.libelle == decl["couche"]]
    if not couches:
        raise ErreurService(f"couche de référence « {decl['couche']} » introuvable parmi les couches fichiers (libellé exact)")
    ref, _ = objets._lire(couches[0])
    if ref.crs is None:
        raise ErreurService(f"couche de référence « {decl['couche']} » sans système de coordonnées")
    zone = ref.to_crs(2154).buffer(float(decl["tampon_m"])).union_all()
    pas = float(decl.get("pas_m") or PAS_CARREAUX_M)
    x0, y0, x1, y1 = zone.bounds
    vers_wgs84 = Transformer.from_crs("EPSG:2154", "EPSG:4326", always_xy=True)
    cases = []
    y = y0
    while y < y1:
        x = x0
        while x < x1:
            carreau = box(x, y, x + pas, y + pas)
            if carreau.intersects(zone):
                lons, lats = vers_wgs84.transform([x, x + pas, x, x + pas], [y, y, y + pas, y + pas])
                cases.append([min(lons), min(lats), max(lons), max(lats)])
            x += pas
        y += pas
    return zone, cases


# ---------------------------------------------------------------- WFS 2.0

def _pages_wfs(client, service, base, emprise, taille, reste):
    """Objets d'une emprise WGS84, page par page (au plus `reste`) ; nombre total annoncé par le service."""
    ouest, sud, est, nord = emprise
    if service.get("filtre_cql"):
        # GeoServer refuse BBOX et CQL_FILTER ensemble : l'emprise entre dans le filtre (CQL : longitude, latitude)
        champ = service.get("champ_geometrie") or "geometrie"
        base = {**base, "CQL_FILTER": f"({service['filtre_cql']}) AND BBOX({champ},{ouest},{sud},{est},{nord},'EPSG:4326')"}
    else:
        # WFS 2.0 : axes de EPSG:4326 dans l'ordre latitude, longitude
        base = {**base, "BBOX": f"{sud},{ouest},{nord},{est},urn:ogc:def:crs:EPSG::4326"}
    features, total, decalage = [], None, 0
    while len(features) < reste:
        n_demande = min(taille, reste - len(features))
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
    return features, total


def _cle_objet(f: dict) -> str:
    """Identité d'un objet renvoyé par plusieurs carreaux : identifiant WFS, sinon contenu complet."""
    return str(f["id"]) if f.get("id") is not None else json.dumps([f.get("geometry"), f.get("properties")], sort_keys=True)


def telecharger_wfs(service: dict, cfg: dict, client: httpx.Client) -> tuple[gpd.GeoDataFrame, dict]:
    debut = time.monotonic()
    systeme = str(service.get("systeme") or SYSTEME_WFS_DEFAUT)
    crs = _crs(service, SYSTEME_WFS_DEFAUT)
    taille = int(service.get("taille_page") or TAILLE_PAGE_WFS)
    max_objets = int(service.get("max_objets") or cfg["esri"]["max_objets"])
    base = {
        "SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature", "TYPENAMES": service["couche"],
        # MapServer (Sandre, BRGM, Géorisques) : « application/json; subtype=geojson », déclaré dans « format_sortie »
        "OUTPUTFORMAT": service.get("format_sortie") or "application/json", "SRSNAME": systeme,
    }
    # Bande le long d'une couche : interrogée carreau par carreau (le service n'accepte qu'un rectangle),
    # objets dédoublonnés puis limités à la bande ; sinon une seule emprise rectangulaire.
    zone = None
    if service.get("le_long_de"):
        zone, emprises = zone_le_long_de(service["le_long_de"], cfg)
        # Bande dans le système demandé au service, préparée : tri des objets carreau par carreau, avant tout comptage
        bande = prep(gpd.GeoSeries([zone], crs="EPSG:2154").to_crs(crs).iloc[0])
    else:
        emprises = [service["emprise_wgs84"]]
    vus, features, total, hors_bande, echecs = set(), [], None, 0, []
    for i, emprise in enumerate(emprises):
        try:
            lot, total_emprise = _pages_wfs(client, service, base, emprise, taille,
                                            max_objets - len(features) + 1 if zone is None else 10 * max_objets)
        except ErreurService as e:
            if zone is None or len(echecs) >= max(3, len(emprises) // 10):
                raise   # une seule emprise, ou trop de carreaux en échec : la couche n'est pas fiable
            echecs.append(f"carreau {i + 1} ({e})")   # ne jamais bloquer : carreau signalé, les autres continuent
            continue
        if len(emprises) == 1:
            total = total_emprise
        for f in lot:
            cle = _cle_objet(f)
            if cle in vus:
                continue
            vus.add(cle)
            if zone is not None:
                try:
                    dedans = f.get("geometry") is not None and bande.intersects(shape(f["geometry"]))
                except Exception:
                    dedans = False
                if not dedans:
                    hors_bande += 1
                    continue
            features.append(f)
        if len(features) > max_objets:
            break
    tronque = len(features) > max_objets or (total is not None and len(features) < total)
    features = features[:max_objets]

    gdf, ecartes = _vers_gdf(features, crs)
    meta = {
        "url": service["url"], "couche": service["couche"],
        "where": (f"{_emprise_texte(service)}, {len(emprises)} carreaux" if zone is not None
                  else f"emprise {_emprise_texte(service)}"),
        "hors_bande_ecartes": hors_bande,
        "carreaux_en_echec": echecs,
        "copyright": service.get("attribution", ""), "date_maj": datetime.now().isoformat(timespec="seconds"),
        "nb_objets": len(gdf), "nb_objets_service": total, "sans_geometrie": ecartes, "tronque": tronque,
        "max_objets": max_objets, "taille_page": taille, "pagination": "decalage",
        "duree_secondes": round(time.monotonic() - debut, 1),
    }
    if gdf.empty:
        raise ErreurService("aucun objet dans l'emprise ou la bande déclarée (« emprise_wgs84 », « le_long_de » "
                            "ou nom de couche à vérifier)")
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

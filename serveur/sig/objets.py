"""Lecture des objets d'une couche inventoriée : GeoJSON en WGS84, fiche d'un objet, recherche texte.

Les géométries d'origine sont conservées : la fiche d'un objet donne ses coordonnées dans le système
du fichier et en Lambert 93.
"""
from __future__ import annotations

import math
import threading
import unicodedata
from collections import OrderedDict
from datetime import date, datetime, time

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import box, mapping

from .inventaire import AnomalieCouche, Couche, lire_csv, lire_geo

CACHE_COUCHES = 8  # nombre de couches gardées en mémoire (technique, pas un paramètre métier)

_verrou = threading.Lock()
_cache: OrderedDict = OrderedDict()


class Charge:
    """Couche chargée : objets dans le système d'origine, en WGS84 et en Lambert 93."""

    def __init__(self, origine: gpd.GeoDataFrame, avertissements: list[str] | None = None):
        self.origine = origine
        self.avertissements = avertissements or []   # géométries réparées ou écartées à la lecture
        self.wgs84 = origine.to_crs(4326)
        self.l93 = origine if origine.crs.to_epsg() == 2154 else origine.to_crs(2154)
        self._index_texte: pd.Series | None = None

    def index_texte(self) -> pd.Series:
        """Concaténation normalisée (minuscules, sans accents) des attributs texte, pour la recherche."""
        if self._index_texte is None:
            attrs = self.origine.drop(columns=self.origine.geometry.name)
            texte = attrs.astype(str).where(attrs.notna(), "").agg(" | ".join, axis=1)
            self._index_texte = texte.map(normaliser)
        return self._index_texte


def normaliser(texte: str) -> str:
    decompose = unicodedata.normalize("NFD", str(texte).lower())
    return "".join(c for c in decompose if unicodedata.category(c) != "Mn")


def _lire(couche: Couche) -> tuple[gpd.GeoDataFrame, list[str]]:
    chemin = couche._chemin
    avert = []
    if couche.format == "CSV":
        df, _ = lire_csv(chemin, couche._csv)
        geo = gpd.GeoDataFrame(df.drop(columns=["_x", "_y"]),
                               geometry=gpd.points_from_xy(df["_x"], df["_y"]), crs=couche.systeme)
    else:
        geo, avert = lire_geo(chemin, couche.couche)
        if couche.systeme_source == "configuration" or geo.crs is None:
            geo = geo.set_crs(couche.systeme, allow_override=True)
    geo = geo[geo.geometry.notna() & ~geo.geometry.is_empty].copy()
    geo.index = pd.RangeIndex(len(geo))  # numéro d'objet stable pour une version du fichier
    return geo, avert


def charger(couche: Couche) -> Charge:
    try:
        cle = (couche.id, couche._chemin.stat().st_mtime_ns, couche.systeme)
    except OSError as e:
        raise AnomalieCouche(f"fichier inaccessible depuis l'inventaire ({type(e).__name__})") from e
    with _verrou:
        if cle in _cache:
            _cache.move_to_end(cle)
            return _cache[cle]
    try:
        charge = Charge(*_lire(couche))
    except AnomalieCouche:
        raise
    except Exception as e:
        raise AnomalieCouche(f"lecture impossible ({type(e).__name__} : {e})") from e
    with _verrou:
        _cache[cle] = charge
        while len(_cache) > CACHE_COUCHES:
            _cache.popitem(last=False)
    return charge


# ---------------------------------------------------------------- sérialisation

def valeur_json(v):
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        f = float(v)
        return f if math.isfinite(f) else None
    if isinstance(v, (np.bool_,)):
        return bool(v)
    if isinstance(v, (pd.Timestamp, datetime, date, time)):
        return None if pd.isna(v) else v.isoformat()
    if isinstance(v, bytes):
        return v.hex()
    if isinstance(v, (str, int, float, bool)):
        return v
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return str(v)


def _attributs(ligne: pd.Series, geom_col: str) -> dict:
    return {k: valeur_json(v) for k, v in ligne.items() if k != geom_col}


def _arrondi(coords, n):
    if isinstance(coords, (list, tuple)) and coords and isinstance(coords[0], (int, float)):
        return [round(c, n) for c in coords]
    return [_arrondi(c, n) for c in coords]


def _geometrie_json(geom, decimales: int) -> dict:
    g = mapping(geom)
    if "coordinates" in g:
        g = {"type": g["type"], "coordinates": _arrondi(g["coordinates"], decimales)}
    elif "geometries" in g:
        g = {"type": g["type"], "geometries": [_geometrie_json(x, decimales) for x in geom.geoms]}
    return g


# ---------------------------------------------------------------- services

def objets(couche: Couche, bbox: list[float] | None, seuil: int) -> dict:
    """FeatureCollection WGS84. Au-delà du seuil, limitée à l'emprise demandée (et tronquée au seuil)."""
    charge = charger(couche)
    geo = charge.wgs84
    filtre = couche.filtre_emprise
    if bbox is not None:
        geo = geo.iloc[geo.sindex.query(box(*bbox), predicate="intersects")].sort_index()
    elif filtre:
        raise ValueError("couche volumineuse : le paramètre bbox (emprise affichée) est obligatoire")
    total = len(geo)
    tronque = total > seuil
    if tronque:
        geo = geo.iloc[:seuil]

    nom_geom = geo.geometry.name
    lib = couche.champ_libelle
    features = []
    for n, ligne in geo.iterrows():
        props = {"_n": int(n)}
        if lib:
            props["_libelle"] = valeur_json(ligne[lib])
        features.append({"type": "Feature", "id": int(n), "geometry": _geometrie_json(ligne[nom_geom], 7),
                         "properties": props})
    return {
        "type": "FeatureCollection",
        "features": features,
        "filtre_emprise": bbox is not None and filtre,
        "total_dans_emprise": total,
        "tronque": tronque,
        "avertissements": charge.avertissements,
    }


def fiche(couche: Couche, n: int) -> dict | None:
    charge = charger(couche)
    if n not in charge.origine.index:
        return None
    orig = charge.origine.loc[n]
    geom_o = orig[charge.origine.geometry.name]
    geom_l = charge.l93.loc[n, charge.l93.geometry.name]
    geom_w = charge.wgs84.loc[n, charge.wgs84.geometry.name]

    def coords(g, decimales):
        if g.geom_type == "Point":
            return {"point": [round(g.x, decimales), round(g.y, decimales)]}
        return {"emprise": [round(v, decimales) for v in g.bounds]}

    lib = couche.champ_libelle
    return {
        "couche": couche.id,
        "n": n,
        "libelle": valeur_json(orig[lib]) if lib else None,
        "type_geometrie": geom_o.geom_type,
        "attributs": _attributs(orig, charge.origine.geometry.name),
        "coordonnees": {
            "origine": {"systeme": couche.systeme, **coords(geom_o, 3 if not charge.origine.crs.is_geographic else 8)},
            "lambert93": {"systeme": "EPSG:2154", **coords(geom_l, 2)},
        },
        "emprise_wgs84": [round(v, 7) for v in geom_w.bounds],
        "geometrie_wgs84": _geometrie_json(geom_w, 7),
    }


def rechercher(couche: Couche, q: str, max_resultats: int) -> dict:
    q = normaliser(q.strip())
    if len(q) < 2:
        return {"q": q, "total": 0, "resultats": []}
    charge = charger(couche)
    trouve = charge.index_texte().str.contains(q, regex=False)
    indices = trouve[trouve].index
    lib = couche.champ_libelle
    attrs = charge.origine.drop(columns=charge.origine.geometry.name)
    resultats = []
    for n in indices[:max_resultats]:
        ligne = attrs.loc[n]
        champ, valeur = next(((k, v) for k, v in ligne.items()
                              if v is not None and not (isinstance(v, float) and math.isnan(v)) and q in normaliser(v)),
                             (None, None))
        resultats.append({
            "n": int(n),
            "libelle": valeur_json(ligne[lib]) if lib else None,
            "champ": champ,
            "valeur": valeur_json(valeur),
            "emprise_wgs84": [round(v, 7) for v in charge.wgs84.geometry.loc[n].bounds],
        })
    return {"q": q, "total": int(len(indices)), "resultats": resultats}

"""Inventaire des couches fichiers (GeoPackage, Shapefile, GeoJSON, CSV) du dossier SIG.

Ne jamais bloquer : un fichier défectueux devient une anomalie, les autres couches restent disponibles.
Ne jamais supposer un système : sans système (fichier ou configuration), ou avec un système incohérent
avec les coordonnées, la couche n'est pas chargée.
"""
from __future__ import annotations

import hashlib
import math
import re
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import shapely
from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError

from .config import dossier_sig

FORMATS = {".gpkg": "GeoPackage", ".shp": "Shapefile", ".geojson": "GeoJSON", ".json": "GeoJSON", ".csv": "CSV"}
# Fichiers compagnons d'un Shapefile ou d'un GeoPackage : ni couche, ni fichier ignoré à signaler.
COMPAGNONS = {".shx", ".dbf", ".prj", ".cpg", ".qix", ".sbn", ".sbx", ".qmd", ".gpkg-wal", ".gpkg-shm"}
PROJETS_QGIS = {".qgz", ".qgs"}
# Pilotes GDAL dont le nom affiché diffère de celui déduit de l'extension (un .json peut être un export ArcGIS).
NOMS_PILOTES = {"ESRIJSON": "Esri JSON"}
# Export ArcGIS (requête « query ») arrêté à la limite du service : drapeau placé en tête ou en fin de fichier.
LIMITE_TRANSFERT = re.compile(rb'"exceededTransferLimit"\s*:\s*true')
# Champ d'affichage déclaré par un export ArcGIS (« displayFieldName »), en tête de fichier.
CHAMP_AFFICHAGE = re.compile(rb'"displayFieldName"\s*:\s*"([^"]+)"')
OCTETS_LUS_LIMITE = 65536


@dataclass
class Couche:
    id: str
    libelle: str
    fichier: str            # chemin relatif au dossier SIG
    couche: str | None      # nom interne (GeoPackage) ; None pour les autres formats
    format: str
    geometrie: str          # point, ligne, polygone (famille utilisée pour le style)
    type_geometrie: str     # type exact déclaré par le fichier
    systeme: str            # système utilisé, ex. "EPSG:2154"
    systeme_nom: str
    systeme_source: str     # "fichier" ou "configuration"
    nb_objets: int
    emprise_wgs84: list[float] | None
    emprise_l93: list[float] | None
    filtre_emprise: bool
    visible: bool
    style: dict
    champs: list[str]
    champ_libelle: str | None
    avertissements: list[str] = field(default_factory=list)
    service: dict | None = None   # couche d'un service Esri : URL, source (service, cache, repli), date de mise à jour
    # Paramètres de lecture (non exposés)
    _csv: dict | None = None
    _chemin: Path | None = None

    def publique(self) -> dict:
        return {k: v for k, v in asdict(self).items() if not k.startswith("_")}


@dataclass
class Inventaire:
    dossier: str
    date: str
    couches: list[Couche]
    anomalies: list[dict]
    ignores: list[dict]

    def couche(self, id_: str) -> Couche | None:
        return next((c for c in self.couches if c.id == id_), None)

    def publique(self) -> dict:
        return {
            "dossier": self.dossier,
            "date": self.date,
            "couches": [c.publique() for c in self.couches],
            "anomalies": self.anomalies,
            "ignores": self.ignores,
        }


class AnomalieCouche(Exception):
    """Couche non chargée ; le message est destiné à l'utilisateur."""


# ---------------------------------------------------------------- systèmes de coordonnées

def _nom_systeme(crs: CRS) -> str:
    epsg = crs.to_epsg(min_confidence=70)
    return f"EPSG:{epsg}" if epsg else (crs.name or "système sans code EPSG")


def _lire_systeme(texte: str) -> CRS:
    try:
        return CRS.from_user_input(texte)
    except CRSError as e:
        raise AnomalieCouche(f"système « {texte} » non reconnu ({e})") from e


def _controler_emprise(crs: CRS, emprise: list[float], marge: float) -> list[float]:
    """Emprise convertie en WGS84 ; lève AnomalieCouche si elle est incompatible avec le système."""
    minx, miny, maxx, maxy = emprise
    if crs.is_geographic and not (-180 <= minx <= maxx <= 180 and -90 <= miny <= maxy <= 90):
        raise AnomalieCouche(
            f"coordonnées incohérentes avec le système déclaré {_nom_systeme(crs)} "
            f"(emprise {minx:.0f}, {miny:.0f}, {maxx:.0f}, {maxy:.0f} : ce ne sont pas des degrés). "
            "Déclarez le vrai système dans config/sig.yaml (clé « systeme »)."
        )
    vers_wgs84 = Transformer.from_crs(crs, 4326, always_xy=True)
    try:
        w = list(vers_wgs84.transform_bounds(minx, miny, maxx, maxy))
    except Exception:
        w = [math.inf] * 4
    zone = crs.area_of_use
    hors_zone = not all(math.isfinite(v) for v in w)
    if not hors_zone and zone is not None:
        hors_zone = (w[0] < zone.west - marge or w[2] > zone.east + marge
                     or w[1] < zone.south - marge or w[3] > zone.north + marge)
    if hors_zone:
        domaine = f" (domaine de validité : {zone.name})" if zone is not None else ""
        raise AnomalieCouche(
            f"coordonnées incohérentes avec le système déclaré {_nom_systeme(crs)}{domaine} : "
            f"emprise {minx:.2f}, {miny:.2f}, {maxx:.2f}, {maxy:.2f}. "
            "Déclarez le vrai système dans config/sig.yaml (clé « systeme »)."
        )
    return [round(v, 7) for v in w]


def _emprise_l93(crs: CRS, emprise: list[float]) -> list[float] | None:
    try:
        b = Transformer.from_crs(crs, 2154, always_xy=True).transform_bounds(*emprise)
    except Exception:
        return None
    return [round(v, 2) for v in b] if all(math.isfinite(v) for v in b) else None


# ---------------------------------------------------------------- configuration des couches

def _declarations(cfg: dict, dossier: Path, fichiers: list[Path]) -> dict[tuple[str, str | None], dict]:
    """Déclarations de config/sig.yaml indexées par (chemin relatif, couche). Nom seul accepté s'il est unique."""
    rel = {p.relative_to(dossier).as_posix(): p for p in fichiers}
    par_nom: dict[str, list[str]] = {}
    for r in rel:
        par_nom.setdefault(Path(r).name.lower(), []).append(r)
    resultat = {}
    for d in cfg.get("couches_fichiers") or []:
        cle = d["fichier"].replace("\\", "/").strip("/")
        if cle not in rel:
            candidats = par_nom.get(Path(cle).name.lower(), []) if "/" not in cle else []
            cle = candidats[0] if len(candidats) == 1 else cle
        resultat[(cle, d.get("couche"))] = d
    return resultat


def _declaration(decl: dict, fichier: str, couche: str | None) -> dict:
    return decl.get((fichier, couche)) or decl.get((fichier, None)) or {}


def _message(e: Exception, chemin: Path) -> str:
    """Message d'erreur GDAL raccourci : sans le chemin complet ni la suggestion de pilote."""
    texte = str(e).replace(f"'{chemin}'", chemin.name).replace(str(chemin), chemin.name)
    return texte.split("; It might help")[0].strip(" ;.")


def _export_tronque(chemin: Path) -> bool:
    """Vrai si un export Esri JSON déclare avoir atteint la limite de transfert du service."""
    with chemin.open("rb") as f:
        tete = f.read(OCTETS_LUS_LIMITE)
        taille = chemin.stat().st_size
        f.seek(max(0, taille - OCTETS_LUS_LIMITE))
        queue = f.read()
    return bool(LIMITE_TRANSFERT.search(tete.split(b'"features"')[0]) or LIMITE_TRANSFERT.search(queue))


def _message_lecture(e: Exception, chemin: Path) -> str:
    """Message d'un fichier illisible ; un JSON interrompu à la dernière position est un fichier tronqué."""
    m = re.search(r"parsing error.*\(at offset (\d+)\)", str(e))
    if m and int(m.group(1)) >= chemin.stat().st_size - 1:
        return ("fichier JSON tronqué : il s'arrête avant la fin (export ou copie incomplet). "
                "Refaites l'export ; le fichier n'est pas modifié")
    return f"fichier illisible ({_message(e, chemin)})"


def _numeros(positions) -> str:
    return ", ".join(str(int(i) + 1) for i in positions[:5]) + (" …" if len(positions) > 5 else "")


def lire_geo(chemin: Path, nom_couche: str | None = None, columns: list[str] | None = None) -> tuple[gpd.GeoDataFrame, list[str]]:
    """Lit une couche fichier. Une géométrie défectueuse (contour non fermé…) ne fait pas tomber la couche :
    elle est réparée à la lecture (shapely, on_invalid="fix"), ou écartée si c'est impossible, et signalée.
    Renvoie la couche et ses avertissements. Le fichier n'est jamais modifié."""
    try:
        return pyogrio.read_dataframe(chemin, layer=nom_couche, columns=columns), []
    except shapely.errors.GEOSException:
        pass
    meta, _, wkb, valeurs = pyogrio.raw.read(chemin, layer=nom_couche, columns=columns)
    geoms = shapely.from_wkb(wkb, on_invalid="ignore")
    fautives = np.flatnonzero(shapely.is_missing(geoms) & pd.notna(wkb))
    reparees = shapely.from_wkb(wkb[fautives], on_invalid="fix")
    # encore invalide après réparation (contour réduit à deux points…) : rien d'exploitable à afficher
    perdues = shapely.is_missing(reparees) | shapely.is_empty(reparees) | ~shapely.is_valid(reparees)
    geoms[fautives] = np.where(perdues, None, reparees)
    avert = []
    if (~perdues).any():
        ix = fautives[~perdues]
        avert.append(f"{len(ix)} géométrie(s) défectueuse(s) (contour non fermé…) réparée(s) à l'affichage : "
                     f"objet(s) n° {_numeros(ix)} du fichier, à corriger à la source ; le fichier n'est pas modifié")
    if perdues.any():
        ix = fautives[perdues]
        avert.append(f"{len(ix)} géométrie(s) irréparable(s) écartée(s) : objet(s) n° {_numeros(ix)} du fichier")
    attributs = pd.DataFrame(dict(zip(meta["fields"], valeurs)))
    return gpd.GeoDataFrame(attributs, geometry=geoms, crs=meta["crs"]), avert


def _types_reels(chemin: Path, nom_couche: str | None) -> list[str]:
    """Types des géométries présentes, lus objet par objet (fichier qui déclare un type « Unknown »)."""
    geoms = lire_geo(chemin, nom_couche, columns=[])[0].geometry
    return sorted(set(geoms[geoms.notna()].geom_type))


def _famille(type_geom: str | None) -> str:
    t = (type_geom or "").lower()
    if "point" in t:
        return "point"
    if "line" in t or "curve" in t:
        return "ligne"
    if "polygon" in t or "surface" in t:
        return "polygone"
    return "inconnue"


def _style(cfg: dict, famille: str, rang: int, surcharge: dict | None) -> dict:
    base = dict(cfg["styles_defaut"].get(famille if famille != "inconnue" else "point", {}))
    base.setdefault("couleur", cfg["palette"][rang % len(cfg["palette"])])
    base.update(surcharge or {})
    return base


def _champ_affichage_esri(chemin: Path) -> str | None:
    """Champ d'affichage choisi dans ArcGIS, déclaré en tête d'un export Esri JSON (« displayFieldName »)."""
    with chemin.open("rb") as f:
        tete = f.read(OCTETS_LUS_LIMITE).split(b'"features"')[0]
    m = CHAMP_AFFICHAGE.search(tete)
    return m.group(1).decode("utf-8", "replace") if m else None


def _champ_libelle(cfg: dict, champs: list[str], declare: str | None, export: str | None = None) -> str | None:
    """Champ affiché comme nom : déclaré dans la configuration, sinon celui de l'export ArcGIS, sinon le premier
    de « champs_libelle » présent."""
    for c in (declare, export):
        if c and c in champs:
            return c
    minuscules = {c.lower(): c for c in champs}
    return next((minuscules[n.lower()] for n in cfg["champs_libelle"] if n.lower() in minuscules), None)


def identifiant(fichier: str, couche: str | None) -> str:
    return hashlib.sha1(f"{fichier}::{couche or ''}".encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------- lecture des métadonnées

def lire_csv(chemin: Path, d: dict) -> tuple[pd.DataFrame, int]:
    """Lignes du CSV aux coordonnées valides (colonnes _x, _y ajoutées) et nombre de lignes écartées."""
    cx, cy = d.get("colonne_x"), d.get("colonne_y")
    if not cx or not cy:
        raise AnomalieCouche("CSV déclaré sans « colonne_x » / « colonne_y » dans config/sig.yaml")
    try:
        df = pd.read_csv(chemin, sep=d.get("separateur", ";"), encoding=d.get("encodage", "utf-8"), dtype=str)
    except UnicodeDecodeError as e:
        raise AnomalieCouche(f"encodage incorrect (déclaré : {d.get('encodage', 'utf-8')}) — précisez « encodage »") from e
    except Exception as e:
        raise AnomalieCouche(f"CSV illisible ({e})") from e
    absentes = [c for c in (cx, cy) if c not in df.columns]
    if absentes:
        raise AnomalieCouche(f"colonnes absentes : {', '.join(absentes)} (colonnes trouvées : {', '.join(df.columns[:12])})")
    x = pd.to_numeric(df[cx].str.replace(",", ".", regex=False), errors="coerce")
    y = pd.to_numeric(df[cy].str.replace(",", ".", regex=False), errors="coerce")
    valides = x.notna() & y.notna()
    df = df[valides].copy()
    df["_x"], df["_y"] = x[valides], y[valides]
    if df.empty:
        raise AnomalieCouche(f"aucune ligne avec des coordonnées numériques dans {cx} / {cy}")
    return df.reset_index(drop=True), int((~valides).sum())


def _couche(cfg, dossier, chemin, nom_couche, d, rang) -> Couche:
    rel = chemin.relative_to(dossier).as_posix()
    fmt = FORMATS[chemin.suffix.lower()]
    avert: list[str] = []
    csv_params = None
    champ_export = None   # champ d'affichage d'un export ArcGIS

    if fmt == "CSV":
        if "systeme" not in d:
            raise AnomalieCouche("CSV déclaré sans « systeme » dans config/sig.yaml : il n'est jamais supposé")
        df, ecartees = lire_csv(chemin, d)
        if ecartees:
            avert.append(f"{ecartees} ligne(s) écartée(s) : coordonnées non numériques ou vides")
        type_geom, nb, champs = "Point", len(df), [c for c in df.columns if not c.startswith("_")]
        emprise = [float(df["_x"].min()), float(df["_y"].min()), float(df["_x"].max()), float(df["_y"].max())]
        crs_fichier = None
        csv_params = {k: d[k] for k in ("colonne_x", "colonne_y", "separateur", "encodage") if k in d}
    else:
        if fmt == "Shapefile":
            manquants = [e for e in (".shx", ".dbf") if not chemin.with_suffix(e).exists()
                         and not chemin.with_suffix(e.upper()).exists()]
            if manquants:
                raise AnomalieCouche(f"Shapefile incomplet : fichier(s) {', '.join(manquants)} absent(s)")
        if chemin.stat().st_size == 0:
            raise AnomalieCouche("fichier vide")
        try:
            info = pyogrio.read_info(chemin, layer=nom_couche, force_feature_count=True, force_total_bounds=True)
        except Exception as e:
            raise AnomalieCouche(_message_lecture(e, chemin)) from e
        type_geom, nb, champs = info.get("geometry_type"), int(info.get("features") or 0), list(info["fields"])
        if not type_geom:
            raise AnomalieCouche("table sans géométrie")
        fmt = NOMS_PILOTES.get(info.get("driver"), fmt)
        if fmt == "Esri JSON":
            champ_export = _champ_affichage_esri(chemin)
        if fmt == "Esri JSON" and _export_tronque(chemin):
            avert.append(f"export ArcGIS tronqué : le service a atteint sa limite de transfert, seuls {nb} objets "
                         "ont été exportés (« exceededTransferLimit »). Refaites l'export par pages ou déclarez le service")
        if _famille(type_geom) == "inconnue" and nb:
            # Ex. Polygon et MultiPolygon mêlés : GDAL déclare « Unknown », la famille se lit sur les objets.
            reels = _types_reels(chemin, nom_couche)
            familles = {_famille(t) for t in reels}
            if len(familles) == 1:
                type_geom = " / ".join(reels)
            elif reels:
                avert.append(f"géométries de natures différentes ({', '.join(reels)}) : style par défaut des points")
        emprise = [float(v) for v in info["total_bounds"]] if info.get("total_bounds") is not None else None
        crs_fichier = info.get("crs")

    # Système : déclaré dans la configuration, sinon celui du fichier ; jamais supposé.
    if "systeme" in d:
        crs, source = _lire_systeme(str(d["systeme"])), "configuration"
        if crs_fichier and fmt != "CSV" and _lire_systeme(crs_fichier) != crs:
            avert.append(f"système du fichier ({crs_fichier}) remplacé par celui de la configuration ({_nom_systeme(crs)})")
    elif crs_fichier:
        crs, source = _lire_systeme(crs_fichier), "fichier"
    else:
        manque = " (fichier .prj absent)" if fmt == "Shapefile" else ""
        raise AnomalieCouche(f"système de coordonnées inconnu{manque} : déclarez-le dans config/sig.yaml (clé « systeme »)")

    attendu = cfg["carte"].get("systeme_donnees_attendu", "EPSG:2154")
    if crs != _lire_systeme(attendu):
        avert.append(f"système {_nom_systeme(crs)} ({crs.name}) différent de {attendu} attendu : converti pour l'affichage")

    if nb == 0 or emprise is None or not all(math.isfinite(v) for v in emprise):
        avert.append("couche vide")
        emprise_wgs84 = emprise_l93 = None
    else:
        emprise_wgs84 = _controler_emprise(crs, emprise, cfg["chargement"]["marge_controle_systeme_deg"])
        emprise_l93 = _emprise_l93(crs, emprise)

    seuil = cfg["chargement"]["seuil_objets_filtre_emprise"]
    famille = _famille(type_geom)
    libelle = d.get("libelle") or (f"{chemin.stem} › {nom_couche}" if fmt == "GeoPackage" else chemin.stem)
    return Couche(
        id=identifiant(rel, nom_couche),
        libelle=libelle,
        fichier=rel,
        couche=nom_couche,
        format=fmt,
        geometrie=famille,
        type_geometrie=type_geom,
        systeme=_nom_systeme(crs),
        systeme_nom=crs.name,
        systeme_source=source,
        nb_objets=nb,
        emprise_wgs84=emprise_wgs84,
        emprise_l93=emprise_l93,
        filtre_emprise=nb > seuil,
        visible=bool(d.get("visible", True)),
        style=_style(cfg, famille, rang, d.get("style")),
        champs=champs,
        champ_libelle=_champ_libelle(cfg, champs, d.get("champ_libelle"), champ_export),
        avertissements=avert,
        _csv=csv_params,
        _chemin=chemin,
    )


def _empreinte(chemin: Path) -> str:
    h = hashlib.sha1()
    with chemin.open("rb") as f:
        for bloc in iter(lambda: f.read(1 << 20), b""):
            h.update(bloc)
    return h.hexdigest()


def _signaler_doublons(couches: list[Couche]) -> None:
    """Fichiers au contenu identique (copie sous un autre nom) : chaque couche signale les autres.

    Seuls les fichiers de même taille sont comparés ; les couches d'un même GeoPackage ne sont pas concernées."""
    par_fichier = {c._chemin: c for c in couches if c._chemin is not None and c.couche is None}
    par_taille: dict[int, list[Path]] = {}
    for chemin in par_fichier:
        par_taille.setdefault(chemin.stat().st_size, []).append(chemin)
    for chemins in par_taille.values():
        if len(chemins) < 2:
            continue
        par_contenu: dict[str, list[Path]] = {}
        for chemin in chemins:
            par_contenu.setdefault(_empreinte(chemin), []).append(chemin)
        for groupe in par_contenu.values():
            for chemin in groupe:
                autres = ", ".join(par_fichier[a].fichier for a in groupe if a != chemin)
                if autres:
                    par_fichier[chemin].avertissements.append(
                        f"contenu identique à {autres} : mêmes objets affichés deux fois (copie sous un autre nom ?)")


def _distinguer_libelles(couches: list[Couche], decl: dict) -> None:
    """Libellés déduits du nom de fichier en double (même nom, autre format ou autre dossier) : extension ajoutée,
    puis chemin relatif s'il le faut. Un libellé déclaré dans config/sig.yaml n'est jamais modifié."""
    def declare(c: Couche) -> bool:
        return bool(_declaration(decl, c.fichier, c.couche).get("libelle"))

    for suffixe in (lambda c: f"{c.libelle} ({Path(c.fichier).suffix.lower()})", lambda c: f"{c.libelle} ({c.fichier})"):
        groupes: dict[str, list[Couche]] = {}
        for c in couches:
            groupes.setdefault(c.libelle.lower(), []).append(c)
        for groupe in groupes.values():
            if len(groupe) > 1:
                for c in groupe:
                    if not declare(c):
                        c.libelle = suffixe(c)


def inventorier(cfg: dict) -> Inventaire:
    dossier = dossier_sig(cfg)
    couches, anomalies, ignores = [], [], []
    if not dossier.is_dir():
        anomalies.append({"fichier": str(dossier), "couche": None, "message": "dossier des couches SIG introuvable"})
        return Inventaire(str(dossier), datetime.now().isoformat(timespec="seconds"), [], anomalies, [])

    fichiers = sorted((p for p in dossier.rglob("*") if p.is_file()), key=lambda p: p.as_posix().lower())
    decl = _declarations(cfg, dossier, fichiers)
    declares_vus = set()

    for chemin in fichiers:
        rel = chemin.relative_to(dossier).as_posix()
        ext = chemin.suffix.lower()
        if ext in COMPAGNONS:
            continue
        if ext in PROJETS_QGIS:
            ignores.append({"fichier": rel, "message": "projet QGIS : non lu (ses couches sont inventoriées directement)"})
            continue
        if ext not in FORMATS:
            ignores.append({"fichier": rel, "message": "format non géré"})
            continue
        if ext == ".csv" and (rel, None) not in decl:
            anomalies.append({"fichier": rel, "couche": None,
                              "message": "CSV non déclaré dans config/sig.yaml (colonnes X/Y et système requis) : non chargé"})
            continue

        noms: list[str | None] = [None]
        if ext == ".gpkg":
            try:
                noms = [n for n, _ in pyogrio.list_layers(chemin)]
            except Exception as e:
                anomalies.append({"fichier": rel, "couche": None, "message": f"fichier illisible ({_message(e, chemin)})"})
                continue
            if not noms:
                anomalies.append({"fichier": rel, "couche": None, "message": "GeoPackage sans couche"})
                continue

        for nom in noms:
            d = _declaration(decl, rel, nom)
            declares_vus.update(k for k in decl if k[0] == rel)
            try:
                couches.append(_couche(cfg, dossier, chemin, nom, d, len(couches)))
            except AnomalieCouche as e:
                anomalies.append({"fichier": rel, "couche": nom, "message": str(e)})
            except Exception as e:  # garde-fou : un fichier ne doit jamais interrompre l'inventaire
                anomalies.append({"fichier": rel, "couche": nom, "message": f"erreur inattendue ({type(e).__name__} : {e})"})

    _signaler_doublons(couches)
    _distinguer_libelles(couches, decl)

    for (fichier, nom) in decl:
        if (fichier, nom) not in declares_vus:
            anomalies.append({"fichier": fichier, "couche": nom,
                              "message": "déclaré dans config/sig.yaml mais introuvable dans le dossier"})

    return Inventaire(str(dossier), datetime.now().isoformat(timespec="seconds"), couches, anomalies, ignores)


# ---------------------------------------------------------------- cache

_verrou = threading.Lock()
_cache: dict = {"cle": None, "inventaire": None}


def _signature(cfg: dict) -> tuple:
    dossier = dossier_sig(cfg)
    fichiers = ()
    if dossier.is_dir():
        fichiers = tuple(sorted((p.as_posix(), p.stat().st_mtime_ns, p.stat().st_size)
                                for p in dossier.rglob("*") if p.is_file()))
    return (str(dossier), repr(cfg), fichiers)


def inventaire(cfg: dict) -> Inventaire:
    """Inventaire mis en cache, recalculé dès qu'un fichier du dossier ou la configuration change."""
    cle = _signature(cfg)
    with _verrou:
        if _cache["cle"] != cle:
            _cache["inventaire"] = inventorier(cfg)
            _cache["cle"] = cle
        return _cache["inventaire"]

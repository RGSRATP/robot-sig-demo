"""Interrogation croisée SIG ↔ maquettes (lot 4), en Lambert 93, et exports CSV.

- maquette → objets SIG des couches demandées qui touchent son emprise (longueur ou surface dans l'emprise) ;
- objet SIG → maquettes positionnées qui le recouvrent, et leurs locaux au droit de l'objet.
Une couche illisible devient une anomalie dans le résultat ; les autres couches sont traitées.
"""
from __future__ import annotations

import csv
import io
import math

import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry import shape

from . import inventaire as inv_sig
from . import objets as obj_sig

_VERS_L93 = Transformer.from_crs(4326, 2154, always_xy=True)


def geometrie_l93(geojson_wgs84: dict):
    """Géométrie shapely en Lambert 93 à partir d'une géométrie GeoJSON WGS84."""
    g = shape(geojson_wgs84)
    return shapely.transform(g, lambda xy: np.column_stack(_VERS_L93.transform(xy[:, 0], xy[:, 1])))


def _mesure(geom, zone):
    """Longueur (m) ou surface (m²) de la partie de l'objet dans la zone ; None pour un point."""
    t = geom.geom_type
    if "Point" in t:
        return None, None
    inter = geom.intersection(zone)
    if "Line" in t:
        return round(inter.length, 2), "m"
    if "Polygon" in t:
        return round(inter.area, 2), "m²"
    return None, None


def _relation(geom, zone) -> str:
    return "contenu" if zone.contains(geom) else "à cheval"


def maquette_vers_sig(couches: list[inv_sig.Couche], zone, max_par_couche: int) -> dict:
    """Objets SIG des couches qui intersectent la zone (Lambert 93), groupés par couche."""
    groupes, anomalies = [], []
    for c in couches:
        try:
            charge = obj_sig.charger(c)
        except inv_sig.AnomalieCouche as e:
            anomalies.append({"couche": c.libelle, "message": str(e)})
            continue
        gdf = charge.l93
        indices = gdf.sindex.query(zone, predicate="intersects")
        indices = sorted(int(gdf.index[i]) for i in indices)
        lib = c.champ_libelle
        objets = []
        for n in indices[:max_par_couche]:
            geom = gdf.geometry.loc[n]
            mesure, unite = _mesure(geom, zone)
            objets.append({
                "n": n,
                "libelle": obj_sig.valeur_json(charge.origine.loc[n, lib]) if lib else None,
                "type_geometrie": geom.geom_type,
                "relation": _relation(geom, zone),
                "mesure": mesure,
                "unite": unite,
                "emprise_wgs84": [round(v, 7) for v in charge.wgs84.geometry.loc[n].bounds],
            })
        if indices:
            groupes.append({"couche": c.id, "libelle": c.libelle, "format": c.format, "total": len(indices),
                            "tronque": len(indices) > max_par_couche, "objets": objets})
    return {"groupes": groupes, "anomalies": anomalies, "total": sum(g["total"] for g in groupes)}


def sig_vers_maquettes(geom, maquettes: list[dict], locaux_par_maquette: dict[str, list[dict]], tampon_m: float) -> dict:
    """Maquettes (et locaux) dont l'emprise touche l'objet (Lambert 93, avec tampon éventuel)."""
    zone = geom.buffer(tampon_m) if tampon_m else geom
    resultats = []
    for m in maquettes:
        pos = m["positionnement"]
        emprise = geometrie_l93(pos["emprise_geojson"])
        if not emprise.intersects(zone):
            continue
        locaux = []
        for l in locaux_par_maquette.get(m["id"], []):
            if l.get("emprise_wgs84_geojson") and geometrie_l93(l["emprise_wgs84_geojson"]).intersects(zone):
                locaux.append({k: l.get(k) for k in ("guid", "code", "nom", "nom_long", "niveau", "intitule_niveau",
                                                     "typologie", "surface")})
        resultats.append({"id": m["id"], "nom": m["nom"], "fichier": m["fichier"], "methode": pos["libelle"],
                          "surface_commune": round(emprise.intersection(zone).area, 2) if "Polygon" in zone.geom_type else None,
                          "locaux": locaux})
    return {"maquettes": resultats, "total": len(resultats), "nb_locaux": sum(len(r["locaux"]) for r in resultats)}


# ---------------------------------------------------------------- CSV

def csv_texte(colonnes: list[tuple[str, str]], lignes: list[dict], separateur: str) -> str:
    """CSV (en-têtes lisibles) ; les nombres utilisent la virgule décimale si le séparateur est « ; »."""
    sortie = io.StringIO()
    w = csv.writer(sortie, delimiter=separateur, lineterminator="\r\n")
    w.writerow([titre for _, titre in colonnes])
    for l in lignes:
        valeurs = []
        for cle, _ in colonnes:
            v = l.get(cle)
            if isinstance(v, float) and math.isfinite(v) and separateur == ";":
                v = f"{v:.2f}".replace(".", ",")
            valeurs.append("" if v is None else v)
        w.writerow(valeurs)
    return sortie.getvalue()


COLONNES_SIG = [("couche", "Couche"), ("n", "N° objet"), ("libelle", "Libellé"), ("type_geometrie", "Géométrie"),
                ("relation", "Relation à l'emprise"), ("mesure", "Longueur ou surface dans l'emprise"), ("unite", "Unité")]
COLONNES_MAQUETTES = [("maquette", "Maquette"), ("fichier", "Fichier"), ("methode", "Positionnement"),
                      ("code", "Code local (SGP_DI_Sous_Ouvrage)"), ("nom_local", "Local"), ("niveau", "Niveau"),
                      ("typologie", "Typologie"), ("surface", "Surface (m²)")]
COLONNES_LOCAUX = [("code", "Code (SGP_DI_Sous_Ouvrage)"), ("nom", "Nom"), ("nom_long", "Nom long"), ("niveau", "Niveau"),
                   ("intitule_niveau", "Intitulé niveau"), ("typologie", "Typologie"), ("code_typologie", "Code typologie"),
                   ("surface", "Surface (m²)"), ("ouvrage", "Ouvrage"), ("batiment", "Bâtiment"),
                   ("x_l93", "X Lambert 93"), ("y_l93", "Y Lambert 93"), ("guid", "GlobalId")]


def lignes_sig(resultat: dict) -> list[dict]:
    return [{**o, "couche": g["libelle"]} for g in resultat["groupes"] for o in g["objets"]]


def lignes_maquettes(resultat: dict) -> list[dict]:
    lignes = []
    for m in resultat["maquettes"]:
        base = {"maquette": m["nom"], "fichier": m["fichier"], "methode": m["methode"]}
        if not m["locaux"]:
            lignes.append(base)
        for l in m["locaux"]:
            lignes.append({**base, "code": l["code"], "nom_local": l["nom_long"] or l["nom"], "niveau": l["niveau"],
                           "typologie": l["typologie"], "surface": l["surface"]})
    return lignes

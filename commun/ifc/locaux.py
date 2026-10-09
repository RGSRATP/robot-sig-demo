"""Locaux (IfcSpace) : identification, niveau, codes « Données d'identification » et emprise au sol.

L'emprise d'un local est l'union des projections au sol de ses triangles (forme exacte, y compris en L),
en coordonnées locales (monde − décalage, comme le modèle 3D), avec les altitudes basse et haute.
Les clés SGP_DI_* sont normalisées (sans accents) : « SGP_DI_Intitulé_Typologie_Espace » et
« SGP_DI_Intitule_Typologie_Espace » donnent la même clé.
"""
from __future__ import annotations

import multiprocessing
import unicodedata
from typing import Callable

import ifcopenshell
import ifcopenshell.geom as geom
import ifcopenshell.util.element as util_element
import numpy as np
import shapely
from shapely.geometry import Polygon, mapping

import ifcopenshell.util.unit as util_unit

from .proprietes import _json, niveau

PREFIXE_CODES = "SGP_DI_"
# Champs usuels extraits des codes (clé normalisée → nom du champ en sortie)
CHAMPS = {
    "SGP_DI_Sous_Ouvrage": "code",
    "SGP_DI_Intitule_Typologie_Espace": "typologie",
    "SGP_DI_Typologie_Espace": "code_typologie",
    "SGP_DI_Niveau": "code_niveau",
    "SGP_DI_Intitule_Niveau": "intitule_niveau",
    "SGP_DI_Ouvrage": "ouvrage",
    "SGP_DI_Intitule_Ouvrage": "intitule_ouvrage",
    "SGP_DI_Batiment": "batiment",
}
NOMS_SURFACE = ("NetFloorArea", "GrossFloorArea", "SGP-P-Surface", "SGP_P_Surface", "Surface")


def normaliser_cle(cle: str) -> str:
    sans_accents = "".join(c for c in unicodedata.normalize("NFD", cle) if unicodedata.category(c) != "Mn")
    return sans_accents.replace("-", "_").replace(" ", "_")


def _codes(element) -> tuple[dict, float | None]:
    codes, surface = {}, None
    for _nom, valeurs in util_element.get_psets(element).items():
        for k, v in valeurs.items():
            if k == "id":
                continue
            n = normaliser_cle(k)
            if n.upper().startswith(PREFIXE_CODES.upper()) and v not in (None, ""):
                codes.setdefault(n, _json(v))
            if surface is None and k in NOMS_SURFACE and isinstance(v, (int, float)) and v > 0:
                surface = float(v)
    return codes, surface


def _empreinte(verts: np.ndarray, faces: np.ndarray):
    tris = [Polygon(verts[t, :2]) for t in faces.reshape(-1, 3)]
    tris = [t for t in tris if t.is_valid and t.area > 1e-6]
    if not tris:
        return None
    e = shapely.union_all(tris).simplify(0.01)
    return e if not e.is_empty else None


def locaux(f: ifcopenshell.file, decalage=(0.0, 0.0, 0.0), processus: int | None = None,
           progression: Callable[[int], None] | None = None) -> list[dict]:
    """Locaux de la maquette, triés par niveau puis par code. Un local sans géométrie est gardé (emprise None)."""
    espaces = f.by_type("IfcSpace")
    if not espaces:
        return []
    formes = {}
    reglages = geom.settings()
    reglages.set("use-world-coords", True)
    reglages.set("model-offset", tuple(-float(v) for v in decalage))
    iterateur = geom.iterator(reglages, f, processus or max(1, multiprocessing.cpu_count() - 1), include=espaces)
    derniere = -1
    if iterateur.initialize():
        while True:
            forme = iterateur.get()
            verts = np.asarray(forme.geometry.verts, dtype=float).reshape(-1, 3)
            faces = np.asarray(forme.geometry.faces, dtype=int)
            if len(verts) and len(faces):
                formes[forme.guid] = (_empreinte(verts, faces), float(verts[:, 2].min()), float(verts[:, 2].max()))
            if progression:
                p = int(iterateur.progress())
                if p != derniere:
                    progression(p)
                    derniere = p
            if not iterateur.next():
                break

    echelle = util_unit.calculate_unit_scale(f)
    resultat = []
    for e in espaces:
        codes, surface = _codes(e)
        etage = niveau(e)
        emprise, zmin, zmax = formes.get(e.GlobalId, (None, None, None))
        local = {
            "guid": e.GlobalId,
            "nom": e.Name,
            "nom_long": getattr(e, "LongName", None),
            "niveau": getattr(etage, "Name", None) if etage else None,
            "altitude_niveau": float(etage.Elevation) * echelle if etage is not None and getattr(etage, "Elevation", None) is not None else None,
            "surface": surface if surface is not None else (round(emprise.area, 2) if emprise is not None else None),
            "surface_source": "propriété" if surface is not None else ("emprise" if emprise is not None else None),
            "emprise_locale": mapping(emprise) if emprise is not None else None,
            "z_min": zmin,
            "z_max": zmax,
            "codes": codes,
        }
        for cle, champ in CHAMPS.items():
            local[champ] = codes.get(cle)
        resultat.append(local)
    resultat.sort(key=lambda l: (l["z_min"] if l["z_min"] is not None else 1e9, l["niveau"] or "", l["code"] or l["nom"] or ""))
    return resultat


def etages(f: ifcopenshell.file, decalage=(0.0, 0.0, 0.0)) -> list[dict]:
    """Étages (IfcBuildingStorey), du plus bas au plus haut, avec leur altitude « z » en coordonnées locales
    (monde − décalage, comme le modèle 3D et les locaux), tirée du placement de l'étage ; à défaut de placement,
    de l'attribut Elevation (z_source le dit). L'attribut Elevation est aussi rendu tel quel : il est relatif au
    bâtiment ou au projet selon les logiciels, il ne sert donc pas de référence."""
    import ifcopenshell.util.placement as util_placement
    echelle = util_unit.calculate_unit_scale(f)
    resultat = []
    for e in f.by_type("IfcBuildingStorey"):
        elevation = float(e.Elevation) * echelle if getattr(e, "Elevation", None) is not None else None
        if e.ObjectPlacement is not None:
            z, source = float(util_placement.get_local_placement(e.ObjectPlacement)[2, 3]) * echelle - float(decalage[2]), "placement"
        elif elevation is not None:
            z, source = elevation, "elevation"
        else:
            z, source = None, None
        resultat.append({"guid": e.GlobalId, "nom": e.Name, "nom_long": getattr(e, "LongName", None),
                         "z": round(z, 3) if z is not None else None, "z_source": source, "elevation": elevation})
    return sorted(resultat, key=lambda d: (d["z"] is None, d["z"] if d["z"] is not None else 0.0, d["nom"] or ""))


def niveaux(liste: list[dict]) -> list[dict]:
    """Niveaux présents (du plus bas au plus haut) avec leur nombre de locaux et leurs altitudes."""
    par_niveau: dict[str, dict] = {}
    for l in liste:
        n = l["niveau"] or l["intitule_niveau"] or "Sans niveau"
        d = par_niveau.setdefault(n, {"niveau": n, "nb_locaux": 0, "z_min": None, "z_max": None,
                                      "intitule": l.get("intitule_niveau")})
        d["nb_locaux"] += 1
        for cle, fn in (("z_min", min), ("z_max", max)):
            if l[cle] is not None:
                d[cle] = l[cle] if d[cle] is None else fn(d[cle], l[cle])
    return sorted(par_niveau.values(), key=lambda d: d["z_min"] if d["z_min"] is not None else 1e9)

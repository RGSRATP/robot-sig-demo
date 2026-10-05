"""Ouverture d'une maquette, métadonnées et données brutes de géoréférencement.

Les données brutes (placement du site, RefLatitude / RefLongitude, nord géographique, IfcMapConversion)
sont lues telles quelles, en mètres ; leur interprétation est faite par georef.resoudre(), sans fichier.
"""
from __future__ import annotations

from pathlib import Path

import ifcopenshell
import ifcopenshell.util.placement as util_placement
import ifcopenshell.util.unit as util_unit


class MaquetteIllisible(Exception):
    """Fichier absent, vide ou non IFC ; le message est destiné à l'utilisateur."""


def ouvrir(chemin: str | Path) -> ifcopenshell.file:
    chemin = Path(chemin)
    if not chemin.is_file():
        raise MaquetteIllisible(f"fichier introuvable : {chemin.name}")
    if chemin.stat().st_size == 0:
        raise MaquetteIllisible("fichier vide")
    with chemin.open("rb") as f:
        if not f.read(64).lstrip().startswith(b"ISO-10303-21"):
            raise MaquetteIllisible("ce n'est pas un fichier IFC (en-tête ISO-10303-21 absent)")
    try:
        return ifcopenshell.open(str(chemin))
    except Exception as e:  # IfcOpenShell lève des types variés selon l'erreur de syntaxe
        raise MaquetteIllisible(f"fichier IFC illisible ({type(e).__name__} : {e})") from e


def _texte(v) -> str | None:
    return v if isinstance(v, str) and v.strip() else None


def metadonnees(f: ifcopenshell.file) -> dict:
    """Nom du projet, schéma, application auteur, date, unité de longueur."""
    projet = (f.by_type("IfcProject") or [None])[0]
    entete = f.header.file_name
    unite = util_unit.get_project_unit(f, "LENGTHUNIT")
    nom_unite = None
    if unite is not None:
        nom_unite = ((getattr(unite, "Prefix", None) or "") + (getattr(unite, "Name", None) or "")).lower() or None
    application = None
    for h in f.by_type("IfcApplication")[:1]:
        application = " ".join(x for x in (_texte(h.ApplicationFullName), _texte(h.Version)) if x)
    return {
        "projet": _texte(projet.Name) if projet else None,
        "description": _texte(projet.Description) if projet else None,
        "schema": f.schema,
        "application": application or _texte(getattr(entete, "originating_system", None)),
        "date": _texte(getattr(entete, "time_stamp", None)),
        "unite_longueur": nom_unite,
        "facteur_metre": util_unit.calculate_unit_scale(f),
        "nb_locaux": len(f.by_type("IfcSpace")),
    }


def _angle(dms) -> float | None:
    """IfcCompoundPlaneAngleMeasure (degrés, minutes, secondes, millionièmes) → degrés décimaux."""
    if not dms:
        return None
    v = list(dms) + [0] * (4 - len(dms))
    return v[0] + v[1] / 60 + (v[2] + v[3] / 1e6) / 3600


def site_principal(f: ifcopenshell.file):
    """Site rattaché au projet (les maquettes liées peuvent ajouter d'autres sites)."""
    for projet in f.by_type("IfcProject"):
        for rel in getattr(projet, "IsDecomposedBy", None) or []:
            for o in rel.RelatedObjects:
                if o.is_a("IfcSite"):
                    return o
    sites = f.by_type("IfcSite")
    return sites[0] if sites else None


def georef_brut(f: ifcopenshell.file) -> dict:
    """Données de géoréférencement présentes dans le fichier, en mètres, sans interprétation."""
    echelle = util_unit.calculate_unit_scale(f)
    brut: dict = {"schema": f.schema, "nb_sites": len(f.by_type("IfcSite")), "site": None,
                  "nord_geographique": None, "map_conversion": None}

    for ctx in f.by_type("IfcGeometricRepresentationContext"):
        if ctx.is_a() == "IfcGeometricRepresentationContext" and ctx.ContextType == "Model" and ctx.TrueNorth:
            brut["nord_geographique"] = [float(v) for v in ctx.TrueNorth.DirectionRatios[:2]]
            break

    site = site_principal(f)
    if site is not None:
        m = util_placement.get_local_placement(site.ObjectPlacement) if site.ObjectPlacement else None
        brut["site"] = {
            "guid": site.GlobalId,
            "nom": _texte(site.Name),
            "origine": [float(v) * echelle for v in m[:3, 3]] if m is not None else [0.0, 0.0, 0.0],
            "axe_x": [float(v) for v in m[:2, 0]] if m is not None else [1.0, 0.0],
            "latitude": _angle(site.RefLatitude),
            "longitude": _angle(site.RefLongitude),
            "altitude": float(site.RefElevation) * echelle if site.RefElevation is not None else None,
        }

    if f.schema not in ("IFC2X3",):
        for mc in f.by_type("IfcMapConversion"):
            crs = mc.TargetCRS
            # Eastings / Northings / OrthogonalHeight sont exprimés dans l'unité du système cible (MapUnit)
            unite_carte = getattr(crs, "MapUnit", None)
            if unite_carte is not None and unite_carte.is_a("IfcSIUnit"):
                echelle = util_unit.get_prefix_multiplier(unite_carte.Prefix) if unite_carte.Prefix else 1.0
            brut["map_conversion"] = {
                "est": float(mc.Eastings) * echelle,
                "nord": float(mc.Northings) * echelle,
                "hauteur": float(mc.OrthogonalHeight) * echelle,
                "axe_x_abscisse": float(mc.XAxisAbscissa) if mc.XAxisAbscissa is not None else 1.0,
                "axe_x_ordonnee": float(mc.XAxisOrdinate) if mc.XAxisOrdinate is not None else 0.0,
                "echelle": float(mc.Scale) if mc.Scale is not None else 1.0,
                "systeme": _texte(getattr(crs, "Name", None)),
                "systeme_description": _texte(getattr(crs, "Description", None)),
            }
            break
    return brut

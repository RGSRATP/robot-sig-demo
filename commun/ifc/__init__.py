"""Lecture IFC autonome (IfcOpenShell), partagée avec le demonstrateur-patrimoine (SPEC section 7).

Ne dépend ni de serveur/, ni d'interface/, ni de config/ : entrées explicites, sorties sérialisables en JSON.

Contenu :
- lecture : ouverture contrôlée, métadonnées, données brutes de géoréférencement ;
- georef : interprétation du géoréférencement (IfcMapConversion, IfcSite projeté ou latitude/longitude, manuel) ;
- geometrie : modèle 3D GLB et emprise au sol en une lecture ;
- proprietes : Psets génériques et index SQLite des objets (consultation sans rouvrir la maquette).
Prévu au lot 4 : locaux (IfcSpace) et « Données d'identification ».
"""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

VERSION_MODULE = "0.2.0"


def version_ifcopenshell() -> str | None:
    """Version d'IfcOpenShell installée, ou None si absent (sans l'importer)."""
    try:
        return version("ifcopenshell")
    except PackageNotFoundError:
        return None

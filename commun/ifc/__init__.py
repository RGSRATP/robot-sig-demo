"""Lecture IFC autonome (IfcOpenShell), partagée avec le demonstrateur-patrimoine (SPEC section 7).

Ne dépend ni de serveur/, ni d'interface/, ni de config/ : entrées explicites, sorties sérialisables en JSON.

Contenu :
- lecture : ouverture contrôlée, métadonnées, données brutes de géoréférencement ;
- georef : interprétation du géoréférencement (IfcMapConversion, IfcSite projeté ou latitude/longitude, manuel) ;
- geometrie : modèles 3D allégés (GLB) et emprise au sol, convertis par web-ifc (convertisseur/, Node) ;
- proprietes : Psets génériques et index SQLite des objets (consultation sans rouvrir la maquette) ;
- locaux : IfcSpace (codes « Données d'identification » SGP_DI_*, niveau, emprise au sol, altitudes).
  Étages (IfcBuildingStorey) et leur altitude en coordonnées locales.
"""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

VERSION_MODULE = "0.3.2"
VERSION_LOCAUX = "1"   # version de l'extraction des locaux (cache séparé de la 3D)


def version_ifcopenshell() -> str | None:
    """Version d'IfcOpenShell installée, ou None si absent (sans l'importer)."""
    try:
        return version("ifcopenshell")
    except PackageNotFoundError:
        return None

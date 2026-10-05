"""Lecture IFC autonome (IfcOpenShell), partagée avec le demonstrateur-patrimoine (SPEC section 7).

Ne dépend ni de serveur/, ni d'interface/, ni de config/ : entrées explicites, sorties sérialisables en JSON.

Contenu prévu :
- lot 3 : métadonnées, géoréférencement (IfcMapConversion, IfcSite, manuel transmis par l'appelant), emprise au sol ;
- lot 4 : locaux (IfcSpace) et Psets (dont « Données d'identification »).
"""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

VERSION_MODULE = "0.1.0"


def version_ifcopenshell() -> str | None:
    """Version d'IfcOpenShell installée, ou None si absent (sans l'importer)."""
    try:
        return version("ifcopenshell")
    except PackageNotFoundError:
        return None

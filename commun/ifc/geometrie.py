"""Géométrie d'une maquette : modèle 3D glTF binaire (GLB) et emprise au sol, en une seule lecture.

Les coordonnées sont « locales » : coordonnées monde (mètres) moins `decalage` (l'origine du site),
pour garder la précision des nombres flottants. Le GLB suit la convention glTF (axe Y vertical) ;
chaque élément est un nœud nommé par son GlobalId.
"""
from __future__ import annotations

import multiprocessing
from pathlib import Path
from typing import Callable

import ifcopenshell
import ifcopenshell.geom as geom
import numpy as np
import shapely
from shapely.geometry import MultiPoint, mapping

EXCLUS_DEFAUT = ("IfcSpace", "IfcOpeningElement", "IfcAnnotation", "IfcGrid", "IfcVirtualElement")
TAILLE_LOT_UNION = 2000   # technique : union des empreintes par lots


def _empreinte(verts: np.ndarray):
    """Enveloppe convexe 2D des sommets d'un élément (None si dégénérée)."""
    if len(verts) < 3:
        return None
    h = MultiPoint(verts[:, :2]).convex_hull
    return h if h.geom_type == "Polygon" and h.area > 1e-6 else None


def convertir(f: ifcopenshell.file, sortie_glb: str | Path, decalage=(0.0, 0.0, 0.0),
              exclure=EXCLUS_DEFAUT, deflexion_m: float | None = None, simplification_m: float = 0.05,
              processus: int | None = None, progression: Callable[[int], None] | None = None) -> dict:
    """Écrit le GLB et renvoie l'emprise au sol (GeoJSON en coordonnées locales) et les altitudes min/max.

    Ne lève pas d'exception pour un élément défectueux : IfcOpenShell l'ignore et il est compté à part.
    """
    reglages = geom.settings()
    reglages.set("use-world-coords", True)
    reglages.set("model-offset", tuple(-float(v) for v in decalage))
    reglages.set("use-element-guids", True)
    reglages.set("apply-default-materials", True)
    if deflexion_m:
        reglages.set("mesher-linear-deflection", float(deflexion_m))

    sortie_glb = Path(sortie_glb)
    sortie_glb.parent.mkdir(parents=True, exist_ok=True)
    serialiseur = geom.serializers.glb(str(sortie_glb), reglages)
    serialiseur.setFile(f)
    serialiseur.writeHeader()

    attendus = sum(1 for p in f.by_type("IfcProduct") if p.Representation is not None and not any(p.is_a(t) for t in exclure))
    iterateur = geom.iterator(reglages, f, processus or max(1, multiprocessing.cpu_count() - 1), exclude=tuple(exclure))
    empreintes, lot = [], []
    zmin, zmax, n, derniere = np.inf, -np.inf, 0, -1
    if iterateur.initialize():
        while True:
            forme = iterateur.get()
            serialiseur.write(forme)
            verts = np.asarray(forme.geometry.verts, dtype=float).reshape(-1, 3)
            if len(verts):
                zmin, zmax = min(zmin, verts[:, 2].min()), max(zmax, verts[:, 2].max())
                e = _empreinte(verts)
                if e is not None:
                    lot.append(e)
                if len(lot) >= TAILLE_LOT_UNION:
                    empreintes.append(shapely.union_all(lot))
                    lot = []
            n += 1
            if progression:
                p = int(iterateur.progress())
                if p != derniere:
                    progression(p)
                    derniere = p
            if not iterateur.next():
                break
    serialiseur.finalize()

    if lot:
        empreintes.append(shapely.union_all(lot))
    emprise = shapely.union_all(empreintes) if empreintes else None
    if emprise is not None and simplification_m:
        emprise = emprise.simplify(simplification_m)
    return {
        "nb_elements": n,
        "nb_elements_attendus": attendus,
        "emprise_locale": mapping(emprise) if emprise is not None and not emprise.is_empty else None,
        "z_min": float(zmin) if n and np.isfinite(zmin) else None,
        "z_max": float(zmax) if n and np.isfinite(zmax) else None,
        "taille_glb": sortie_glb.stat().st_size,
    }

"""Géométrie d'une maquette : modèles 3D allégés (GLB) et emprise au sol, convertis par web-ifc.

La triangulation est faite par le convertisseur Node `convertisseur/convertir.mjs` (web-ifc, le moteur de la
visionneuse du patrimoine) : 10 à 100 fois plus rapide que la géométrie exacte d'IfcOpenShell, il produit des
modèles regroupés par couleur et compressés (voir l'en-tête du convertisseur). Ce module le lance dans un
processus séparé, suit son avancement et réunit les empreintes des objets en une emprise au sol.

Repères :
- emprise et altitudes renvoyées en coordonnées « locales » : coordonnées monde (mètres) moins `decalage`
  (l'origine du site), comme le reste du module ;
- les GLB sont exprimés par rapport à leur propre origine ; `origine_glb` donne cette origine en coordonnées
  locales (x, y, z ; z vertical) : l'appelant l'ajoute au placement du modèle.

Lancement en deux temps (`lancer` puis `terminer`) pour lire la maquette avec IfcOpenShell pendant la conversion.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

import shapely
from shapely.geometry import Polygon, mapping

EXCLUS_DEFAUT = ("IfcSpace", "IfcOpeningElement", "IfcAnnotation", "IfcGrid", "IfcVirtualElement")
DOSSIER_CONVERTISSEUR = Path(__file__).resolve().parent / "convertisseur"
TAILLE_LOT_UNION = 2000   # technique : union des empreintes par lots


class ConversionImpossible(Exception):
    """Le convertisseur n'a pas pu produire le modèle 3D ; le message est destiné à l'utilisateur."""


def _commande_node() -> str:
    node = shutil.which("node")
    if node is None:
        raise ConversionImpossible("Node.js introuvable : il est nécessaire à la conversion 3D des maquettes")
    if not (DOSSIER_CONVERTISSEUR / "node_modules" / "web-ifc").is_dir():
        raise ConversionImpossible(f"dépendances du convertisseur absentes : lancer « npm install » dans {DOSSIER_CONVERTISSEUR}")
    return node


def arreter_processus(p: subprocess.Popen) -> None:
    """Arrête un processus et ses descendants (sous Windows, tuer le parent ne suffit pas)."""
    if p.poll() is not None:
        return
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        p.kill()
    p.wait()


class Conversion:
    """Conversion en cours : processus Node, avancement et résultat lus sur sa sortie standard."""

    def __init__(self, processus: subprocess.Popen, sortie: Path):
        self.processus = processus
        self.sortie = sortie
        self.progression = 0
        self.fin: dict | None = None
        self.erreurs: list[str] = []
        self._lecteurs = [threading.Thread(target=self._lire_sortie, daemon=True),
                          threading.Thread(target=self._lire_erreurs, daemon=True)]
        for t in self._lecteurs:
            t.start()

    def _lire_sortie(self) -> None:
        for ligne in self.processus.stdout:
            ligne = ligne.strip()
            if ligne.startswith("progression "):
                self.progression = int(ligne.split()[1])
            elif ligne.startswith("fin "):
                self.fin = json.loads(ligne[4:])

    def _lire_erreurs(self) -> None:
        for ligne in self.processus.stderr:
            if ligne.strip():
                self.erreurs.append(ligne.strip())


def lancer(chemin_ifc: str | Path, dossier_sortie: str | Path, exclure=EXCLUS_DEFAUT,
           petits_elements_m: float = 0.0, simplification_3d_m: float = 0.0) -> Conversion:
    """Démarre la conversion (modele.glb, detail.glb, geometrie.json dans le dossier de sortie)."""
    node = _commande_node()
    sortie = Path(dossier_sortie)
    sortie.mkdir(parents=True, exist_ok=True)
    for nom in ("modele.glb", "detail.glb", "geometrie.json"):
        (sortie / nom).unlink(missing_ok=True)
    params = sortie / "parametres_3d.json"
    params.write_text(json.dumps({"exclure": list(exclure), "petits_elements_m": petits_elements_m,
                                  "simplification_3d_m": simplification_3d_m}), encoding="utf-8")
    p = subprocess.Popen([node, str(DOSSIER_CONVERTISSEUR / "convertir.mjs"), str(chemin_ifc), str(sortie), str(params)],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return Conversion(p, sortie)


def terminer(conv: Conversion, decalage=(0.0, 0.0, 0.0), simplification_m: float = 0.05,
             delai_s: float | None = None, progression: Callable[[int], None] | None = None,
             simplification_affichage_m: float = 0.0) -> dict:
    """Attend la fin de la conversion et renvoie emprise au sol, altitudes et description des modèles."""
    debut = time.monotonic()
    derniere = -1
    while conv.processus.poll() is None:
        if delai_s is not None and time.monotonic() - debut > delai_s:
            arreter_processus(conv.processus)
            raise ConversionImpossible(f"conversion 3D interrompue après {delai_s / 60:.0f} minutes")
        if progression and conv.progression != derniere:
            derniere = conv.progression
            progression(derniere)
        time.sleep(0.2)
    for t in conv._lecteurs:
        t.join(timeout=5)
    if conv.processus.returncode != 0 or conv.fin is None:
        detail = next((e[7:] for e in reversed(conv.erreurs) if e.startswith("erreur ")), None) \
            or (conv.erreurs[-1] if conv.erreurs else f"code de sortie {conv.processus.returncode}")
        if "memory" in detail.lower() or conv.processus.returncode in (134, 3221225477, 3221226505):
            detail = f"mémoire insuffisante ({detail})"
        raise ConversionImpossible(f"conversion 3D impossible : {detail}")
    if progression:
        progression(100)
    return _assembler(conv.sortie, decalage, simplification_m, simplification_affichage_m)


def _assembler(sortie: Path, decalage, simplification_m: float, simplification_affichage_m: float = 0.0) -> dict:
    g = json.loads((sortie / "geometrie.json").read_text(encoding="utf-8"))
    dx, dy, dz = (float(g["origine"][i]) - float(decalage[i]) for i in range(3))
    formes, empreintes = [], []
    for _objet, pts in g["empreintes"]:
        if len(pts) < 3:
            continue
        p = Polygon([(x + dx, y + dy) for x, y in pts])
        if p.is_valid and p.area > 1e-6:
            formes.append(p)
        if len(formes) >= TAILLE_LOT_UNION:
            empreintes.append(shapely.union_all(formes))
            formes = []
    if formes:
        empreintes.append(shapely.union_all(formes))
    emprise = shapely.union_all(empreintes) if empreintes else None
    if emprise is not None and simplification_m:
        emprise = emprise.simplify(simplification_m)
    # emprise à dessiner : morceaux distants de moins de deux fois la tolérance fusionnés, contour simplifié
    affichage = None
    t = simplification_affichage_m
    if emprise is not None and not emprise.is_empty and t:
        affichage = emprise.buffer(t, join_style="mitre").buffer(-t, join_style="mitre").simplify(t)
    n = g["nb_elements"]
    return {
        "moteur": g.get("moteur"),
        "nb_elements": n,
        "emprise_locale": mapping(emprise) if emprise is not None and not emprise.is_empty else None,
        "emprise_affichage_locale": mapping(affichage) if affichage is not None and not affichage.is_empty else None,
        "z_min": g["z_min"] + dz if n and g["z_min"] is not None else None,
        "z_max": g["z_max"] + dz if n and g["z_max"] is not None else None,
        "origine_glb": [round(dx, 6), round(dy, 6), round(dz, 6)],
        "taille_glb": g["taille_glb"],
        "taille_detail_glb": g["taille_detail_glb"],
        "triangles": g["triangles"],
        "triangles_detail": g["triangles_detail"],
        "triangles_avant_simplification": g["triangles_avant_simplification"],
        "triangles_apres_simplification": g["triangles_apres_simplification"],
        "nb_morceaux": g["nb_morceaux"],
        "nb_petits_morceaux": g["nb_petits_morceaux"],
        "duree_conversion_s": g["duree_secondes"],
    }


# Représentations sans surface (lignes, points) : rien à trianguler, l'objet n'est pas attendu dans le modèle 3D
TYPES_SANS_SURFACE = {"Point", "PointCloud", "Curve", "Curve2D", "Curve3D", "GeometricCurveSet", "GeometricSet",
                      "Annotation2D", "FillArea"}


def objets_attendus(f, exclure=EXCLUS_DEFAUT) -> dict[str, str]:
    """GlobalId → type IFC des objets qui ont une représentation surfacique ou volumique (hors types exclus).

    Sert à signaler les objets que la conversion n'a pas rendus.
    """
    attendus = {}
    for p in f.by_type("IfcProduct"):
        if p.Representation is None or any(p.is_a(t) for t in exclure):
            continue
        if any((r.RepresentationType or "") not in TYPES_SANS_SURFACE for r in p.Representation.Representations):
            attendus[p.GlobalId] = p.is_a()
    return attendus


def non_convertis(attendus: dict[str, str], dossier_sortie: str | Path) -> dict[str, int]:
    """Nombre d'objets attendus absents du modèle 3D, par type IFC."""
    rendus = set(json.loads((Path(dossier_sortie) / "geometrie.json").read_text(encoding="utf-8"))["guids"])
    compte: dict[str, int] = {}
    for guid, type_ifc in attendus.items():
        if guid not in rendus:
            compte[type_ifc] = compte.get(type_ifc, 0) + 1
    return dict(sorted(compte.items(), key=lambda kv: -kv[1]))


def convertir(chemin_ifc: str | Path, dossier_sortie: str | Path, decalage=(0.0, 0.0, 0.0), exclure=EXCLUS_DEFAUT,
              petits_elements_m: float = 0.0, simplification_m: float = 0.05, delai_s: float | None = None,
              progression: Callable[[int], None] | None = None, simplification_affichage_m: float = 0.0,
              simplification_3d_m: float = 0.0) -> dict:
    """Conversion complète en un appel (lancer puis terminer)."""
    return terminer(lancer(chemin_ifc, dossier_sortie, exclure, petits_elements_m, simplification_3d_m), decalage,
                    simplification_m, delai_s, progression, simplification_affichage_m)

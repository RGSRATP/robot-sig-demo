"""Propriétés des objets d'une maquette : lecture générique des Psets et index SQLite pour la consultation.

L'index évite de rouvrir une grosse maquette à chaque clic : il est construit une fois (GlobalId → type,
nom, étage, Psets) et interrogé ensuite sans IfcOpenShell.
"""
from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Callable

import ifcopenshell
import ifcopenshell.util.element as util_element


def _json(v):
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    if isinstance(v, (list, tuple)):
        return [_json(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _json(x) for k, x in v.items()}
    return str(v)


def psets(element) -> dict:
    """Jeux de propriétés et de quantités d'un objet (dont ceux de son type), sans les identifiants internes."""
    return {nom: {k: _json(v) for k, v in valeurs.items() if k != "id"}
            for nom, valeurs in util_element.get_psets(element).items()}


def fiche(element) -> dict:
    etage = util_element.get_container(element)
    type_objet = util_element.get_type(element)
    return {
        "guid": element.GlobalId,
        "type_ifc": element.is_a(),
        "nom": getattr(element, "Name", None),
        "description": getattr(element, "Description", None),
        "type_objet": getattr(type_objet, "Name", None) if type_objet else None,
        "etage": getattr(etage, "Name", None) if etage else None,
        "psets": psets(element),
    }


def indexer(f: ifcopenshell.file, sortie: str | Path, progression: Callable[[int], None] | None = None) -> int:
    """Écrit l'index SQLite des objets ayant une représentation ; renvoie le nombre d'objets indexés."""
    sortie = Path(sortie)
    sortie.parent.mkdir(parents=True, exist_ok=True)
    tmp = sortie.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    objets = [p for p in f.by_type("IfcProduct") if p.Representation is not None]
    base = sqlite3.connect(tmp)   # fermeture explicite : sous Windows, un fichier ouvert ne peut être renommé
    try:
        base.execute("CREATE TABLE objets (guid TEXT PRIMARY KEY, type_ifc TEXT, nom TEXT, fiche TEXT)")
        derniere = -1
        for i, o in enumerate(objets):
            try:
                fo = fiche(o)
            except Exception as e:  # un objet mal formé ne bloque pas l'index
                fo = {"guid": o.GlobalId, "type_ifc": o.is_a(), "nom": getattr(o, "Name", None),
                      "anomalie": f"propriétés illisibles ({type(e).__name__})", "psets": {}}
            base.execute("INSERT OR REPLACE INTO objets VALUES (?, ?, ?, ?)",
                         (fo["guid"], fo["type_ifc"], fo["nom"], json.dumps(fo, ensure_ascii=False)))
            if progression and objets:
                p = int(100 * (i + 1) / len(objets))
                if p != derniere:
                    progression(p)
                    derniere = p
        base.commit()
    finally:
        base.close()
    tmp.replace(sortie)
    return len(objets)


def lire_fiche(index: str | Path, guid: str) -> dict | None:
    base = sqlite3.connect(f"file:{Path(index).as_posix()}?mode=ro", uri=True)
    try:
        ligne = base.execute("SELECT fiche FROM objets WHERE guid = ?", (guid,)).fetchone()
    finally:
        base.close()
    return json.loads(ligne[0]) if ligne else None

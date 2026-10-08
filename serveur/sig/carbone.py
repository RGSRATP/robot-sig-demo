"""Carbone d'une maquette (lot 5, croisements) : ORDRE DE GRANDEUR des émissions de production des matériaux, à
partir de l'index des propriétés (proprietes.sqlite, produit au traitement de la maquette).

Pour chaque objet : un seul volume (premier champ présent de « champs_volume », m³ ; valeur nulle ou douteuse
écartée), un matériau (déclaré dans « champs_materiau », sinon déduit du nom ou du type de l'objet, sinon supposé
d'après le type IFC), un facteur (kg CO2e / m³) de la configuration. La fiabilité de chaque part est indiquée.
"""
from __future__ import annotations

import json
import re
import sqlite3
from functools import lru_cache
from pathlib import Path

ORIGINES = ("déclaré", "déduit du nom", "supposé (type IFC)")


def _regles(cfg_carbone: dict) -> tuple:
    mats = tuple((m["nom"], float(m["facteur_kgco2e_m3"]), re.compile("|".join(m["motifs"]), re.I)) for m in cfg_carbone["materiaux"])
    vol = {k.lower(): i for i, k in enumerate(cfg_carbone["champs_volume"])}
    mat = {k.lower(): i for i, k in enumerate(cfg_carbone["champs_materiau"])}
    return mats, vol, mat


def _classer(texte: str | None, mats) -> str | None:
    if not texte:
        return None
    return next((nom for nom, _, motif in mats if motif.search(texte)), None)


def objet(fiche: dict, cfg_carbone: dict, regles) -> dict:
    """Volume, matériau et origine du matériau d'un objet (fiche de l'index)."""
    mats, cles_vol, cles_mat = regles
    volume, rang_v, texte_mat, rang_m = None, 99, None, 99
    for props in (fiche.get("psets") or {}).values():
        for k, v in props.items():
            kl = k.lower()
            if kl in cles_vol and isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 and cles_vol[kl] < rang_v:
                volume, rang_v = float(v), cles_vol[kl]
            if kl in cles_mat and isinstance(v, str) and v.strip() and cles_mat[kl] < rang_m:
                texte_mat, rang_m = v.strip(), cles_mat[kl]
    materiau, origine = _classer(texte_mat, mats), "déclaré"
    if materiau is None:
        materiau, origine = _classer(f"{fiche.get('nom') or ''} {fiche.get('type_objet') or ''}", mats), "déduit du nom"
    if materiau is None:
        materiau, origine = cfg_carbone.get("par_type_ifc", {}).get(fiche.get("type_ifc")), "supposé (type IFC)"
    if materiau is None:
        origine = None
    return {"volume_m3": volume, "materiau": materiau, "origine": origine, "texte_materiau": texte_mat}


def bilan(chemin_index: Path, cfg_carbone: dict) -> dict:
    """Bilan d'une maquette : par matériau (volume, t CO2e, origine du matériau), totaux, objets non comptés."""
    return _bilan(str(chemin_index), Path(chemin_index).stat().st_mtime_ns, json.dumps(cfg_carbone, sort_keys=True))


@lru_cache(maxsize=32)
def _bilan(chemin: str, _mtime: int, cfg_json: str) -> dict:
    cfg_carbone = json.loads(cfg_json)
    regles = _regles(cfg_carbone)
    facteurs = {nom: f for nom, f, _ in regles[0]}
    vmax = float(cfg_carbone["volume_max_objet_m3"])
    par_mat: dict[str, dict] = {}
    non_classes: dict[str, int] = {}
    nb = sans_volume = douteux = 0
    con = sqlite3.connect(f"file:{chemin}?mode=ro", uri=True)
    try:
        for type_ifc, fiche_json in con.execute("SELECT type_ifc, fiche FROM objets"):
            nb += 1
            try:
                o = objet(json.loads(fiche_json), cfg_carbone, regles)
            except (ValueError, TypeError):
                continue
            if o["volume_m3"] is None:
                sans_volume += 1
                continue
            if o["volume_m3"] > vmax:
                douteux += 1
                continue
            if o["materiau"] is None:
                non_classes[type_ifc] = non_classes.get(type_ifc, 0) + 1
                continue
            m = par_mat.setdefault(o["materiau"], {"materiau": o["materiau"], "facteur_kgco2e_m3": facteurs[o["materiau"]],
                                                   "nb_objets": 0, "volume_m3": 0.0, **{f"volume_{x}": 0.0 for x in ORIGINES}})
            m["nb_objets"] += 1
            m["volume_m3"] += o["volume_m3"]
            m[f"volume_{o['origine']}"] += o["volume_m3"]
    finally:
        con.close()
    lignes = []
    for m in sorted(par_mat.values(), key=lambda m: -m["volume_m3"] * m["facteur_kgco2e_m3"]):
        m["tco2e"] = round(m["volume_m3"] * m["facteur_kgco2e_m3"] / 1000, 1)
        m["part_declaree"] = round(m["volume_déclaré"] / m["volume_m3"], 3) if m["volume_m3"] else 0
        for k in ("volume_m3", *(f"volume_{x}" for x in ORIGINES)):
            m[k] = round(m[k], 2)
        lignes.append(m)
    total = sum(m["tco2e"] for m in lignes)
    vol = sum(m["volume_m3"] for m in lignes)
    return {
        "materiaux": lignes, "total_tco2e": round(total, 1), "volume_compte_m3": round(vol, 1),
        "nb_objets": nb, "nb_comptes": sum(m["nb_objets"] for m in lignes), "nb_sans_volume": sans_volume,
        "nb_volume_douteux": douteux, "non_classes": dict(sorted(non_classes.items(), key=lambda x: -x[1])),
        "part_volume_materiau_declare": round(sum(m["volume_déclaré"] for m in lignes) / vol, 3) if vol else 0,
    }


COLONNES = [("materiau", "Matériau"), ("facteur_kgco2e_m3", "Facteur (kg CO2e/m³)"), ("nb_objets", "Objets"),
            ("volume_m3", "Volume (m³)"), ("tco2e", "Émissions (t CO2e)"), ("volume_déclaré", "Volume, matériau déclaré (m³)"),
            ("volume_déduit du nom", "Volume, matériau déduit du nom (m³)"),
            ("volume_supposé (type IFC)", "Volume, matériau supposé (m³)")]

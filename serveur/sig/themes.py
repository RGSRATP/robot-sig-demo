"""Valeurs par maquette pour les vues thématiques (interface/sig/vues.js) : ici le carbone de toutes les maquettes.

Le bilan carbone d'une maquette (carbone.py, ~0,6 s) est calculé en arrière-plan pour toutes les maquettes traitées,
une à la fois, et gardé dans le cache de la maquette (carbone.json, clé : date de l'index des propriétés et réglages
« carbone ») : seul le premier affichage après un changement attend. L'interface redemande tant que « complet » est faux.
"""
from __future__ import annotations

import hashlib
import json
import threading

from . import carbone as carb_sig
from . import maquettes as maq_sig

_verrou = threading.Lock()
_calcul: dict = {"fil": None, "valeurs": {}, "vus": set(), "erreurs": 0}   # vus : maquettes déjà tentées


def _cle(index, cfg_carbone: dict) -> str:
    reglages = hashlib.sha1(json.dumps(cfg_carbone, sort_keys=True).encode()).hexdigest()[:12]
    return f"{index.stat().st_mtime_ns}-{reglages}"


def _valeur(cfgm: dict, id_: str) -> dict | None:
    """Bilan résumé d'une maquette (cache carbone.json, sinon calcul) ; None sans index des propriétés."""
    dossier = maq_sig.dossier_cache(cfgm, id_)
    index = dossier / "proprietes.sqlite"
    if not index.is_file():
        return None
    cle = _cle(index, cfgm["carbone"])
    cache = dossier / "carbone.json"
    try:
        d = json.loads(cache.read_text(encoding="utf-8"))
        if d.get("cle") == cle:
            return d
    except (OSError, ValueError):
        pass
    b = carb_sig.bilan(index, cfgm["carbone"])
    d = {"cle": cle, "tco2e": round(b["total_tco2e"], 1), "volume_m3": round(b["volume_compte_m3"], 1),
         "part_declaree": round(b["part_volume_materiau_declare"], 3)}
    try:
        cache.write_text(json.dumps(d), encoding="utf-8")
    except OSError:
        pass   # cache facultatif
    return d


def _calculer(cfgm: dict, ids: list[str]) -> None:
    for id_ in ids:
        with _verrou:
            _calcul["vus"].add(id_)
        try:
            v = _valeur(cfgm, id_)
        except Exception:   # une maquette illisible n'arrête pas les autres
            with _verrou:
                _calcul["erreurs"] += 1
            continue
        if v is not None:
            with _verrou:
                _calcul["valeurs"][id_] = v


def carbone(cfgm: dict) -> dict:
    """Carbone (t CO2e) des maquettes déjà calculées ; lance le calcul des autres en arrière-plan."""
    ids = [id_ for id_, _rel, _chemin in maq_sig.fichiers(cfgm)]
    with _verrou:
        # maquettes traitées (index présent) pas encore tentées : une maquette traitée plus tard est reprise
        manquants = [i for i in ids if i not in _calcul["vus"] and (maq_sig.dossier_cache(cfgm, i) / "proprietes.sqlite").is_file()]
        fil = _calcul["fil"]
        if manquants and (fil is None or not fil.is_alive()):
            fil = threading.Thread(target=_calculer, args=(cfgm, manquants), name="themes-carbone", daemon=True)
            _calcul["fil"] = fil
            fil.start()
        en_cours = fil is not None and fil.is_alive()
        valeurs = {i: dict(v) for i, v in _calcul["valeurs"].items() if i in set(ids)}
    for v in valeurs.values():
        v.pop("cle", None)
    return {"valeurs": valeurs, "complet": not en_cours, "calcules": len(valeurs), "total": len(ids),
            "erreurs": _calcul["erreurs"]}


def reinitialiser() -> None:
    with _verrou:
        _calcul.update(fil=None, valeurs={}, vus=set(), erreurs=0)

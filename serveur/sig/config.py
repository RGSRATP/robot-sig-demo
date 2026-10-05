"""Lecture et contrôle de config/sig.yaml. Aucun paramètre cartographique n'est en dur dans le code."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

RACINE = Path(__file__).resolve().parents[2]
CHEMIN_CONFIG = RACINE / "config" / "sig.yaml"
DOSSIER_DONNEES = RACINE / "donnees"


class ConfigInvalide(ValueError):
    """Configuration absente, illisible ou incomplète ; le message dit quoi corriger."""


def _controler(cfg: dict, chemin: Path) -> dict:
    def erreur(msg: str) -> ConfigInvalide:
        return ConfigInvalide(f"{chemin.name} : {msg}")

    if not isinstance(cfg, dict):
        raise erreur("le fichier ne contient pas de paramètres")

    carte = cfg.get("carte")
    if not isinstance(carte, dict):
        raise erreur("section « carte » absente")
    centre = carte.get("centre_wgs84")
    if (
        not isinstance(centre, list)
        or len(centre) != 2
        or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in centre)
        or not (-180 <= centre[0] <= 180 and -90 <= centre[1] <= 90)
    ):
        raise erreur("« carte.centre_wgs84 » doit valoir [longitude, latitude] en degrés")
    zoom = carte.get("zoom_initial")
    if not isinstance(zoom, (int, float)) or isinstance(zoom, bool) or not 0 <= zoom <= 22:
        raise erreur("« carte.zoom_initial » doit être un nombre entre 0 et 22")

    fonds = cfg.get("fonds_ign")
    if not isinstance(fonds, dict):
        raise erreur("section « fonds_ign » absente")
    defaut = fonds.get("defaut")
    noms = [k for k, v in fonds.items() if k != "defaut" and isinstance(v, dict)]
    if not noms:
        raise erreur("aucun fond déclaré dans « fonds_ign »")
    if defaut not in noms:
        raise erreur(f"« fonds_ign.defaut » ({defaut!r}) doit être l'un de : {', '.join(noms)}")
    for nom in noms:
        url = fonds[nom].get("url")
        if not isinstance(url, str) or not all(p in url for p in ("{z}", "{x}", "{y}")):
            raise erreur(f"« fonds_ign.{nom}.url » doit contenir {{z}}, {{x}} et {{y}}")
    return cfg


def charger_config(chemin: Path = CHEMIN_CONFIG) -> dict:
    """Lit et contrôle le fichier ; lève ConfigInvalide avec un message clair en cas de problème."""
    chemin = Path(chemin)
    if not chemin.is_file():
        raise ConfigInvalide(f"fichier de configuration introuvable : {chemin}")
    try:
        with chemin.open(encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigInvalide(f"{chemin.name} : syntaxe YAML invalide ({e})") from e
    return _controler(cfg, chemin)


@lru_cache(maxsize=1)
def config() -> dict:
    return charger_config()


def config_carte(cfg: dict) -> dict:
    """Paramètres utiles à l'interface : centre, zoom et fonds IGN, dans l'ordre du fichier."""
    fonds = cfg["fonds_ign"]
    return {
        "centre_wgs84": cfg["carte"]["centre_wgs84"],
        "zoom_initial": cfg["carte"]["zoom_initial"],
        "fond_defaut": fonds["defaut"],
        "fonds": [
            {
                "id": nom,
                "libelle": f.get("libelle", nom),
                "url": f["url"],
                "attribution": f.get("attribution", ""),
            }
            for nom, f in fonds.items()
            if nom != "defaut" and isinstance(f, dict)
        ],
    }

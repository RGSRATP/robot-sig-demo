"""Lecture et contrôle de config/sig.yaml. Aucun paramètre cartographique n'est en dur dans le code."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

RACINE = Path(__file__).resolve().parents[2]
# SIG_CONFIG permet de lancer le démonstrateur sur une autre configuration (ex. le jeu de test open data).
CHEMIN_CONFIG = Path(os.environ.get("SIG_CONFIG") or RACINE / "config" / "sig.yaml")
if not CHEMIN_CONFIG.is_absolute():
    CHEMIN_CONFIG = RACINE / CHEMIN_CONFIG
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

    dossier = (cfg.get("donnees") or {}).get("dossier_sig")
    if not isinstance(dossier, str) or not dossier:
        raise erreur("« donnees.dossier_sig » absent")
    chargement = cfg.get("chargement") or {}
    for cle in ("seuil_objets_filtre_emprise", "max_resultats_recherche", "marge_controle_systeme_deg"):
        if not _nombre(chargement.get(cle)):
            raise erreur(f"« chargement.{cle} » doit être un nombre positif")
    if not isinstance(cfg.get("champs_libelle"), list):
        raise erreur("« champs_libelle » doit être une liste de noms de champs")
    styles = cfg.get("styles_defaut")
    if not isinstance(styles, dict) or not all(isinstance(styles.get(k), dict) for k in ("point", "ligne", "polygone")):
        raise erreur("« styles_defaut » doit définir point, ligne et polygone")
    palette = cfg.get("palette")
    if not isinstance(palette, list) or not palette or not all(isinstance(c, str) for c in palette):
        raise erreur("« palette » doit être une liste de couleurs")
    if not isinstance(cfg.get("style_selection"), dict):
        raise erreur("« style_selection » absent")

    declarations = cfg.get("couches_fichiers")
    if declarations is None:
        cfg["couches_fichiers"] = declarations = []
    if not isinstance(declarations, list):
        raise erreur("« couches_fichiers » doit être une liste")
    for i, d in enumerate(declarations, 1):
        if not isinstance(d, dict) or not isinstance(d.get("fichier"), str):
            raise erreur(f"« couches_fichiers » n° {i} : clé « fichier » absente")
        if "style" in d and not isinstance(d["style"], dict):
            raise erreur(f"« couches_fichiers » n° {i} ({d['fichier']}) : « style » doit être un dictionnaire")

    reseau = cfg.get("reseau")
    if not isinstance(reseau, dict):
        raise erreur("section « reseau » absente (proxy, certificats, délai des appels du serveur)")
    if reseau.get("proxy") is not None and not isinstance(reseau["proxy"], str):
        raise erreur("« reseau.proxy » doit être une URL (ou null pour une connexion directe)")
    if reseau.get("certificats") is not None and not isinstance(reseau["certificats"], str):
        raise erreur("« reseau.certificats » doit être un chemin de fichier .pem (ou null)")
    if not _nombre(reseau.get("delai_secondes"), True):
        raise erreur("« reseau.delai_secondes » doit être un nombre strictement positif")

    services = cfg.get("services_esri")
    if services is None:
        cfg["services_esri"] = services = []
    if not isinstance(services, list):
        raise erreur("« services_esri » doit être une liste")
    if services:
        _controler_esri(cfg, services, erreur)
    return cfg


def _nombre(v, strictement_positif=False) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and (v > 0 if strictement_positif else v >= 0)


def _controler_esri(cfg: dict, services: list, erreur) -> None:
    from .esri import MOTIF_URL, identifiant  # import local : esri dépend de config

    esri = cfg.get("esri")
    if not isinstance(esri, dict):
        raise erreur("section « esri » absente (requise dès qu'un service est déclaré dans « services_esri »)")
    for cle in ("dossier_cache", "dossier_exports"):
        if not isinstance(esri.get(cle), str) or not esri[cle]:
            raise erreur(f"« esri.{cle} » absent")
    for cle, strict in (("max_objets", True), ("cache_heures_defaut", False), ("nouvel_essai_minutes", False)):
        if not _nombre(esri.get(cle), strict):
            raise erreur(f"« esri.{cle} » doit être un nombre {'strictement ' if strict else ''}positif")

    vus = {}
    for i, s in enumerate(services, 1):
        if not isinstance(s, dict) or not isinstance(s.get("libelle"), str) or not s["libelle"]:
            raise erreur(f"« services_esri » n° {i} : clé « libelle » absente")
        nom = f"« services_esri » n° {i} ({s['libelle']})"
        if not isinstance(s.get("url"), str) or not MOTIF_URL.match(s["url"]):
            raise erreur(f"{nom} : « url » doit désigner une couche, …/FeatureServer/<n> ou …/MapServer/<n>")
        if "token" in s.get("url", "").lower() or any(k in s for k in ("token", "jeton", "mot_de_passe", "password")):
            raise erreur(f"{nom} : aucun jeton ni mot de passe ici ; indiquez le nom de la variable de .env dans « jeton_env »")
        for cle in ("taille_page", "max_objets"):
            if cle in s and not (isinstance(s[cle], int) and not isinstance(s[cle], bool) and s[cle] > 0):
                raise erreur(f"{nom} : « {cle} » doit être un entier strictement positif")
        if "cache_heures" in s and not _nombre(s["cache_heures"]):
            raise erreur(f"{nom} : « cache_heures » doit être un nombre positif")
        if "style" in s and not isinstance(s["style"], dict):
            raise erreur(f"{nom} : « style » doit être un dictionnaire")
        for cle in ("where", "champs", "jeton_env", "export", "export_couche", "systeme", "champ_libelle"):
            if cle in s and not isinstance(s[cle], str):
                raise erreur(f"{nom} : « {cle} » doit être un texte")
        id_ = identifiant(s)
        if id_ in vus:
            raise erreur(f"{nom} : même URL et même filtre que le service n° {vus[id_]}")
        vus[id_] = i


def dossier_sig(cfg: dict) -> Path:
    """Dossier des couches fichiers, relatif à la racine du projet sauf s'il est absolu."""
    p = Path(cfg["donnees"]["dossier_sig"])
    return p if p.is_absolute() else RACINE / p


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

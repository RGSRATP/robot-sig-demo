"""Lecture et contrôle de config/sig.yaml. Aucun paramètre cartographique n'est en dur dans le code."""
from __future__ import annotations

import os
import re
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

    if cfg.get("relief") is not None:
        _controler_relief(cfg["relief"], erreur)
    if cfg.get("batiments") is not None:
        _controler_batiments(cfg["batiments"], erreur)
    if cfg.get("lidar") is not None:
        _controler_lidar(cfg["lidar"], erreur)
    if cfg.get("temps_reel") is not None:
        _controler_temps_reel(cfg["temps_reel"], erreur)

    if not _nombre(carte.get("hauteur_coupe_plan_m"), True):
        raise erreur("« carte.hauteur_coupe_plan_m » doit être un nombre strictement positif (ex. 1.5)")

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
    croisement = cfg.get("croisement") or {}
    if not _nombre(croisement.get("tampon_m")) or not _nombre(croisement.get("max_objets_par_couche"), True):
        raise erreur("section « croisement » incomplète (tampon_m, max_objets_par_couche)")
    export = cfg.get("export") or {}
    if export.get("separateur") not in (";", ",", "\t") or not isinstance(export.get("encodage"), str):
        raise erreur("section « export » incomplète (separateur « ; », « , » ou tabulation ; encodage)")
    try:
        "é".encode(export["encodage"])
    except LookupError:
        raise erreur(f"« export.encodage » inconnu : {export['encodage']}") from None

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


def _controler_relief(relief, erreur) -> None:
    """Section « relief » (facultative) : sources d'altitude WMS en Web Mercator, appelées par le navigateur."""
    if not isinstance(relief, dict):
        raise erreur("« relief » doit être une section (actif, ombrage, zoom_max, valeur_absente_sous, defaut, sources)")
    sources = relief.get("sources")
    if not isinstance(sources, dict) or not sources or not all(isinstance(v, dict) for v in sources.values()):
        raise erreur("« relief.sources » doit déclarer au moins une source d'altitude")
    if relief.get("defaut") not in sources:
        raise erreur(f"« relief.defaut » ({relief.get('defaut')!r}) doit être l'une de : {', '.join(sources)}")
    for cle in ("actif", "ombrage"):
        if not isinstance(relief.get(cle), bool):
            raise erreur(f"« relief.{cle} » doit valoir true ou false")
    z = relief.get("zoom_max")
    if not isinstance(z, int) or isinstance(z, bool) or not 1 <= z <= 20:
        raise erreur("« relief.zoom_max » doit être un entier entre 1 et 20")
    v = relief.get("valeur_absente_sous")
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        raise erreur("« relief.valeur_absente_sous » doit être un nombre (ex. -1000)")
    for nom, s in sources.items():
        url = s.get("url")
        if not isinstance(url, str) or "{bbox-epsg-3857}" not in url:
            raise erreur(f"« relief.sources.{nom}.url » doit contenir {{bbox-epsg-3857}} (tuile Web Mercator)")
        if "x-bil" not in url.lower() or "WIDTH=256" not in url or "HEIGHT=256" not in url:
            raise erreur(f"« relief.sources.{nom}.url » doit demander des altitudes brutes "
                         "(FORMAT=image/x-bil;bits=32) en tuiles de 256 × 256 (WIDTH=256&HEIGHT=256)")
        secours = s.get("secours")
        if secours is not None and (secours not in sources or secours == nom):
            raise erreur(f"« relief.sources.{nom}.secours » doit désigner une autre source de « relief.sources »")


COULEUR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _controler_batiments(b, erreur) -> None:
    """Section « batiments » (facultative) : tuiles vectorielles BD TOPO extrudées, appelées par le navigateur."""
    if not isinstance(b, dict):
        raise erreur("« batiments » doit être une section (url, couche_source, champ_hauteur…)")
    url = b.get("url")
    if not isinstance(url, str) or not all(p in url for p in ("{z}", "{x}", "{y}")):
        raise erreur("« batiments.url » doit contenir {z}, {x} et {y} (tuiles vectorielles)")
    for cle in ("couche_source", "champ_identifiant", "champ_hauteur", "champ_couleur"):
        if not isinstance(b.get(cle), str) or not b[cle]:
            raise erreur(f"« batiments.{cle} » doit être un nom (texte)")
    for cle in ("zoom_min", "zoom_max_tuiles"):
        z = b.get(cle)
        if not isinstance(z, int) or isinstance(z, bool) or not 0 <= z <= 22:
            raise erreur(f"« batiments.{cle} » doit être un entier entre 0 et 22")
    if b["zoom_max_tuiles"] < b["zoom_min"]:
        raise erreur("« batiments.zoom_max_tuiles » doit être supérieur ou égal à « zoom_min »")
    for cle in ("visible", "masquer_sous_maquettes"):
        if not isinstance(b.get(cle), bool):
            raise erreur(f"« batiments.{cle} » doit valoir true ou false")
    o = b.get("opacite")
    if not _nombre(o) or o > 1:
        raise erreur("« batiments.opacite » doit être un nombre entre 0 et 1")
    if not _nombre(b.get("hauteur_defaut_m")):
        raise erreur("« batiments.hauteur_defaut_m » doit être un nombre positif")
    couleurs = b.get("couleurs")
    if not isinstance(couleurs, dict) or not all(isinstance(v, str) and COULEUR.match(v) for v in couleurs.values()):
        raise erreur("« batiments.couleurs » doit associer chaque valeur à une couleur #rrvvbb")
    if not isinstance(b.get("couleur_defaut"), str) or not COULEUR.match(b["couleur_defaut"]):
        raise erreur("« batiments.couleur_defaut » doit être une couleur #rrvvbb")
    if not isinstance(b.get("champs_fiche", []), list):
        raise erreur("« batiments.champs_fiche » doit être une liste de champs")


def _controler_lidar(li, erreur) -> None:
    """Section « lidar » (facultative) : dalles COPC listées par WFS, lues par le navigateur."""
    if not isinstance(li, dict):
        raise erreur("« lidar » doit être une section (wfs_dalles, champ_url, classes…)")
    wfs = li.get("wfs_dalles")
    if not isinstance(wfs, str) or not all(f"{{{k}}}" in wfs for k in ("sud", "ouest", "nord", "est")):
        raise erreur("« lidar.wfs_dalles » doit contenir {sud}, {ouest}, {nord} et {est} (emprise affichée)")
    for cle in ("champ_url", "systeme"):
        if not isinstance(li.get(cle), str) or not li[cle]:
            raise erreur(f"« lidar.{cle} » doit être un texte")
    z = li.get("zoom_min")
    if not isinstance(z, int) or isinstance(z, bool) or not 0 <= z <= 22:
        raise erreur("« lidar.zoom_min » doit être un entier entre 0 et 22")
    for cle in ("max_dalles", "budget_points", "taille_point_px"):
        if not _nombre(li.get(cle), True):
            raise erreur(f"« lidar.{cle} » doit être un nombre strictement positif")
    if li.get("couleur") not in ("classe", "altitude", "rgb"):
        raise erreur("« lidar.couleur » doit valoir classe, altitude ou rgb")
    classes = li.get("classes")
    if not isinstance(classes, dict) or not classes:
        raise erreur("« lidar.classes » doit déclarer les classes (code: {libelle, couleur})")
    for code, c in classes.items():
        if not isinstance(code, int) or not isinstance(c, dict) or not isinstance(c.get("libelle"), str)                 or not isinstance(c.get("couleur"), str) or not COULEUR.match(c["couleur"]):
            raise erreur(f"« lidar.classes.{code} » : code entier, libelle et couleur #rrvvbb attendus")
    if not isinstance(li.get("couleur_autres_classes"), str) or not COULEUR.match(li["couleur_autres_classes"]):
        raise erreur("« lidar.couleur_autres_classes » doit être une couleur #rrvvbb")


# Clés obligatoires de chaque type de source temps réel (en plus de id, type, libelle, periode_s).
CLES_TEMPS_REEL = {
    "gbfs": ("url_stations", "url_etat"),
    "hubeau_hydrometrie": ("url", "emprise_wgs84", "heures"),
    "open_meteo": ("url", "emprise_wgs84", "pas_deg"),
    "idfm_prochains_passages": ("url", "url_zones", "jeton_env", "zones_arret", "horizon_min"),
}


def _controler_temps_reel(sources, erreur) -> None:
    """Section « temps_reel » (facultative) : sources interrogées par le serveur."""
    if not isinstance(sources, list):
        raise erreur("« temps_reel » doit être une liste de sources")
    vus = set()
    for i, s in enumerate(sources, 1):
        if not isinstance(s, dict) or not isinstance(s.get("id"), str) or not re.fullmatch(r"[a-z0-9_-]+", s["id"]):
            raise erreur(f"« temps_reel » n° {i} : « id » attendu (minuscules, chiffres, - ou _)")
        nom = f"« temps_reel » {s['id']}"
        if s["id"] in vus:
            raise erreur(f"{nom} : identifiant en double")
        vus.add(s["id"])
        if s.get("type") not in CLES_TEMPS_REEL:
            raise erreur(f"{nom} : « type » doit être l'un de : {', '.join(CLES_TEMPS_REEL)}")
        if not isinstance(s.get("libelle"), str) or not _nombre(s.get("periode_s"), True):
            raise erreur(f"{nom} : « libelle » et « periode_s » (secondes, > 0) attendus")
        if any(k in s for k in ("cle", "apikey", "token", "jeton", "mot_de_passe")):
            raise erreur(f"{nom} : aucune clé ici ; indiquez le NOM de la variable du fichier .env dans « jeton_env »")
        manquantes = [k for k in CLES_TEMPS_REEL[s["type"]] if s.get(k) in (None, "", [])]
        if manquantes:
            raise erreur(f"{nom} : clé(s) {', '.join(manquantes)} absente(s)")
        e = s.get("emprise_wgs84")
        if e is not None and not (isinstance(e, list) and len(e) == 4 and all(isinstance(v, (int, float)) for v in e)
                                  and e[0] < e[2] and e[1] < e[3]):
            raise erreur(f"{nom} : « emprise_wgs84 » doit valoir [ouest, sud, est, nord] en degrés")


def config_lidar(cfg: dict) -> dict | None:
    """Nuage LiDAR pour l'interface (None si la section est absente)."""
    li = cfg.get("lidar")
    if not li:
        return None
    return {**li, "attribution": li.get("attribution", ""), "champ_date": li.get("champ_date"),
            "classes": [{"code": code, "libelle": c["libelle"], "couleur": c["couleur"], "visible": c.get("visible", True)}
                        for code, c in li["classes"].items()]}


def config_batiments(cfg: dict) -> dict | None:
    """Bâtiments 3D pour l'interface (None si la section est absente)."""
    b = cfg.get("batiments")
    if not b:
        return None
    return {**b, "libelle": b.get("libelle", "Bâtiments 3D"), "attribution": b.get("attribution", ""),
            "champs_fiche": b.get("champs_fiche", [])}


def config_relief(cfg: dict) -> dict | None:
    """Relief pour l'interface (None si la section est absente)."""
    r = cfg.get("relief")
    if not r:
        return None
    return {
        "actif": r["actif"],
        "ombrage": r["ombrage"],
        "zoom_max": r["zoom_max"],
        "valeur_absente_sous": r["valeur_absente_sous"],
        "defaut": r["defaut"],
        "sources": [
            {"id": nom, "libelle": s.get("libelle", nom), "url": s["url"], "attribution": s.get("attribution", ""),
             "secours": s.get("secours")}
            for nom, s in r["sources"].items()
        ],
    }


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
        "glyphes": cfg["carte"].get("glyphes"),
        "hauteur_coupe_plan_m": cfg["carte"]["hauteur_coupe_plan_m"],
        "police": cfg["carte"].get("police"),
        "relief": config_relief(cfg),
        "batiments": config_batiments(cfg),
        "lidar": config_lidar(cfg),
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

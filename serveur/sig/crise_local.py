"""Sources « autour de l'événement » de la carte de crise (lot C3) : rues, bâtiments, population carroyée.

Ces couches nationales (BD TOPO, carroyage INSEE) sont trop volumineuses pour l'Île-de-France entière. Elles sont
demandées au WFS par carreaux réguliers de Lambert 93 (`carreau_m`) qui recouvrent le disque de `rayon_m` autour
du point de l'événement. Chaque carreau est gardé en cache (`cache_heures`) : un événement voisin réutilise les
carreaux déjà lus.

Ne jamais bloquer : les carreaux manquants sont chargés en arrière-plan, l'analyse répond tout de suite avec
« chargement en cours » (l'interface la relance). Un carreau périmé est servi et actualisé en arrière-plan ; un
carreau sans réponse est signalé, les autres sont utilisés.
"""
from __future__ import annotations

import gzip
import json
import math
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import shapely

from . import flux, reseau
from .config import RACINE
from .esri import ErreurService

CARREAU_DEFAUT_M = 500
REQUETES_PARALLELES = 2
REQUETES_PAR_SECONDE = 4.0     # Géoplateforme : HTTP 429 au-delà d'un débit par poste (constaté le 2026-10-22)
REPRISES_DEBIT = 3             # tours de reprise des carreaux refusés pour débit
ATTENTE_REPRISE_S = 2.0
MAX_OBJETS_CARREAU = 20000     # garde-fou par carreau (au-delà : carreau tronqué, signalé)
MEMOIRE = 12                   # assemblages de carreaux gardés en mémoire (technique)

_verrou = threading.Lock()
_en_cours: set = set()                    # (source, carreau) en cours de téléchargement
_echecs: dict = {}                        # (source, carreau) -> (instant, message) du dernier échec
_memoire: OrderedDict = OrderedDict()     # (source, carreaux, dates) -> objets assemblés
_entetes: dict = {}                       # fichier de carreau -> (date de modification, {date, tronque})
_fils: list[threading.Thread] = []
_creneaux: threading.BoundedSemaphore | None = None   # requêtes simultanées, toutes sources confondues
_verrou_cadence = threading.Lock()
_derniere = [0.0]                                     # instant de la dernière requête (débit plafonné)


def controler(c: dict, erreur) -> None:
    """Section « crise.sources_locales » (facultative) ; « crise.dossier_cache » obligatoire si elle existe."""
    sources = c.get("sources_locales", [])
    if not isinstance(sources, list):
        raise erreur("« crise.sources_locales » doit être une liste de sources WFS")
    if sources and not (isinstance(c.get("dossier_cache"), str) and c["dossier_cache"]):
        raise erreur("« crise.dossier_cache » attendu (carreaux des sources locales, hors Git)")
    ids = set()
    for i, s in enumerate(sources, 1):
        nom = f"« crise.sources_locales » n° {i}"
        if not (isinstance(s, dict) and isinstance(s.get("id"), str) and isinstance(s.get("libelle"), str)
                and isinstance(s.get("url"), str) and flux.MOTIF_URL.match(s["url"]) and isinstance(s.get("couche"), str)):
            raise erreur(f"{nom} : « id », « libelle », « url » (http…) et « couche » attendus")
        if s["id"] in ids:
            raise erreur(f"{nom} : identifiant « {s['id']} » en double")
        ids.add(s["id"])
        r = s.get("rayon_m")
        if not (isinstance(r, (int, float)) and not isinstance(r, bool) and 0 < r <= c["rayon_max_m"]):
            raise erreur(f"{nom} : « rayon_m » doit être compris entre 0 et « crise.rayon_max_m »")
        for cle in ("carreau_m", "cache_heures"):
            v = s.get(cle, 1)
            if not (isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0):
                raise erreur(f"{nom} : « {cle} » doit être un nombre strictement positif")
        if "champs" in s and not (isinstance(s["champs"], list) and all(isinstance(x, str) for x in s["champs"])):
            raise erreur(f"{nom} : « champs » doit être une liste de noms d'attributs")


def source(cfg: dict, id_: str) -> dict | None:
    return next((s for s in cfg["crise"].get("sources_locales", []) if s["id"] == id_), None)


# ---------------------------------------------------------------- carreaux

def carreaux(centre_l93: tuple[float, float], rayon_m: float, pas_m: float) -> list[tuple[int, int]]:
    """Carreaux (i, j) de `pas_m` mètres, alignés sur l'origine de Lambert 93, qui touchent le disque."""
    x, y = centre_l93
    sortie = []
    for i in range(math.floor((x - rayon_m) / pas_m), math.floor((x + rayon_m) / pas_m) + 1):
        for j in range(math.floor((y - rayon_m) / pas_m), math.floor((y + rayon_m) / pas_m) + 1):
            dx = max(i * pas_m - x, 0, x - (i + 1) * pas_m)   # distance du point au carreau
            dy = max(j * pas_m - y, 0, y - (j + 1) * pas_m)
            if math.hypot(dx, dy) <= rayon_m:
                sortie.append((i, j))
    return sortie


def _pas(s: dict) -> float:
    return float(s.get("carreau_m") or CARREAU_DEFAUT_M)


def _dossier(cfg: dict, s: dict) -> Path:
    d = Path(cfg["crise"]["dossier_cache"])
    return (d if d.is_absolute() else RACINE / d) / s["id"]


def _fichier(cfg: dict, s: dict, c: tuple[int, int]) -> Path:
    return _dossier(cfg, s) / f"{int(_pas(s))}_{c[0]}_{c[1]}.json.gz"


def _lire(chemin: Path) -> dict | None:
    try:
        with gzip.open(chemin, "rt", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) and isinstance(d.get("features"), list) else None
    except (OSError, ValueError, EOFError):   # carreau absent ou abîmé : relu depuis le service
        return None


def _entete(chemin: Path) -> dict | None:
    """Date et troncature d'un carreau, gardées en mémoire tant que le fichier ne change pas : le contenu
    n'est relu que pour assembler de nouveaux objets (0,6 s de relecture évitée à chaque analyse)."""
    try:
        mtime = chemin.stat().st_mtime_ns
    except OSError:
        return None
    with _verrou:
        e = _entetes.get(chemin)
    if e is not None and e[0] == mtime:
        return e[1]
    d = _lire(chemin)
    if d is None:
        return None
    entete = {"date": d.get("date"), "tronque": d.get("tronque", False)}
    with _verrou:
        _entetes[chemin] = (mtime, entete)
    return entete


def _ecrire(chemin: Path, contenu: dict) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    tmp = chemin.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(contenu, f, ensure_ascii=False)
    for essai in range(5):   # Windows refuse de remplacer un fichier en cours de lecture
        try:
            tmp.replace(chemin)
            return
        except PermissionError:
            time.sleep(0.2 * (essai + 1))
    tmp.unlink(missing_ok=True)


def telecharger_carreau(cfg: dict, s: dict, c: tuple[int, int], client) -> dict:
    """Objets du carreau, en Lambert 93, écrits dans le cache."""
    pas = _pas(s)
    emprise = [c[0] * pas, c[1] * pas, (c[0] + 1) * pas, (c[1] + 1) * pas]
    base = {"SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature", "TYPENAMES": s["couche"],
            "OUTPUTFORMAT": s.get("format_sortie") or "application/json", "SRSNAME": "EPSG:2154"}
    if s.get("champs"):   # attributs utiles seulement (volume divisé par 3 à 5 pour les bâtiments)
        base["PROPERTYNAME"] = ",".join([s.get("champ_geometrie") or "geometrie", *s["champs"]])
    max_objets = int(s.get("max_objets_carreau") or MAX_OBJETS_CARREAU)
    features, total = flux._pages_wfs(client, s, base, emprise, int(s.get("taille_page") or flux.TAILLE_PAGE_WFS),
                                      max_objets + 1, systeme_emprise="EPSG:2154")
    contenu = {"date": datetime.now().isoformat(timespec="seconds"), "tronque": len(features) > max_objets,
               "features": [{"id": f.get("id"), "geometry": f.get("geometry"), "properties": f.get("properties")}
                            for f in features[:max_objets]]}
    _ecrire(_fichier(cfg, s, c), contenu)
    return contenu


def _charger(cfg: dict, s: dict, liste: list[tuple[int, int]], fabrique) -> None:
    """Téléchargement des carreaux en arrière-plan : quelques requêtes en parallèle, débit plafonné. Les carreaux
    refusés pour débit (HTTP 429, après les nouveaux essais du connecteur) sont repris aussitôt, un par un : ils
    restent « en cours » jusque-là (jamais 10 minutes de données manquantes pour un refus passager)."""
    reste = list(liste)
    try:
        with fabrique(cfg) as client:
            for tour in range(REPRISES_DEBIT + 1):
                refuses = []

                def un(c):
                    try:
                        with _creneau(cfg):   # le service limite le débit par poste (HTTP 429)
                            _cadence(cfg)
                            telecharger_carreau(cfg, s, c, client)
                        with _verrou:
                            _echecs.pop((s["id"], c), None)
                    except (ErreurService, OSError) as e:
                        if "HTTP 429" in str(e):
                            refuses.append(c)
                        with _verrou:
                            _echecs[(s["id"], c)] = (time.monotonic(), str(e))
                    except Exception as e:   # un carreau ne doit jamais arrêter les autres
                        with _verrou:
                            _echecs[(s["id"], c)] = (time.monotonic(), f"erreur inattendue ({type(e).__name__} : {e})")

                paralleles = int(cfg["crise"].get("requetes_paralleles") or REQUETES_PARALLELES) if tour == 0 else 1
                with ThreadPoolExecutor(max_workers=paralleles) as pool:
                    list(pool.map(un, reste))
                if not refuses or tour == REPRISES_DEBIT:
                    break
                reste = refuses
                time.sleep(ATTENTE_REPRISE_S * (tour + 1))
    finally:
        with _verrou:
            for c in liste:
                _en_cours.discard((s["id"], c))


def _cadence(cfg: dict) -> None:
    """Débit plafonné (requêtes par seconde, toutes sources confondues) : le service répond 429 au-delà."""
    debit = float(cfg["crise"].get("requetes_par_seconde") or REQUETES_PAR_SECONDE)
    with _verrou_cadence:
        attente = _derniere[0] + 1 / debit - time.monotonic()
        if attente > 0:
            time.sleep(attente)
        _derniere[0] = time.monotonic()


def _creneau(cfg: dict) -> threading.BoundedSemaphore:
    global _creneaux
    with _verrou:
        if _creneaux is None:
            _creneaux = threading.BoundedSemaphore(int(cfg["crise"].get("requetes_paralleles") or REQUETES_PARALLELES))
        return _creneaux


def _lancer(cfg: dict, s: dict, liste: list[tuple[int, int]], fabrique) -> None:
    with _verrou:
        liste = [c for c in liste if (s["id"], c) not in _en_cours]
        if not liste:
            return
        _en_cours.update((s["id"], c) for c in liste)
    t = threading.Thread(target=_charger, args=(cfg, s, liste, fabrique), daemon=True, name=f"crise-{s['id']}")
    _fils.append(t)
    t.start()


def attendre(delai_s: float = 120) -> None:
    """Fin des téléchargements en arrière-plan (tests, arrêt du serveur)."""
    fin = time.monotonic() + delai_s
    while _fils:
        t = _fils.pop()
        t.join(max(0.0, fin - time.monotonic()))


def reinitialiser() -> None:
    attendre()
    global _creneaux
    with _verrou:
        _en_cours.clear()
        _echecs.clear()
        _memoire.clear()
        _entetes.clear()
        _creneaux = None


# ---------------------------------------------------------------- objets autour du point

def objets(cfg: dict, s: dict, centre_l93: tuple[float, float], fabrique=reseau.client) -> dict:
    """Objets de la source autour du point, ou état du chargement.

    Retour : {"en_cours": bool, "progression": [lus, total], "objets": (GeoDataFrame, géométries, index) | None,
    "date": date du carreau le plus ancien, "origine", "anomalies": [...]}.
    """
    liste = carreaux(centre_l93, float(s["rayon_m"]), _pas(s))
    validite = float(s.get("cache_heures") or cfg["esri"]["cache_heures_defaut"]) * 3600
    nouvel_essai = float(cfg["esri"]["nouvel_essai_minutes"]) * 60
    maintenant = time.monotonic()
    lus, perimes, manquants, en_echec = {}, [], [], []
    for c in liste:
        d = _entete(_fichier(cfg, s, c))
        with _verrou:
            echec = _echecs.get((s["id"], c))
            en_cours = (s["id"], c) in _en_cours
        if en_cours and d is None:
            manquants.append(c)
        elif d is not None:
            lus[c] = d
            age = (datetime.now() - datetime.fromisoformat(d["date"])).total_seconds() if d.get("date") else math.inf
            if age > validite and not (echec and maintenant - echec[0] < nouvel_essai):
                perimes.append(c)
        elif echec and maintenant - echec[0] < nouvel_essai:
            en_echec.append((c, echec[1]))
        else:
            manquants.append(c)
    if manquants or perimes:
        _lancer(cfg, s, manquants + perimes, fabrique)
    if manquants:
        return {"en_cours": True, "progression": [len(lus) + len(en_echec), len(liste)], "objets": None,
                "date": None, "origine": "chargement", "anomalies": []}
    anomalies = []
    if en_echec:
        anomalies.append(f"{len(en_echec)} carreau(x) sur {len(liste)} sans réponse du service ({en_echec[0][1]}) : "
                         "données incomplètes autour du point")
    if not lus:
        return {"en_cours": False, "progression": [0, len(liste)], "objets": None, "date": None,
                "origine": "indisponible", "anomalies": anomalies}
    tronques = sum(1 for d in lus.values() if d.get("tronque"))
    if tronques:
        anomalies.append(f"{tronques} carreau(x) tronqué(s) (plus de {s.get('max_objets_carreau') or MAX_OBJETS_CARREAU} objets)")
    dates = sorted(d.get("date") or "" for d in lus.values())
    cle = (s["id"], tuple(sorted(lus)), tuple(lus[c].get("date") for c in sorted(lus)))
    with _verrou:
        val = _memoire.get(cle)
        if val is not None:
            _memoire.move_to_end(cle)
    if val is None:
        vus, features = set(), []
        for c in sorted(lus):
            for f in (_lire(_fichier(cfg, s, c)) or {"features": []})["features"]:
                cle_objet = f.get("id") or json.dumps(f, sort_keys=True)
                if cle_objet not in vus:   # objet à cheval sur deux carreaux
                    vus.add(cle_objet)
                    features.append(f)
        gdf, _ = flux._vers_gdf(features, "EPSG:2154")
        gdf = gdf.reset_index(drop=True)
        geoms = gdf.geometry.values if len(gdf) else shapely.points([])
        val = (gdf, geoms, shapely.STRtree(geoms))
        with _verrou:
            _memoire[cle] = val
            while len(_memoire) > MEMOIRE:
                _memoire.popitem(last=False)
    return {"en_cours": False, "progression": [len(liste), len(liste)], "objets": val, "date": dates[0] or None,
            "origine": "cache ancien, actualisation en cours" if perimes else "cache", "anomalies": anomalies}

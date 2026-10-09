"""Maquettes IFC sur la carte : inventaire, traitements en arrière-plan, positionnement et calage vertical.

Le module IFC partagé (commun/ifc) fait la lecture ; ce module y ajoute ce qui relève du démonstrateur SIG :
configuration (config/georef_maquettes.yaml), cache, file d'attente des traitements, altimétrie IGN,
conversion des emprises en WGS84 et paramètres d'affichage 3D sur la carte.

Ne jamais bloquer : une maquette illisible ou non positionnée est signalée, les autres s'affichent.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import math
import os
import subprocess
import sys
import threading
import time
from functools import lru_cache
from pathlib import Path

import httpx
import numpy as np
import yaml
from pyproj import Transformer

from commun.ifc import VERSION_LOCAUX, VERSION_MODULE, georef
from commun.ifc.geometrie import arreter_processus

from . import reseau
from .config import RACINE

CHEMIN_GEOREF = Path(os.environ.get("SIG_GEOREF") or RACINE / "config" / "georef_maquettes.yaml")
if not CHEMIN_GEOREF.is_absolute():
    CHEMIN_GEOREF = RACINE / CHEMIN_GEOREF
PAS_CONTROLE_M = 100.0   # technique : pas des points de contrôle du repère local (affichage 3D)


class ConfigMaquettesInvalide(ValueError):
    """config/georef_maquettes.yaml absent, illisible ou incomplet ; le message dit quoi corriger."""


# ---------------------------------------------------------------- configuration

def _chemin(v: str) -> Path:
    p = Path(v)
    return p if p.is_absolute() else RACINE / p


def _nombre(v, strict=False) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and (v > 0 if strict else v >= 0)


def charger_config(chemin: Path = CHEMIN_GEOREF) -> dict:
    chemin = Path(chemin)

    def erreur(msg):
        return ConfigMaquettesInvalide(f"{chemin.name} : {msg}")

    if not chemin.is_file():
        raise ConfigMaquettesInvalide(f"fichier de configuration introuvable : {chemin}")
    try:
        cfg = yaml.safe_load(chemin.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise erreur(f"syntaxe YAML invalide ({e})") from e
    if not isinstance(cfg, dict):
        raise erreur("le fichier ne contient pas de paramètres")
    for cle in ("dossier_ifc", "dossier_cache"):
        if not isinstance(cfg.get(cle), str) or not cfg[cle]:
            raise erreur(f"« {cle} » absent")
    t = cfg.get("traitement") or {}
    if not _nombre(t.get("petits_elements_m")) or not _nombre(t.get("simplification_emprise_m")) \
            or not _nombre(t.get("simplification_emprise_affichage_m")) or not _nombre(t.get("simplification_3d_m")) \
            or not _nombre(t.get("delai_max_minutes"), True) or not isinstance(t.get("exclure"), list) \
            or not (isinstance(t.get("traitements_simultanes"), int) and t["traitements_simultanes"] >= 1):
        raise erreur("section « traitement » incomplète (exclure, petits_elements_m, simplification_emprise_m, "
                     "simplification_emprise_affichage_m, simplification_3d_m, "
                     "delai_max_minutes, traitements_simultanes)")
    a3d = cfg.get("affichage_3d") or {}
    if not all(_nombre(a3d.get(k)) for k in ("chargement_automatique_max_mo", "zoom_chargement_automatique", "zoom_details",
                                               "budget_triangles_millions")):
        raise erreur("« affichage_3d » : chargement_automatique_max_mo, zoom_chargement_automatique, zoom_details et "
                     "budget_triangles_millions (nombres positifs) attendus")
    if not all(_nombre(a3d.get(k)) for k in ("zoom_emprises", "max_emprises")):
        raise erreur("« affichage_3d » : zoom_emprises et max_emprises (nombres positifs) attendus")
    rendu = a3d.setdefault("rendu", {})
    nombres = (("angle_aretes_deg", 30), ("aretes_max_triangles_millions", 1.5), ("opacite_aretes", 0.4),
               ("exposition", 1.0), ("contraste_lumiere", 1.0), ("zoom_aretes", 17))
    if (not isinstance(rendu, dict) or not isinstance(rendu.setdefault("aretes", True), bool)
            or not all(_nombre(rendu.setdefault(k, v)) for k, v in nombres)
            or not isinstance(rendu.setdefault("couleur_aretes", "#1f2328"), str)):
        raise erreur("« affichage_3d.rendu » : aretes (true/false), angle_aretes_deg, aretes_max_triangles_millions, "
                     "couleur_aretes, opacite_aretes, exposition, contraste_lumiere, zoom_aretes attendus")
    carbone = cfg.get("carbone")
    if carbone is not None:
        _controler_carbone(carbone, erreur)
    if not _nombre((cfg.get("inventaire") or {}).get("balayage_secondes")):
        raise erreur("« inventaire.balayage_secondes » : nombre positif attendu")
    for i, f in enumerate(cfg.get("familles_objets") or [], 1):
        if not isinstance(f, dict) or not isinstance(f.get("nom"), str) or not all(
                isinstance(f.get(k, []), list) for k in ("types_ifc", "fichiers", "domaines_sgp")):
            raise erreur(f"« familles_objets » n° {i} : « nom » et listes types_ifc / fichiers / domaines_sgp attendus")
    ident = cfg.get("identification") or {}
    if not isinstance(ident.get("systemes_candidats"), list) or not _nombre(ident.get("tolerance_m"), True):
        raise erreur("section « identification » incomplète (systemes_candidats, tolerance_m)")
    emp = ident.get("emprise_plausible_wgs84")
    if emp is not None and not (isinstance(emp, list) and len(emp) == 4 and all(_nombre(abs(v)) for v in emp)):
        raise erreur("« identification.emprise_plausible_wgs84 » : [ouest, sud, est, nord] attendu")
    if ident.get("ecart_max_geometrie_m") is not None and not _nombre(ident["ecart_max_geometrie_m"], True):
        raise erreur("« identification.ecart_max_geometrie_m » : nombre positif attendu")
    alti = cfg.get("altimetrie")
    if alti is not None and (not isinstance(alti.get("url"), str) or not _nombre(alti.get("ecart_max_terrain_m"), True)):
        raise erreur("section « altimetrie » incomplète (url, ressource, ecart_max_terrain_m)")
    for cle in ("systemes_declares", "maquettes"):
        if cfg.get(cle) is None:
            cfg[cle] = []
        if not isinstance(cfg[cle], list):
            raise erreur(f"« {cle} » doit être une liste")
    for i, d in enumerate(cfg["systemes_declares"], 1):
        if not isinstance(d, dict) or not isinstance(d.get("motif"), str) or not isinstance(d.get("systeme"), str):
            raise erreur(f"« systemes_declares » n° {i} : « motif » et « systeme » attendus")
    for i, m in enumerate(cfg["maquettes"], 1):
        if not isinstance(m, dict) or not isinstance(m.get("fichier"), str):
            raise erreur(f"« maquettes » n° {i} : clé « fichier » absente")
        if ("x" in m) != ("y" in m):
            raise erreur(f"« maquettes » n° {i} ({m['fichier']}) : « x » et « y » vont ensemble")
    return cfg


def _controler_carbone(c, erreur) -> None:
    """Section « carbone » : champs de volume et de matériau, matériaux (facteur, motifs), types IFC par défaut."""
    import re
    if not isinstance(c, dict) or not all(isinstance(c.get(k), list) and c[k] for k in ("champs_volume", "champs_materiau", "materiaux"))             or not _nombre(c.get("volume_max_objet_m3"), True):
        raise erreur("« carbone » : champs_volume, champs_materiau, materiaux (listes) et volume_max_objet_m3 attendus")
    noms = set()
    for i, m in enumerate(c["materiaux"], 1):
        if not isinstance(m, dict) or not isinstance(m.get("nom"), str) or not _nombre(m.get("facteur_kgco2e_m3"))                 or not isinstance(m.get("motifs"), list) or not m["motifs"]:
            raise erreur(f"« carbone.materiaux » n° {i} : nom, facteur_kgco2e_m3 et motifs attendus")
        for motif in m["motifs"]:
            try:
                re.compile(motif)
            except (re.error, TypeError) as e:
                raise erreur(f"« carbone.materiaux » {m['nom']} : motif invalide {motif!r} ({e})") from None
        noms.add(m["nom"])
    inconnus = sorted({v for v in (c.get("par_type_ifc") or {}).values() if v not in noms})
    if inconnus:
        raise erreur(f"« carbone.par_type_ifc » : matériau(x) inconnu(s) {', '.join(inconnus)} (absents de « materiaux »)")


@lru_cache(maxsize=4)
def _config_cache(chemin: str, mtime: int) -> dict:
    return charger_config(Path(chemin))


def config() -> dict:
    """Configuration relue dès que le fichier change (déclarations et saisies manuelles prises en compte à chaud)."""
    try:
        mtime = CHEMIN_GEOREF.stat().st_mtime_ns
    except OSError:
        raise ConfigMaquettesInvalide(f"fichier de configuration introuvable : {CHEMIN_GEOREF}") from None
    return _config_cache(str(CHEMIN_GEOREF), mtime)


# ---------------------------------------------------------------- inventaire et cache

def identifiant(rel: str) -> str:
    return hashlib.sha1(f"ifc::{rel}".encode("utf-8")).hexdigest()[:12]


def fichiers(cfgm: dict) -> list[tuple[str, str, Path]]:
    """(id, chemin relatif, chemin) des maquettes du dossier, triées par chemin."""
    dossier = _chemin(cfgm["dossier_ifc"])
    if not dossier.is_dir():
        return []
    trouves = sorted((p for p in dossier.rglob("*") if p.is_file() and p.suffix.lower() == ".ifc"),
                     key=lambda p: p.as_posix().lower())
    return [(identifiant(p.relative_to(dossier).as_posix()), p.relative_to(dossier).as_posix(), p) for p in trouves]


def cle_traitement(chemin: Path, cfgm: dict) -> str:
    st = chemin.stat()
    t = cfgm["traitement"]
    return (f"{st.st_size}-{st.st_mtime_ns}-{VERSION_MODULE}-{t['petits_elements_m']}-{t['simplification_3d_m']}-"
            f"{t['simplification_emprise_m']}-{t['simplification_emprise_affichage_m']}-{','.join(sorted(t['exclure']))}")


def cle_locaux(chemin: Path, cfgm: dict) -> str:
    return f"{cle_traitement(chemin, cfgm)}-locaux{VERSION_LOCAUX}"


VERSION_ETAGES = "1"   # extraction des étages (IfcBuildingStorey), cache séparé de la 3D et des locaux


def cle_etages(chemin: Path, cfgm: dict) -> str:
    return f"{cle_traitement(chemin, cfgm)}-etages{VERSION_ETAGES}"


def parametres_traitement(chemin: Path, cfgm: dict, tache: str = "complet") -> dict:
    """Paramètres transmis au travailleur (serveur/sig/travail_ifc.py)."""
    t = cfgm["traitement"]
    return {"cle": cle_traitement(chemin, cfgm), "cle_locaux": cle_locaux(chemin, cfgm),
            "cle_etages": cle_etages(chemin, cfgm), "tache": tache,
            "exclure": t["exclure"], "petits_elements_m": t["petits_elements_m"],
            "simplification_3d_m": t["simplification_3d_m"],
            "simplification_emprise_m": t["simplification_emprise_m"],
            "simplification_emprise_affichage_m": t["simplification_emprise_affichage_m"],
            "delai_max_minutes": t["delai_max_minutes"]}


def dossier_cache(cfgm: dict, id_: str) -> Path:
    return _chemin(cfgm["dossier_cache"]) / id_


def _lire_json(p: Path) -> dict | None:
    """Contenu d'un fichier JSON du cache, relu seulement s'il a changé. Ne pas modifier l'objet renvoyé."""
    try:
        st = p.stat()
    except OSError:
        return None
    return _json_fichier(str(p), st.st_mtime_ns, st.st_size)


@lru_cache(maxsize=1024)
def _json_fichier(chemin: str, _mtime: int, _taille: int) -> dict | None:
    try:
        return json.loads(Path(chemin).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def resultat(cfgm: dict, id_: str, cle: str) -> dict | None:
    r = _lire_json(dossier_cache(cfgm, id_) / "resultat.json")
    return r if r and r.get("cle") == cle else None


def erreur(cfgm: dict, id_: str, cle: str) -> dict | None:
    e = _lire_json(dossier_cache(cfgm, id_) / "erreur.json")
    return e if e and e.get("cle") == cle else None


def locaux(cfgm: dict, id_: str, cle: str) -> dict | None:
    """Locaux extraits (clé à jour) : {"locaux": [...], "niveaux": [...]}, ou None."""
    d = _lire_json(dossier_cache(cfgm, id_) / "locaux.json")
    return d if d and d.get("cle") == cle else None


def erreur_locaux(cfgm: dict, id_: str, cle: str) -> dict | None:
    e = _lire_json(dossier_cache(cfgm, id_) / "erreur_locaux.json")
    return e if e and e.get("cle") == cle else None


# ---------------------------------------------------------------- verrou inter-processus

def _processus_actif(pid: int) -> bool:
    """Le processus existe-t-il encore ? (sous Windows, os.kill(pid, 0) le terminerait : API système.)"""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32
        poignee = k32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not poignee:
            return False
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(poignee, ctypes.byref(code))
        k32.CloseHandle(poignee)
        return bool(ok) and code.value == 259          # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def prendre_verrou(dossier: Path) -> bool:
    """Verrou d'une maquette (fichier verrou.json, PID du serveur) : deux serveurs ne traitent jamais la même.

    Un verrou laissé par un processus disparu est repris.
    """
    dossier.mkdir(parents=True, exist_ok=True)
    chemin = dossier / "verrou.json"
    for _ in range(2):
        try:
            fd = os.open(chemin, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            v = _lire_json(chemin) or {}
            if _processus_actif(int(v.get("pid") or 0)):
                return False
            chemin.unlink(missing_ok=True)   # verrou orphelin
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "depuis": time.time()}, f)
        return True
    return False


def rendre_verrou(dossier: Path) -> None:
    v = _lire_json(dossier / "verrou.json") or {}
    if v.get("pid") == os.getpid():
        (dossier / "verrou.json").unlink(missing_ok=True)


def verrou_tiers(dossier: Path) -> bool:
    """Maquette en cours de traitement par un autre serveur."""
    v = _lire_json(dossier / "verrou.json") or {}
    pid = int(v.get("pid") or 0)
    return pid != os.getpid() and _processus_actif(pid)


# ---------------------------------------------------------------- file d'attente des traitements

class Traitements:
    """File des traitements, chacun dans un processus séparé ; « traitements_simultanes » à la fois.

    Locaux seuls d'abord (rapides), puis les maquettes les plus petites.
    """

    def __init__(self):
        self._verrou = threading.Lock()
        self._attente: dict[str, tuple[int, Path, dict, str]] = {}   # id -> (taille, chemin, config, tâche)
        self._courants: set[str] = set()
        self._processus: dict[str, subprocess.Popen] = {}
        self._fils: list[threading.Thread] = []
        self.historique: list[tuple] = []   # (id, tâche, code de sortie ou « verrou »), pour le diagnostic
        self.termines: list[tuple[int, str]] = []   # (n° d'ordre, id) des traitements finis : suivi de l'inventaire
        self._numero = 0

    def assurer(self, cfgm: dict, maquettes: list[tuple[str, str, Path]]) -> None:
        with self._verrou:
            for id_, _rel, chemin in maquettes:
                if id_ in self._courants:
                    continue
                cle = cle_traitement(chemin, cfgm)
                if erreur(cfgm, id_, cle):
                    continue
                if not resultat(cfgm, id_, cle):
                    tache = "complet"
                else:
                    cl = cle_locaux(chemin, cfgm)
                    if locaux(cfgm, id_, cl) or erreur_locaux(cfgm, id_, cl):
                        continue
                    tache = "locaux"   # maquette déjà traitée : locaux seuls, sans refaire la 3D
                self._attente[id_] = (chemin.stat().st_size, chemin, cfgm, tache)
            # chaque fil se désinscrit lui-même sous le verrou quand la file est vide (pas de course à l'arrêt)
            simultanes = int(cfgm["traitement"]["traitements_simultanes"])
            while self._attente and len(self._fils) < simultanes:
                fil = threading.Thread(target=self._boucle, name="traitements-ifc", daemon=True)
                self._fils.append(fil)
                fil.start()

    def relancer(self, cfgm: dict, id_: str, chemin: Path) -> None:
        d = dossier_cache(cfgm, id_)
        for nom in ("resultat.json", "erreur.json", "locaux.json", "erreur_locaux.json"):
            (d / nom).unlink(missing_ok=True)
        self.assurer(cfgm, [(id_, "", chemin)])

    def _ordre(self) -> list[str]:
        return sorted(self._attente, key=lambda k: (self._attente[k][3] != "locaux", self._attente[k][0]))

    def position(self, id_: str) -> int | None:
        with self._verrou:
            ordre = self._ordre()
        return ordre.index(id_) + 1 if id_ in ordre else None

    def en_cours(self) -> list[str]:
        with self._verrou:
            return sorted(self._courants)

    def nb_attente(self) -> int:
        with self._verrou:
            return len(self._attente)

    def occupee(self) -> bool:
        """Traitement en cours ou en attente (lu en une fois : une maquette peut passer de l'un à l'autre)."""
        with self._verrou:
            return bool(self._attente or self._courants)

    def _boucle(self) -> None:
        while True:
            with self._verrou:
                if not self._attente:
                    self._fils.remove(threading.current_thread())
                    return
                id_ = self._ordre()[0]
                _taille, chemin, cfgm, tache = self._attente.pop(id_)
                self._courants.add(id_)
            try:
                self._executer(id_, chemin, cfgm, tache)
            except Exception as e:   # un incident sur une maquette ne doit pas arrêter la file
                self.historique.append((id_, tache, f"{type(e).__name__} : {e}"))
            finally:
                with self._verrou:
                    self._courants.discard(id_)
                    self._numero += 1
                    self.termines.append((self._numero, id_))
                    del self.termines[:-1000]

    def termines_depuis(self, numero: int) -> tuple[int, set[str]]:
        """Maquettes dont le traitement s'est terminé après « numero » ; renvoie aussi le dernier numéro."""
        with self._verrou:
            return self._numero, {i for n, i in self.termines if n > numero}

    def _executer(self, id_: str, chemin: Path, cfgm: dict, tache: str = "complet") -> None:
        sortie = dossier_cache(cfgm, id_)
        if not prendre_verrou(sortie):
            self.historique.append((id_, tache, "verrou"))
            return   # traitée par un autre serveur : reprise au prochain inventaire si nécessaire
        try:
            self._executer_verrouille(id_, chemin, cfgm, tache, sortie)
        finally:
            rendre_verrou(sortie)

    def _executer_verrouille(self, id_, chemin, cfgm, tache, sortie) -> None:
        a_effacer = (("erreur_locaux.json",) if tache == "locaux"
                     else ("resultat.json", "erreur.json", "trace.txt", "erreur_locaux.json"))
        for nom in a_effacer:
            (sortie / nom).unlink(missing_ok=True)
        params = sortie / "parametres.json"
        params.write_text(json.dumps(parametres_traitement(chemin, cfgm, tache)), encoding="utf-8")
        commande = [sys.executable, "-m", "serveur.sig.travail_ifc", str(chemin), str(sortie), str(params)]
        delai = float(cfgm["traitement"]["delai_max_minutes"]) * 60
        journal = (sortie / "journal.txt").open("w", encoding="utf-8", errors="replace")
        try:
            p = subprocess.Popen(commande, cwd=RACINE, stdout=journal, stderr=subprocess.STDOUT,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self._processus[id_] = p
            try:
                # le travailleur applique lui-même le délai à la conversion 3D ; marge pour sa propre lecture
                code = p.wait(timeout=delai + 120)
            except subprocess.TimeoutExpired:
                arreter_processus(p)
                code = None
                message = (f"traitement interrompu après {cfgm['traitement']['delai_max_minutes']} minutes "
                           "(délai « delai_max_minutes »)")
        except OSError as e:
            code, message = None, f"lancement du traitement impossible ({e})"
        finally:
            journal.close()
            self._processus.pop(id_, None)
        self.historique.append((id_, tache, code))
        fichier_erreur, cle_erreur = (("erreur_locaux.json", cle_locaux(chemin, cfgm)) if tache == "locaux"
                                      else ("erreur.json", cle_traitement(chemin, cfgm)))
        if code != 0 and not (sortie / fichier_erreur).is_file():
            if code is not None:
                message = f"le traitement s'est arrêté brutalement (code {code}) : mémoire insuffisante ?"
            (sortie / fichier_erreur).write_text(json.dumps({"cle": cle_erreur, "message": message}, ensure_ascii=False),
                                                 encoding="utf-8")
        (sortie / "etat.json").unlink(missing_ok=True)

    def arreter(self) -> None:
        with self._verrou:
            self._attente.clear()
        for p in list(self._processus.values()):
            arreter_processus(p)


traitements = Traitements()


# ---------------------------------------------------------------- altimétrie

def altitude_sol(cfg: dict, cfgm: dict, lon: float, lat: float, cache: Path) -> tuple[float | None, str | None]:
    """Altitude du terrain (m NGF) au point donné, mise en cache ; (None, message) si indisponible."""
    alti = cfgm.get("altimetrie")
    if not alti:
        return None, "altimétrie non configurée"
    cle = f"{lon:.5f},{lat:.5f},{alti.get('ressource')}"
    connu = _lire_json(cache)
    if connu and connu.get("cle") == cle:
        return connu.get("altitude"), connu.get("message")
    altitude, message = None, None
    try:
        with fabrique_client(cfg) as c:
            r = c.get(alti["url"], params={"lon": f"{lon:.7f}", "lat": f"{lat:.7f}",
                                           "resource": alti.get("ressource", ""), "zonly": "true"})
        r.raise_for_status()
        z = (r.json().get("elevations") or [None])[0]
        if isinstance(z, (int, float)) and z > -1000:
            altitude = float(z)
        else:
            message = "altitude du terrain inconnue à cet endroit"
    except httpx.HTTPError as e:
        message = f"service d'altimétrie {reseau.message_erreur(e)}"
        return None, message   # pas de cache : nouvel essai au prochain affichage
    except ValueError:
        message = "réponse du service d'altimétrie illisible"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"cle": cle, "altitude": altitude, "message": message}), encoding="utf-8")
    return altitude, message


def fabrique_client(cfg: dict) -> httpx.Client:
    """Client HTTP de l'altimétrie (remplacé dans les tests)."""
    return reseau.client(cfg)


# ---------------------------------------------------------------- positionnement

def _systeme_declare(cfgm: dict, rel: str) -> tuple[str | None, str | None]:
    for d in cfgm["systemes_declares"]:
        if fnmatch.fnmatch(rel, d["motif"]) or fnmatch.fnmatch(rel.lower(), d["motif"].lower()):
            return d["systeme"], d["motif"]
    return None, None


def _saisie(cfgm: dict, rel: str) -> dict | None:
    for m in cfgm["maquettes"]:
        if m["fichier"].replace("\\", "/").strip("/").lower() == rel.lower():
            return m
    return None


def _geojson_vers_wgs84(geom: dict, repere: dict, vers_wgs84: Transformer) -> dict:
    """Polygone(s) du repère local vers WGS84 : tous les sommets convertis en un seul appel (des milliers
    d'anneaux par maquette), puis redécoupés en anneaux."""
    cos, sin = math.cos(math.radians(repere["rotation_deg"])), math.sin(math.radians(repere["rotation_deg"]))
    ech = repere["echelle"]
    ox, oy, _ = repere["origine"]
    polygones = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
    anneaux = [a for p in polygones for a in p]
    if not anneaux:
        return {"type": geom["type"], "coordinates": []}
    xy = np.concatenate([np.asarray(a, dtype=float)[:, :2] for a in anneaux])
    # même calcul que georef.vers_projete
    px = ox + ech * (cos * xy[:, 0] - sin * xy[:, 1])
    py = oy + ech * (sin * xy[:, 0] + cos * xy[:, 1])
    lon, lat = vers_wgs84.transform(px, py)
    tous = np.round(np.column_stack([lon, lat]), 8).tolist()
    sortie, i = [], 0
    for p in polygones:
        poly = []
        for a in p:
            poly.append(tous[i:i + len(a)])
            i += len(a)
        sortie.append(poly)
    return {"type": "Polygon", "coordinates": sortie[0]} if geom["type"] == "Polygon"         else {"type": "MultiPolygon", "coordinates": sortie}


def _emprise(geom: dict) -> list[float]:
    pts = [p for poly in ([geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"])
           for a in poly for p in a]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return [min(xs), min(ys), max(xs), max(ys)]


def _emprise_des_locaux(donnees_locaux: dict | None) -> tuple[dict | None, float | None, float | None]:
    """Emprise (union des locaux) et altitudes, pour une maquette qui ne contient que des locaux."""
    if not donnees_locaux:
        return None, None, None
    import shapely
    from shapely.geometry import mapping, shape
    formes = [shape(l["emprise_locale"]) for l in donnees_locaux["locaux"] if l.get("emprise_locale")]
    if not formes:
        return None, None, None
    zs = [l[k] for l in donnees_locaux["locaux"] for k in ("z_min", "z_max") if l.get(k) is not None]
    return mapping(shapely.union_all(formes).simplify(0.05)), min(zs), max(zs)


def positionner(cfg: dict, cfgm: dict, id_: str, rel: str, res: dict, donnees_locaux: dict | None = None) -> dict:
    """Positionnement complet d'une maquette traitée : méthode, emprise WGS84, calage vertical, affichage 3D."""
    ident = cfgm["identification"]
    declare, motif = _systeme_declare(cfgm, rel)
    saisie = _saisie(cfgm, rel)
    manuel = saisie if saisie and "x" in saisie else None
    centre = None
    if res["geometrie"].get("emprise_locale"):
        x1, y1, x2, y2 = _emprise(res["geometrie"]["emprise_locale"])
        centre = ((x1 + x2) / 2, (y1 + y2) / 2)
    pos = georef.resoudre(res["georef_brut"], ident["systemes_candidats"], ident["tolerance_m"], declare,
                          ident.get("emprise_plausible_wgs84"), manuel, centre_geometrie=centre,
                          ecart_max_geometrie_m=ident.get("ecart_max_geometrie_m"))
    if motif and pos.get("systeme_source", "").startswith("déclaré"):
        pos["systeme_source"] = f"déclaré (configuration, motif « {motif} »)"
    geo = dict(res["geometrie"])
    if pos["statut"] != "positionnee":
        return pos
    if not geo.get("emprise_locale"):
        # maquette de locaux seuls (IfcSpace, exclus du modèle 3D) : emprise et altitudes tirées des locaux
        emprise, zmin, zmax = _emprise_des_locaux(donnees_locaux)
        if emprise is None:
            pos.update(statut="non_positionnee", raison="aucune géométrie exploitable dans la maquette"
                       + ("" if donnees_locaux is not None else " (locaux en cours d'extraction)"))
            return pos
        geo.update(emprise_locale=emprise, z_min=zmin, z_max=zmax)
        pos["locaux_seuls"] = True

    repere = pos["repere"]
    vers_wgs84 = georef.transformateur(pos["systeme"], 4326)
    vers_l93 = georef.transformateur(pos["systeme"], 2154)
    emprise_wgs84 = _geojson_vers_wgs84(geo["emprise_locale"], repere, vers_wgs84)
    emprise_affichage = (_geojson_vers_wgs84(geo["emprise_affichage_locale"], repere, vers_wgs84)
                         if geo.get("emprise_affichage_locale") else emprise_wgs84)
    bbox = _emprise(emprise_wgs84)
    if not all(math.isfinite(v) for v in bbox):
        pos.update(statut="non_positionnee", raison="conversion des coordonnées impossible")
        return pos

    def lonlat(x, y):
        px, py, _ = georef.vers_projete(repere, x, y)
        return [round(v, 9) for v in vers_wgs84.transform(px, py)]

    ex = geo["emprise_locale"]
    bl = _emprise(ex)
    cx, cy = (bl[0] + bl[2]) / 2, (bl[1] + bl[3]) / 2
    centre = lonlat(cx, cy)
    px, py, _ = georef.vers_projete(repere, cx, cy)
    centre_l93 = [round(v, 2) for v in vers_l93.transform(px, py)]

    # Calage vertical par rapport au terrain
    oz = repere["origine"][2]
    zmin, zmax = geo["z_min"] or 0.0, geo["z_max"] or 0.0
    sol, msg_sol = altitude_sol(cfg, cfgm, centre[0], centre[1], dossier_cache(cfgm, id_) / "sol.json")
    ecart_max = (cfgm.get("altimetrie") or {}).get("ecart_max_terrain_m", 80)
    milieu_abs, milieu_local = oz + (zmin + zmax) / 2, (zmin + zmax) / 2
    if sol is None:
        decalage_z = -zmin
        calage = "base posée au sol"
        pos["avertissements"].append(f"altitude non calée sur le terrain ({msg_sol}) : base de la maquette posée au sol")
    elif abs(milieu_abs - sol) <= ecart_max:
        decalage_z, calage = oz - sol, "altitudes du fichier, terrain IGN"
    elif abs(milieu_local - sol) <= ecart_max:
        decalage_z, calage = -sol, "altitudes des objets (altitude du site ignorée), terrain IGN"
        pos["avertissements"].append(
            f"altitude du site ({oz:.2f} m) ignorée : les objets portent déjà des altitudes NGF (sinon ils seraient "
            f"{milieu_abs - sol:.0f} m au-dessus du terrain)")
    else:
        decalage_z, calage = oz - sol, "altitudes du fichier, incohérentes avec le terrain"
        pos["avertissements"].append(f"altitude incohérente avec le terrain : écart de {milieu_abs - sol:.0f} m à vérifier")
    if saisie and saisie.get("decalage_vertical_m"):
        decalage_z += float(saisie["decalage_vertical_m"])
        calage += f", décalage manuel {float(saisie['decalage_vertical_m']):+.2f} m"

    taille = geo.get("taille_glb") or 0
    taille_detail = geo.get("taille_detail_glb") or 0
    pos.update({
        "emprise_wgs84": [round(v, 8) for v in bbox],
        "emprise_geojson": emprise_wgs84,          # exacte : interrogation croisée
        "emprise_affichage": emprise_affichage,    # simplifiée : dessin sur la carte
        "centre_wgs84": centre,
        "centre_l93": centre_l93,
        "altitude_terrain": sol,
        "calage_vertical": calage,
        "hauteurs_affichees": [round(zmin + decalage_z, 2), round(zmax + decalage_z, 2)],
        "affichage": {
            "origine": lonlat(0, 0),
            "axe_x": lonlat(PAS_CONTROLE_M, 0),
            "axe_y": lonlat(0, PAS_CONTROLE_M),
            "pas_m": PAS_CONTROLE_M,
            "decalage_z_m": round(decalage_z, 3),
            # origine des modèles 3D dans le repère local ci-dessus (x, y, z ; z vertical)
            "origine_modele": geo.get("origine_glb") or [0.0, 0.0, 0.0],
            "taille_mo": round(taille / 1e6, 1),
            "taille_detail_mo": round(taille_detail / 1e6, 1),
            "detail": taille_detail > 0,
            "triangles": geo.get("triangles") or 0,
            "triangles_detail": geo.get("triangles_detail") or 0,
            "chargement_automatique": taille / 1e6 <= cfgm["affichage_3d"]["chargement_automatique_max_mo"],
        },
    })
    return pos


# ---------------------------------------------------------------- vue d'ensemble

def etat_locaux(cfgm: dict, id_: str, chemin: Path) -> dict:
    cl = cle_locaux(chemin, cfgm)
    d = locaux(cfgm, id_, cl)
    if d is not None:
        return {"phase": "pret", "nb": len(d["locaux"]), "niveaux": len(d["niveaux"])}
    e = erreur_locaux(cfgm, id_, cl)
    if e:
        return {"phase": "erreur", "message": e["message"]}
    if id_ in traitements.en_cours():
        return {"phase": "en_cours"}
    return {"phase": "en_attente"}


def etat_maquette(cfgm: dict, id_: str, cle: str) -> dict:
    if resultat(cfgm, id_, cle):
        return {"phase": "pret"}
    e = erreur(cfgm, id_, cle)
    if e:
        return {"phase": "erreur", "message": e["message"]}
    if id_ in traitements.en_cours() or verrou_tiers(dossier_cache(cfgm, id_)):
        en_cours = _lire_json(dossier_cache(cfgm, id_) / "etat.json") or {}
        return {"phase": "en_cours", "etape": en_cours.get("phase", "démarrage"),
                "progression": en_cours.get("progression", 0), "message": en_cours.get("message", "démarrage"),
                "depuis_secondes": round(time.time() - en_cours["debut"]) if en_cours.get("debut") else None}
    return {"phase": "en_attente", "position": traitements.position(id_)}


def decrire(cfg: dict, cfgm: dict, id_: str, rel: str, chemin: Path, complet: bool = True) -> dict:
    """Description d'une maquette : état, métadonnées, positionnement.

    complet=False : ligne de la liste (des milliers de maquettes), sans métadonnées ni données lourdes du
    positionnement (repère, emprises exactes) ; la fiche demande la description complète.
    """
    cle = cle_traitement(chemin, cfgm)
    res = resultat(cfgm, id_, cle)
    m = {"id": id_, "fichier": rel, "nom": chemin.stem, "dossier": str(Path(rel).parent.as_posix()),
         "taille_mo": round(chemin.stat().st_size / 1e6, 1), "etat": etat_maquette(cfgm, id_, cle)}
    if not complet:
        m["etat"].pop("position", None)   # change à chaque maquette traitée : non suivi dans la liste
    if res:
        m["locaux"] = etat_locaux(cfgm, id_, chemin)
        m["nb_objets"] = res["geometrie"]["nb_elements"]
        if complet:
            m["metadonnees"] = res["metadonnees"]
            m["nb_objets_attendus"] = res["geometrie"].get("nb_elements_attendus")
            m["non_convertis"] = res["geometrie"].get("non_convertis") or {}
            m["moteur_3d"] = res["geometrie"].get("moteur")
            m["duree_traitement_s"] = res.get("duree_secondes")
        try:
            d_locaux = locaux(cfgm, id_, cle_locaux(chemin, cfgm))
            m["positionnement"] = (positionner(cfg, cfgm, id_, rel, res, d_locaux) if complet
                                   else _positionner_memo(cfg, cfgm, id_, rel, res, d_locaux))
        except Exception as e:  # garde-fou : une maquette ne doit pas interrompre l'inventaire
            m["positionnement"] = {"statut": "non_positionnee", "methode": None, "libelle": "Non positionnée",
                                   "raison": f"erreur de positionnement ({type(e).__name__} : {e})",
                                   "avertissements": []}
    return m


_MEMO_POSITIONNEMENT: dict[str, tuple] = {}   # id -> (clé, cfg, cfgm, positionnement allégé, emprise d'affichage)
# Données lourdes du positionnement : jamais gardées en mémoire pour toutes les maquettes (des milliers) ;
# l'emprise exacte est recalculée à la demande (fiche, interrogation croisée), l'emprise d'affichage est servie par zone.
CLES_LOURDES = ("emprise_geojson", "emprise_affichage", "repere", "ecarts_latlon_m")


def alleger(pos: dict) -> dict:
    return {k: v for k, v in pos.items() if k not in CLES_LOURDES}


def _positionner_memo(cfg: dict, cfgm: dict, id_: str, rel: str, res: dict, donnees_locaux: dict | None) -> dict:
    """positionner() allégé, recalculé seulement si la maquette, ses locaux ou la configuration changent.

    L'inventaire est redemandé régulièrement par l'interface : sans cela, chaque appel reconvertirait toutes les
    emprises. Un positionnement sans altitude du terrain (service indisponible) n'est pas gardé : nouvel essai.
    """
    cle = (rel, res["cle"], donnees_locaux is not None)
    memo = _MEMO_POSITIONNEMENT.get(id_)
    if memo and memo[0] == cle and memo[1] is cfg and memo[2] is cfgm:
        return dict(memo[3])
    pos = positionner(cfg, cfgm, id_, rel, res, donnees_locaux)
    leger = alleger(pos)
    if pos.get("statut") != "positionnee" or pos.get("altitude_terrain") is not None or not cfgm.get("altimetrie"):
        g = pos.get("emprise_affichage")
        _MEMO_POSITIONNEMENT[id_] = (cle, cfg, cfgm, leger,
                                     json.dumps(g, separators=(",", ":")) if g else None)
    return dict(leger)


def emprise_affichage(id_: str) -> str | None:
    """Emprise simplifiée (texte GeoJSON WGS84) d'une maquette décrite par l'inventaire, ou None."""
    memo = _MEMO_POSITIONNEMENT.get(id_)
    return memo[4] if memo else None


def une_maquette(cfg: dict, cfgm: dict, id_: str) -> dict | None:
    trouve = chemin_maquette(cfgm, id_)
    return decrire(cfg, cfgm, id_, *trouve) if trouve else None


def locaux_affichables(cfg: dict, cfgm: dict, m: dict) -> dict | None:
    """Locaux d'une maquette décrite, avec emprises WGS84 / Lambert 93 et hauteurs par rapport au terrain.

    None si les locaux ne sont pas encore extraits. Sans positionnement, la liste est donnée sans géographie.
    """
    trouve = chemin_maquette(cfgm, m["id"])
    if trouve is None:
        return None
    d = locaux(cfgm, m["id"], cle_locaux(trouve[1], cfgm))
    if d is None:
        return None
    pos = m.get("positionnement") or {}
    positionnee = pos.get("statut") == "positionnee"
    dz = pos["affichage"]["decalage_z_m"] if positionnee else 0.0
    if positionnee:
        repere = pos["repere"]
        vers_wgs84 = georef.transformateur(pos["systeme"], 4326)
        vers_l93 = georef.transformateur(pos["systeme"], 2154)
    sortie = []
    for l in d["locaux"]:
        e = {k: v for k, v in l.items()}
        if l["z_min"] is not None:
            e["hauteurs"] = [round(l["z_min"] + dz, 2), round(l["z_max"] + dz, 2)]
        if positionnee and l.get("emprise_locale"):
            e["emprise_wgs84_geojson"] = _geojson_vers_wgs84(l["emprise_locale"], repere, vers_wgs84)
            b = _emprise(l["emprise_locale"])
            cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            px, py, _ = georef.vers_projete(repere, cx, cy)
            e["centre_wgs84"] = [round(v, 8) for v in vers_wgs84.transform(px, py)]
            e["x_l93"], e["y_l93"] = (round(v, 2) for v in vers_l93.transform(px, py))
            e["emprise_wgs84"] = [round(v, 8) for v in _emprise(e["emprise_wgs84_geojson"])]
        sortie.append(e)
    niveaux = [{**n, "hauteurs": [round(n["z_min"] + dz, 2), round(n["z_max"] + dz, 2)] if n["z_min"] is not None else None}
               for n in d["niveaux"]]
    return {"maquette": m["id"], "positionnee": positionnee, "niveaux": niveaux, "locaux": sortie}


_VERROUS_ETAGES: dict[str, threading.Lock] = {}


def _extraire_etages(cfgm: dict, id_: str, chemin: Path, delai_s: float) -> str | None:
    """Lance l'extraction des étages (processus séparé, comme les traitements) et l'attend ; message si échec."""
    sortie = dossier_cache(cfgm, id_)
    params = sortie / "parametres_etages.json"
    params.write_text(json.dumps(parametres_traitement(chemin, cfgm, "etages")), encoding="utf-8")
    commande = [sys.executable, "-m", "serveur.sig.travail_ifc", str(chemin), str(sortie), str(params)]
    try:
        with (sortie / "journal_etages.txt").open("w", encoding="utf-8", errors="replace") as journal:
            p = subprocess.Popen(commande, cwd=RACINE, stdout=journal, stderr=subprocess.STDOUT,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                code = p.wait(timeout=delai_s)
            except subprocess.TimeoutExpired:
                arreter_processus(p)
                return f"lecture des étages interrompue après {delai_s:.0f} s (« contexte.delai_etages_s »)"
    except OSError as e:
        return f"lecture des étages impossible ({e})"
    return None if code == 0 else "lecture des étages en échec (voir journal_etages.txt dans le cache)"


def niveaux_ngf(cfgm: dict, m: dict, delai_s: float = 0) -> dict:
    """Niveaux d'une maquette positionnée en altitude NGF (terrain IGN + hauteur affichée, comme le point bas).

    Source : étages IFC (IfcBuildingStorey, altitude de leur placement), extraits à la demande s'ils ne sont pas
    en cache (delai_s > 0) ; à défaut, niveaux des locaux (altitude basse des IfcSpace de chaque niveau) ; à défaut,
    points bas et haut du modèle 3D (tunnels, ouvrages sans étages).
    """
    pos = m.get("positionnement") or {}
    sortie = {"source": None, "niveaux": [], "message": None}
    trouve = chemin_maquette(cfgm, m["id"])
    if pos.get("statut") != "positionnee" or trouve is None:
        sortie["message"] = "maquette non positionnée"
        return sortie
    sol, dz = pos.get("altitude_terrain"), pos["affichage"]["decalage_z_m"]
    if sol is None:
        sortie["message"] = "altitude du terrain inconnue : niveaux non convertis en NGF"
        return sortie
    cle = cle_etages(trouve[1], cfgm)
    fichier = dossier_cache(cfgm, m["id"]) / "etages.json"
    d = _lire_json(fichier)
    if (not d or d.get("cle") != cle) and delai_s > 0:
        with _VERROUS_ETAGES.setdefault(m["id"], threading.Lock()):
            d = _lire_json(fichier)
            if not d or d.get("cle") != cle:
                sortie["message"] = _extraire_etages(cfgm, m["id"], trouve[1], delai_s)
                d = _lire_json(fichier)

    def niveau(nom, z, source):
        h = round(z + dz, 2)
        return {"nom": nom, "source": source, "hauteur_m": h, "altitude_ngf": round(sol + h, 2)}

    if d and d.get("cle") == cle:
        etages = [e for e in d["etages"] if e["z"] is not None]
        if etages:
            sortie.update(source="étages IFC", niveaux=[niveau(e["nom"] or e["nom_long"] or "étage sans nom", e["z"], e["z_source"])
                                                        for e in etages])
            return sortie
    dl = locaux(cfgm, m["id"], cle_locaux(trouve[1], cfgm))
    if dl and any(n["z_min"] is not None for n in dl["niveaux"]):
        sortie.update(source="locaux", niveaux=[niveau(n["niveau"], n["z_min"], "bas des locaux")
                                                for n in dl["niveaux"] if n["z_min"] is not None])
        return sortie
    h = pos.get("hauteurs_affichees")
    if h:   # ouvrage sans étages (tunnel, ouvrage d'art) : points bas et haut du modèle 3D
        sortie.update(source="modèle 3D", niveaux=[
            {"nom": "Point bas de la maquette", "source": "modèle 3D", "hauteur_m": h[0], "altitude_ngf": round(sol + h[0], 2)},
            {"nom": "Point haut de la maquette", "source": "modèle 3D", "hauteur_m": h[1], "altitude_ngf": round(sol + h[1], 2)}])
        sortie["message"] = sortie["message"] or "ni étage ni local situé : points bas et haut du modèle 3D"
        return sortie
    sortie["message"] = sortie["message"] or "ni étage ni local situé dans la maquette"
    return sortie


def inventaire(cfg: dict, cfgm: dict) -> dict:
    liste = fichiers(cfgm)
    traitements.assurer(cfgm, liste)
    maquettes = [decrire(cfg, cfgm, id_, rel, chemin) for id_, rel, chemin in liste]
    return {
        "dossier": cfgm["dossier_ifc"],
        "zoom_chargement_automatique": cfgm["affichage_3d"]["zoom_chargement_automatique"],
        "maquettes": maquettes,
        "traitements_en_cours": traitements.en_cours(),
        "zoom_details": cfgm["affichage_3d"]["zoom_details"],
        "budget_triangles": cfgm["affichage_3d"]["budget_triangles_millions"] * 1e6,
        "en_attente": traitements.nb_attente(),
    }


LIGNE = "ligne.json"     # ligne de la liste enregistrée dans le cache de chaque maquette
VERSION_LIGNE = "1"      # à changer si le contenu d'une ligne change (les lignes enregistrées sont recalculées)


class SuiviInventaire:
    """Liste allégée des maquettes, tenue à jour par différence (prévue pour des milliers de maquettes).

    - balayage complet du dossier (nouvelles maquettes, fichiers modifiés) au plus toutes les
      « inventaire.balayage_secondes », ou sur demande ; une maquette n'est redécrite que si son fichier ou les
      fichiers JSON de son cache ont changé ;
    - entre deux balayages, seules les maquettes en cours de traitement ou qui viennent de finir sont redécrites ;
    - chaque ligne porte un numéro de version : l'interface ne reçoit que ce qui a changé depuis son dernier appel.
    """

    def __init__(self, file: "Traitements"):
        self.file = file
        self._verrou = threading.Lock()
        self.session = hashlib.sha1(f"{time.time()}-{id(self)}".encode()).hexdigest()[:8]
        self.version = 0
        self.lignes: dict[str, dict] = {}
        self._versions: dict[str, int] = {}
        self._signatures: dict[str, tuple] = {}
        self._fichiers: dict[str, tuple[str, Path]] = {}
        self._supprimees: dict[str, int] = {}
        self._balayage = -math.inf
        self._configs: tuple = (None, None)
        self._termine = 0
        self._emprises: dict[str, str | None] = {}   # id -> emprise d'affichage (texte GeoJSON)
        self._empreinte_config = ""

    @staticmethod
    def _signature(cfgm: dict, id_: str, chemin: Path) -> tuple:
        try:
            st = chemin.stat()
        except OSError:
            return ()
        try:   # un seul parcours du dossier de cache (sous Windows, les dates viennent avec la liste)
            with os.scandir(dossier_cache(cfgm, id_)) as it:
                cache = tuple(sorted((e.name, e.stat().st_mtime_ns) for e in it
                                     if e.name.endswith(".json") and e.name not in (LIGNE, "types.json")))
        except OSError:
            cache = ()
        return st.st_size, st.st_mtime_ns, cache

    def _actives(self) -> set[str]:
        return {i for i, l in self.lignes.items()
                if l["etat"]["phase"] == "en_cours" or (l.get("locaux") or {}).get("phase") == "en_cours"}

    def actualiser(self, cfg: dict, cfgm: dict, forcer: bool = False) -> None:
        with self._verrou:
            maintenant = time.monotonic()
            delai = float((cfgm.get("inventaire") or {}).get("balayage_secondes", 30))
            nouvelle_config = self._configs[0] is not cfg or self._configs[1] is not cfgm
            complet = forcer or nouvelle_config or maintenant - self._balayage >= delai
            if nouvelle_config:   # déclarations, saisies manuelles… : toutes les lignes sont redécrites
                self._signatures.clear()
                self._empreinte_config = hashlib.sha1(json.dumps(
                    [VERSION_LIGNE, VERSION_MODULE, cfg, {k: v for k, v in cfgm.items() if not k.startswith("_")}],
                    sort_keys=True, default=str).encode("utf-8")).hexdigest()
            self._termine, termines = self.file.termines_depuis(self._termine)
            actives = self._actives() | set(self.file.en_cours()) | termines
            if complet:
                self._fichiers = {i: (rel, chemin) for i, rel, chemin in fichiers(cfgm)}
                for i in set(self.lignes) - set(self._fichiers):
                    self.version += 1
                    self._supprimees[i] = self.version
                    for d in (self.lignes, self._versions, self._signatures):
                        d.pop(i, None)
                candidats = list(self._fichiers)
                self._balayage, self._configs = maintenant, (cfg, cfgm)
            else:
                candidats = [i for i in actives if i in self._fichiers]
            changees = []
            for i in candidats:
                sig = self._signature(cfgm, i, self._fichiers[i][1])
                if i in actives or self._signatures.get(i) != sig or i not in self.lignes:
                    changees.append((i, sig))
            if complet and changees:
                self.file.assurer(cfgm, [(i, *self._fichiers[i]) for i, _ in changees])
            for i, sig in changees:
                rel, chemin = self._fichiers[i]
                ligne, emprise = self._decrire(cfg, cfgm, i, rel, chemin, sig, i in actives)
                self._emprises[i] = emprise
                self._signatures[i] = sig
                if ligne != self.lignes.get(i):
                    self.version += 1
                    self.lignes[i] = ligne
                    self._versions[i] = self.version

    def _decrire(self, cfg, cfgm, id_, rel, chemin, sig, active) -> tuple[dict, str | None]:
        """Ligne de la liste et emprise d'affichage ; relues sur disque (ligne.json du cache) si ni la maquette
        ni la configuration n'ont changé : au redémarrage, des milliers de maquettes ne sont pas repositionnées."""
        fichier = dossier_cache(cfgm, id_) / LIGNE
        cle = hashlib.sha1(json.dumps([rel, sig, self._empreinte_config], default=str).encode("utf-8")).hexdigest()
        if not active:
            enr = _lire_json(fichier)
            if enr and enr.get("cle") == cle:
                return enr["ligne"], enr.get("emprise")
        ligne = decrire(cfg, cfgm, id_, rel, chemin, complet=False)
        emprise = emprise_affichage(id_)
        pos = ligne.get("positionnement") or {}
        stable = (ligne["etat"]["phase"] in ("pret", "erreur")
                  and (ligne.get("locaux") or {}).get("phase", "pret") in ("pret", "erreur")
                  and not (pos.get("statut") == "positionnee" and pos.get("altitude_terrain") is None
                           and cfgm.get("altimetrie")))   # sans altitude du terrain : nouvel essai plus tard
        if stable and fichier.parent.is_dir():
            try:
                fichier.write_text(json.dumps({"cle": cle, "ligne": ligne, "emprise": emprise}, ensure_ascii=False,
                                              separators=(",", ":")), encoding="utf-8")
            except OSError:
                pass   # cache en lecture seule : la ligne sera recalculée au prochain démarrage
        return ligne, emprise

    def oublier_balayage(self) -> None:
        """Le prochain appel refera un balayage complet (maquette relancée…)."""
        self._balayage = -math.inf

    def depuis(self, cfgm: dict, jeton: str | None) -> dict:
        """Lignes modifiées depuis le jeton « session:version » (toutes si le jeton est absent ou vient d'une autre
        session du serveur), maquettes supprimées, jeton suivant et paramètres d'affichage."""
        with self._verrou:
            version = None
            if jeton and jeton.startswith(f"{self.session}:"):
                try:
                    version = int(jeton.split(":", 1)[1])
                except ValueError:
                    version = None
            complet = version is None
            ids = list(self.lignes) if complet else [i for i, v in self._versions.items() if v > version]
            a3d = cfgm["affichage_3d"]
            return {
                "jeton": f"{self.session}:{self.version}",
                "complet": complet,
                "maquettes": [self.lignes[i] for i in sorted(ids, key=lambda i: self.lignes[i]["fichier"].lower())],
                "supprimees": [] if complet else [i for i, v in self._supprimees.items() if v > version],
                "total": len(self.lignes),
                "dossier": cfgm["dossier_ifc"],
                "zoom_chargement_automatique": a3d["zoom_chargement_automatique"],
                "zoom_details": a3d["zoom_details"],
                "zoom_emprises": a3d["zoom_emprises"],
                "budget_triangles": a3d["budget_triangles_millions"] * 1e6,
                "rendu": a3d["rendu"],
                "traitements_en_cours": self.file.en_cours(),
                "en_attente": self.file.nb_attente(),
            }

    def dans_emprise(self, bbox: list[float]) -> list[dict]:
        """Lignes des maquettes positionnées dont l'emprise (rectangle WGS84) touche bbox [ouest, sud, est, nord]."""
        o, s, e, n = bbox
        with self._verrou:
            lignes = list(self.lignes.values())
        sortie = []
        for m in lignes:
            pos = m.get("positionnement") or {}
            if pos.get("statut") != "positionnee":
                continue
            x1, y1, x2, y2 = pos["emprise_wgs84"]
            if x2 >= o and x1 <= e and y2 >= s and y1 <= n:
                sortie.append(m)
        return sortie


suivi = SuiviInventaire(traitements)


def emprises_zone(cfgm: dict, bbox: list[float]) -> str:
    """Emprises simplifiées des maquettes positionnées de la zone (GeoJSON WGS84), les plus proches du centre
    d'abord, au plus « affichage_3d.max_emprises » ; « tronque » si la zone en contient davantage."""
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2

    def eloignement(m):
        x, y = m["positionnement"]["centre_wgs84"]
        return (x - cx) ** 2 + (y - cy) ** 2

    lignes = sorted(suivi.dans_emprise(bbox), key=eloignement)
    maxi = int(cfgm["affichage_3d"]["max_emprises"])
    features = []
    for m in lignes[:maxi]:
        g = suivi._emprises.get(m["id"])
        if g:
            features.append('{"type":"Feature","geometry":%s,"properties":{"id":%s}}' % (g, json.dumps(m["id"])))
    # texte JSON assemblé directement : les emprises sont gardées sérialisées
    return ('{"type":"FeatureCollection","features":[%s],"tronque":%s,"total":%d}'
            % (",".join(features), "true" if len(lignes) > maxi else "false", len(lignes)))


def emprises(inv: dict) -> dict:
    """Emprises au sol exactes des maquettes positionnées d'un inventaire complet (GeoJSON WGS84)."""
    features = []
    for m in inv["maquettes"]:
        pos = m.get("positionnement") or {}
        if pos.get("statut") == "positionnee":
            features.append({"type": "Feature", "id": len(features), "geometry": pos["emprise_geojson"],
                             "properties": {"id": m["id"], "nom": m["nom"], "methode": pos["methode"],
                                            "precision": pos.get("precision")}})
    return {"type": "FeatureCollection", "features": features}


def chemin_maquette(cfgm: dict, id_: str) -> tuple[str, Path] | None:
    connu = suivi._fichiers.get(id_)
    if connu and connu[1].is_file():
        return connu
    for i, rel, chemin in fichiers(cfgm):
        if i == id_:
            return rel, chemin
    return None


# ---------------------------------------------------------------- types et familles des objets (filtres 3D)

VERSION_TYPES = "1"


def _domaine_sgp(fiche_json: str | None) -> str | None:
    try:
        psets = json.loads(fiche_json or "{}").get("psets") or {}
    except ValueError:
        return None
    for valeurs in psets.values():
        if isinstance(valeurs, dict) and valeurs.get("SGP_DI_Domaine"):
            return str(valeurs["SGP_DI_Domaine"])
    return None


def types_objets(cfgm: dict, id_: str, rel: str, chemin: Path) -> dict:
    """Type IFC et famille de chaque objet du modèle 3D, dans l'ordre de sa table d'objets (geometrie.json,
    commune au modèle et aux petits éléments) : {"types": [...], "objets": [indice de type...],
    "familles": [{"nom", "couleur", "nb"}...], "famille": [indice de famille ou -1...]}.

    Famille : domaine SGP d'abord (Pset SGP_DI_Domaine), puis type IFC, puis motif du fichier. Mis en cache
    (types.json) tant que la maquette et la déclaration des familles ne changent pas.
    """
    import sqlite3

    dossier = dossier_cache(cfgm, id_)
    familles = cfgm.get("familles_objets") or []
    cle = hashlib.sha1(json.dumps([cle_traitement(chemin, cfgm), familles, VERSION_TYPES], sort_keys=True)
                       .encode("utf-8")).hexdigest()
    enr = _lire_json(dossier / "types.json")
    if enr and enr.get("cle") == cle:
        return enr["donnees"]
    geo = _lire_json(dossier / "geometrie.json") or {}
    guids = geo.get("guids") or []
    avec_domaines = any(f.get("domaines_sgp") for f in familles)
    infos: dict[str, tuple[str, str | None]] = {}
    base = dossier / "proprietes.sqlite"
    if base.is_file():
        con = sqlite3.connect(f"file:{base}?mode=ro", uri=True)
        try:
            requete = "SELECT guid, type_ifc, fiche FROM objets" if avec_domaines else "SELECT guid, type_ifc, NULL FROM objets"
            for guid, type_ifc, fiche in con.execute(requete):
                infos[guid] = (type_ifc or "?", _domaine_sgp(fiche) if fiche else None)
        finally:
            con.close()
    par_fichier = next((i for i, f in enumerate(familles)
                        if any(fnmatch.fnmatch(rel.lower(), m.lower()) for m in f.get("fichiers") or [])), -1)
    par_type = {t: i for i, f in reversed(list(enumerate(familles))) for t in f.get("types_ifc") or []}
    par_domaine = {d: i for i, f in reversed(list(enumerate(familles))) for d in f.get("domaines_sgp") or []}
    types: dict[str, int] = {}
    objets, famille = [], []
    for g in guids:
        type_ifc, domaine = infos.get(g, ("?", None))
        objets.append(types.setdefault(type_ifc, len(types)))
        famille.append(par_domaine.get(domaine, par_type.get(type_ifc, par_fichier)))
    donnees = {
        "types": list(types),
        "objets": objets,
        "familles": [{"nom": f["nom"], "couleur": f.get("couleur", "#888888"), "nb": famille.count(i)}
                     for i, f in enumerate(familles)],
        "famille": famille,
    }
    try:
        (dossier / "types.json").write_text(json.dumps({"cle": cle, "donnees": donnees}, separators=(",", ":")),
                                            encoding="utf-8")
    except OSError:
        pass
    return donnees

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
import yaml
from pyproj import Transformer

from commun.ifc import VERSION_MODULE, georef

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
    if not _nombre(t.get("deflexion_lineaire_m"), True) or not _nombre(t.get("simplification_emprise_m")) \
            or not _nombre(t.get("delai_max_minutes"), True) or not isinstance(t.get("exclure"), list):
        raise erreur("section « traitement » incomplète (deflexion_lineaire_m, exclure, simplification_emprise_m, "
                     "delai_max_minutes)")
    if not _nombre((cfg.get("affichage_3d") or {}).get("chargement_automatique_max_mo")):
        raise erreur("« affichage_3d.chargement_automatique_max_mo » doit être un nombre positif")
    ident = cfg.get("identification") or {}
    if not isinstance(ident.get("systemes_candidats"), list) or not _nombre(ident.get("tolerance_m"), True):
        raise erreur("section « identification » incomplète (systemes_candidats, tolerance_m)")
    emp = ident.get("emprise_plausible_wgs84")
    if emp is not None and not (isinstance(emp, list) and len(emp) == 4 and all(_nombre(abs(v)) for v in emp)):
        raise erreur("« identification.emprise_plausible_wgs84 » : [ouest, sud, est, nord] attendu")
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
    return (f"{st.st_size}-{st.st_mtime_ns}-{VERSION_MODULE}-{t['deflexion_lineaire_m']}-"
            f"{t['simplification_emprise_m']}-{','.join(sorted(t['exclure']))}")


def dossier_cache(cfgm: dict, id_: str) -> Path:
    return _chemin(cfgm["dossier_cache"]) / id_


def _lire_json(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def resultat(cfgm: dict, id_: str, cle: str) -> dict | None:
    r = _lire_json(dossier_cache(cfgm, id_) / "resultat.json")
    return r if r and r.get("cle") == cle else None


def erreur(cfgm: dict, id_: str, cle: str) -> dict | None:
    e = _lire_json(dossier_cache(cfgm, id_) / "erreur.json")
    return e if e and e.get("cle") == cle else None


# ---------------------------------------------------------------- file d'attente des traitements

class Traitements:
    """Un traitement à la fois (mémoire), dans un processus séparé ; les plus petites maquettes d'abord."""

    def __init__(self):
        self._verrou = threading.Lock()
        self._attente: dict[str, tuple[int, Path, str, dict]] = {}   # id -> (taille, chemin, clé, config)
        self._courant: str | None = None
        self._processus: subprocess.Popen | None = None
        self._fil: threading.Thread | None = None

    def assurer(self, cfgm: dict, maquettes: list[tuple[str, str, Path]]) -> None:
        with self._verrou:
            for id_, _rel, chemin in maquettes:
                cle = cle_traitement(chemin, cfgm)
                if id_ == self._courant or resultat(cfgm, id_, cle) or erreur(cfgm, id_, cle):
                    continue
                self._attente[id_] = (chemin.stat().st_size, chemin, cle, cfgm)
            if self._attente and (self._fil is None or not self._fil.is_alive()):
                self._fil = threading.Thread(target=self._boucle, name="traitements-ifc", daemon=True)
                self._fil.start()

    def relancer(self, cfgm: dict, id_: str, chemin: Path) -> None:
        d = dossier_cache(cfgm, id_)
        for nom in ("resultat.json", "erreur.json"):
            (d / nom).unlink(missing_ok=True)
        self.assurer(cfgm, [(id_, "", chemin)])

    def position(self, id_: str) -> int | None:
        with self._verrou:
            ordre = sorted(self._attente, key=lambda k: self._attente[k][0])
        return ordre.index(id_) + 1 if id_ in ordre else None

    def en_cours(self) -> str | None:
        return self._courant

    def nb_attente(self) -> int:
        with self._verrou:
            return len(self._attente)

    def _boucle(self) -> None:
        while True:
            with self._verrou:
                if not self._attente:
                    self._courant = None
                    return
                id_ = min(self._attente, key=lambda k: self._attente[k][0])
                _taille, chemin, cle, cfgm = self._attente.pop(id_)
                self._courant = id_
            self._executer(id_, chemin, cle, cfgm)

    def _executer(self, id_: str, chemin: Path, cle: str, cfgm: dict) -> None:
        sortie = dossier_cache(cfgm, id_)
        sortie.mkdir(parents=True, exist_ok=True)
        for nom in ("resultat.json", "erreur.json", "trace.txt"):
            (sortie / nom).unlink(missing_ok=True)
        t = cfgm["traitement"]
        params = sortie / "parametres.json"
        params.write_text(json.dumps({"cle": cle, "exclure": t["exclure"],
                                      "deflexion_lineaire_m": t["deflexion_lineaire_m"],
                                      "simplification_emprise_m": t["simplification_emprise_m"]}), encoding="utf-8")
        commande = [sys.executable, "-m", "serveur.sig.travail_ifc", str(chemin), str(sortie), str(params)]
        journal = (sortie / "journal.txt").open("w", encoding="utf-8", errors="replace")
        try:
            self._processus = subprocess.Popen(commande, cwd=RACINE, stdout=journal, stderr=subprocess.STDOUT,
                                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                code = self._processus.wait(timeout=float(t["delai_max_minutes"]) * 60)
            except subprocess.TimeoutExpired:
                self._processus.kill()
                self._processus.wait()
                code = None
                message = f"traitement interrompu après {t['delai_max_minutes']} minutes (délai « delai_max_minutes »)"
        except OSError as e:
            code, message = None, f"lancement du traitement impossible ({e})"
        finally:
            journal.close()
            self._processus = None
        if code != 0 and not (sortie / "erreur.json").is_file():
            if code is not None:
                message = f"le traitement s'est arrêté brutalement (code {code}) : mémoire insuffisante ?"
            (sortie / "erreur.json").write_text(json.dumps({"cle": cle, "message": message}, ensure_ascii=False),
                                                encoding="utf-8")
        (sortie / "etat.json").unlink(missing_ok=True)

    def arreter(self) -> None:
        with self._verrou:
            self._attente.clear()
        if self._processus is not None:
            self._processus.kill()


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
    def anneau(coords):
        sortie = []
        for x, y, *_ in coords:
            px, py, _ = georef.vers_projete(repere, x, y)
            lon, lat = vers_wgs84.transform(px, py)
            sortie.append([round(lon, 8), round(lat, 8)])
        return sortie

    if geom["type"] == "Polygon":
        return {"type": "Polygon", "coordinates": [anneau(a) for a in geom["coordinates"]]}
    return {"type": "MultiPolygon", "coordinates": [[anneau(a) for a in p] for p in geom["coordinates"]]}


def _emprise(geom: dict) -> list[float]:
    pts = [p for poly in ([geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"])
           for a in poly for p in a]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return [min(xs), min(ys), max(xs), max(ys)]


def positionner(cfg: dict, cfgm: dict, id_: str, rel: str, res: dict) -> dict:
    """Positionnement complet d'une maquette traitée : méthode, emprise WGS84, calage vertical, affichage 3D."""
    ident = cfgm["identification"]
    declare, motif = _systeme_declare(cfgm, rel)
    saisie = _saisie(cfgm, rel)
    manuel = saisie if saisie and "x" in saisie else None
    pos = georef.resoudre(res["georef_brut"], ident["systemes_candidats"], ident["tolerance_m"], declare,
                          ident.get("emprise_plausible_wgs84"), manuel)
    if motif and pos.get("systeme_source", "").startswith("déclaré"):
        pos["systeme_source"] = f"déclaré (configuration, motif « {motif} »)"
    geo = res["geometrie"]
    if pos["statut"] != "positionnee":
        return pos
    if not geo.get("emprise_locale"):
        pos.update(statut="non_positionnee", raison="aucune géométrie exploitable dans la maquette")
        return pos

    repere = pos["repere"]
    vers_wgs84 = Transformer.from_crs(pos["systeme"], 4326, always_xy=True)
    vers_l93 = Transformer.from_crs(pos["systeme"], 2154, always_xy=True)
    emprise_wgs84 = _geojson_vers_wgs84(geo["emprise_locale"], repere, vers_wgs84)
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

    taille = (dossier_cache(cfgm, id_) / "modele.glb").stat().st_size if (dossier_cache(cfgm, id_) / "modele.glb").is_file() else 0
    pos.update({
        "emprise_wgs84": [round(v, 8) for v in bbox],
        "emprise_geojson": emprise_wgs84,
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
            "taille_mo": round(taille / 1e6, 1),
            "chargement_automatique": taille / 1e6 <= cfgm["affichage_3d"]["chargement_automatique_max_mo"],
        },
    })
    return pos


# ---------------------------------------------------------------- vue d'ensemble

def etat_maquette(cfgm: dict, id_: str, cle: str) -> dict:
    if resultat(cfgm, id_, cle):
        return {"phase": "pret"}
    e = erreur(cfgm, id_, cle)
    if e:
        return {"phase": "erreur", "message": e["message"]}
    if traitements.en_cours() == id_:
        en_cours = _lire_json(dossier_cache(cfgm, id_) / "etat.json") or {}
        return {"phase": "en_cours", "etape": en_cours.get("phase", "démarrage"),
                "progression": en_cours.get("progression", 0), "message": en_cours.get("message", "démarrage"),
                "depuis_secondes": round(time.time() - en_cours["debut"]) if en_cours.get("debut") else None}
    return {"phase": "en_attente", "position": traitements.position(id_)}


def inventaire(cfg: dict, cfgm: dict) -> dict:
    liste = fichiers(cfgm)
    traitements.assurer(cfgm, liste)
    maquettes = []
    for id_, rel, chemin in liste:
        cle = cle_traitement(chemin, cfgm)
        res = resultat(cfgm, id_, cle)
        m = {"id": id_, "fichier": rel, "nom": chemin.stem, "dossier": str(Path(rel).parent.as_posix()),
             "taille_mo": round(chemin.stat().st_size / 1e6, 1), "etat": etat_maquette(cfgm, id_, cle)}
        if res:
            m["metadonnees"] = res["metadonnees"]
            m["nb_objets"] = res["geometrie"]["nb_elements"]
            m["duree_traitement_s"] = res.get("duree_secondes")
            try:
                m["positionnement"] = positionner(cfg, cfgm, id_, rel, res)
            except Exception as e:  # garde-fou : une maquette ne doit pas interrompre l'inventaire
                m["positionnement"] = {"statut": "non_positionnee", "methode": None, "libelle": "Non positionnée",
                                       "raison": f"erreur de positionnement ({type(e).__name__} : {e})",
                                       "avertissements": []}
        maquettes.append(m)
    return {
        "dossier": cfgm["dossier_ifc"],
        "maquettes": maquettes,
        "traitement_en_cours": traitements.en_cours(),
        "en_attente": traitements.nb_attente(),
    }


def emprises(inv: dict) -> dict:
    """Emprises au sol des maquettes positionnées (GeoJSON WGS84)."""
    features = []
    for m in inv["maquettes"]:
        pos = m.get("positionnement") or {}
        if pos.get("statut") == "positionnee":
            features.append({"type": "Feature", "id": len(features), "geometry": pos["emprise_geojson"],
                             "properties": {"id": m["id"], "nom": m["nom"], "methode": pos["methode"],
                                            "precision": pos.get("precision")}})
    return {"type": "FeatureCollection", "features": features}


def chemin_maquette(cfgm: dict, id_: str) -> tuple[str, Path] | None:
    for i, rel, chemin in fichiers(cfgm):
        if i == id_:
            return rel, chemin
    return None

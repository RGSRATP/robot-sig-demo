r"""Traitement d'une maquette dans un processus séparé : analyse, modèle 3D + emprise, index des propriétés.

Lancé par serveur/sig/maquettes.py :
    python -m serveur.sig.travail_ifc <fichier.ifc> <dossier_sortie> <parametres.json>

Tâches : « complet » (modèle 3D, propriétés, locaux, étages), « locaux » seuls (maquette déjà traitée) ou « etages »
seuls (altitudes des IfcBuildingStorey, demandées par le contexte d'une maquette traitée avant le lot 5).
Écrit dans le dossier de sortie : etat.json (avancement), modele.glb et detail.glb (convertisseur web-ifc, lancé
en parallèle de la lecture IfcOpenShell), proprietes.sqlite, locaux.json, puis resultat.json en dernier (sa présence signifie « traitement terminé »). Un processus séparé isole la mémoire et les
plantages éventuels d'IfcOpenShell : le serveur reste disponible.
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

from commun.ifc import VERSION_LOCAUX, VERSION_MODULE, geometrie, lecture, locaux, proprietes


def _ecrire(chemin: Path, donnees: dict, essais: int = 20) -> None:
    """Écriture atomique (fichier temporaire puis remplacement). Sous Windows, le remplacement est refusé tant que
    le serveur lit le fichier (« Accès refusé ») : nouvel essai, quelques dixièmes de seconde plus tard."""
    tmp = chemin.with_suffix(".tmp")
    tmp.write_text(json.dumps(donnees, ensure_ascii=False, indent=1), encoding="utf-8")
    for essai in range(essais):
        try:
            tmp.replace(chemin)
            return
        except PermissionError:
            if essai == essais - 1:
                raise
            time.sleep(0.05 * (essai + 1))


def _ecrire_etat(chemin: Path, donnees: dict) -> None:
    """Avancement (etat.json) : une écriture manquée ne doit jamais interrompre le traitement."""
    try:
        _ecrire(chemin, donnees, essais=5)
    except OSError:
        pass


def traiter(ifc: Path, sortie: Path, params: dict) -> dict:
    sortie.mkdir(parents=True, exist_ok=True)
    debut = time.time()
    # poids des phases dans la progression globale ; la conversion 3D (web-ifc) tourne pendant la lecture
    phases = {"ouverture": (0, 35), "proprietes": (35, 80), "locaux": (80, 90), "geometrie": (90, 100)}

    def etat(phase: str, p: int = 0, message: str = "") -> None:
        a, b = phases[phase]
        _ecrire_etat(sortie / "etat.json", {"phase": phase, "progression": round(a + (b - a) * p / 100),
                                       "message": message, "debut": debut, "cle": params["cle"]})

    etat("ouverture", 0, "lecture du fichier IFC et conversion 3D")
    conversion = geometrie.lancer(ifc, sortie, exclure=params["exclure"], petits_elements_m=params["petits_elements_m"],
                                  simplification_3d_m=params["simplification_3d_m"])
    try:
        f = lecture.ouvrir(ifc)
        meta = lecture.metadonnees(f)
        brut = lecture.georef_brut(f)
        decalage = brut["site"]["origine"] if brut.get("site") else [0.0, 0.0, 0.0]
        attendus = geometrie.objets_attendus(f, params["exclure"])

        etat("proprietes", 0, "index des propriétés")
        nb = proprietes.indexer(f, sortie / "proprietes.sqlite",
                                progression=lambda p: etat("proprietes", p, "index des propriétés"))

        etat("locaux", 0, "locaux")
        try:
            _ecrire_locaux(f, decalage, sortie, params, lambda p: etat("locaux", p, "locaux"))
        except Exception as e:  # l'échec des locaux n'empêche pas d'afficher la maquette
            _ecrire(sortie / "erreur_locaux.json", {"cle": params["cle_locaux"], "message": f"{type(e).__name__} : {e}"})
        del f   # libère la mémoire d'IfcOpenShell avant la fin de la conversion

        etat("geometrie", 0, "modèle 3D")
        geo = geometrie.terminer(conversion, decalage, simplification_m=params["simplification_emprise_m"],
                                 simplification_affichage_m=params["simplification_emprise_affichage_m"],
                                 delai_s=float(params["delai_max_minutes"]) * 60,
                                 progression=lambda p: etat("geometrie", p, "modèle 3D"))
    finally:
        geometrie.arreter_processus(conversion.processus)
    geo["nb_elements_attendus"] = len(attendus)
    geo["non_convertis"] = geometrie.non_convertis(attendus, sortie)

    resultat = {"cle": params["cle"], "version_module": VERSION_MODULE, "metadonnees": meta, "georef_brut": brut,
                "decalage": decalage, "geometrie": geo, "nb_objets_index": nb,
                "duree_secondes": round(time.time() - debut, 1)}
    _ecrire(sortie / "resultat.json", resultat)
    (sortie / "etat.json").unlink(missing_ok=True)
    return resultat


def _ecrire_locaux(f, decalage, sortie: Path, params: dict, progression) -> None:
    liste = locaux.locaux(f, decalage, progression=progression)
    _ecrire(sortie / "locaux.json", {"cle": params["cle_locaux"], "locaux": liste, "niveaux": locaux.niveaux(liste)})
    _ecrire_etages(f, decalage, sortie, params)


def _ecrire_etages(f, decalage, sortie: Path, params: dict) -> None:
    if params.get("cle_etages"):
        _ecrire(sortie / "etages.json", {"cle": params["cle_etages"], "etages": locaux.etages(f, decalage)})


def traiter_etages(ifc: Path, sortie: Path, params: dict) -> None:
    """Étages seuls (lecture du fichier, sans géométrie)."""
    f = lecture.ouvrir(ifc)
    brut = lecture.georef_brut(f)
    _ecrire_etages(f, brut["site"]["origine"] if brut.get("site") else [0.0, 0.0, 0.0], sortie, params)


def traiter_locaux(ifc: Path, sortie: Path, params: dict) -> None:
    """Locaux seuls, pour une maquette dont le modèle 3D est déjà en cache."""
    debut = time.time()

    def etat(p, message):
        _ecrire_etat(sortie / "etat.json", {"phase": "locaux", "progression": p, "message": message,
                                       "debut": debut, "cle": params["cle"]})

    etat(0, "lecture du fichier IFC (locaux)")
    f = lecture.ouvrir(ifc)
    brut = lecture.georef_brut(f)
    decalage = brut["site"]["origine"] if brut.get("site") else [0.0, 0.0, 0.0]
    etat(30, "locaux")
    _ecrire_locaux(f, decalage, sortie, params, lambda p: etat(30 + p * 70 // 100, "locaux"))
    (sortie / "etat.json").unlink(missing_ok=True)


def main(argv: list[str]) -> int:
    ifc, sortie, params = Path(argv[0]), Path(argv[1]), json.loads(Path(argv[2]).read_text(encoding="utf-8"))
    # un serveur plus ancien ne transmet pas la clé des locaux : même règle que serveur/sig/maquettes.cle_locaux
    params.setdefault("cle_locaux", f"{params['cle']}-locaux{VERSION_LOCAUX}")
    locaux_seuls = params.get("tache") == "locaux"
    if params.get("tache") == "etages":   # échec : simple absence d'etages.json, signalée par le serveur
        try:
            traiter_etages(ifc, sortie, params)
            return 0
        except Exception:
            traceback.print_exc()
            return 1
    try:
        (traiter_locaux if locaux_seuls else traiter)(ifc, sortie, params)
        return 0
    except (lecture.MaquetteIllisible, geometrie.ConversionImpossible) as e:
        message = str(e)
    except MemoryError:
        message = "mémoire insuffisante pour traiter cette maquette"
    except Exception as e:  # tout est tracé dans erreur.json, le serveur continue
        message = f"erreur inattendue ({type(e).__name__} : {e})"
        (sortie / "trace.txt").write_text(traceback.format_exc(), encoding="utf-8")
    if locaux_seuls:   # l'échec des locaux ne rend pas la maquette inutilisable
        _ecrire(sortie / "erreur_locaux.json", {"cle": params["cle_locaux"], "message": message})
    else:
        _ecrire(sortie / "erreur.json", {"cle": params["cle"], "message": message})
    (sortie / "etat.json").unlink(missing_ok=True)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

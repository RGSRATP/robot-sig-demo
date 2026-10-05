r"""Traitement d'une maquette dans un processus séparé : analyse, modèle 3D + emprise, index des propriétés.

Lancé par serveur/sig/maquettes.py :
    python -m serveur.sig.travail_ifc <fichier.ifc> <dossier_sortie> <parametres.json>

Écrit dans le dossier de sortie : etat.json (avancement), modele.glb, proprietes.sqlite, puis resultat.json
en dernier (sa présence signifie « traitement terminé »). Un processus séparé isole la mémoire et les
plantages éventuels d'IfcOpenShell : le serveur reste disponible.
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

from commun.ifc import VERSION_MODULE, geometrie, lecture, proprietes


def _ecrire(chemin: Path, donnees: dict) -> None:
    tmp = chemin.with_suffix(".tmp")
    tmp.write_text(json.dumps(donnees, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(chemin)


def traiter(ifc: Path, sortie: Path, params: dict) -> dict:
    sortie.mkdir(parents=True, exist_ok=True)
    debut = time.time()
    # poids des phases dans la progression globale
    phases = {"ouverture": (0, 10), "geometrie": (10, 85), "proprietes": (85, 100)}

    def etat(phase: str, p: int = 0, message: str = "") -> None:
        a, b = phases[phase]
        _ecrire(sortie / "etat.json", {"phase": phase, "progression": round(a + (b - a) * p / 100),
                                       "message": message, "debut": debut, "cle": params["cle"]})

    etat("ouverture", 0, "lecture du fichier IFC")
    f = lecture.ouvrir(ifc)
    meta = lecture.metadonnees(f)
    brut = lecture.georef_brut(f)
    decalage = brut["site"]["origine"] if brut.get("site") else [0.0, 0.0, 0.0]

    etat("geometrie", 0, "modèle 3D et emprise au sol")
    geo = geometrie.convertir(
        f, sortie / "modele.glb", decalage, exclure=params["exclure"], deflexion_m=params["deflexion_lineaire_m"],
        simplification_m=params["simplification_emprise_m"],
        progression=lambda p: etat("geometrie", p, "modèle 3D et emprise au sol"))

    etat("proprietes", 0, "index des propriétés")
    nb = proprietes.indexer(f, sortie / "proprietes.sqlite",
                            progression=lambda p: etat("proprietes", p, "index des propriétés"))

    resultat = {"cle": params["cle"], "version_module": VERSION_MODULE, "metadonnees": meta, "georef_brut": brut,
                "decalage": decalage, "geometrie": geo, "nb_objets_index": nb,
                "duree_secondes": round(time.time() - debut, 1)}
    _ecrire(sortie / "resultat.json", resultat)
    (sortie / "etat.json").unlink(missing_ok=True)
    return resultat


def main(argv: list[str]) -> int:
    ifc, sortie, params = Path(argv[0]), Path(argv[1]), json.loads(Path(argv[2]).read_text(encoding="utf-8"))
    try:
        traiter(ifc, sortie, params)
        return 0
    except lecture.MaquetteIllisible as e:
        message = str(e)
    except MemoryError:
        message = "mémoire insuffisante pour traiter cette maquette"
    except Exception as e:  # tout est tracé dans erreur.json, le serveur continue
        message = f"erreur inattendue ({type(e).__name__} : {e})"
        (sortie / "trace.txt").write_text(traceback.format_exc(), encoding="utf-8")
    _ecrire(sortie / "erreur.json", {"cle": params["cle"], "message": message})
    (sortie / "etat.json").unlink(missing_ok=True)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

r"""Écrit le jeu de maquettes IFC de test du démonstrateur (tests/donnees/ifc/), versionné.

    .venv\Scripts\python tests\donnees\generer_ifc.py

Les maquettes sont fabriquées par le module partagé (commun/ifc/tests/fabrique.py).
"""
from pathlib import Path

from commun.ifc.tests.fabrique import ecrire_jeu

if __name__ == "__main__":
    dossier = Path(__file__).resolve().parent / "ifc"
    ref = ecrire_jeu(dossier)
    print(f"maquettes écrites dans {dossier} ; références : {ref}")

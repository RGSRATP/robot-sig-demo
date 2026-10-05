"""Le dépôt Git exclut les données, les secrets et les environnements."""
import shutil
import subprocess
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or not (RACINE / ".git").exists(), reason="Git ou dépôt absent"
)


def _ignore(chemin: str) -> bool:
    r = subprocess.run(["git", "check-ignore", "-q", "--no-index", chemin], cwd=RACINE)
    return r.returncode == 0


@pytest.mark.parametrize(
    "chemin",
    ["donnees/sig/couche.gpkg", "donnees/ifc/maquette.ifc", "donnees/esri/export.geojson",
     ".env", ".venv/pyvenv.cfg", "interface/node_modules/x.js"],
)
def test_exclus_de_git(chemin):
    assert _ignore(chemin)


def test_lisezmoi_donnees_versionne():
    assert not _ignore("donnees/LISEZMOI.md")

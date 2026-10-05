"""Configuration et client de test sur le jeu tests/donnees/jeu/ (données ouvertes IDFM / SNCF)."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from serveur.sig import routes
from serveur.sig.app import app
from serveur.sig.config import charger_config
from serveur.sig.inventaire import inventorier

CONFIG_TEST = Path(__file__).resolve().parents[1] / "donnees" / "sig_test.yaml"


@pytest.fixture(scope="session")
def cfg_test():
    return charger_config(CONFIG_TEST)


@pytest.fixture(scope="session")
def inv(cfg_test):
    return inventorier(cfg_test)


@pytest.fixture
def couche(inv):
    """Accès à une couche par son libellé."""
    def _par_libelle(libelle):
        trouvees = [c for c in inv.couches if c.libelle == libelle]
        assert trouvees, f"couche « {libelle} » absente ; couches : {[c.libelle for c in inv.couches]}"
        return trouvees[0]
    return _par_libelle


@pytest.fixture
def anomalie(inv):
    def _par_fichier(fichier):
        return [a for a in inv.anomalies if a["fichier"] == fichier]
    return _par_fichier


@pytest.fixture
def client_test(cfg_test):
    app.dependency_overrides[routes._config_ou_erreur] = lambda: cfg_test
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()

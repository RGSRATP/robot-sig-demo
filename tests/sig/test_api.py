"""Routes /api/sig : santé et paramètres de carte ; une configuration invalide ne bloque pas le serveur."""
import pytest
from fastapi.testclient import TestClient

from serveur.sig import routes
from serveur.sig.app import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_sante_repond(client):
    r = client.get("/api/sig/sante")
    assert r.status_code == 200
    corps = r.json()
    assert corps["statut"] in ("ok", "degrade")
    assert corps["configuration_chargee"] is True
    assert corps["ifcopenshell"]
    assert set(corps["donnees"]) == {"sig", "ifc", "esri"}


def test_config_carte(client):
    r = client.get("/api/sig/config/carte")
    assert r.status_code == 200
    corps = r.json()
    assert len(corps["centre_wgs84"]) == 2
    assert corps["fond_defaut"] in [f["id"] for f in corps["fonds"]]


def test_config_invalide_signalee_sans_bloquer(client):
    app.dependency_overrides[routes._config_ou_erreur] = lambda: {"erreur": "sig.yaml : section « carte » absente"}
    r = client.get("/api/sig/sante")
    assert r.status_code == 200
    assert r.json()["statut"] == "degrade"
    assert any("carte" in a for a in r.json()["anomalies"])
    r = client.get("/api/sig/config/carte")
    assert r.status_code == 503
    assert "carte" in r.json()["detail"]


def test_routes_sous_api_sig():
    for route in routes.routeur.routes:
        assert route.path.startswith("/api/sig/")

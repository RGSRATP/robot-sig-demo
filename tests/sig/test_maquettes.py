"""Lot 3 — maquettes sur la carte : configuration, traitements, positionnement, calage vertical, API.

Jeu : tests/donnees/ifc/ (une maquette par méthode, une sans géoréférencement, un faux IFC).
Altimétrie : faux service (altitude du terrain 60 m). Recette : les maquettes géoréférencées sont
positionnées et leur modèle 3D est servi ; la maquette sans géoréférencement est « non positionnée ».
"""
import copy
import json
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from serveur.sig import maquettes as maq
from serveur.sig import routes, travail_ifc
from serveur.sig.app import app

CONFIG = Path(__file__).resolve().parents[1] / "donnees" / "georef_test.yaml"
ALTITUDE_TERRAIN = 60.0


@pytest.fixture(scope="module")
def cache_traite(tmp_path_factory):
    """Cache rempli une fois pour le module (traitement direct, sans sous-processus)."""
    cfgm = maq.charger_config(CONFIG)
    cfgm["dossier_cache"] = str(tmp_path_factory.mktemp("cache_ifc"))
    t = cfgm["traitement"]
    for id_, _rel, chemin in maq.fichiers(cfgm):
        sortie = maq.dossier_cache(cfgm, id_)
        params = {"cle": maq.cle_traitement(chemin, cfgm), "exclure": t["exclure"],
                  "deflexion_lineaire_m": t["deflexion_lineaire_m"], "simplification_emprise_m": t["simplification_emprise_m"]}
        p = sortie / "parametres.json"
        sortie.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(params), encoding="utf-8")
        travail_ifc.main([str(chemin), str(sortie), str(p)])
    return cfgm


@pytest.fixture
def cfgm(cache_traite, monkeypatch):
    cfgm = copy.deepcopy(cache_traite)
    appels = []

    def repondre(requete):
        appels.append(requete)
        return httpx.Response(200, json={"elevations": [ALTITUDE_TERRAIN]})

    monkeypatch.setattr(maq, "fabrique_client", lambda cfg: httpx.Client(transport=httpx.MockTransport(repondre)))
    cfgm["_appels_altimetrie"] = appels
    for f in Path(cfgm["dossier_cache"]).glob("*/sol.json"):
        f.unlink()
    return cfgm


@pytest.fixture
def inv(cfg_test, cfgm, monkeypatch):
    monkeypatch.setattr(maq.traitements, "assurer", lambda *a, **k: None)
    return {m["nom"]: m for m in maq.inventaire(cfg_test, cfgm)["maquettes"]}


# ---------------------------------------------------------------- configuration

def test_config_projet_valide():
    cfgm = maq.charger_config()
    assert cfgm["identification"]["systemes_candidats"] and cfgm["altimetrie"]["url"]


def test_config_incomplete(tmp_path):
    p = tmp_path / "g.yaml"
    p.write_text("dossier_ifc: x\ndossier_cache: y\n", encoding="utf-8")
    with pytest.raises(maq.ConfigMaquettesInvalide, match="traitement"):
        maq.charger_config(p)
    with pytest.raises(maq.ConfigMaquettesInvalide, match="introuvable"):
        maq.charger_config(tmp_path / "absent.yaml")


def test_config_saisie_manuelle_incomplete(tmp_path):
    texte = CONFIG.read_text(encoding="utf-8").replace("    y: 6860000.0\n", "")
    p = tmp_path / "g.yaml"
    p.write_text(texte, encoding="utf-8")
    with pytest.raises(maq.ConfigMaquettesInvalide, match="vont ensemble"):
        maq.charger_config(p)


# ---------------------------------------------------------------- inventaire et positionnement (recette)

def test_inventaire_toutes_les_maquettes(inv):
    assert set(inv) == {"methode1_map_conversion_ifc4", "methode2a_site_cc49_boston", "methode2a_site_cc49_identifie",
                        "methode2b_latlon", "methode3_manuelle", "piege_pas_un_ifc", "sans_georeferencement"}


def test_recette_maquettes_positionnees(inv):
    attendu = {"methode1_map_conversion_ifc4": "ifc_map_conversion", "methode2a_site_cc49_identifie": "ifc_site_projete",
               "methode2b_latlon": "ifc_site_latlon", "methode3_manuelle": "manuel"}
    for nom, methode in attendu.items():
        pos = inv[nom]["positionnement"]
        assert pos["statut"] == "positionnee" and pos["methode"] == methode, nom
        assert pos["emprise_geojson"]["type"] in ("Polygon", "MultiPolygon")


def test_recette_maquette_non_positionnee(inv):
    for nom in ("sans_georeferencement", "methode2a_site_cc49_boston"):
        pos = inv[nom]["positionnement"]
        assert pos["statut"] == "non_positionnee" and pos["raison"], nom


def test_faux_ifc_en_erreur_sans_bloquer(inv):
    e = inv["piege_pas_un_ifc"]["etat"]
    assert e["phase"] == "erreur" and "ISO-10303-21" in e["message"]


def test_position_au_bon_endroit(inv):
    # même point (nord de Massy) pour les méthodes 1, 2a, 2b : centres à moins d'un mètre (murs identiques)
    centres = [inv[n]["positionnement"]["centre_l93"] for n in
               ("methode1_map_conversion_ifc4", "methode2a_site_cc49_identifie", "methode2b_latlon")]
    for c in centres[1:]:
        assert abs(c[0] - centres[0][0]) < 1 and abs(c[1] - centres[0][1]) < 1
    lon, lat = inv["methode1_map_conversion_ifc4"]["positionnement"]["centre_wgs84"]
    assert 2.26 < lon < 2.28 and 48.76 < lat < 48.78


def test_calage_vertical_sur_le_terrain(inv, cfgm):
    pos = inv["methode2a_site_cc49_identifie"]["positionnement"]
    # site à 80 m NGF, murs de 0 à 3 m, terrain à 60 m : 20 à 23 m au-dessus du terrain
    assert pos["altitude_terrain"] == ALTITUDE_TERRAIN
    assert pos["hauteurs_affichees"] == pytest.approx([20.0, 23.0], abs=0.01)
    assert pos["affichage"]["decalage_z_m"] == pytest.approx(20.0)
    manuel = inv["methode3_manuelle"]["positionnement"]
    assert manuel["hauteurs_affichees"] == pytest.approx([31.5, 34.5], abs=0.01)   # 90 - 60 + 1,5
    assert "décalage manuel" in manuel["calage_vertical"]


def test_altimetrie_mise_en_cache(cfg_test, cfgm, monkeypatch):
    monkeypatch.setattr(maq.traitements, "assurer", lambda *a, **k: None)
    maq.inventaire(cfg_test, cfgm)
    n = len(cfgm["_appels_altimetrie"])
    maq.inventaire(cfg_test, cfgm)
    assert n > 0 and len(cfgm["_appels_altimetrie"]) == n


def test_altimetrie_indisponible_base_au_sol(cfg_test, cfgm, monkeypatch):
    def panne(requete):
        raise httpx.ConnectError("refus", request=requete)
    monkeypatch.setattr(maq, "fabrique_client", lambda cfg: httpx.Client(transport=httpx.MockTransport(panne)))
    monkeypatch.setattr(maq.traitements, "assurer", lambda *a, **k: None)
    inv = {m["nom"]: m for m in maq.inventaire(cfg_test, cfgm)["maquettes"]}
    pos = inv["methode2a_site_cc49_identifie"]["positionnement"]
    assert pos["statut"] == "positionnee" and pos["hauteurs_affichees"][0] == pytest.approx(0.0, abs=0.01)
    assert any("non calée" in a for a in pos["avertissements"])


def test_altitudes_deja_absolues_detectees(cfg_test, cfgm, monkeypatch):
    """Site à 80 m et objets portant déjà leur altitude NGF : l'altitude du site n'est pas comptée deux fois."""
    def repondre(requete):
        return httpx.Response(200, json={"elevations": [-75.0]})   # terrain fictif proche des objets seuls
    monkeypatch.setattr(maq, "fabrique_client", lambda cfg: httpx.Client(transport=httpx.MockTransport(repondre)))
    monkeypatch.setattr(maq.traitements, "assurer", lambda *a, **k: None)
    cfgm["altimetrie"]["ecart_max_terrain_m"] = 100
    inv = {m["nom"]: m for m in maq.inventaire(cfg_test, cfgm)["maquettes"]}
    pos = inv["methode2a_site_cc49_identifie"]["positionnement"]
    assert any(a.startswith("altitude du site") and "ignorée" in a for a in pos["avertissements"])


def test_declaration_par_motif(cfg_test, cfgm, monkeypatch):
    monkeypatch.setattr(maq.traitements, "assurer", lambda *a, **k: None)
    cfgm["systemes_declares"] = [{"motif": "methode2a_*", "systeme": "EPSG:3949"}]
    inv = {m["nom"]: m for m in maq.inventaire(cfg_test, cfgm)["maquettes"]}
    pos = inv["methode2a_site_cc49_boston"]["positionnement"]
    assert pos["statut"] == "positionnee" and "methode2a_*" in pos["systeme_source"]


# ---------------------------------------------------------------- file de traitement (sous-processus réel)

def test_file_de_traitement_sous_processus(cfgm, tmp_path):
    cfgm = copy.deepcopy(cfgm)
    cfgm["dossier_cache"] = str(tmp_path / "cache")
    file = maq.Traitements()
    choix = [m for m in maq.fichiers(cfgm) if m[1] in ("methode1_map_conversion_ifc4.ifc", "piege_pas_un_ifc.ifc")]
    file.assurer(cfgm, choix)
    debut = time.time()
    while (file.en_cours() or file.nb_attente()) and time.time() - debut < 120:
        time.sleep(0.2)
    ok, ko = choix if choix[0][1].startswith("methode") else choix[::-1]
    assert maq.resultat(cfgm, ok[0], maq.cle_traitement(ok[2], cfgm)) is not None
    assert (maq.dossier_cache(cfgm, ok[0]) / "modele.glb").is_file()
    assert "ISO-10303-21" in maq.erreur(cfgm, ko[0], maq.cle_traitement(ko[2], cfgm))["message"]
    # relance : le résultat est effacé puis recalculé
    file.relancer(cfgm, ok[0], ok[2])
    while (file.en_cours() or file.nb_attente()) and time.time() - debut < 180:
        time.sleep(0.2)
    assert maq.resultat(cfgm, ok[0], maq.cle_traitement(ok[2], cfgm)) is not None


# ---------------------------------------------------------------- API

@pytest.fixture
def client(cfg_test, cfgm, monkeypatch):
    monkeypatch.setattr(maq.traitements, "assurer", lambda *a, **k: None)
    app.dependency_overrides[routes._config_ou_erreur] = lambda: cfg_test
    app.dependency_overrides[routes._config_maquettes] = lambda: cfgm
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_api_maquettes_et_emprises(client):
    r = client.get("/api/sig/maquettes").json()
    assert len(r["maquettes"]) == 7
    em = client.get("/api/sig/maquettes/emprises").json()
    assert em["type"] == "FeatureCollection" and len(em["features"]) == 4


def test_api_modele_glb_et_objet(client):
    r = client.get("/api/sig/maquettes").json()
    m = next(x for x in r["maquettes"] if x["nom"] == "methode1_map_conversion_ifc4")
    glb = client.get(f"/api/sig/maquettes/{m['id']}/modele.glb")
    assert glb.status_code == 200 and glb.content[:4] == b"glTF"
    assert glb.headers["content-type"] == "model/gltf-binary"
    import struct
    n = struct.unpack("<I", glb.content[12:16])[0]
    guid = next(nd["name"] for nd in json.loads(glb.content[20:20 + n])["nodes"] if len(nd.get("name") or "") == 22)
    fiche = client.get(f"/api/sig/maquettes/{m['id']}/objets/{guid}").json()
    assert fiche["type_ifc"] == "IfcWall" and "Données d'identification" in fiche["psets"]
    assert client.get(f"/api/sig/maquettes/{m['id']}/objets/inconnu").status_code == 404


def test_api_maquette_inconnue_ou_en_erreur(client):
    assert client.get("/api/sig/maquettes/inconnue/modele.glb").status_code == 404
    r = client.get("/api/sig/maquettes").json()
    piege = next(x for x in r["maquettes"] if x["nom"] == "piege_pas_un_ifc")
    assert client.get(f"/api/sig/maquettes/{piege['id']}/modele.glb").status_code == 409


def test_api_config_maquettes_invalide_503(cfg_test, monkeypatch):
    def invalide():
        raise maq.ConfigMaquettesInvalide("georef_maquettes.yaml : essai")
    monkeypatch.setattr(maq, "config", invalide)
    app.dependency_overrides[routes._config_ou_erreur] = lambda: cfg_test
    with TestClient(app) as c:
        r = c.get("/api/sig/maquettes")
    app.dependency_overrides.clear()
    assert r.status_code == 503 and "essai" in r.json()["detail"]

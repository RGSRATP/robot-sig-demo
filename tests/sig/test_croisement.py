"""Lot 4 — interrogation croisée SIG ↔ maquettes : intersections sur des cas construits, CSV, API.

Cas construit (Lambert 93) : une « maquette » carrée de 100 m × 100 m ; une couche avec un point dedans,
un point dehors, une ligne qui traverse le carré sur 100 m, un polygone qui le recouvre pour 50 × 100 m.
"""
import csv
import io

import geopandas as gpd
import pytest
from fastapi.testclient import TestClient
from pyproj import Transformer
from shapely.geometry import LineString, Point, Polygon, box, mapping

from serveur.sig import croisement as crois
from serveur.sig import inventaire as inv_sig
from serveur.sig import maquettes as maq
from serveur.sig import routes
from serveur.sig.app import app

X0, Y0 = 646000.0, 6852000.0
CARRE = box(X0, Y0, X0 + 100, Y0 + 100)
VERS_WGS84 = Transformer.from_crs(2154, 4326, always_xy=True)


def en_wgs84(geom):
    import shapely
    import numpy as np
    return shapely.transform(geom, lambda xy: np.column_stack(VERS_WGS84.transform(xy[:, 0], xy[:, 1])))


@pytest.fixture
def couche_test(cfg_test, tmp_path):
    gdf = gpd.GeoDataFrame(
        {"nom": ["dedans", "dehors", "traversante", "recouvrante"]},
        geometry=[Point(X0 + 50, Y0 + 50), Point(X0 + 500, Y0 + 50),
                  LineString([(X0 - 50, Y0 + 50), (X0 + 150, Y0 + 50)]),
                  Polygon([(X0 + 50, Y0 - 20), (X0 + 300, Y0 - 20), (X0 + 300, Y0 + 120), (X0 + 50, Y0 + 120)])],
        crs="EPSG:2154")
    chemin = tmp_path / "croisement.gpkg"
    gdf.to_file(chemin, layer="objets", driver="GPKG")
    return inv_sig._couche(cfg_test, tmp_path, chemin, "objets", {"champ_libelle": "nom"}, 0)


def test_geometrie_l93_aller_retour():
    g = crois.geometrie_l93(mapping(en_wgs84(CARRE)))
    assert g.area == pytest.approx(10000, rel=1e-6)
    assert g.bounds == pytest.approx(CARRE.bounds, abs=0.01)


def test_maquette_vers_sig_cas_construit(couche_test):
    r = crois.maquette_vers_sig([couche_test], CARRE, max_par_couche=100)
    assert r["total"] == 3 and len(r["groupes"]) == 1
    objets = {o["libelle"]: o for o in r["groupes"][0]["objets"]}
    assert set(objets) == {"dedans", "traversante", "recouvrante"}   # le point « dehors » est exclu
    assert objets["dedans"]["relation"] == "contenu" and objets["dedans"]["mesure"] is None
    assert objets["traversante"]["relation"] == "à cheval"
    assert (objets["traversante"]["mesure"], objets["traversante"]["unite"]) == (pytest.approx(100.0), "m")
    assert (objets["recouvrante"]["mesure"], objets["recouvrante"]["unite"]) == (pytest.approx(5000.0), "m²")


def test_tampon_elargit_la_zone(couche_test):
    r = crois.maquette_vers_sig([couche_test], CARRE.buffer(400), max_par_couche=100)
    assert r["total"] == 4


def test_troncature_par_couche(couche_test):
    r = crois.maquette_vers_sig([couche_test], CARRE, max_par_couche=2)
    g = r["groupes"][0]
    assert g["total"] == 3 and g["tronque"] and len(g["objets"]) == 2


def test_couche_illisible_en_anomalie(couche_test, tmp_path):
    couche_test._chemin.unlink()
    r = crois.maquette_vers_sig([couche_test], CARRE, max_par_couche=100)
    assert r["total"] == 0 and r["anomalies"]


def test_sig_vers_maquettes_et_locaux():
    maquettes = [
        {"id": "a", "nom": "A", "fichier": "a.ifc",
         "positionnement": {"libelle": "x", "emprise_geojson": mapping(en_wgs84(CARRE))}},
        {"id": "b", "nom": "B", "fichier": "b.ifc",
         "positionnement": {"libelle": "x", "emprise_geojson": mapping(en_wgs84(box(X0 + 1000, Y0, X0 + 1100, Y0 + 100)))}},
    ]
    local_touche = {"guid": "g1", "code": "L-001", "emprise_wgs84_geojson": mapping(en_wgs84(box(X0, Y0, X0 + 10, Y0 + 10)))}
    local_loin = {"guid": "g2", "code": "L-002", "emprise_wgs84_geojson": mapping(en_wgs84(box(X0 + 80, Y0 + 80, X0 + 90, Y0 + 90)))}
    objet = Point(X0 + 5, Y0 + 5)
    r = crois.sig_vers_maquettes(objet, maquettes, {"a": [local_touche, local_loin]}, tampon_m=0)
    assert r["total"] == 1 and r["maquettes"][0]["id"] == "a"
    assert [l["code"] for l in r["maquettes"][0]["locaux"]] == ["L-001"]
    r = crois.sig_vers_maquettes(objet, maquettes, {"a": [local_touche, local_loin]}, tampon_m=150)
    assert r["total"] == 1 and len(r["maquettes"][0]["locaux"]) == 2
    r = crois.sig_vers_maquettes(objet, maquettes, {}, tampon_m=1000)
    assert r["total"] == 2


def test_csv_excel_francais():
    texte = crois.csv_texte([("a", "Colonne A"), ("b", "Surface (m²)")], [{"a": "x;y", "b": 12.5}, {"a": None, "b": None}], ";")
    lignes = list(csv.reader(io.StringIO(texte), delimiter=";"))
    assert lignes == [["Colonne A", "Surface (m²)"], ["x;y", "12,50"], ["", ""]]
    assert crois.csv_texte([("b", "B")], [{"b": 1.5}], ",").splitlines()[1] == "1.5"


def test_lignes_csv_maquettes():
    r = {"maquettes": [{"nom": "A", "fichier": "a.ifc", "methode": "m",
                        "locaux": [{"code": "L1", "nom": "1", "nom_long": "Bureau", "niveau": "N00", "typologie": "T", "surface": 5.0}]},
                       {"nom": "B", "fichier": "b.ifc", "methode": "m", "locaux": []}]}
    lignes = crois.lignes_maquettes(r)
    assert [l["maquette"] for l in lignes] == ["A", "B"] and lignes[0]["code"] == "L1" and lignes[0]["nom_local"] == "Bureau"


# ---------------------------------------------------------------- maquette de locaux seuls

def test_maquette_de_locaux_seuls_positionnee_par_ses_locaux(cfg_test, monkeypatch):
    import httpx
    monkeypatch.setattr(maq, "fabrique_client", lambda cfg: httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"elevations": [60.0]}))))
    cfgm = maq.charger_config(__import__("tests.sig.test_maquettes", fromlist=["CONFIG"]).CONFIG)
    l93 = Transformer.from_crs(2154, 3949, always_xy=True).transform(646310.0, 6852080.0)
    lon, lat = VERS_WGS84.transform(646310.0, 6852080.0)
    res = {"georef_brut": {"site": {"origine": [l93[0], l93[1], 80.0], "latitude": lat, "longitude": lon},
                           "map_conversion": None},
           "geometrie": {"emprise_locale": None, "z_min": None, "z_max": None}}
    donnees_locaux = {"locaux": [{"emprise_locale": mapping(box(0, 0, 4, 3)), "z_min": 0.0, "z_max": 2.8}], "niveaux": []}
    import tempfile
    cfgm["dossier_cache"] = tempfile.mkdtemp()
    sans = maq.positionner(cfg_test, cfgm, "x", "a.ifc", res, None)
    assert sans["statut"] == "non_positionnee"
    pos = maq.positionner(cfg_test, cfgm, "x", "a.ifc", res, donnees_locaux)
    assert pos["statut"] == "positionnee" and pos["locaux_seuls"]
    assert pos["hauteurs_affichees"] == pytest.approx([20.0, 22.8], abs=0.01)


# ---------------------------------------------------------------- verrou inter-processus

def test_verrou_inter_processus(tmp_path):
    d = tmp_path / "m"
    assert maq.prendre_verrou(d)
    assert not maq.prendre_verrou(d)          # déjà pris (par ce processus, vivant)
    maq.rendre_verrou(d)
    assert maq.prendre_verrou(d)
    maq.rendre_verrou(d)
    (d / "verrou.json").write_text('{"pid": 999999, "depuis": 0}', encoding="utf-8")
    assert maq.prendre_verrou(d)              # verrou d'un processus disparu : repris
    assert not maq.verrou_tiers(d)            # c'est le nôtre
    maq.rendre_verrou(d)


# ---------------------------------------------------------------- API (maquettes de test)

@pytest.fixture
def client(cfg_test, monkeypatch, tmp_path_factory):
    from tests.sig import test_maquettes as tm
    import httpx
    cfgm = maq.charger_config(tm.CONFIG)
    cfgm["dossier_cache"] = str(tmp_path_factory.mktemp("cache_croisement"))
    for id_, _rel, chemin in maq.fichiers(cfgm):
        sortie = maq.dossier_cache(cfgm, id_)
        sortie.mkdir(parents=True)
        t = cfgm["traitement"]
        p = sortie / "parametres.json"
        p.write_text(__import__("json").dumps({"cle": maq.cle_traitement(chemin, cfgm), "cle_locaux": maq.cle_locaux(chemin, cfgm),
                                               "exclure": t["exclure"], "deflexion_lineaire_m": t["deflexion_lineaire_m"],
                                               "simplification_emprise_m": t["simplification_emprise_m"]}), encoding="utf-8")
        __import__("serveur.sig.travail_ifc", fromlist=["main"]).main([str(chemin), str(sortie), str(p)])
    monkeypatch.setattr(maq, "fabrique_client", lambda cfg: httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"elevations": [60.0]}))))
    monkeypatch.setattr(maq.traitements, "assurer", lambda *a, **k: None)
    app.dependency_overrides[routes._config_ou_erreur] = lambda: cfg_test
    app.dependency_overrides[routes._config_maquettes] = lambda: cfgm
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _id(client, nom):
    return next(m["id"] for m in client.get("/api/sig/maquettes").json()["maquettes"] if m["nom"] == nom)


def test_api_locaux_json_et_csv(client):
    i = _id(client, "methode2a_site_cc49_identifie")
    d = client.get(f"/api/sig/maquettes/{i}/locaux").json()
    assert d["positionnee"] and len(d["locaux"]) == 1 and d["niveaux"][0]["niveau"] == "N00"
    l = d["locaux"][0]
    assert l["code"] == "TEST-B-N00-LT-001" and l["hauteurs"] == pytest.approx([20.0, 22.8], abs=0.01)
    assert l["emprise_wgs84_geojson"]["type"] == "Polygon" and l["x_l93"] is not None
    r = client.get(f"/api/sig/maquettes/{i}/locaux", params={"format": "csv"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.content.startswith(b"\xef\xbb\xbf")   # BOM : Excel reconnaît l'UTF-8
    texte = r.content.decode("utf-8-sig")
    assert texte.splitlines()[0].startswith("Code (SGP_DI_Sous_Ouvrage);") and "TEST-B-N00-LT-001" in texte
    assert "attachment" in r.headers["content-disposition"]


def test_api_croisement_maquette(client):
    i = _id(client, "methode1_map_conversion_ifc4")
    r = client.get(f"/api/sig/maquettes/{i}/croisement").json()
    assert {"groupes", "total", "surface_emprise_m2", "nb_couches_interrogees"} <= set(r)
    assert r["nb_couches_interrogees"] > 0
    csvr = client.get(f"/api/sig/maquettes/{i}/croisement", params={"format": "csv", "tampon": 5})
    assert csvr.status_code == 200 and csvr.content.decode("utf-8-sig").startswith("Couche;")


def test_api_croisement_maquette_non_positionnee(client):
    i = _id(client, "sans_georeferencement")
    assert client.get(f"/api/sig/maquettes/{i}/croisement").status_code == 422


def test_api_objet_vers_maquettes(client, inv):
    # un objet SIG de la couche de test : la réponse est structurée même sans maquette au droit
    c = next(c for c in inv.couches if c.format == "GeoJSON")
    r = client.get(f"/api/sig/couches/{c.id}/objets/0/maquettes").json()
    assert {"maquettes", "total", "nb_locaux", "couche"} <= set(r)
    assert client.get(f"/api/sig/couches/{c.id}/objets/999999/maquettes").status_code == 404

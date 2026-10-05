"""Lot 2 — connecteur des services Esri : conversion Esri JSON, pagination, cache, repli, jeton, API.

Tous les tests passent par le faux serveur (faux_arcgis.py), sauf le test « reseau » (désactivé par défaut).
"""
import copy
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from shapely.geometry import MultiPolygon, Polygon

from serveur.sig import esri
from serveur.sig.config import ConfigInvalide, _controler, charger_config
from serveur.sig.inventaire import AnomalieCouche
from tests.sig.faux_arcgis import RACINE, FauxArcGIS

URL_GARES = f"{RACINE}/Gares/FeatureServer/0"
URL_COMMUNES = f"{RACINE}/Communes/MapServer/1"


def service(cfg, libelle):
    return next(s for s in cfg["services_esri"] if s["libelle"] == libelle)


@pytest.fixture
def gares(cfg_test):
    return copy.deepcopy(service(cfg_test, "Gares (faux service)"))


# ---------------------------------------------------------------- Esri JSON → géométries

def test_geometrie_point_et_point_vide():
    assert esri.geometrie({"x": 1.5, "y": 2.5}).coords[0] == (1.5, 2.5)
    assert esri.geometrie({"x": None, "y": None}) is None
    assert esri.geometrie({"x": float("nan"), "y": 0}) is None
    assert esri.geometrie(None) is None


def test_geometrie_multipoint_et_polylignes():
    assert esri.geometrie({"points": [[0, 0], [1, 1]]}).geom_type == "MultiPoint"
    assert esri.geometrie({"paths": [[[0, 0], [1, 1]]]}).geom_type == "LineString"
    assert esri.geometrie({"paths": [[[0, 0], [1, 1]], [[2, 2], [3, 3, 9]]]}).geom_type == "MultiLineString"


def test_geometrie_polygone_troue_et_multipolygone():
    exterieur = [[0, 0], [0, 10], [10, 10], [10, 0], [0, 0]]       # horaire
    trou = [[2, 2], [4, 2], [4, 4], [2, 4], [2, 2]]                # antihoraire
    g = esri.geometrie({"rings": [exterieur, trou]})
    assert isinstance(g, Polygon) and len(g.interiors) == 1 and g.area == 96
    autre = [[20, 0], [20, 5], [25, 5], [25, 0], [20, 0]]
    m = esri.geometrie({"rings": [exterieur, autre]})
    assert isinstance(m, MultiPolygon) and len(m.geoms) == 2


def test_geometrie_trou_orphelin_garde_comme_exterieur():
    antihoraire = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
    g = esri.geometrie({"rings": [antihoraire]})
    assert isinstance(g, Polygon) and g.area == 100


def test_geometrie_courbe_signalee():
    with pytest.raises(esri.ErreurService, match="courbes"):
        esri.geometrie({"curveRings": []})


def test_systeme_esri_jamais_suppose():
    assert esri.systeme_esri({"wkid": 102110, "latestWkid": 2154}).to_epsg() == 2154
    assert esri.systeme_esri({"wkid": 102100}).to_epsg() == 3857
    assert "Lambert" in esri.systeme_esri({"wkid": 102110}).name
    with pytest.raises(esri.ErreurService, match="ne déclare pas"):
        esri.systeme_esri(None)
    with pytest.raises(esri.ErreurService, match="non reconnu"):
        esri.systeme_esri({"wkid": 999999})


# ---------------------------------------------------------------- téléchargement

def test_pagination_par_decalage(cfg_test, gares, faux):
    gdf, meta = esri.telecharger(gares, cfg_test, faux.client())
    assert len(gdf) == 5 and meta["pagination"] == "decalage" and meta["taille_page"] == 2
    assert faux.nb_requetes("Gares/FeatureServer/0/query") == 1 + 3   # comptage + 3 pages
    assert gdf.crs.to_epsg() == 2154
    assert list(gdf["nom"])[0] == "Massy-Palaiseau"
    assert gdf.loc[0, "mise_en_service"] == "2013-01-01T00:00:00+00:00"   # date Esri (ms) convertie
    assert "FID_esri" in gdf.columns and "FID" not in gdf.columns          # « fid » réservé par le GeoPackage


def test_taille_page_declaree_bornee_par_le_service(cfg_test, gares, faux):
    gares["taille_page"] = 1
    gdf, meta = esri.telecharger(gares, cfg_test, faux.client())
    assert len(gdf) == 5 and faux.nb_requetes("Gares/FeatureServer/0/query") == 1 + 5
    gares["taille_page"] = 50
    assert esri.telecharger(gares, cfg_test, faux.client())[1]["taille_page"] == 2


def test_pagination_par_identifiants_mapserver(cfg_test, faux):
    s = service(cfg_test, "Communes (faux service sans pagination)")
    gdf, meta = esri.telecharger(s, cfg_test, faux.client())
    assert meta["pagination"] == "identifiants" and len(gdf) == 3
    assert gdf.geometry.iloc[0].geom_type == "Polygon" and len(gdf.geometry.iloc[0].interiors) == 1
    assert gdf.geometry.iloc[1].geom_type == "MultiPolygon"
    assert gdf.geometry.iloc[2] is None


def test_max_objets_tronque_et_signale(cfg_test, gares, faux):
    gares["max_objets"] = 3
    c = esri.couche_service(cfg_test, gares, 0)
    assert c.nb_objets == 3
    assert any("3 objets téléchargés sur 5" in a for a in c.avertissements)


def test_ressource_qui_n_est_pas_une_couche(cfg_test, faux):
    s = {"libelle": "Groupe", "url": f"{RACINE}/Groupe/MapServer/0"}
    with pytest.raises(esri.ErreurService, match="Group Layer"):
        esri.telecharger(s, cfg_test, faux.client())


def test_service_sans_systeme_jamais_suppose(cfg_test, faux):
    s = {"libelle": "Sans", "url": f"{RACINE}/SansSysteme/FeatureServer/0"}
    with pytest.raises(esri.ErreurService, match="ne déclare pas de système"):
        esri.telecharger(s, cfg_test, faux.client())
    s["systeme"] = "EPSG:2154"   # déclaration explicite dans la configuration
    gdf, _ = esri.telecharger(s, cfg_test, faux.client())
    assert gdf.crs.to_epsg() == 2154


def test_url_inconnue_du_service(cfg_test, faux):
    s = {"libelle": "X", "url": f"{RACINE}/Inconnue/FeatureServer/0"}
    with pytest.raises(esri.ErreurService, match="Invalid URL"):
        esri.telecharger(s, cfg_test, faux.client())


def test_reponse_html_portail(cfg_test, gares):
    faux = FauxArcGIS(reponse_html={"Gares/FeatureServer/0"})
    with pytest.raises(esri.ErreurService, match="illisible"):
        esri.telecharger(gares, cfg_test, faux.client())


# ---------------------------------------------------------------- jeton

def test_jeton_dans_le_corps_jamais_dans_l_url_ni_le_cache(cfg_test, gares, monkeypatch, tmp_path):
    secret = "JETON-SECRET-123"
    faux = FauxArcGIS(jeton=secret)
    monkeypatch.setattr(esri, "fabrique_client", lambda cfg: faux.client())
    monkeypatch.setenv("FAUX_JETON", secret)
    gares["jeton_env"] = "FAUX_JETON"
    c = esri.couche_service(cfg_test, gares, 0)
    assert c.nb_objets == 5 and c.service["source"] == "service"
    assert all(secret not in str(r.url) for r in faux.requetes)
    assert all(f"token={secret}" in r.content.decode() for r in faux.requetes)
    for f in Path(cfg_test["esri"]["dossier_cache"]).iterdir():
        assert secret.encode() not in f.read_bytes()
    assert secret not in json.dumps(c.publique())


def test_jeton_absent_ou_refuse(cfg_test, gares, monkeypatch):
    faux = FauxArcGIS(jeton="attendu")
    monkeypatch.setattr(esri, "fabrique_client", lambda cfg: faux.client())
    monkeypatch.delenv("FAUX_JETON_ABSENT", raising=False)
    gares["jeton_env"] = "FAUX_JETON_ABSENT"
    with pytest.raises(AnomalieCouche, match="FAUX_JETON_ABSENT"):
        esri.couche_service(cfg_test, gares, 0)
    monkeypatch.setenv("FAUX_JETON_FAUX", "mauvais")
    gares["jeton_env"] = "FAUX_JETON_FAUX"
    with pytest.raises(AnomalieCouche, match="jeton invalide") as e:
        esri.couche_service(cfg_test, gares, 0, forcer=True)
    assert "mauvais" not in str(e.value)


# ---------------------------------------------------------------- cache et repli

def test_cache_reutilise_sans_reseau(cfg_test, gares, faux):
    c1 = esri.couche_service(cfg_test, gares, 0)
    n = len(faux.requetes)
    c2 = esri.couche_service(cfg_test, gares, 0)
    assert c1.service["source"] == "service" and c2.service["source"] == "cache"
    assert len(faux.requetes) == n
    assert c2.service["date_maj"] == c1.service["date_maj"]
    assert c2.format == "Service Esri" and c2.fichier == URL_GARES and c2.systeme == "EPSG:2154"


def test_actualisation_forcee(cfg_test, gares, faux):
    esri.couche_service(cfg_test, gares, 0)
    n = len(faux.requetes)
    c = esri.couche_service(cfg_test, gares, 0, forcer=True)
    assert c.service["source"] == "service" and len(faux.requetes) > n


def _vieillir_cache(cfg, service, heures):
    fiche = Path(cfg["esri"]["dossier_cache"]) / f"{esri.identifiant(service)}.json"
    meta = json.loads(fiche.read_text(encoding="utf-8"))
    meta["date_maj"] = (datetime.now() - timedelta(hours=heures)).isoformat(timespec="seconds")
    fiche.write_text(json.dumps(meta), encoding="utf-8")


def test_cache_expire_recharge(cfg_test, gares, faux):
    esri.couche_service(cfg_test, gares, 0)
    _vieillir_cache(cfg_test, gares, 25)
    n = len(faux.requetes)
    assert esri.couche_service(cfg_test, gares, 0).service["source"] == "service"
    assert len(faux.requetes) > n


def test_service_en_panne_cache_perime_utilise(cfg_test, gares, faux):
    esri.couche_service(cfg_test, gares, 0)
    _vieillir_cache(cfg_test, gares, 48)
    faux.panne.add("Gares")
    c = esri.couche_service(cfg_test, gares, 0)
    assert c.service["source"] == "cache_perime" and c.nb_objets == 5
    assert "service indisponible" in c.avertissements[0] and "cache du" in c.avertissements[0]


def test_service_en_panne_repli_sur_export(cfg_test, faux):
    c = esri.couche_service(cfg_test, service(cfg_test, "Service en panne avec repli"), 0)
    assert c.service["source"] == "repli" and c.service["export"] == "idfm_gares_rer.geojson"
    assert c.format.startswith("Service Esri (export") and c.nb_objets > 0
    assert "repli sur l'export" in c.avertissements[0]


def test_service_en_panne_sans_rien_anomalie_et_nouvel_essai_differe(cfg_test, faux):
    s = service(cfg_test, "Service en panne sans repli")
    with pytest.raises(AnomalieCouche, match="injoignable.*ni cache local ni export"):
        esri.couche_service(cfg_test, s, 0)
    n = len(faux.requetes)
    with pytest.raises(AnomalieCouche):
        esri.couche_service(cfg_test, s, 0)
    assert len(faux.requetes) == n   # pas de nouvel essai avant « nouvel_essai_minutes »


def test_export_de_repli_introuvable(cfg_test, faux):
    s = {"libelle": "P", "url": f"{RACINE}/Panne/FeatureServer/2", "export": "absent.gpkg"}
    with pytest.raises(AnomalieCouche, match="export de repli introuvable"):
        esri.couche_service(cfg_test, s, 0)


def test_un_service_en_anomalie_ne_bloque_pas_les_autres(cfg_test, faux):
    couches, anomalies = esri.couches_services(cfg_test)
    assert [c.libelle for c in couches] == [
        "Gares (faux service)", "Communes (faux service sans pagination)", "Service en panne avec repli"]
    assert [a["couche"] for a in anomalies] == ["Service en panne sans repli"]
    assert anomalies[0]["service"] is True


# ---------------------------------------------------------------- configuration

def _cfg_avec(cfg_test, **service):
    cfg = copy.deepcopy(cfg_test)
    cfg["services_esri"] = [{"libelle": "S", "url": URL_GARES, **service}]
    return cfg


def test_config_url_de_couche_exigee(cfg_test):
    cfg = _cfg_avec(cfg_test)
    cfg["services_esri"][0]["url"] = f"{RACINE}/Gares/FeatureServer"
    with pytest.raises(ConfigInvalide, match="FeatureServer/<n>"):
        _controler(cfg, Path("sig.yaml"))


def test_config_refuse_un_jeton_en_clair(cfg_test):
    with pytest.raises(ConfigInvalide, match="jeton_env"):
        _controler(_cfg_avec(cfg_test, token="abc"), Path("sig.yaml"))
    cfg = _cfg_avec(cfg_test)
    cfg["services_esri"][0]["url"] += "?token=abc"
    with pytest.raises(ConfigInvalide):
        _controler(cfg, Path("sig.yaml"))


def test_config_section_esri_requise(cfg_test):
    cfg = _cfg_avec(cfg_test)
    del cfg["esri"]
    with pytest.raises(ConfigInvalide, match="section « esri »"):
        _controler(cfg, Path("sig.yaml"))


def test_config_service_en_double(cfg_test):
    cfg = _cfg_avec(cfg_test)
    cfg["services_esri"].append({"libelle": "T", "url": URL_GARES + "/"})
    with pytest.raises(ConfigInvalide, match="même URL"):
        _controler(cfg, Path("sig.yaml"))


def test_config_projet_valide():
    cfg = charger_config()
    assert cfg["services_esri"] and cfg["reseau"]["proxy"]


# ---------------------------------------------------------------- API

def test_api_couches_inclut_services_et_anomalies(client_test):
    inv = client_test.get("/api/sig/couches").json()
    services = [c for c in inv["couches"] if c["service"]]
    assert {c["service"]["source"] for c in services} == {"service", "repli"}
    assert inv["nb_services"] == 4
    assert any(a.get("service") and a["couche"] == "Service en panne sans repli" for a in inv["anomalies"])
    fichiers = [c for c in inv["couches"] if not c["service"]]
    assert fichiers, "les couches fichiers restent présentes"


def test_api_objets_fiche_recherche_sur_service(client_test):
    inv = client_test.get("/api/sig/couches").json()
    g = next(c for c in inv["couches"] if c["libelle"] == "Gares (faux service)")
    fc = client_test.get(f"/api/sig/couches/{g['id']}/objets").json()
    assert len(fc["features"]) == 5
    lon, lat = fc["features"][0]["geometry"]["coordinates"]
    assert 2.2 < lon < 2.3 and 48.7 < lat < 48.8   # Massy
    fiche = client_test.get(f"/api/sig/couches/{g['id']}/objets/0").json()
    assert fiche["libelle"] == "Massy-Palaiseau"
    assert fiche["coordonnees"]["origine"]["point"] == [646310.0, 6852080.0]
    r = client_test.get(f"/api/sig/couches/{g['id']}/recherche", params={"q": "versailles"}).json()
    assert r["total"] == 1


def test_api_actualiser(client_test, faux):
    inv = client_test.get("/api/sig/couches").json()
    g = next(c for c in inv["couches"] if c["libelle"] == "Gares (faux service)")
    n = faux.nb_requetes("Gares")
    r = client_test.post(f"/api/sig/services/{g['id']}/actualiser")
    assert r.status_code == 200 and faux.nb_requetes("Gares") > n
    assert client_test.post("/api/sig/services/inconnu/actualiser").status_code == 404


def test_api_couche_service_sans_donnees_422(client_test):
    inv = client_test.get("/api/sig/couches").json()
    assert not any(c["libelle"] == "Service en panne sans repli" for c in inv["couches"])
    s = {"libelle": "Service en panne sans repli", "url": f"{RACINE}/Panne/FeatureServer/1"}
    r = client_test.get(f"/api/sig/couches/{esri.identifiant(s)}/objets")
    assert r.status_code == 422 and "ni cache local" in r.json()["detail"]


# ---------------------------------------------------------------- service réel (réseau)

@pytest.mark.reseau
def test_service_public_reel(monkeypatch, tmp_path):
    """Service public de config/sig.yaml, à travers le proxy : pytest -m reseau"""
    cfg = charger_config()
    cfg["esri"]["dossier_cache"] = str(tmp_path)
    monkeypatch.undo()   # vrai client HTTP
    s = dict(cfg["services_esri"][0], taille_page=100)
    c = esri.couche_service(cfg, s, 0, forcer=True)
    assert c.service["source"] == "service" and c.nb_objets > 100 and c.systeme == "EPSG:2154"

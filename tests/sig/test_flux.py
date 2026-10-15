"""Connecteurs WFS 2.0 et GeoJSON par URL (lot 5, catalogue Open Data) : téléchargement, emprise, système,
troncature, erreurs, cache et repli communs avec les services Esri, contrôles de configuration."""
import copy
from pathlib import Path

import pytest

from serveur.sig import esri, flux, objets
from serveur.sig import inventaire as inv_sig
from serveur.sig.config import ConfigInvalide, _controler
from tests.sig import faux_flux as ff

MASSY = [2.20, 48.69, 2.32, 48.76]


def _wfs(**kw):
    return {"libelle": "Zonages PLU", "url": ff.WFS, "couche": "wfs_du:zone_urba", "emprise_wgs84": MASSY, **kw}


def _geojson(**kw):
    return {"libelle": "Gares", "url": ff.GEOJSON, **kw}


def _connecteur(type_):
    return next(c for c in esri.connecteurs() if c.type == type_)


def _couche(cfg, service, type_, serveur, **kw):
    return esri.couche_service(cfg, service, rang=0, client=serveur.client(), connecteur=_connecteur(type_), **kw)


# ---------------------------------------------------------------- WFS

def test_wfs_pagination_emprise_et_systeme(cfg_test):
    serveur = ff.FauxFlux()
    c = _couche(cfg_test, _wfs(taille_page=2), "WFS", serveur)

    assert c.nb_objets == 5 and c.format == "Service WFS"
    assert c.systeme == "EPSG:2154" and c.systeme_source == "demandé au service"
    assert c.service["type"] == "WFS" and c.service["couche"] == "wfs_du:zone_urba"
    p = [dict(r.url.params) for r in serveur.requetes]
    assert [q["STARTINDEX"] for q in p] == ["0", "2", "4"]                # 3 pages de 2
    assert p[0]["BBOX"] == "48.69,2.2,48.76,2.32,urn:ogc:def:crs:EPSG::4326"   # latitude, longitude en WFS 2.0
    assert p[0]["SRSNAME"] == "EPSG:2154" and p[0]["OUTPUTFORMAT"] == "application/json"


def test_wfs_max_objets_tronque_et_signale(cfg_test):
    c = _couche(cfg_test, _wfs(taille_page=2, max_objets=3), "WFS", ff.FauxFlux())
    assert c.nb_objets == 3
    assert any("3 objets téléchargés sur 5" in a for a in c.avertissements)


def test_wfs_champ_fid_renomme(cfg_test):
    """« fid » est réservé par le GeoPackage du cache."""
    c = _couche(cfg_test, _wfs(), "WFS", ff.FauxFlux())
    gdf, _ = objets._lire(c)
    assert "fid_source" in gdf.columns


def test_wfs_couche_inconnue_message_ogc(cfg_test):
    with pytest.raises(inv_sig.AnomalieCouche, match="Feature type wfs_du:inconnue unknown"):
        _couche(cfg_test, _wfs(couche="wfs_du:inconnue"), "WFS", ff.FauxFlux())


def test_wfs_rien_dans_l_emprise(cfg_test, monkeypatch):
    monkeypatch.setattr(ff, "ZONES", [])
    with pytest.raises(inv_sig.AnomalieCouche, match="aucun objet dans l'emprise"):
        _couche(cfg_test, _wfs(), "WFS", ff.FauxFlux())


# ---------------------------------------------------------------- GeoJSON par URL

def test_geojson_systeme_par_defaut_wgs84_et_attributs_imbriques(cfg_test):
    c = _couche(cfg_test, _geojson(), "GeoJSON", ff.FauxFlux())
    assert c.nb_objets == 3 and c.format == "GeoJSON (URL)"
    assert c.systeme == "EPSG:4326" and c.systeme_source == "norme GeoJSON (WGS84)"
    assert any("1 objet(s) sans géométrie écarté(s)" in a for a in c.avertissements)
    gdf, _ = objets._lire(c)
    assert '"cp": "75001"' in gdf.loc[gdf["nom"] == "Châtelet-Les Halles", "adresse"].iloc[0]


def test_geojson_filtre_d_emprise(cfg_test):
    c = _couche(cfg_test, _geojson(emprise_wgs84=MASSY), "GeoJSON", ff.FauxFlux())
    assert c.nb_objets == 2            # Châtelet est hors de l'emprise de Massy
    assert c.service["where"].startswith("emprise")


def test_geojson_systeme_declare_lambert93(cfg_test):
    c = _couche(cfg_test, _geojson(url=ff.GEOJSON_L93, systeme="EPSG:2154", emprise_wgs84=MASSY), "GeoJSON", ff.FauxFlux())
    assert c.systeme == "EPSG:2154" and c.systeme_source == "configuration" and c.nb_objets == 2


def test_geojson_page_html_message_clair(cfg_test):
    with pytest.raises(inv_sig.AnomalieCouche, match="ce n'est pas du GeoJSON"):
        _couche(cfg_test, _geojson(url=ff.GEOJSON_HTML), "GeoJSON", ff.FauxFlux())


def test_geojson_trop_gros_refuse(cfg_test):
    with pytest.raises(inv_sig.AnomalieCouche, match="plus de 1 Mo"):
        _couche(cfg_test, _geojson(url=ff.GEOJSON_GROS, max_mo=1), "GeoJSON", ff.FauxFlux())


# ---------------------------------------------------------------- cache et repli communs

def test_cache_puis_panne_cache_perime(cfg_test):
    service = _geojson(cache_heures=0)
    _couche(cfg_test, service, "GeoJSON", ff.FauxFlux())
    esri.reinitialiser()
    c = _couche(cfg_test, service, "GeoJSON", ff.FauxFlux(panne=True))
    assert c.service["source"] == "cache_perime" and c.nb_objets == 3
    assert any("service indisponible" in a for a in c.avertissements)


def test_panne_sans_cache_anomalie_dans_l_inventaire(cfg_test, monkeypatch):
    cfg = copy.deepcopy(cfg_test)
    cfg["services_wfs"] = [_wfs()]
    serveur = ff.FauxFlux(panne=True)
    couches, anomalies = esri.couches_services(cfg, client=serveur.client())
    assert any(a["couche"] == "Zonages PLU" and "ni cache local" in a["message"] for a in anomalies)


def test_identifiants_distincts_et_service_retrouve(cfg_test):
    cfg = copy.deepcopy(cfg_test)
    cfg["services_wfs"], cfg["services_geojson"] = [_wfs()], [_geojson(), _geojson(emprise_wgs84=MASSY)]
    ids = [c.identifiant(s) for s, c in esri.services_declares(cfg)]
    assert len(set(ids)) == len(ids)
    service, connecteur = esri.trouver_service(cfg, flux.identifiant_wfs(_wfs()))
    assert connecteur.type == "WFS" and service["couche"] == "wfs_du:zone_urba"


# ---------------------------------------------------------------- configuration

@pytest.mark.parametrize("section, service, attendu", [
    ("services_wfs", {"libelle": "x", "url": ff.WFS, "couche": "a:b"}, "emprise_wgs84"),
    ("services_wfs", {"libelle": "x", "url": ff.WFS, "emprise_wgs84": MASSY}, "couche"),
    ("services_wfs", {"libelle": "x", "url": ff.WFS, "couche": "a:b", "emprise_wgs84": [2.3, 48.7, 2.2, 48.8]}, "ouest, sud, est, nord"),
    ("services_geojson", {"libelle": "x", "url": "ftp://serveur/f.geojson"}, "http"),
    ("services_geojson", {"libelle": "x", "url": ff.GEOJSON + "?apikey=secret"}, "aucun jeton"),
    ("services_geojson", {"libelle": "x", "url": ff.GEOJSON, "max_mo": 0}, "max_mo"),
])
def test_configuration_invalide_message_clair(cfg_test, section, service, attendu):
    cfg = copy.deepcopy(cfg_test)
    cfg[section] = [service]
    with pytest.raises(ConfigInvalide, match=attendu):
        _controler(cfg, Path("sig.yaml"))


def test_configuration_doublon_refuse(cfg_test):
    cfg = copy.deepcopy(cfg_test)
    cfg["services_geojson"] = [_geojson(), _geojson(libelle="autre")]
    with pytest.raises(ConfigInvalide, match="même source"):
        _controler(cfg, Path("sig.yaml"))

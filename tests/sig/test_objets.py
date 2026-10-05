"""Objets des couches : conversion WGS84, coordonnées d'origine, filtre d'emprise, recherche, routes."""
import pytest

from serveur.sig import objets as obj


def test_conversion_identique_a_celle_de_la_sncf(couche):
    """Contrôle indépendant : le jeu SNCF fournit pour chaque gare Lambert 93 et WGS84 ; écart < 1e-6° (~10 cm)."""
    c = couche("Gares SNCF (Île-de-France)")
    charge = obj.charger(c)
    sncf_lon = charge.origine["x_wgs84"].astype(float)
    sncf_lat = charge.origine["y_wgs84"].astype(float)
    assert (charge.wgs84.geometry.x - sncf_lon).abs().max() < 1e-6
    assert (charge.wgs84.geometry.y - sncf_lat).abs().max() < 1e-6


def test_objets_convertis_en_wgs84(couche, cfg_test):
    fc = obj.objets(couche("Gares SNCF (Île-de-France)"), None, cfg_test["chargement"]["seuil_objets_filtre_emprise"])
    assert fc["type"] == "FeatureCollection" and fc["tronque"] is False
    for f in fc["features"]:
        lon, lat = f["geometry"]["coordinates"]
        assert 1.4 < lon < 3.6 and 48.1 < lat < 49.3
        assert f["id"] == f["properties"]["_n"]


@pytest.mark.parametrize("libelle", ["idfm_reseau › gares", "idfm_metro_l93", "idfm_gares_rer",
                                     "autre_systeme_cc49 › gares_cc49", "systeme_declare_config › gares"])
def test_chaque_format_au_bon_endroit(couche, libelle):
    fc = obj.objets(couche(libelle), [0.8, 47.5, 4.0, 49.8], 100_000)
    assert len(fc["features"]) > 0
    assert fc["total_dans_emprise"] == len(obj.charger(couche(libelle)).wgs84)  # tout dans le Bassin parisien


def test_cc49_et_l93_au_meme_endroit(couche):
    """La même gare, lue en CC49 et en Lambert 93, tombe au même point après conversion."""
    a = obj.charger(couche("autre_systeme_cc49 › gares_cc49")).wgs84.geometry.iloc[0]
    b = obj.charger(couche("idfm_reseau › gares")).wgs84.geometry.iloc[0]
    assert a.distance(b) < 1e-7


def test_grosse_couche_exige_emprise(couche):
    with pytest.raises(ValueError, match="bbox"):
        obj.objets(couche("Gares SNCF (France)"), None, 1000)


def test_grosse_couche_filtree_sur_emprise(couche):
    paris = [2.25, 48.81, 2.42, 48.91]
    fc = obj.objets(couche("Gares SNCF (France)"), paris, 1000)
    assert fc["filtre_emprise"] is True and 0 < len(fc["features"]) < 1000
    for f in fc["features"]:
        lon, lat = f["geometry"]["coordinates"]
        assert paris[0] <= lon <= paris[2] and paris[1] <= lat <= paris[3]


def test_grosse_couche_tronquee_au_seuil(couche):
    france = [-5.5, 41.0, 10.0, 51.5]
    fc = obj.objets(couche("Gares SNCF (France)"), france, 1000)
    assert fc["tronque"] is True and len(fc["features"]) == 1000 and fc["total_dans_emprise"] > 1000


def test_fiche_garde_coordonnees_origine(couche):
    c = couche("autre_systeme_cc49 › gares_cc49")
    f = obj.fiche(c, 0)
    assert f["coordonnees"]["origine"]["systeme"] == "EPSG:3949"
    x, y = f["coordonnees"]["origine"]["point"]
    assert 1_600_000 < x < 1_720_000 and 8_150_000 < y < 8_230_000
    x93, y93 = f["coordonnees"]["lambert93"]["point"]
    attendu = obj.charger(couche("idfm_reseau › gares")).origine.geometry.iloc[0]
    assert x93 == pytest.approx(attendu.x, abs=0.01) and y93 == pytest.approx(attendu.y, abs=0.01)
    assert f["attributs"]["nom_gares"] == f["libelle"]


def test_fiche_ligne_donne_une_emprise(couche):
    f = obj.fiche(couche("Réseau ferré IDF"), 0)
    assert "emprise" in f["coordonnees"]["lambert93"]


def test_fiche_objet_absent(couche):
    assert obj.fiche(couche("Gares SNCF (Île-de-France)"), 999_999) is None


def test_recherche_insensible_casse_et_accents(couche):
    c = couche("idfm_reseau › gares")
    r = obj.rechercher(c, "GARE DE LYON", 50)
    assert r["total"] >= 1
    assert all("lyon" in obj.normaliser(x["valeur"]) for x in r["resultats"])
    assert obj.rechercher(c, "chatelet", 50)["total"] >= 1  # « Châtelet » dans les données IDFM


def test_recherche_limitee(couche):
    r = obj.rechercher(couche("Gares SNCF (France)"), "pa", 5)
    assert r["total"] > 5 and len(r["resultats"]) == 5


def test_recherche_trop_courte(couche):
    assert obj.rechercher(couche("Gares SNCF (France)"), "a", 50)["total"] == 0


# ---------------------------------------------------------------- routes

def test_route_couches(client_test):
    r = client_test.get("/api/sig/couches")
    assert r.status_code == 200
    corps = r.json()
    assert corps["couches"] and corps["anomalies"]
    assert all("_chemin" not in c and "_csv" not in c for c in corps["couches"])


def test_routes_objets_fiche_recherche(client_test, couche):
    c = couche("Gares SNCF (Île-de-France)")
    assert client_test.get(f"/api/sig/couches/{c.id}/objets").status_code == 200
    r = client_test.get(f"/api/sig/couches/{c.id}/recherche", params={"q": "versailles"}).json()
    assert r["total"] >= 1
    n = r["resultats"][0]["n"]
    assert client_test.get(f"/api/sig/couches/{c.id}/objets/{n}").json()["n"] == n


def test_routes_erreurs_claires(client_test, couche):
    assert client_test.get("/api/sig/couches/inconnue/objets").status_code == 404
    gros = couche("Gares SNCF (France)").id
    assert client_test.get(f"/api/sig/couches/{gros}/objets").status_code == 400
    assert client_test.get(f"/api/sig/couches/{gros}/objets", params={"bbox": "1,2,3"}).status_code == 400
    assert client_test.get(f"/api/sig/couches/{gros}/objets", params={"bbox": "2.2,48.8,2.5,48.95"}).status_code == 200

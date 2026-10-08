"""Inventaire des couches fichiers : un test par format, un test par cas piégé."""
import pytest


# ---------------------------------------------------------------- un test par format

def test_geopackage_plusieurs_couches(couche):
    gares = couche("idfm_reseau › gares")
    traces = couche("Réseau ferré IDF")  # libellé surchargé dans la config
    assert gares.format == traces.format == "GeoPackage"
    assert gares.geometrie == "point" and traces.geometrie == "ligne"
    assert gares.systeme == "EPSG:2154" and gares.avertissements == []
    assert gares.nb_objets == 1240
    assert traces.style["couleur"] == "#495057"  # style surchargé


def test_shapefile(couche):
    metro = couche("idfm_metro_l93")
    assert metro.format == "Shapefile"
    assert metro.geometrie == "ligne"
    assert metro.systeme == "EPSG:2154"
    assert metro.nb_objets > 0


def test_geojson_wgs84_signale(couche):
    rer = couche("idfm_gares_rer")
    assert rer.format == "GeoJSON"
    assert rer.systeme == "EPSG:4326"
    assert any("différent de EPSG:2154" in a for a in rer.avertissements)


def test_csv_declare(couche):
    gares = couche("Gares SNCF (Île-de-France)")
    assert gares.format == "CSV" and gares.geometrie == "point"
    assert gares.systeme == "EPSG:2154" and gares.systeme_source == "configuration"
    assert gares.champ_libelle == "libelle"
    assert gares.nb_objets > 500


# ---------------------------------------------------------------- informations communes

def test_emprise_bassin_parisien(couche):
    """Le réseau IDFM déborde de l'Île-de-France (Dreux, Évreux, Beauvais, Montargis…)."""
    gares = couche("idfm_reseau › gares")
    minx, miny, maxx, maxy = gares.emprise_wgs84
    assert 0.8 < minx < maxx < 4.0 and 47.5 < miny < maxy < 49.8
    minx, miny, maxx, maxy = gares.emprise_l93
    assert 500_000 < minx < maxx < 800_000 and 6_700_000 < miny < maxy < 6_980_000


def test_seuil_filtre_emprise(couche, cfg_test):
    seuil = cfg_test["chargement"]["seuil_objets_filtre_emprise"]
    assert couche("Gares SNCF (France)").filtre_emprise is True
    assert couche("Gares SNCF (Île-de-France)").nb_objets <= seuil
    assert couche("Gares SNCF (Île-de-France)").filtre_emprise is False


def test_couleurs_par_defaut_distinctes(inv):
    sans_surcharge = [c.style["couleur"] for c in inv.couches if c.libelle != "Réseau ferré IDF"][:5]
    assert len(set(sans_surcharge)) == len(sans_surcharge)


def test_autre_systeme_converti_et_signale(couche):
    cc49 = couche("autre_systeme_cc49 › gares_cc49")
    assert cc49.systeme == "EPSG:3949"
    assert any("EPSG:3949" in a for a in cc49.avertissements)
    assert cc49.emprise_wgs84 == pytest.approx(couche("idfm_reseau › gares").emprise_wgs84, abs=1e-3)


def test_systeme_declare_en_configuration(couche):
    c = couche("systeme_declare_config › gares")
    assert c.systeme == "EPSG:2154" and c.systeme_source == "configuration"
    assert any("remplacé par celui de la configuration" in a for a in c.avertissements)


# ---------------------------------------------------------------- jeu piégé : anomalies sans blocage

@pytest.mark.parametrize(
    "fichier, extrait",
    [
        ("piege_corrompu.gpkg", "illisible"),
        ("piege_vide.geojson", "vide"),
        ("piege_texte.geojson", "illisible"),
        ("piege_sans_prj.shp", ".prj absent"),
        ("piege_sans_shx.shp", ".shx"),
        ("piege_csv_non_declare.csv", "non déclaré"),
        ("piege_csv_sans_colonnes.csv", "colonnes absentes"),
        ("piege_systeme_incoherent.gpkg", "incohérentes"),
        ("absent.gpkg", "introuvable"),
    ],
)
def test_piege_en_anomalie(anomalie, fichier, extrait):
    trouvees = anomalie(fichier)
    assert trouvees, f"{fichier} devrait être en anomalie"
    assert any(extrait in a["message"] for a in trouvees), trouvees


def test_messages_sans_chemin_complet(inv):
    for a in inv.anomalies:
        assert inv.dossier not in a["message"], a


def test_csv_lignes_invalides_ecartees(couche):
    c = couche("piege_csv_xy_invalides")
    assert c.nb_objets == 15
    assert any("5 ligne(s) écartée(s)" in a for a in c.avertissements)


def test_pieges_ne_bloquent_pas_les_autres(inv):
    assert len(inv.couches) >= 8
    assert len(inv.anomalies) >= 9


def test_fichiers_non_geographiques_ignores(inv):
    assert {"fichier": "notes.txt", "message": "format non géré"} in inv.ignores
    fichiers = {i["fichier"] for i in inv.ignores}
    assert not any(f.endswith((".dbf", ".prj", ".shx", ".cpg")) for f in fichiers)


# ---------------------------------------------------------------- exports InfraGéo (cas réels du 2026-10-08)

def test_polygones_mixtes_famille_polygone(couche):
    """Polygon et MultiPolygon mêlés : GDAL déclare « Unknown », la couche doit garder le style des polygones."""
    c = couche("infrageo_polygones_mixtes")
    assert c.geometrie == "polygone"
    assert c.type_geometrie == "MultiPolygon / Polygon"
    assert c.style["opacite"] == 0.35 and "rayon" not in c.style


def test_esri_json_lu_en_cc49(couche):
    c = couche("infrageo_esri (.json)")
    assert c.format == "Esri JSON" and c.geometrie == "ligne"
    assert c.systeme == "EPSG:3949" and c.nb_objets == 12
    minx, miny, maxx, maxy = c.emprise_wgs84   # CC49 converti : on retombe en Île-de-France
    assert 0.8 < minx < maxx < 4.0 and 47.5 < miny < maxy < 49.8
    assert not any("tronqué" in a for a in c.avertissements)


def test_esri_json_tronque_signale(couche):
    c = couche("infrageo_esri_tronque")
    assert c.nb_objets == 12   # la couche s'affiche quand même
    assert any("export ArcGIS tronqué" in a and "12 objets" in a for a in c.avertissements)


def test_json_coupe_en_anomalie(anomalie):
    messages = [a["message"] for a in anomalie("piege_json_coupe.json")]
    assert messages and "tronqué" in messages[0]


def test_copie_identique_signalee(couche):
    original, copie = couche("infrageo_polygones_mixtes"), couche("infrageo_copie")
    assert any("contenu identique à infrageo_copie.json" in a for a in original.avertissements)
    assert any("contenu identique à infrageo_polygones_mixtes.geojson" in a for a in copie.avertissements)
    assert not any("contenu identique" in a for a in couche("infrageo_esri (.json)").avertissements)


def test_libelles_en_double_distingues(couche):
    """CANA_interstations.geojson et CANA_interstations.json : deux couches, deux libellés distincts."""
    esri, geojson = couche("infrageo_esri (.json)"), couche("infrageo_esri (.geojson)")
    assert esri.format == "Esri JSON" and geojson.format == "GeoJSON"
    # Rectangle CC49 converti en degrés : légèrement plus large que celui des mêmes objets en WGS84 (~100 m).
    assert esri.emprise_wgs84 == pytest.approx(geojson.emprise_wgs84, abs=2e-3)

"""Module IFC partagé (lot 3) : lecture, géoréférencement (une méthode par test), géométrie, propriétés."""
import json
import math
import struct

import pytest
from pyproj import Transformer
from shapely.geometry import shape

from commun.ifc import geometrie, georef, lecture, proprietes
from commun.ifc.tests.fabrique import ecrire_jeu

CANDIDATS = ["EPSG:2154", "EPSG:3949"]
EMPRISE_IDF = [1.4, 48.1, 3.6, 49.3]


@pytest.fixture(scope="module")
def jeu(tmp_path_factory):
    dossier = tmp_path_factory.mktemp("ifc")
    ref = ecrire_jeu(dossier)
    return dossier, ref


def resoudre(jeu, nom, **kw):
    f = lecture.ouvrir(jeu[0] / nom)
    params = {"systemes_candidats": CANDIDATS, "tolerance_m": 2000, "emprise_plausible_wgs84": EMPRISE_IDF, **kw}
    return georef.resoudre(lecture.georef_brut(f), **params)


def en_l93(pos, x=0.0, y=0.0):
    px, py, _ = georef.vers_projete(pos["repere"], x, y)
    return Transformer.from_crs(pos["systeme"], 2154, always_xy=True).transform(px, py)


# ---------------------------------------------------------------- lecture

def test_metadonnees(jeu):
    m = lecture.metadonnees(lecture.ouvrir(jeu[0] / "methode1_map_conversion_ifc4.ifc"))
    assert m["schema"] == "IFC4" and m["projet"] == "Map conversion"
    assert m["unite_longueur"] == "metre" and m["facteur_metre"] == 1.0


def test_fichier_qui_n_est_pas_un_ifc(jeu, tmp_path):
    with pytest.raises(lecture.MaquetteIllisible, match="ISO-10303-21"):
        lecture.ouvrir(jeu[0] / "piege_pas_un_ifc.ifc")
    vide = tmp_path / "vide.ifc"
    vide.write_bytes(b"")
    with pytest.raises(lecture.MaquetteIllisible, match="vide"):
        lecture.ouvrir(vide)
    with pytest.raises(lecture.MaquetteIllisible, match="introuvable"):
        lecture.ouvrir(tmp_path / "absent.ifc")


def test_georef_brut_angles_et_site(jeu):
    b = lecture.georef_brut(lecture.ouvrir(jeu[0] / "methode2a_site_cc49_identifie.ifc"))
    lon, lat = jeu[1]["wgs84"]
    assert b["schema"] == "IFC2X3" and b["map_conversion"] is None
    assert b["site"]["latitude"] == pytest.approx(lat, abs=1e-6)
    assert b["site"]["longitude"] == pytest.approx(lon, abs=1e-6)
    assert b["site"]["origine"][:2] == pytest.approx(list(jeu[1]["cc49"]), abs=0.01)


# ---------------------------------------------------------------- une méthode par test

def test_methode1_map_conversion(jeu):
    pos = resoudre(jeu, "methode1_map_conversion_ifc4.ifc")
    assert pos["statut"] == "positionnee" and pos["methode"] == "ifc_map_conversion" and pos["systeme"] == "EPSG:2154"
    assert en_l93(pos) == pytest.approx(jeu[1]["l93"], abs=0.01)
    assert pos["repere"]["origine"][2] == 80.0


def test_methode2a_site_projete_systeme_identifie(jeu):
    pos = resoudre(jeu, "methode2a_site_cc49_identifie.ifc")
    assert pos["methode"] == "ifc_site_projete" and pos["systeme"] == "EPSG:3949"
    assert pos["systeme_source"].startswith("identifié")
    assert en_l93(pos) == pytest.approx(jeu[1]["l93"], abs=0.01)
    assert pos["ecarts_latlon_m"]["EPSG:3949"] < 1


def test_methode2a_latlon_par_defaut_systeme_a_declarer(jeu):
    pos = resoudre(jeu, "methode2a_site_cc49_boston.ifc")
    assert pos["statut"] == "non_positionnee" and "système à déclarer" in pos["raison"]
    declare = resoudre(jeu, "methode2a_site_cc49_boston.ifc", systeme_declare="EPSG:3949")
    assert declare["statut"] == "positionnee" and declare["systeme_source"].startswith("déclaré")
    assert any("incohérentes" in a for a in declare["avertissements"])
    assert en_l93(declare) == pytest.approx(jeu[1]["l93"], abs=0.01)


def test_systeme_declare_hors_domaine_refuse(jeu):
    pos = resoudre(jeu, "methode2a_site_cc49_boston.ifc", systeme_declare="EPSG:2154")
    assert pos["statut"] == "non_positionnee" and "hors du domaine" in pos["raison"]


def test_systeme_identifie_prioritaire_sur_declaration(jeu):
    pos = resoudre(jeu, "methode2a_site_cc49_identifie.ifc", systeme_declare="EPSG:3948")
    assert pos["systeme"] == "EPSG:3949" and any("différent" in a for a in pos["avertissements"])


def test_methode2b_latitude_longitude(jeu):
    pos = resoudre(jeu, "methode2b_latlon.ifc")
    assert pos["methode"] == "ifc_site_latlon" and pos["precision"] == "approchée"
    assert en_l93(pos) == pytest.approx(jeu[1]["l93"], abs=0.01)
    # convergence des méridiens à l'ouest du méridien central du Lambert 93 : rotation négative d'environ 0,5°
    assert -0.7 < pos["repere"]["rotation_deg"] < -0.4


def test_methode2b_hors_emprise_plausible(jeu):
    pos = resoudre(jeu, "methode2b_latlon.ifc", emprise_plausible_wgs84=[5.0, 43.0, 6.0, 44.0])
    assert pos["statut"] == "non_positionnee" and "emprise plausible" in pos["raison"]


def test_methode3_manuelle(jeu):
    manuel = {"x": 650000.0, "y": 6860000.0, "altitude": 90.0, "rotation_deg": 30}
    pos = resoudre(jeu, "methode3_manuelle.ifc", manuel=manuel)
    assert pos["methode"] == "manuel" and pos["systeme"] == "EPSG:2154"
    # le point local (10, 0) tourné de 30° autour du point d'insertion
    x, y = en_l93(pos, 10, 0)
    assert (x, y) == pytest.approx((650000 + 10 * math.cos(math.radians(30)), 6860000 + 10 * math.sin(math.radians(30))), abs=1e-6)


def test_manuel_force_prioritaire(jeu):
    pos = resoudre(jeu, "methode1_map_conversion_ifc4.ifc", manuel={"x": 650000.0, "y": 6860000.0, "forcer": True})
    assert pos["methode"] == "manuel"


def test_manuel_invalide(jeu):
    pos = resoudre(jeu, "methode3_manuelle.ifc", manuel={"x": 1.0, "y": 2.0})
    assert pos["statut"] == "non_positionnee" and "hors du domaine" in pos["raison"]


def test_sans_georeferencement_non_positionnee(jeu):
    pos = resoudre(jeu, "sans_georeferencement.ifc")
    assert pos["statut"] == "non_positionnee" and pos["methode"] is None and pos["raison"]


def test_map_conversion_systeme_inconnu_jamais_suppose():
    brut = {"site": None, "map_conversion": {"est": 646310.0, "nord": 6852080.0, "hauteur": 0.0, "axe_x_abscisse": 1.0,
                                             "axe_x_ordonnee": 0.0, "echelle": 1.0, "systeme": "Lambert maison"}}
    pos = georef.resoudre(brut, CANDIDATS)
    assert pos["statut"] == "non_positionnee" and any("non reconnu" in a for a in pos["avertissements"])


def test_map_conversion_rotation():
    brut = {"site": None, "map_conversion": {"est": 0.0, "nord": 0.0, "hauteur": 0.0, "axe_x_abscisse": 0.0,
                                             "axe_x_ordonnee": 1.0, "echelle": 1.0, "systeme": "EPSG:2154"}}
    brut["map_conversion"].update(est=646310.0, nord=6852080.0)
    pos = georef.resoudre(brut, CANDIDATS)
    assert pos["repere"]["rotation_deg"] == pytest.approx(90)
    x, y, _ = georef.vers_projete(pos["repere"], 10, 0)
    assert (x, y) == pytest.approx((646310.0, 6852090.0), abs=1e-9)


# ---------------------------------------------------------------- géométrie et propriétés

def _glb(chemin):
    b = chemin.read_bytes()
    assert b[:4] == b"glTF"
    n = struct.unpack("<I", b[12:16])[0]
    return json.loads(b[20:20 + n])


def test_geometrie_glb_et_emprise(jeu, tmp_path):
    chemin = jeu[0] / "methode2a_site_cc49_identifie.ifc"
    decalage = lecture.georef_brut(lecture.ouvrir(chemin))["site"]["origine"]
    avancement = []
    r = geometrie.convertir(chemin, tmp_path, decalage, petits_elements_m=0.3, progression=avancement.append,
                            simplification_3d_m=0.005)
    assert r["nb_elements"] == 2 and r["moteur"].startswith("web-ifc")
    # deux pavés : 12 triangles chacun, rien à simplifier sans déformer
    assert r["triangles"] == 24 and r["triangles_detail"] == 0
    assert r["triangles_apres_simplification"] == r["triangles_avant_simplification"]
    assert avancement and avancement[-1] == 100
    g = _glb(tmp_path / "modele.glb")
    # format allégé : compression meshopt, objets regroupés par couleur, table des GlobalId pour le clic
    assert "EXT_meshopt_compression" in g["extensionsRequired"]
    guids = g["scenes"][0]["extras"]["guids"]
    assert len(guids) == 2 and all(len(x) == 22 for x in guids)
    assert len(g["meshes"]) == 1   # deux murs de même couleur : un seul maillage
    plages = g["meshes"][0]["primitives"][0]["extras"]["plages"]
    assert plages[0] == 0 and sorted(plages[1::2]) == [0, 1]
    assert not (tmp_path / "detail.glb").exists()   # aucun élément de moins de 0,3 m
    # emprise en coordonnées locales (origine du site), à la simplification près :
    # deux murs de 10 m × 0,2 m en équerre (recouvrement de 0,2 × 0,2 m)
    emprise = shape(r["emprise_locale"])
    assert emprise.area == pytest.approx(2 * 2.0 - 0.04, rel=0.05)
    assert max(abs(v) for v in emprise.bounds) < 20
    assert r["z_min"] == pytest.approx(0.0, abs=1e-3) and r["z_max"] == pytest.approx(3.0, abs=1e-3)
    # le modèle a sa propre origine, petite par rapport à celle du site
    assert max(abs(v) for v in r["origine_glb"]) < 20


def test_geometrie_objets_attendus_et_non_convertis(jeu, tmp_path):
    chemin = jeu[0] / "methode2a_site_cc49_identifie.ifc"
    attendus = geometrie.objets_attendus(lecture.ouvrir(chemin))
    assert sorted(attendus.values()) == ["IfcWall", "IfcWall"]   # le local (IfcSpace) est exclu
    geometrie.convertir(chemin, tmp_path)
    assert geometrie.non_convertis(attendus, tmp_path) == {}
    assert geometrie.non_convertis({**attendus, "0" * 22: "IfcBeam"}, tmp_path) == {"IfcBeam": 1}


def test_geometrie_petits_elements_dans_le_detail(jeu, tmp_path):
    chemin = jeu[0] / "methode2a_site_cc49_identifie.ifc"
    r = geometrie.convertir(chemin, tmp_path, petits_elements_m=50)
    assert r["taille_glb"] == 0 and r["taille_detail_glb"] > 0 and r["nb_petits_morceaux"] == r["nb_morceaux"]
    assert not (tmp_path / "modele.glb").exists()
    assert len(_glb(tmp_path / "detail.glb")["scenes"][0]["extras"]["guids"]) == 2


def test_geometrie_lancement_en_deux_temps(jeu, tmp_path):
    conv = geometrie.lancer(jeu[0] / "methode1_map_conversion_ifc4.ifc", tmp_path)
    r = geometrie.terminer(conv, delai_s=60)
    assert r["nb_elements"] >= 1 and (tmp_path / "modele.glb").is_file()


def test_geometrie_echec_signale(tmp_path):
    with pytest.raises(geometrie.ConversionImpossible):
        geometrie.convertir(tmp_path / "absent.ifc", tmp_path / "sortie")


def test_proprietes_index_et_fiche(jeu, tmp_path):
    f = lecture.ouvrir(jeu[0] / "methode2a_site_cc49_identifie.ifc")
    n = proprietes.indexer(f, tmp_path / "p.sqlite")
    assert n == 3   # deux murs et un local
    mur = f.by_type("IfcWall")[0]
    fiche = proprietes.lire_fiche(tmp_path / "p.sqlite", mur.GlobalId)
    assert fiche["type_ifc"] == "IfcWall" and fiche["etage"] == "N00"
    assert fiche["psets"]["Données d'identification"]["SGP_DI_Sous_Ouvrage"] in ("SO-01", "SO-02")
    assert proprietes.lire_fiche(tmp_path / "p.sqlite", "inconnu") is None
    (tmp_path / "p.sqlite").unlink()   # fichier fermé : supprimable (Windows)


# ---------------------------------------------------------------- site à l'origine, objets en coordonnées projetées

def _brut_objets_projetes(latlon=(49.0, 2.0)):
    """Cas réel (tunnel L15 1011I) : IfcSite à l'origine, RefLatitude/RefLongitude « rondes », objets en CC49."""
    lat, lon = latlon
    return {"schema": "IFC2X3", "nb_sites": 1, "nord_geographique": [0.0, 1.0], "map_conversion": None,
            "site": {"origine": [0.0, 0.0, 0.0], "axe_x": [1.0, 0.0], "latitude": lat, "longitude": lon, "altitude": 0.0}}


def test_objets_projetes_latlon_seules_refusees():
    pos = georef.resoudre(_brut_objets_projetes(), ["EPSG:2154"], 2000, emprise_plausible_wgs84=EMPRISE_IDF,
                          centre_geometrie=(1663283.0, 8179781.0), ecart_max_geometrie_m=20000)
    assert pos["statut"] == "non_positionnee" and "km de l'origine du site" in pos["raison"]


def test_objets_projetes_systeme_a_declarer_puis_declare():
    params = dict(systemes_candidats=CANDIDATS, tolerance_m=2000, emprise_plausible_wgs84=EMPRISE_IDF,
                  centre_geometrie=(1663283.0, 8179781.0), ecart_max_geometrie_m=20000)
    pos = georef.resoudre(_brut_objets_projetes(), **params)
    assert pos["statut"] == "non_positionnee" and "EPSG:3949" in pos["raison"]
    pos = georef.resoudre(_brut_objets_projetes(), systeme_declare="EPSG:3949", **params)
    assert pos["statut"] == "positionnee" and pos["methode"] == "ifc_site_projete" and pos["systeme"] == "EPSG:3949"
    # repère du site : coordonnées projetées = coordonnées monde des objets
    assert georef.vers_projete(pos["repere"], 1663283.0, 8179781.0)[:2] == pytest.approx((1663283.0, 8179781.0))
    assert any("objets en coordonnées projetées" in a for a in pos["avertissements"])


def test_objets_projetes_systeme_identifie_par_latlon():
    lon, lat = Transformer.from_crs(3949, 4326, always_xy=True).transform(1663283.0, 8179781.0)
    pos = georef.resoudre(_brut_objets_projetes((lat, lon)), CANDIDATS, 2000, emprise_plausible_wgs84=EMPRISE_IDF,
                          centre_geometrie=(1663283.0, 8179781.0), ecart_max_geometrie_m=20000)
    assert pos["statut"] == "positionnee" and pos["systeme"] == "EPSG:3949"


def test_geometrie_proche_du_site_latlon_toujours_acceptees(jeu):
    pos = resoudre(jeu, "methode2b_latlon.ifc", centre_geometrie=(3.0, 2.0), ecart_max_geometrie_m=20000)
    assert pos["methode"] == "ifc_site_latlon"

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
    f = lecture.ouvrir(jeu[0] / "methode2a_site_cc49_identifie.ifc")
    decalage = lecture.georef_brut(f)["site"]["origine"]
    avancement = []
    r = geometrie.convertir(f, tmp_path / "m.glb", decalage, processus=1, progression=avancement.append)
    assert r["nb_elements"] == 2 == r["nb_elements_attendus"]
    assert avancement and avancement[-1] <= 100
    g = _glb(tmp_path / "m.glb")
    noms = {n.get("name") for n in g["nodes"]}
    assert len({n for n in noms if n and len(n) == 22}) == 2   # un nœud par mur, nommé par son GlobalId
    # coordonnées locales : petites malgré les coordonnées CC49 du site
    maxi = max(abs(v) for a in g["accessors"] if "max" in a for v in a["max"])
    assert maxi < 20
    emprise = shape(r["emprise_locale"])
    # deux murs de 10 m × 0,2 m en équerre (recouvrement de 0,2 × 0,2 m) — à la simplification près
    assert emprise.area == pytest.approx(2 * 2.0 - 0.04, rel=0.05)
    assert r["z_min"] == pytest.approx(0.0, abs=1e-6) and r["z_max"] == pytest.approx(3.0, abs=1e-6)


def test_proprietes_index_et_fiche(jeu, tmp_path):
    f = lecture.ouvrir(jeu[0] / "methode2a_site_cc49_identifie.ifc")
    n = proprietes.indexer(f, tmp_path / "p.sqlite")
    assert n == 2
    mur = f.by_type("IfcWall")[0]
    fiche = proprietes.lire_fiche(tmp_path / "p.sqlite", mur.GlobalId)
    assert fiche["type_ifc"] == "IfcWall" and fiche["etage"] == "N00"
    assert fiche["psets"]["Données d'identification"]["SGP_DI_Sous_Ouvrage"] in ("SO-01", "SO-02")
    assert proprietes.lire_fiche(tmp_path / "p.sqlite", "inconnu") is None
    (tmp_path / "p.sqlite").unlink()   # fichier fermé : supprimable (Windows)

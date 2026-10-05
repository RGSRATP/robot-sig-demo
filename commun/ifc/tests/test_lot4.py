"""Module IFC partagé (lot 4) : locaux (IfcSpace), codes « Données d'identification », niveau, emprise."""
import pytest
from shapely.geometry import shape

from commun.ifc import lecture, locaux, proprietes
from commun.ifc.tests.fabrique import ecrire_jeu


@pytest.fixture(scope="module")
def maquette(tmp_path_factory):
    dossier = tmp_path_factory.mktemp("ifc")
    ecrire_jeu(dossier)
    f = lecture.ouvrir(dossier / "methode2a_site_cc49_identifie.ifc")
    return f, lecture.georef_brut(f)["site"]["origine"]


def test_locaux_codes_niveau_emprise(maquette):
    f, decalage = maquette
    liste = locaux.locaux(f, decalage, processus=1)
    assert len(liste) == 1
    l = liste[0]
    assert l["code"] == "TEST-B-N00-LT-001" and l["nom"] == "B1.01" and l["nom_long"] == "Local technique"
    assert l["niveau"] == "N00"                 # étage retrouvé par agrégation (IfcSpace non « contenu »)
    assert l["typologie"] == "Local technique"  # clé accentuée « SGP_DI_Intitulé_… » normalisée
    assert set(l["codes"]) >= {"SGP_DI_Sous_Ouvrage", "SGP_DI_Intitule_Typologie_Espace", "SGP_DI_Niveau"}
    e = shape(l["emprise_locale"])
    assert e.area == pytest.approx(12.0, abs=0.01)                # 4 m × 3 m, coordonnées locales
    assert e.bounds == pytest.approx((-4.0, 0.2, 0.0, 3.2), abs=1e-3)
    assert (l["z_min"], l["z_max"]) == pytest.approx((0.0, 2.8), abs=1e-6)
    assert l["surface"] == pytest.approx(12.0) and l["surface_source"] == "emprise"


def test_niveaux(maquette):
    f, decalage = maquette
    n = locaux.niveaux(locaux.locaux(f, decalage, processus=1))
    assert n == [{"niveau": "N00", "nb_locaux": 1, "z_min": 0.0, "z_max": 2.8, "intitule": "RDC"}]


def test_maquette_sans_locaux():
    import ifcopenshell
    assert locaux.locaux(ifcopenshell.file(schema="IFC4")) == []


def test_normalisation_des_cles():
    assert locaux.normaliser_cle("SGP_DI_Intitulé_Typologie_Espace") == "SGP_DI_Intitule_Typologie_Espace"
    assert locaux.normaliser_cle("SGP-DI-Hauteur sous plafond") == "SGP_DI_Hauteur_sous_plafond"


def test_fiche_d_un_local_donne_son_etage(maquette):
    f, _ = maquette
    fiche = proprietes.fiche(f.by_type("IfcSpace")[0])
    assert fiche["etage"] == "N00" and "Données d'identification" in fiche["psets"]

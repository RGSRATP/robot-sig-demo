"""Carte de gestion de crise (lot C1) : distances en Lambert 93, zone la plus grave, stations touchées par un accès,
points de rassemblement PLAI, ouvrages (puits), anomalies, polygones, événements enregistrés, temps de calcul.

Jeu synthétique construit autour d'un point en Lambert 93 (couches simulées : aucune lecture de fichier).
La recette sur les données réelles (Bastille, République, Stade de France) est marquée « donnees_reelles ».
"""
import copy
import time
from types import SimpleNamespace

import geopandas as gpd
import numpy as np
import pytest
import shapely
from shapely.geometry import LineString, Point, box

from serveur.sig import crise

X0, Y0 = 652000.0, 6862000.0       # Paris, Lambert 93
CENTRE = crise.vers_wgs84(X0, Y0)

ZONES_CFG = [
    {"id": "evacuation", "libelle": "Évacuation", "couleur": "#e03131"},
    {"id": "fermeture", "libelle": "Fermeture", "couleur": "#f76707"},
    {"id": "vigilance", "libelle": "Vigilance", "couleur": "#f2c200"},
]
CFG = {"crise": {
    "dossier_travail": "travail/crise", "rayon_max_m": 3000, "rayon_defaut_m": 500, "pas_m": 10,
    "raccourcis_m": [500, 600, 1000], "rattachement_acces_m": 250, "types": ["Explosion"], "zones": ZONES_CFG,
    "categories": [
        {"id": "stations", "libelle": "Stations", "role": "stations", "couches": ["Gares"], "regroupement": "nom",
         "champ_detail": "ligne", "propositions": {"evacuation": "Fermer la station"}},
        {"id": "acces", "libelle": "Accès", "role": "acces", "couches": ["Accès"], "champ_libelle": "nom",
         "propositions": {"evacuation": "Fermer l'accès"}},
        {"id": "lignes", "libelle": "Lignes", "role": "lignes", "couches": ["Tracés"], "regroupement": "ligne"},
        {"id": "plai", "libelle": "PLAI", "role": "plai", "couches": ["PLAI"], "champ_libelle": "nom",
         "champ_type": "type", "type_rassemblement": "rassemblement", "zones_bloquantes": ["evacuation", "fermeture"]},
        {"id": "ecoles", "libelle": "Écoles", "couches": ["Écoles", "Couche absente"], "champ_libelle": "nom"},
    ],
}}


def _couche(libelle, lignes, geoms):
    origine = gpd.GeoDataFrame(lignes, geometry=geoms, crs=2154)
    c = SimpleNamespace(id=f"id-{libelle}", libelle=libelle, champ_libelle="nom", avertissements=[], _chemin=None,
                        service={"date_maj": "2026-10-21T08:00:00", "source": "cache"})
    g = origine.geometry.values
    return c, (origine, g, shapely.STRtree(g))


def _jeu():
    """Couches simulées ; distances au centre choisies pour les critères de recette."""
    e = lambda d: Point(X0 + d, Y0)   # noqa: E731 — point à d mètres à l'est
    return {
        "Gares": _couche("Gares", {"nom": ["Proche", "Proche", "Accès seul", "Loin", "Même zone"],
                                   "ligne": ["M1", "M5", "M8", "M9", "M11"]},
                         [e(495), e(495), Point(X0, Y0 - 700), e(2000), Point(X0 - 505, Y0)]),
        "Accès": _couche("Accès", {"nom": ["Hors du cercle", "Accès touché", "Accès plus loin"]},
                         [e(505), Point(X0, Y0 - 450), Point(X0 - 565, Y0)]),
        "Tracés": _couche("Tracés", {"nom": ["t1", "t2"], "ligne": ["M1", "M1"]},
                          [LineString([(X0 - 2000, Y0 + 100), (X0 + 2000, Y0 + 100)]),
                           LineString([(X0 + 2000, Y0 + 100), (X0 + 2500, Y0 + 900)])]),
        "PLAI": _couche("PLAI", {"nom": ["Rassemblement A", "Rassemblement B", "Rassemblement C", "Pompiers"],
                                 "type": ["rassemblement", "rassemblement", "rassemblement", "pompiers"]},
                        [e(100), Point(X0, Y0 + 1200), Point(X0, Y0 + 2500), e(90)]),
        "Écoles": _couche("Écoles", {"nom": [f"École {i}" for i in range(3)]},
                          [e(300), e(800), box(X0 - 1000, Y0 - 10, X0 - 980, Y0 + 10)]),
    }


@pytest.fixture
def jeu(monkeypatch):
    j = _jeu()
    crise.reinitialiser()
    monkeypatch.setattr(crise, "_couches", lambda cfg, libs: ({k: v[0] for k, v in j.items() if k in libs},
                                                               {"Couche absente": "service injoignable"}))
    monkeypatch.setattr(crise, "_objets_l93", lambda c: next(v[1] for v in j.values() if v[0] is c))
    yield j
    crise.reinitialiser()


def _analyse(zones, cfg=CFG):
    return crise.analyser(cfg, CENTRE, zones)


def _cat(r, id_):
    return next(k for k in r["categories"] if k["id"] == id_)


def _el(r, cat, libelle):
    return next(e for e in _cat(r, cat)["elements"] if e["libelle"] == libelle)


# ---------------------------------------------------------------- géométrie

def test_cercle_en_lambert93_495_dedans_505_dehors(jeu):
    r = _analyse([{"role": "evacuation", "forme": "cercle", "rayon_m": 500}])
    assert _el(r, "stations", "Proche")["zone"] == "evacuation"
    assert _el(r, "stations", "Proche")["distance_m"] == pytest.approx(495, abs=0.1)
    assert _el(r, "acces", "Hors du cercle")["zone"] is None
    assert _el(r, "acces", "Hors du cercle")["distance_m"] == pytest.approx(505, abs=0.1)


def test_zone_la_plus_grave_l_emporte(jeu):
    zones = [{"role": "vigilance", "rayon_m": 1000}, {"role": "fermeture", "rayon_m": 400}, {"role": "evacuation", "rayon_m": 200}]
    r = _analyse(zones)
    assert _el(r, "ecoles", "École 0")["zone"] == "fermeture"     # 300 m : dans 400 et 1000, pas dans 200
    assert _el(r, "ecoles", "École 1")["zone"] == "vigilance"


def test_direction_et_distance_d_une_surface(jeu):
    r = _analyse([{"role": "vigilance", "rayon_m": 1000}])
    e = _el(r, "ecoles", "École 2")
    assert e["distance_m"] == pytest.approx(980, abs=0.1)        # point le plus proche de la surface
    assert e["direction"] == "O"
    assert crise.direction(1, 1) == "NE" and crise.direction(0, -5) == "S"


def test_polygone_dessine(jeu):
    sommets = [crise.vers_wgs84(X0 + 250, Y0 - 50), crise.vers_wgs84(X0 + 350, Y0 - 50), crise.vers_wgs84(X0 + 350, Y0 + 50)]
    r = _analyse([{"role": "fermeture", "forme": "polygone", "coordonnees": sommets}])
    assert _el(r, "ecoles", "École 0")["zone"] == "fermeture"
    assert _el(r, "ecoles", "École 1")["zone"] is None
    assert r["zones"][0]["surface_m2"] == pytest.approx(5000, rel=0.01)


def test_zone_mal_formee_refusee(jeu):
    with pytest.raises(crise.ErreurCrise, match="rôle"):
        _analyse([{"role": "inconnu", "rayon_m": 100}])
    with pytest.raises(crise.ErreurCrise, match="rayon_m"):
        _analyse([{"role": "evacuation", "rayon_m": -1}])
    with pytest.raises(crise.ErreurCrise, match="polygone"):
        _analyse([{"role": "evacuation", "forme": "polygone", "coordonnees": [[2.3, 48.8]]}])


# ---------------------------------------------------------------- stations et accès

def test_station_regroupee_avec_ses_lignes(jeu):
    r = _analyse([{"role": "evacuation", "rayon_m": 500}])
    e = _el(r, "stations", "Proche")
    assert e["detail"] == "M1, M5"
    assert e["proposition"] == "Fermer la station"


def test_station_touchee_seulement_par_un_acces(jeu):
    r = _analyse([{"role": "evacuation", "rayon_m": 500}])
    e = _el(r, "stations", "Accès seul")
    assert e["zone"] == "evacuation"
    assert e["distance_m"] == pytest.approx(450, abs=0.1)
    assert "Accès touché" in e["cause"]
    a = _el(r, "acces", "Accès touché")
    assert "Accès seul" in a["detail"]


def test_pas_de_cause_quand_la_station_est_elle_meme_dans_la_zone(jeu):
    r = _analyse([{"role": "fermeture", "rayon_m": 600}])
    e = _el(r, "stations", "Même zone")                 # station à 505 m, accès à 565 m : même zone
    assert e["zone"] == "fermeture" and "cause" not in e
    e = _el(r, "stations", "Proche")                    # station à 495 m, accès plus proche à… 505 m
    assert "cause" not in e


def test_pas_de_cause_meme_si_un_acces_est_plus_proche(jeu, monkeypatch):
    j = _jeu()
    j["Accès"] = _couche("Accès", {"nom": ["Accès très proche"]}, [Point(X0 + 480, Y0)])   # station « Proche » à 495 m
    monkeypatch.setattr(crise, "_couches", lambda cfg, libs: ({k: v[0] for k, v in j.items() if k in libs}, {}))
    monkeypatch.setattr(crise, "_objets_l93", lambda c: next(v[1] for v in j.values() if v[0] is c))
    crise.reinitialiser()
    e = _el(_analyse([{"role": "evacuation", "rayon_m": 500}]), "stations", "Proche")
    assert e["zone"] == "evacuation" and e["distance_m"] == pytest.approx(480, abs=0.1) and "cause" not in e


# ---------------------------------------------------------------- lignes, PLAI

def test_ligne_longueur_dans_la_zone_et_trace(jeu):
    r = _analyse([{"role": "evacuation", "rayon_m": 500}])
    e = _el(r, "lignes", "M1")
    assert e["distance_m"] == pytest.approx(100, abs=0.1)
    assert e["longueur_zone_m"] == pytest.approx(2 * (500 ** 2 - 100 ** 2) ** 0.5, abs=2)
    assert e["geometrie"]["type"] in ("LineString", "MultiLineString")


def test_point_de_rassemblement_inutilisable_et_remplacement(jeu):
    r = _analyse([{"role": "evacuation", "rayon_m": 500}, {"role": "vigilance", "rayon_m": 2000}])
    a = _el(r, "plai", "Rassemblement A")
    assert a["inutilisable"] is True
    assert a["remplacement"]["libelle"] == "Rassemblement B"   # en vigilance : zone non bloquante
    assert "Rassemblement B" in a["proposition"]
    assert "inutilisable" not in _el(r, "plai", "Pompiers")
    assert "inutilisable" not in _el(r, "plai", "Rassemblement B")


def test_aucun_rassemblement_hors_zone(jeu):
    r = _analyse([{"role": "fermeture", "rayon_m": 2900}])
    a = _el(r, "plai", "Rassemblement A")
    assert a["inutilisable"] and "remplacement" not in a and "à définir" in a["proposition"]


# ---------------------------------------------------------------- robustesse

def test_couche_indisponible_signalee_sans_bloquer(jeu):
    r = _analyse([{"role": "evacuation", "rayon_m": 500}])
    assert any("Couche absente" in a and "injoignable" in a for a in r["anomalies"])
    assert _cat(r, "ecoles")["touches"] == 1


def test_sources_datees(jeu):
    r = _analyse([{"role": "evacuation", "rayon_m": 500}])
    assert {s["libelle"] for s in r["sources"]} >= {"Gares", "Accès", "Écoles"}
    assert all(s["date"] == "2026-10-21T08:00:00" for s in r["sources"])


def test_export_csv_seulement_les_elements_touches(jeu):
    r = _analyse([{"role": "evacuation", "rayon_m": 500}])
    lignes = crise.lignes_csv(CFG, r)
    assert lignes and all(l["zone"] == "Évacuation" for l in lignes)
    assert {"Proche", "Accès seul"} <= {l["libelle"] for l in lignes}


def test_temps_de_calcul_un_kilometre(monkeypatch):
    """50 000 objets dans 3 km (densité de Paris largement dépassée) : analyse en moins de 2 s."""
    rng = np.random.default_rng(1)
    xy = rng.uniform(-3000, 3000, (50000, 2))
    j = {"Écoles": _couche("Écoles", {"nom": [f"o{i}" for i in range(len(xy))]}, [Point(X0 + a, Y0 + b) for a, b in xy])}
    cfg = copy.deepcopy(CFG)
    cfg["crise"]["categories"] = [cfg["crise"]["categories"][-1]]
    cfg["crise"]["categories"][0]["couches"] = ["Écoles"]
    crise.reinitialiser()
    monkeypatch.setattr(crise, "_couches", lambda c, libs: ({"Écoles": j["Écoles"][0]}, {}))
    monkeypatch.setattr(crise, "_objets_l93", lambda c: j["Écoles"][1])
    t = time.perf_counter()
    r = crise.analyser(cfg, CENTRE, [{"role": "evacuation", "rayon_m": 1000}])
    assert time.perf_counter() - t < 2
    attendu = int((np.hypot(xy[:, 0], xy[:, 1]) <= 1000).sum())
    assert _cat(r, "ecoles")["touches"] == attendu


# ---------------------------------------------------------------- ouvrages (maquettes)

def test_ouvrage_d_une_maquette_selon_son_dossier():
    d = {"gare": "Gare", "intergare": "Tunnel", "ouvrage annexe": "Ouvrage annexe"}
    assert crise._ouvrage("L15/EST/Gare/64SDF - Stade de France/x.ifc", d) == ("L15/EST/Gare/64SDF - Stade de France", "64SDF - Stade de France", "gare")
    assert crise._ouvrage("L15/EST/Ouvrage Annexe/6301P/a.ifc", d)[2] == "ouvrage annexe"
    assert crise._ouvrage("L16/Gare/b.ifc", d) == ("L16/Gare/b.ifc", "b", "gare")
    assert crise._ouvrage("L15/SUD/17IGR/c.ifc", d) is None


def test_ouvrage_annexe_signale_comme_puits(monkeypatch):
    cat = {"id": "ouvrages", "libelle": "Ouvrages", "role": "maquettes", "puits": ["ouvrage annexe"],
           "dossiers": {"gare": "Gare", "ouvrage annexe": "Ouvrage annexe"}, "propositions": {"vigilance": "Surveiller"}}
    cfg = copy.deepcopy(CFG)
    cfg["crise"]["categories"] = [cat]

    def faux(cfg_, k, centre, rayon):
        g1, g2 = box(X0 + 700, Y0, X0 + 760, Y0 + 40), box(X0 - 30, Y0 - 30, X0 + 30, Y0 + 30)
        return [{"n": None, "couche": None, "geom": g1, "distance_m": g1.distance(centre), "proche": Point(X0 + 700, Y0),
                 "libelle": "6301P", "groupe": "L15/OA/6301P", "type": "ouvrage annexe", "detail": None, "maquette": "m1"},
                {"n": None, "couche": None, "geom": g2, "distance_m": 0.0, "proche": Point(X0, Y0),
                 "libelle": "64SDF", "groupe": "L15/Gare/64SDF", "type": "gare", "detail": None, "maquette": "m2"}], []
    crise.reinitialiser()
    monkeypatch.setattr(crise, "_candidats_maquettes", faux)
    monkeypatch.setattr(crise, "_couches", lambda c, libs: ({}, {}))
    r = crise.analyser(cfg, CENTRE, [{"role": "vigilance", "rayon_m": 1000}])
    oa = _el(r, "ouvrages", "6301P")
    assert oa["puits"] is True and "puits" in oa["proposition"] and oa["maquettes"] == ["m1"]
    assert _el(r, "ouvrages", "64SDF")["puits"] is False
    assert oa["geometrie"]["type"] == "Polygon"


# ---------------------------------------------------------------- configuration, événements

def _erreur(msg):
    return ValueError(msg)


def test_configuration_controlee():
    crise.controler(copy.deepcopy(CFG["crise"]), _erreur)
    for modif, motif in [
        (lambda c: c.update(rayon_defaut_m=5000), "rayon_defaut_m"),
        (lambda c: c["categories"][0]["propositions"].update(inconnue="x"), "propositions"),
        (lambda c: c["categories"].pop(0), "acces"),
        (lambda c: c["zones"].append(dict(c["zones"][0])), "double"),
        (lambda c: c["categories"][3].pop("champ_type"), "champ_type"),
    ]:
        c = copy.deepcopy(CFG["crise"])
        modif(c)
        with pytest.raises(ValueError, match=motif):
            crise.controler(c, _erreur)


def test_la_configuration_du_depot_est_valide():
    from serveur.sig.config import charger_config
    cfg = charger_config()
    assert crise.config_interface(cfg)["raccourcis_m"] == [500, 600, 1000]


def test_evenement_enregistre_puis_relu(tmp_path):
    cfg = copy.deepcopy(CFG)
    cfg["crise"]["dossier_travail"] = str(tmp_path)
    e = crise.enregistrer(cfg, {"titre": "Explosion Bastille", "type": "Explosion", "mode": "exercice",
                                "centre": CENTRE, "zones": [{"role": "evacuation", "rayon_m": 500}]})
    assert crise.evenement(cfg, e["id"])["titre"] == "Explosion Bastille"
    assert crise.evenements(cfg)[0]["id"] == e["id"]
    assert crise.evenement(cfg, "../../secret") is None
    with pytest.raises(crise.ErreurCrise, match="mode"):
        crise.enregistrer(cfg, {"titre": "x", "mode": "?"})


def test_routes_crise(client_test, cfg_test, jeu, monkeypatch):
    assert client_test.post("/api/sig/crise/analyse", json={"centre": CENTRE, "zones": []}).status_code == 404
    monkeypatch.setitem(cfg_test, "crise", CFG["crise"])
    r = client_test.post("/api/sig/crise/analyse", json={"centre": CENTRE, "zones": [{"role": "nulle", "rayon_m": 10}]})
    assert r.status_code == 422 and "nulle" in r.json()["detail"]
    r = client_test.post("/api/sig/crise/analyse", json={"centre": CENTRE, "zones": [{"role": "evacuation", "rayon_m": 500}]})
    assert r.status_code == 200 and _el(r.json(), "stations", "Proche")["zone"] == "evacuation"
    r = client_test.post("/api/sig/crise/analyse?format=csv", json={"centre": CENTRE, "titre": "Essai",
                                                                     "zones": [{"role": "evacuation", "rayon_m": 500}]})
    assert r.status_code == 200 and "crise_Essai.csv" in r.headers["content-disposition"]
    assert "Accès seul" in r.content.decode(cfg_test["export"]["encodage"])
    assert client_test.get("/api/sig/crise/lieux", params={"q": "pro"}).json()["resultats"][0]["libelle"] == "Proche"


# ---------------------------------------------------------------- recette sur les données réelles (lot C1)

SCENARIOS = {
    # point de l'événement, catégorie, éléments attendus en zone d'évacuation (500 m)
    "bastille": ([2.36914, 48.85320], "stations", {"Bastille", "Bréguet-Sabin", "Ledru Rollin", "Chemin Vert"}),
    "republique": ([2.36350, 48.86745], "stations", {"République", "Temple", "Oberkampf", "Jacques Bonsergent"}),
    "stade_de_france": ([2.3631, 48.9179], "ouvrages", {"64SDF - Stade de France", "3364I - Saint-Denis Pleyel - Stade de France"}),
}


@pytest.mark.donnees_reelles
@pytest.mark.parametrize("nom", list(SCENARIOS))
def test_recette_scenarios(nom):
    from serveur.sig.config import charger_config
    centre, cat, attendus = SCENARIOS[nom]
    r = crise.analyser(charger_config(), centre, [{"role": "evacuation", "rayon_m": 500}, {"role": "vigilance", "rayon_m": 1000}])
    evac = {e["libelle"] for e in _cat(r, cat)["elements"] if e["zone"] == "evacuation"}
    assert attendus <= evac, f"{nom} : manquent {attendus - evac}"
    if nom == "republique":
        rassemblement = [e for e in _cat(r, "plai")["elements"] if e.get("inutilisable")]
        assert rassemblement and rassemblement[0].get("remplacement")
    if nom == "stade_de_france":
        assert any(e["puits"] and e["zone"] for e in _cat(r, "ouvrages")["elements"])

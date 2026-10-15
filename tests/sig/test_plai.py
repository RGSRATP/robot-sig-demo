"""PLAI : lecture d'un plan synthétique, calage, positionnement aux accès IDFM, connecteur, routes, configuration."""
import math

import geopandas as gpd
import pytest
from shapely.geometry import Point

from serveur.sig import config as cfg_sig
from serveur.sig import plai
from tests.sig.plai_synthetique import EPINGLE, PICTOS, ecrire_plai


def l93(x, y):
    """Calage fictif du plan synthétique : 0,5 m par point, nord vers le haut de la page."""
    return 650000 + 0.5 * x, 6862000 - 0.5 * y


@pytest.fixture(scope="module")
def lecture(tmp_path_factory):
    chemin = ecrire_plai(tmp_path_factory.mktemp("plai") / "PLAI_TEST.pdf")
    return plai.lire(chemin)


def _idfm(lec, sans=()):
    """Gares et accès IDFM cohérents avec le plan (accès à l'endroit de leur étiquette)."""
    gares = gpd.GeoDataFrame({"nom_gares": ["Station Test", "Station Test"], "indice_lig": ["14", "7"]},
                             geometry=[Point(l93(240, 450))] * 2, crs="EPSG:2154")
    acces = [a for a in lec.acces if a["numero"] not in sans]
    acces_idfm = gpd.GeoDataFrame(
        {"accid": [f"A{a['numero']}" for a in acces], "accname": [a["nom"] for a in acces],
         "accshortname": [a["numero"] for a in acces]},
        geometry=[Point(l93(a["x"], a["y"])) for a in acces], crs="EPSG:2154")
    return gares, acces_idfm


def _copie(lec):
    return plai.Lecture(**{k: (list(map(dict, v)) if isinstance(v, list) and v and isinstance(v[0], dict) else
                               list(v) if isinstance(v, list) else v) for k, v in lec.__dict__.items()})


# ---------------------------------------------------------------- lecture du plan

def test_lecture_en_tete_et_acces(lecture):
    lec, _ = lecture
    assert lec.station == "STATION TEST"
    assert lec.adresse.startswith("Adresse : 12 rue de la Paix")
    assert [(a["numero"], a["nom"]) for a in lec.acces] == [(1, "rue Alpha"), (2, "bd Beta"), (3, "av. Gamma")]
    assert lec.anomalies == []


def test_pictogrammes_trouves_par_correlation(lecture):
    lec, icones = lecture
    trouves = {p["type"]: p for p in lec.pictos}
    assert set(trouves) == set(plai.TYPES)
    for type_, (x, y) in PICTOS.items():
        assert math.hypot(trouves[type_]["x"] - x, trouves[type_]["y"] - y) < 3, type_
        assert trouves[type_]["score"] >= plai.SEUIL_CORRELATION
    # l'épingle de la légende est ignorée ; la pointe de celle du plan désigne l'accès
    assert [(p["x"], p["y"]) for p in lec.pictos if p["type"] == "quais"] == [pytest.approx(EPINGLE, abs=1)]
    assert set(icones) == set(plai.TYPES) and all(png.startswith(b"\x89PNG") for png in icones.values())


def test_plan_sans_legende_signale(tmp_path):
    lec, icones = plai.lire(ecrire_plai(tmp_path / "PLAI_X.pdf", avec_legende=False))
    assert icones == {} and lec.pictos == []
    assert any("légende" in a for a in lec.anomalies)


def test_cache_des_lectures(tmp_path):
    chemin = ecrire_plai(tmp_path / "PLAI_TEST.pdf")
    lec1, ic1 = plai.lire_avec_cache(chemin, tmp_path / "lectures")
    lec2, ic2 = plai.lire_avec_cache(chemin, tmp_path / "lectures")   # relu depuis le cache
    assert [(p["type"], p["x"], p["y"]) for p in lec2.pictos] == [(p["type"], p["x"], p["y"]) for p in lec1.pictos]
    assert ic2 == ic1 and len(list((tmp_path / "lectures").glob("*.json"))) == 1


# ---------------------------------------------------------------- calage et positionnement

def test_transformation_affine_et_similitude():
    src = [(0, 0), (100, 0), (0, 100), (100, 100)]
    dst = [l93(*p) for p in src]
    f, residu = plai._transformation(src, dst)
    assert residu == pytest.approx(0, abs=1e-6) and f(50, 50) == pytest.approx(l93(50, 50))
    f, residu = plai._transformation(src[:2], dst[:2])
    assert residu == pytest.approx(0, abs=1e-6) and f(30, 70) == pytest.approx(l93(30, 70))
    assert plai._transformation(src[:1], dst[:1]) == (None, None)


def test_positionnement_aux_acces_idfm(lecture):
    lec = _copie(lecture[0])
    points, anomalies = plai.positionner(lec, *_idfm(lec), rayon_m=400)
    assert anomalies == []
    par_type = {p["type"]: p for p in points}
    assert set(par_type) == set(plai.TYPES)
    a1, a2 = lec.acces[0], lec.acces[1]
    for type_ in ("ratp", "pompiers", "rassemblement"):
        p = par_type[type_]
        assert (p["x"], p["y"]) == pytest.approx(l93(a1["x"], a1["y"]))
        assert p["acces_idfm"] == "A1" and p["methode"] == "position IDFM de l'accès"
        attendu = 0.5 * math.hypot(PICTOS[type_][0] - a1["x"], PICTOS[type_][1] - a1["y"])
        assert p["ecart_dessin_m"] == pytest.approx(attendu, abs=2)
        assert "calage sur 3 accès" in p["note_ecart"]
    assert par_type["quais"]["acces_idfm"] == "A2"
    assert (par_type["quais"]["x"], par_type["quais"]["y"]) == pytest.approx(l93(a2["x"], a2["y"]))
    # graphie et lignes de la station prises dans IDFM
    assert par_type["ratp"]["station"] == "Station Test" and par_type["ratp"]["lignes"] == "7 14"


def test_acces_absent_d_idfm_place_a_l_endroit_dessine(lecture):
    lec = _copie(lecture[0])
    points, anomalies = plai.positionner(lec, *_idfm(lec, sans=(2,)), rayon_m=400)
    quais = next(p for p in points if p["type"] == "quais")
    assert quais["methode"].startswith("position dessinée")
    assert (quais["x"], quais["y"]) == pytest.approx(l93(*EPINGLE), abs=0.5)
    assert any("2 bd Beta" in a for a in anomalies)


def test_station_inconnue_signalee(lecture):
    lec = _copie(lecture[0])
    gares, acces = _idfm(lec)
    gares["nom_gares"] = "Autre Gare"
    points, anomalies = plai.positionner(lec, gares, acces, rayon_m=400)
    assert points == [] and any("introuvable dans les gares IDFM" in a for a in anomalies)


# ---------------------------------------------------------------- connecteur et routes

@pytest.fixture
def service_plai(cfg_test, tmp_path, monkeypatch, lecture):
    dossier = tmp_path / "plans"
    dossier.mkdir()
    ecrire_plai(dossier / "PLAI_TEST.pdf")
    service = {"libelle": "PLAI test", "dossier": str(dossier), "couche_gares": "G", "couche_acces": "A",
               "rayon_m": 400, "url": f"plai:{dossier}"}
    monkeypatch.setitem(cfg_test, "services_plai", [service])
    idfm = dict(zip(("G", "A"), _idfm(lecture[0])))
    monkeypatch.setattr(plai, "_couche_idfm", lambda cfg, libelle: idfm[libelle])
    return service


def test_connecteur_produit_couche_controle_et_icones(cfg_test, service_plai):
    gdf, meta = plai.telecharger(service_plai, cfg_test)
    assert len(gdf) == 4 and gdf.crs.to_epsg() == 2154
    assert set(gdf["plai_pdf"]) == {"/api/sig/plai/pdf/PLAI_TEST.pdf"}
    assert meta["avertissements"] == [] and meta["nb_objets"] == 4
    cache = plai.dossier_cache(cfg_test)
    assert all((cache / f"icone_{t}.png").is_file() for t in plai.TYPES)
    assert (cache / "controle.json").is_file()


def test_connecteur_sans_plan_en_erreur(cfg_test, service_plai, tmp_path):
    from serveur.sig.esri import ErreurService
    vide = tmp_path / "vide"
    vide.mkdir()
    with pytest.raises(ErreurService, match="aucun PLAI"):
        plai.telecharger({**service_plai, "dossier": str(vide)}, cfg_test)


def test_routes_plai(client_test, cfg_test, service_plai):
    assert client_test.get("/api/sig/plai/icones/ratp.png").status_code == 404     # pas encore lu
    assert client_test.get("/api/sig/plai/controle").status_code == 404
    plai.telecharger(service_plai, cfg_test)
    r = client_test.get("/api/sig/plai/icones/ratp.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert client_test.get("/api/sig/plai/icones/inconnu.png").status_code == 404
    r = client_test.get("/api/sig/plai/pdf/PLAI_TEST.pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert client_test.get("/api/sig/plai/pdf/..%2Fsecret.pdf").status_code == 404
    r = client_test.get("/api/sig/plai/controle")
    assert r.status_code == 200 and "PLAI_TEST.pdf" in r.content.decode(cfg_test["export"]["encodage"])


def test_routes_sans_plai_declare(client_test, cfg_test, monkeypatch):
    monkeypatch.setitem(cfg_test, "services_plai", [])
    assert client_test.get("/api/sig/plai/controle").status_code == 404


# ---------------------------------------------------------------- configuration

def test_plai_declare_dans_la_config_du_depot():
    cfg = cfg_sig.charger_config()
    s = cfg["services_plai"][0]
    assert s["url"] == f"plai:{s['dossier']}"
    assert set(s["style"]["icones"]["images"]) == set(plai.TYPES)


@pytest.mark.parametrize("service, attendu", [
    ({"libelle": "P", "dossier": "d", "couche_gares": "G"}, "couche_acces"),
    ({"libelle": "P", "dossier": "d", "couche_gares": "G", "couche_acces": "A", "rayon_m": 5000}, "rayon_m"),
    ({"libelle": "P", "dossier": "d", "couche_gares": "G", "couche_acces": "A", "processus": 0}, "processus"),
    ({"libelle": "P", "dossier": "d", "couche_gares": "G", "couche_acces": "A", "style": "bleu"}, "style"),
])
def test_plai_config_invalide(tmp_path, service, attendu):
    import yaml
    c = yaml.safe_load(cfg_sig.CHEMIN_CONFIG.read_text(encoding="utf-8"))
    c["services_plai"] = [service]
    p = tmp_path / "sig.yaml"
    p.write_text(yaml.safe_dump(c, allow_unicode=True), encoding="utf-8")
    with pytest.raises(cfg_sig.ConfigInvalide, match=attendu):
        cfg_sig.charger_config(p)

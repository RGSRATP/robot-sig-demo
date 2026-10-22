"""Carte de gestion de crise, lot C3 : sources autour de l'événement (carreaux WFS en cache, chargement en
arrière-plan, débit limité par le service), rues touchées, points de bouclage, bâtiments et population.

Faux service WFS (httpx.MockTransport) et objets synthétiques en Lambert 93 : aucun accès réseau.
"""
import copy
import json
import math

import geopandas as gpd
import httpx
import pytest
import shapely
from shapely.geometry import LineString, Point, box

from serveur.sig import crise, crise_local, flux

X0, Y0 = 652000.0, 6862000.0
CENTRE = crise.vers_wgs84(X0, Y0)
WFS = "https://faux.wfs.test/wfs/ows"

ZONES_CFG = [
    {"id": "evacuation", "libelle": "Évacuation", "couleur": "#e03131"},
    {"id": "fermeture", "libelle": "Fermeture", "couleur": "#f76707"},
    {"id": "vigilance", "libelle": "Vigilance", "couleur": "#f2c200"},
]


def _cfg(tmp_path, **sources):
    return {
        "esri": {"cache_heures_defaut": 24, "nouvel_essai_minutes": 10},
        "reseau": {"delai_secondes": 5},
        "crise": {
            "dossier_travail": str(tmp_path / "travail"), "dossier_cache": str(tmp_path / "cache"),
            "rayon_max_m": 3000, "rayon_defaut_m": 500, "pas_m": 10, "raccourcis_m": [500], "rattachement_acces_m": 250,
            "types": ["Explosion"], "zones": ZONES_CFG, "requetes_paralleles": 2,
            "sources_locales": [
                {"id": "rues", "libelle": "Rues (test)", "url": WFS, "couche": "rues", "rayon_m": 1500, "carreau_m": 1000},
                {"id": "bati", "libelle": "Bâtiments (test)", "url": WFS, "couche": "bati", "rayon_m": 1500, "carreau_m": 1000},
                {"id": "pop", "libelle": "Population (test)", "url": WFS, "couche": "pop", "rayon_m": 1500, "carreau_m": 1000,
                 "date_donnee": "Filosofi (test)"},
            ],
            "categories": [
                {"id": "rues", "libelle": "Rues", "role": "rues", "source_locale": "rues",
                 "regroupement": ["nom_g", "nom_d"], "champ_type": "nature", "libelle_sans_nom": "Voie sans nom"},
                {"id": "bouclage", "libelle": "Points de bouclage", "role": "bouclage", "source_locale": "rues",
                 "zones_bouclage": ["evacuation", "fermeture"], "regroupement": ["nom_g", "nom_d"],
                 "champ_type": "nature", "champ_ordre": "importance", "libelle_sans_nom": "Voie sans nom",
                 "propositions": {"fermeture": "Barrer la voie"}},
                {"id": "bati", "libelle": "Bâtiments", "role": "denombrement", "source_locale": "bati",
                 "mesures": [{"id": "n", "libelle": "Bâtiments"}, {"id": "log", "libelle": "Logements", "champ": "logements"}]},
                {"id": "pop", "libelle": "Population", "role": "denombrement", "source_locale": "pop", "prorata": True,
                 "mesures": [{"id": "hab", "libelle": "Habitants", "champ": "ind"}]},
            ],
            **sources,
        },
    }


def _f(id_, geom, **props):
    return {"type": "Feature", "id": id_, "geometry": shapely.geometry.mapping(geom), "properties": props}


# Rues : un boulevard est-ouest qui traverse tout (coupe deux fois chaque cercle), une rue nord-sud nommée à
# droite seulement, une voie sans nom, une impasse dans le cercle de 300 m (ne coupe aucune limite)
RUES = [
    _f("r.1", LineString([(X0 - 1400, Y0), (X0 + 1400, Y0)]), nom_g="BD TEST", nom_d="BD TEST", nature="Route à 2 chaussées", importance="2"),
    _f("r.2", LineString([(X0 + 100, Y0 - 1400), (X0 + 100, Y0 + 1400)]), nom_g=None, nom_d="R DROITE", nature="Route à 1 chaussée", importance="4"),
    _f("r.3", LineString([(X0 - 200, Y0 + 50), (X0 - 200, Y0 + 900)]), nom_g=None, nom_d=None, nature="Rond-point", importance="5"),
    _f("r.4", LineString([(X0 + 50, Y0 + 50), (X0 + 150, Y0 + 150)]), nom_g="IMP COURTE", nom_d="IMP COURTE", nature="Route à 1 chaussée", importance="5"),
]
# Bâtiments : à 100 m (2 logements), 400 m (10), 800 m (5), 1200 m (logements inconnus)
BATI = [_f(f"b.{d}", box(X0 + d, Y0 - 5, X0 + d + 10, Y0 + 5), logements=n) for d, n in ((100, 2), (400, 10), (800, 5), (1200, None))]
# Population : carreau de 200 m centré sur le point (1 000 hab.), carreau à cheval sur le cercle de 500 m
# (de 400 à 600 m à l'est : 400 hab.), carreau à 1 km (200 hab.)
POP = [_f("p.0", box(X0 - 100, Y0 - 100, X0 + 100, Y0 + 100), ind=1000),
       _f("p.1", box(X0 + 400, Y0 - 100, X0 + 600, Y0 + 100), ind=400),
       _f("p.2", box(X0 + 1000, Y0 - 100, X0 + 1200, Y0 + 100), ind=200)]
JEUX = {"rues": RUES, "bati": BATI, "pop": POP}


class FauxWfs:
    """Répond aux requêtes par carreau (BBOX ou CQL BBOX en Lambert 93) ; pannes et 429 sur demande."""

    def __init__(self, panne_couches=(), trop_de_requetes=0):
        self.requetes = []
        self.panne_couches = set(panne_couches)
        self.trop_de_requetes = trop_de_requetes

    def fabrique(self, cfg):
        return httpx.Client(transport=httpx.MockTransport(self._repondre))

    def _repondre(self, r: httpx.Request) -> httpx.Response:
        self.requetes.append(r)
        if self.trop_de_requetes:
            self.trop_de_requetes -= 1
            return httpx.Response(429, headers={"retry-after": "0"})
        p = dict(r.url.params)
        couche = p["TYPENAMES"]
        if couche in self.panne_couches:
            return httpx.Response(503, text="indisponible")
        assert p["SRSNAME"] == "EPSG:2154"
        x0, y0, x1, y1, _ = p["BBOX"].split(",", 4)
        emprise = box(float(x0), float(y0), float(x1), float(y1))
        feats = [f for f in JEUX[couche] if shapely.geometry.shape(f["geometry"]).intersects(emprise)]
        debut = int(p.get("STARTINDEX", 0))
        page = feats[debut:debut + int(p["COUNT"])]
        return httpx.Response(200, json={"type": "FeatureCollection", "features": page, "numberMatched": len(feats)})


@pytest.fixture(autouse=True)
def propre(monkeypatch):
    crise.reinitialiser()
    monkeypatch.setattr(flux.time, "sleep", lambda s: None)
    # aucune autre couche analysée : seules les catégories à source locale
    monkeypatch.setattr(crise, "_couches", lambda cfg, libs: ({}, {}))
    yield
    crise.reinitialiser()


def _analyse(cfg, zones, faux):
    r = crise.analyser(cfg, CENTRE, zones, fabrique=faux.fabrique)
    if r["chargement"]:
        crise_local.attendre(30)
        r = crise.analyser(cfg, CENTRE, zones, fabrique=faux.fabrique)
    return r


def _cat(r, id_):
    return next(k for k in r["categories"] if k["id"] == id_)


def _mesure(r, cat, id_):
    return next(m for m in _cat(r, cat)["mesures"] if m["id"] == id_)


# ---------------------------------------------------------------- carreaux et cache

def test_carreaux_qui_touchent_le_disque():
    c = crise_local.carreaux((500.0, 500.0), 400, 1000)
    assert c == [(0, 0)]
    c = crise_local.carreaux((X0, Y0), 1500, 1000)
    assert (652, 6862) in c and (650, 6862) in c and (653, 6863) in c
    assert (650, 6860) in c   # coin du carreau à 1 414 m du point
    assert (650, 6860) not in crise_local.carreaux((X0, Y0), 1400, 1000)
    assert all(math.hypot(max(i * 1000 - X0, 0, X0 - (i + 1) * 1000), max(j * 1000 - Y0, 0, Y0 - (j + 1) * 1000)) <= 1500
               for i, j in c)


def test_premier_appel_en_arriere_plan_puis_cache(tmp_path):
    cfg, faux = _cfg(tmp_path), FauxWfs()
    r = crise.analyser(cfg, CENTRE, [{"role": "evacuation", "rayon_m": 500}], fabrique=faux.fabrique)
    assert {c["source"] for c in r["chargement"]} == {"Rues (test)", "Bâtiments (test)", "Population (test)"}
    assert _cat(r, "rues")["en_cours"] and _cat(r, "rues")["elements"] == []
    crise_local.attendre(30)
    n = len(faux.requetes)
    r = crise.analyser(cfg, CENTRE, [{"role": "evacuation", "rayon_m": 500}], fabrique=faux.fabrique)
    assert r["chargement"] == [] and not _cat(r, "rues")["en_cours"]
    assert len(faux.requetes) == n   # tout vient du cache
    assert list((tmp_path / "cache" / "rues").glob("1000_*.json.gz"))
    src = next(s for s in r["sources"] if s["libelle"] == "Population (test)")
    assert src["date"] == "Filosofi (test)" and src["origine"] == "cache"


def test_objet_a_cheval_sur_deux_carreaux_compte_une_fois(tmp_path):
    r = _analyse(_cfg(tmp_path), [{"role": "evacuation", "rayon_m": 1500}], FauxWfs())
    # le boulevard traverse 3 carreaux ; une seule fois dans la liste, longueur réelle
    bd = next(e for e in _cat(r, "rues")["elements"] if e["libelle"] == "BD TEST")
    assert bd["longueur_zone_m"] == pytest.approx(2800, abs=2)
    assert _mesure(r, "bati", "n")["par_zone"]["evacuation"] == 4


def test_trop_de_requetes_nouvel_essai(tmp_path):
    faux = FauxWfs(trop_de_requetes=3)
    r = _analyse(_cfg(tmp_path), [{"role": "evacuation", "rayon_m": 500}], faux)
    assert r["anomalies"] == []
    assert sum(1 for q in faux.requetes if q.url.params.get("TYPENAMES")) >= 3


def test_service_en_panne_signale_sans_bloquer(tmp_path):
    faux = FauxWfs(panne_couches={"bati"})
    r = _analyse(_cfg(tmp_path), [{"role": "evacuation", "rayon_m": 500}], faux)
    assert any("Bâtiments (test)" in a and "injoignable" in a for a in r["anomalies"])
    assert _mesure(r, "pop", "hab")["par_zone"]["evacuation"] > 0   # les autres sources continuent
    assert any(e["libelle"] == "BD TEST" for e in _cat(r, "rues")["elements"])


def test_zone_au_dela_des_donnees_signalee(tmp_path):
    r = _analyse(_cfg(tmp_path), [{"role": "vigilance", "rayon_m": 2000}], FauxWfs())
    assert any("Rues (test)" in a and "1,5 km" in a and "incomplets" in a for a in r["anomalies"])


# ---------------------------------------------------------------- rues et bouclage

def test_rues_regroupees_par_nom_avec_repli_et_sans_nom(tmp_path):
    r = _analyse(_cfg(tmp_path), [{"role": "evacuation", "rayon_m": 300}, {"role": "vigilance", "rayon_m": 1000}], FauxWfs())
    rues = {e["libelle"]: e for e in _cat(r, "rues")["elements"] if e["zone"]}
    assert set(rues) == {"BD TEST", "R DROITE", "Voie sans nom (Rond-point)", "IMP COURTE"}
    assert rues["BD TEST"]["zone"] == "evacuation" and rues["BD TEST"]["longueur_zone_m"] == pytest.approx(600, abs=2)
    assert rues["Voie sans nom (Rond-point)"]["distance_m"] == pytest.approx(math.hypot(200, 50), abs=0.5)


def test_points_de_bouclage_sur_la_limite_exterieure(tmp_path):
    zones = [{"role": "evacuation", "rayon_m": 300}, {"role": "fermeture", "rayon_m": 600}, {"role": "vigilance", "rayon_m": 1000}]
    r = _analyse(_cfg(tmp_path), zones, FauxWfs())
    pts = _cat(r, "bouclage")["elements"]
    # limite extérieure des zones bouclées (600 m) : boulevard (2 points), rue nord-sud (2), voie sans nom (1) ;
    # l'impasse ne coupe pas la limite ; la vigilance (1 km) n'est pas bouclée
    assert sorted(e["libelle"] for e in pts) == ["BD TEST", "BD TEST", "R DROITE", "R DROITE", "Voie sans nom (Rond-point)"]
    assert all(e["zone"] == "fermeture" and e["fixe"] for e in pts)
    assert all(e["distance_m"] == pytest.approx(600, abs=0.5) for e in pts)
    assert pts[0]["libelle"] == "BD TEST" and pts[0]["ordre"] == 2   # voie principale en tête
    assert pts[0]["proposition"] == "Barrer la voie" and "importance 2" in pts[0]["detail"]
    assert len({e["cle"] for e in pts}) == len(pts)


def test_bouclage_sans_zone_bouclee(tmp_path):
    r = _analyse(_cfg(tmp_path), [{"role": "vigilance", "rayon_m": 1000}], FauxWfs())
    assert _cat(r, "bouclage")["elements"] == []


def test_bouclage_d_un_polygone(tmp_path):
    sommets = [crise.vers_wgs84(X0 - 500, Y0 - 500), crise.vers_wgs84(X0 + 500, Y0 - 500),
               crise.vers_wgs84(X0 + 500, Y0 + 500), crise.vers_wgs84(X0 - 500, Y0 + 500)]
    r = _analyse(_cfg(tmp_path), [{"role": "fermeture", "forme": "polygone", "coordonnees": sommets}], FauxWfs())
    pts = _cat(r, "bouclage")["elements"]
    assert sorted(e["libelle"] for e in pts) == ["BD TEST", "BD TEST", "R DROITE", "R DROITE", "Voie sans nom (Rond-point)"]


# ---------------------------------------------------------------- dénombrement

def test_batiments_entiers_dans_la_zone_la_plus_grave(tmp_path):
    zones = [{"role": "evacuation", "rayon_m": 300}, {"role": "vigilance", "rayon_m": 1000}]
    r = _analyse(_cfg(tmp_path), zones, FauxWfs())
    assert _mesure(r, "bati", "n")["par_zone"] == {"evacuation": 1, "vigilance": 2}
    assert _mesure(r, "bati", "log")["par_zone"] == {"evacuation": 2, "vigilance": 15}   # inconnu : 0


def test_courbe_pour_les_compteurs_en_direct(tmp_path):
    r = _analyse(_cfg(tmp_path), [{"role": "evacuation", "rayon_m": 300}], FauxWfs())
    k = _cat(r, "bati")
    m = next(x for x in k["mesures"] if x["id"] == "n")
    pas = k["pas_courbe_m"]
    assert k["rayon_donnees_m"] == 1500 and len(m["courbe"]) == int(1500 / pas) + 1
    assert m["courbe"][int(100 / pas)] == 1 and m["courbe"][int(400 / pas)] == 2 and m["courbe"][-1] == 4


def test_population_au_prorata_de_la_surface(tmp_path):
    zones = [{"role": "evacuation", "rayon_m": 500}, {"role": "vigilance", "rayon_m": 1500}]
    r = _analyse(_cfg(tmp_path), zones, FauxWfs())
    hab = _mesure(r, "pop", "hab")
    # carreau 400-600 m coupé par le cercle de 500 m : environ la moitié (bord courbe) dans chaque zone
    assert hab["par_zone"]["evacuation"] == pytest.approx(1000 + 200, abs=8)
    assert hab["par_zone"]["evacuation"] + hab["par_zone"]["vigilance"] == pytest.approx(1600, abs=0.5)
    pas = _cat(r, "pop")["pas_courbe_m"]
    assert hab["courbe"][int(500 / pas)] == pytest.approx(hab["par_zone"]["evacuation"], abs=0.5)
    assert hab["courbe"][int(1300 / pas)] == pytest.approx(1600, abs=0.5)


def test_population_d_un_polygone_exacte(tmp_path):
    # rectangle qui couvre la moitié ouest du carreau central
    sommets = [crise.vers_wgs84(X0 - 100, Y0 - 100), crise.vers_wgs84(X0, Y0 - 100),
               crise.vers_wgs84(X0, Y0 + 100), crise.vers_wgs84(X0 - 100, Y0 + 100)]
    r = _analyse(_cfg(tmp_path), [{"role": "evacuation", "forme": "polygone", "coordonnees": sommets}], FauxWfs())
    assert _mesure(r, "pop", "hab")["par_zone"]["evacuation"] == pytest.approx(500, abs=2)


# ---------------------------------------------------------------- configuration

def _erreur(msg):
    return ValueError(msg)


def test_configuration_controlee(tmp_path):
    base = _cfg(tmp_path)["crise"]
    crise.controler(copy.deepcopy(base), _erreur)
    c = copy.deepcopy(base)
    c["categories"][0]["source_locale"] = "inconnue"
    with pytest.raises(ValueError, match="source_locale"):
        crise.controler(c, _erreur)
    c = copy.deepcopy(base)
    del c["categories"][1]["source_locale"]
    with pytest.raises(ValueError, match="demande « source_locale »"):
        crise.controler(c, _erreur)
    c = copy.deepcopy(base)
    c["categories"][1]["zones_bouclage"] = ["inconnue"]
    with pytest.raises(ValueError, match="zones_bouclage"):
        crise.controler(c, _erreur)
    c = copy.deepcopy(base)
    c["sources_locales"][0]["rayon_m"] = 5000
    with pytest.raises(ValueError, match="rayon_m"):
        crise.controler(c, _erreur)
    c = copy.deepcopy(base)
    del c["categories"][2]["mesures"]
    with pytest.raises(ValueError, match="mesures"):
        crise.controler(c, _erreur)


def test_configuration_du_projet_valide():
    from serveur.sig import config
    cfg = config.charger_config()
    roles = {k["id"]: k.get("role") for k in cfg["crise"]["categories"]}
    assert roles["rues"] == "rues" and roles["bouclage"] == "bouclage" and roles["population"] == "denombrement"
    assert {s["id"] for s in cfg["crise"]["sources_locales"]} == {"rues", "batiments", "population"}

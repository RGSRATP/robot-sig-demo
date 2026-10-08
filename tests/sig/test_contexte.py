"""Contexte d'une maquette : risques (Géorisques), bâti (BDNB, pages de 10, bande autour de l'emprise, bâti ancien),
niveau du cours d'eau en NGF (Hub'Eau), sources indépendantes, cache. Faux serveurs : aucun accès réseau."""
import httpx
import pytest
from pyproj import Transformer

from serveur.sig import config as cfg_sig
from serveur.sig import contexte as ctx

_wgs = Transformer.from_crs(2154, 4326, always_xy=True)


@pytest.fixture
def cfg(tmp_path):
    c = cfg_sig.charger_config()
    c["contexte"] = {**c["contexte"], "dossier_cache": str(tmp_path / "contexte")}
    return c


def carre_wgs84(x, y, cote):
    pts = [(x, y), (x + cote, y), (x + cote, y + cote), (x, y + cote), (x, y)]
    return {"type": "Polygon", "coordinates": [[list(_wgs.transform(*p)) for p in pts]]}


# maquette : carré de 20 m en Lambert 93 (Massy), point bas 62 m NGF
MAQUETTE = {"id": "m1", "nom": "Gare test", "positionnement": {
    "centre_wgs84": list(_wgs.transform(645460, 6847460)), "emprise_geojson": carre_wgs84(645450, 6847450, 20),
    "altitude_terrain": 83.6, "hauteurs_affichees": [-21.6, 14.3]}}


def bdnb(x, y, cote, annee, ident):
    pts = [[x, y], [x + cote, y], [x + cote, y + cote], [x, y + cote], [x, y]]
    return {"batiment_groupe_id": ident, "annee_construction": annee, "nb_niveau": 5, "hauteur_mean": 15,
            "mat_mur_txt": "PIERRE", "alea_argile": "Moyen",
            "geom_groupe": {"type": "MultiPolygon", "crs": {"type": "name", "properties": {"name": "EPSG:2154"}}, "coordinates": [[pts]]}}


# 12 bâtiments : 2 pages de 10 ; 1 ancien au droit, 1 récent à 10 m (dans la bande de 30 m), 10 loin (hors bande)
BATIMENTS = [bdnb(645455, 6847455, 5, 1890, "ancien-au-droit"), bdnb(645480, 6847455, 5, 2010, "recent-10m")] + \
            [bdnb(645600 + 20 * i, 6847600, 5, 1900, f"loin-{i}") for i in range(10)]


def faux(pannes=()):
    appels = []

    def gerer(req: httpx.Request):
        u = str(req.url)
        appels.append(u)
        for motif in pannes:
            if motif in u:
                return httpx.Response(503, json={})
        if "/rga" in u:
            return httpx.Response(200, json={"codeExposition": "3", "exposition": "Exposition forte"})
        if "zonage_sismique" in u:
            return httpx.Response(200, json={"data": [{"code_insee": "91377", "code_zone": "1", "zone_sismicite": "1 - TRES FAIBLE"}]})
        if "/radon" in u:
            return httpx.Response(200, json={"data": [{"classe_potentiel": "1"}]})
        if "gaspar/azi" in u:
            return httpx.Response(200, json={"data": [{"libelle_azi": "L'Orge"}]})
        if "installations_classees" in u:
            return httpx.Response(200, json={"results": 1, "data": [{"raisonSociale": "Usine", "longitude": 2.26, "latitude": 48.72}]})
        if "ssp/casias" in u:
            return httpx.Response(200, json={"results": 1, "data": [{"nom_etablissement": "Garage", "geom": {"type": "Point", "coordinates": [2.25, 48.72]}}]})
        if "/cavites" in u or "/mvt" in u:
            return httpx.Response(200, json={"results": 0, "data": []})
        if "bdnb" in u:
            off = int(req.url.params["offset"])
            return httpx.Response(200, json=BATIMENTS[off:off + 10])
        if "referentiel/stations" in u:
            return httpx.Response(200, json={"data": [{"code_station": "F1", "libelle_station": "L'Yvette à Villebon",
                                                        "libelle_cours_eau": "L'Yvette", "longitude_station": 2.23,
                                                        "latitude_station": 48.70, "altitude_ref_alti_station": 49.184}]})
        if "observations_tr" in u:
            return httpx.Response(200, json={"data": [{"resultat_obs": 314.0, "date_obs": "2026-10-08T22:20:00Z"}]})
        return httpx.Response(404, json={})
    return (lambda cfg: httpx.Client(transport=httpx.MockTransport(gerer))), appels


def test_risques_et_points(cfg):
    fab, _ = faux()
    d = ctx.contexte(cfg, MAQUETTE, fabrique=fab)
    r = {x["theme"].split(" (")[0]: x for x in d["risques"]}
    assert r["Retrait-gonflement des argiles"]["niveau"] == "élevé"
    assert r["Sismicité"]["niveau"] == "faible"
    assert r["Zones inondables"]["niveau"] == "à vérifier" and "pas forcément" in r["Zones inondables"]["detail"]
    cats = sorted(f["properties"]["categorie"] for f in d["points"]["features"])
    assert cats == ["Installation classée", "Site potentiellement pollué"]
    assert d["erreurs"] == []


def test_bati_bande_et_ancien(cfg):
    fab, appels = faux()
    d = ctx.contexte(cfg, MAQUETTE, fabrique=fab)
    p = {f["properties"]["id"]: f["properties"] for f in d["batiments"]["features"]}
    assert set(p) == {"ancien-au-droit", "recent-10m"}   # les 10 bâtiments lointains sont hors de la bande de 30 m
    assert p["ancien-au-droit"]["ancien"] and p["ancien-au-droit"]["au_droit"] and p["ancien-au-droit"]["distance_m"] == 0
    assert not p["recent-10m"]["ancien"] and not p["recent-10m"]["au_droit"] and p["recent-10m"]["distance_m"] == pytest.approx(10, abs=0.2)
    assert d["bati"]["anciens_au_droit"] == 1 and d["bati"]["nb"] == 2
    assert sum("bdnb" in u for u in appels) == 2   # deux pages de 10


def test_bati_tronque_au_plafond(cfg):
    cfg["contexte"]["max_batiments"] = 10
    fab, _ = faux()
    d = ctx.contexte(cfg, MAQUETTE, fabrique=fab)
    assert d["bati"]["tronque"] and any("arrêtée" in e for e in d["erreurs"])


def test_crue_niveau_ngf_et_marge(cfg):
    fab, _ = faux()
    c = ctx.contexte(cfg, MAQUETTE, fabrique=fab)["crue"]
    assert c["niveau_ngf"] == pytest.approx(49.498, abs=0.01)   # zéro d'échelle 49,184 + 0,314 m
    assert c["point_bas_maquette_ngf"] == pytest.approx(62.0) and c["marge_m"] == pytest.approx(12.5, abs=0.01)


def test_source_en_panne_ne_bloque_pas(cfg):
    fab, _ = faux(pannes=("bdnb", "/rga"))
    d = ctx.contexte(cfg, MAQUETTE, fabrique=fab)
    assert d["batiments"]["features"] == [] and any(e.startswith("BDNB") for e in d["erreurs"])
    assert any("argiles" in e for e in d["erreurs"])
    assert any(x["theme"].startswith("Sismicité") for x in d["risques"])   # les autres sources répondent
    assert d["crue"] is not None


def test_cache(cfg):
    fab, appels = faux()
    ctx.contexte(cfg, MAQUETTE, fabrique=fab)
    n = len(appels)
    ctx.contexte(cfg, MAQUETTE, fabrique=fab)
    assert len(appels) == n                   # relu dans le cache
    ctx.contexte(cfg, MAQUETTE, actualiser=True, fabrique=fab)
    assert len(appels) > n


@pytest.mark.reseau
def test_contexte_reel(cfg):
    """Vrais services, à travers le proxy : pytest -m reseau"""
    from serveur.sig import reseau
    d = ctx.contexte(cfg, MAQUETTE, fabrique=reseau.client)
    assert d["risques"] and d["crue"] is not None

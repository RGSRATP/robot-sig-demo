"""Contrôle du calage vertical (lot 5, croisement 6) : points de l'emprise, deux sources d'altitude comparées,
constats (écart au centre, amplitude, terrain modifié, niveau le plus proche du sol), panne d'une source.
Faux service d'altimétrie : aucun accès réseau."""
import httpx
import pytest
from pyproj import Transformer

from serveur.sig import calage
from serveur.sig import maquettes as maq

_wgs = Transformer.from_crs(2154, 4326, always_xy=True)
CFGM = {"altimetrie": {"url": "https://alti.test/elevation.json", "ressource": "rge", "ecart_max_terrain_m": 80,
                       "controle": {"ressource_comparaison": "lidar", "libelle_comparaison": "MNT LiDAR HD",
                                    "points_max": 30, "ecart_tolere_m": 0.5, "amplitude_signalee_m": 2}}}


def carre(x, y, cote):
    pts = [(x, y), (x + cote, y), (x + cote, y + cote), (x, y + cote), (x, y)]
    return {"type": "Polygon", "coordinates": [[list(_wgs.transform(*p)) for p in pts]]}


MAQUETTE = {"id": "m1", "nom": "Gare test", "positionnement": {
    "statut": "positionnee", "emprise_geojson": carre(645450, 6847450, 40), "centre_wgs84": list(_wgs.transform(645470, 6847470)),
    "altitude_terrain": 83.6, "calage_vertical": "altitudes du fichier, terrain IGN"}}


def fabrique(rge, lidar, panne=None):
    """rge, lidar : fonctions (x, y Lambert 93) → altitude."""
    vers_l93 = Transformer.from_crs(4326, 2154, always_xy=True)

    def gerer(req: httpx.Request):
        res = req.url.params["resource"]
        if res == panne:
            return httpx.Response(503)
        lon = [float(v) for v in req.url.params["lon"].split("|")]
        lat = [float(v) for v in req.url.params["lat"].split("|")]
        f = rge if res == "rge" else lidar
        return httpx.Response(200, json={"elevations": [f(*vers_l93.transform(a, b)) for a, b in zip(lon, lat)]})
    return lambda cfg: httpx.Client(transport=httpx.MockTransport(gerer))


def test_points_emprise_centre_sommets_grille():
    pts = calage.points_emprise(MAQUETTE["positionnement"]["emprise_geojson"], MAQUETTE["positionnement"]["centre_wgs84"], 30)
    assert 20 <= len(pts) <= 30
    assert pts[0] == pytest.approx(MAQUETTE["positionnement"]["centre_wgs84"], abs=1e-7)   # le centre d'abord


def test_calage_conforme():
    d = calage.controler({}, CFGM, MAQUETTE, {"source": "étages IFC", "niveaux": [
        {"nom": "N-1", "altitude_ngf": 79.0}, {"nom": "N0", "altitude_ngf": 84.0}]},
        fabrique=fabrique(lambda x, y: 83.6, lambda x, y: 83.5))
    assert d["centre"]["ecart_m"] == pytest.approx(0.1) and d["erreurs"] == []
    assert [c["niveau"] for c in d["constats"]] == ["conforme", "conforme", "info"]
    assert d["niveau_proche_terrain"]["nom"] == "N0" and d["niveau_proche_terrain"]["ecart_terrain_m"] == pytest.approx(0.5)


def test_terrain_modifie_et_ecart_au_centre():
    # fouille de 20 m dans la moitié ouest de l'emprise vue par le LiDAR seul ; écart de 1 m au centre
    d = calage.controler({}, CFGM, MAQUETTE, None,
                         fabrique=fabrique(lambda x, y: 84.6, lambda x, y: 63.6 if x < 645465 else 83.6))
    textes = " ".join(c["texte"] for c in d["constats"])
    assert d["centre"]["ecart_m"] == pytest.approx(1.0)
    assert "au-delà de la tolérance" in textes and "terrain modifié" in textes and d["points_ecart_fort"] > 0


def test_niveau_loin_du_sol_a_verifier_sauf_tunnel():
    loin = {"source": "étages IFC", "niveaux": [{"nom": "N0", "altitude_ngf": 120.0}]}
    d = calage.controler({}, CFGM, MAQUETTE, loin, fabrique=fabrique(lambda x, y: 83.6, lambda x, y: 83.6))
    assert d["constats"][-1]["niveau"] == "à vérifier"
    tunnel = {"source": "modèle 3D", "niveaux": [{"nom": "Point haut de la maquette", "altitude_ngf": 70.0}]}
    d = calage.controler({}, CFGM, MAQUETTE, tunnel, fabrique=fabrique(lambda x, y: 83.6, lambda x, y: 83.6))
    assert d["constats"][-1]["niveau"] == "info"


def test_source_en_panne_ou_sans_donnee_ne_bloque_pas():
    d = calage.controler({}, CFGM, MAQUETTE, None, fabrique=fabrique(lambda x, y: 83.6, None, panne="lidar"))
    assert d["comparaison"] is None and d["reference"]["nb"] > 0 and d["erreurs"][0].startswith("altimétrie (lidar)")
    d = calage.controler({}, CFGM, MAQUETTE, None, fabrique=fabrique(lambda x, y: 83.6, lambda x, y: -99999))
    assert d["centre"]["ecart_m"] is None and any("sans donnée" in c["texte"] for c in d["constats"])


def test_config_controle_incomplete(tmp_path):
    from pathlib import Path
    import yaml
    src = Path(__file__).resolve().parents[1] / "donnees" / "georef_test.yaml"
    c = yaml.safe_load(src.read_text(encoding="utf-8"))
    c.setdefault("altimetrie", {"url": "u", "ressource": "r", "ecart_max_terrain_m": 80})["controle"] = {"points_max": 10}
    p = tmp_path / "g.yaml"
    p.write_text(yaml.safe_dump(c, allow_unicode=True), encoding="utf-8")
    with pytest.raises(Exception, match="altimetrie.controle"):
        maq.charger_config(p)

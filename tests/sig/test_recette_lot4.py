"""Recette du lot 4 sur un cas réel connu : gare d'Orsay (51ORS, bâtiment voyageurs).

Le résultat de l'application (chaîne CC49 → WGS84 → Lambert 93, index spatial) est comparé à un calcul
indépendant fait directement dans le système natif des données (CC49) : emprise de la maquette =
emprise locale + origine du site, couches SIG lues telles quelles, intersection Shapely. Le nombre de
locaux est compté dans le fichier IFC brut (lignes IFCSPACE), sans IfcOpenShell.

Données internes (donnees/, cache/) : test désactivé par défaut — pytest -m donnees_reelles
"""
import json
import re
from pathlib import Path

import pyogrio
import pytest
from fastapi.testclient import TestClient
from shapely import affinity
from shapely.geometry import shape

from serveur.sig import maquettes as maq
from serveur.sig import routes
from serveur.sig.app import app
from serveur.sig.config import RACINE, charger_config

pytestmark = pytest.mark.donnees_reelles
NOM = "PN1515_05_PRO_BIM_000088_16_51ORS_BAT_A01"
COUCHES_SIG = ["Export__Gares", "Export__Lignes du GPE", "Export__Trafic intergare", "Export_Chantier_Ouvrages annexes"]


@pytest.fixture(scope="module")
def contexte():
    cfg = charger_config()
    cfgm = maq.charger_config()
    trouve = next(((i, r, c) for i, r, c in maq.fichiers(cfgm) if c.stem == NOM), None)
    if trouve is None:
        pytest.skip("maquette 51ORS absente de donnees/ifc")
    i, rel, chemin = trouve
    res = maq.resultat(cfgm, i, maq.cle_traitement(chemin, cfgm))
    if res is None or maq.locaux(cfgm, i, maq.cle_locaux(chemin, cfgm)) is None:
        pytest.skip("maquette 51ORS pas encore traitée (lancer le serveur et attendre la fin du traitement)")
    return cfg, cfgm, i, chemin, res


def test_recette_croisement_orsay(contexte):
    cfg, cfgm, id_, _chemin, res = contexte
    # --- calcul indépendant, en CC49
    ox, oy, _ = res["decalage"]
    emprise_cc49 = affinity.translate(shape(res["geometrie"]["emprise_locale"]), ox, oy)
    attendu = {}
    for nom in COUCHES_SIG:
        shp = next((RACINE / "donnees" / "sig").rglob(f"{nom}.shp"))
        gdf = pyogrio.read_dataframe(shp)
        assert gdf.crs.to_epsg() == 3949
        touches = gdf[gdf.intersects(emprise_cc49)]
        if len(touches):
            longueurs = sorted(round(g.intersection(emprise_cc49).length, 1) for g in touches.geometry if "Line" in g.geom_type)
            attendu[nom] = (len(touches), longueurs)

    # --- application
    app.dependency_overrides[routes._config_ou_erreur] = lambda: cfg
    app.dependency_overrides[routes._config_maquettes] = lambda: cfgm
    try:
        with TestClient(app) as c:
            ids = [x["id"] for x in c.get("/api/sig/couches").json()["couches"] if x["libelle"] in COUCHES_SIG]
            r = c.get(f"/api/sig/maquettes/{id_}/croisement", params={"couches": ",".join(ids)}).json()
    finally:
        app.dependency_overrides.clear()
    obtenu = {g["libelle"]: (g["total"], sorted(o["mesure"] for o in g["objets"] if o["unite"] == "m")) for g in r["groupes"]}

    assert set(obtenu) == set(attendu), (obtenu, attendu)
    for nom, (n, longueurs) in attendu.items():
        assert obtenu[nom][0] == n, nom
        # longueurs : CC49 et Lambert 93 diffèrent par leur facteur d'échelle local (quelques 1e-4)
        assert obtenu[nom][1] == pytest.approx(longueurs, rel=2e-3, abs=0.2), nom
    print("\nrecette 51ORS :", json.dumps(obtenu, ensure_ascii=False))


def test_recette_locaux_orsay(contexte):
    _cfg, cfgm, id_, chemin, _res = contexte
    texte = chemin.read_bytes()
    nb_ifcspace = len(re.findall(rb"=IFCSPACE\(", texte))
    d = maq.locaux(cfgm, id_, maq.cle_locaux(chemin, cfgm))
    assert len(d["locaux"]) == nb_ifcspace == 138
    codes = [l["code"] for l in d["locaux"]]
    assert all(c and c.startswith("51ORS-") for c in codes)
    assert len(set(codes)) == len(codes), "codes SGP_DI_Sous_Ouvrage en double"

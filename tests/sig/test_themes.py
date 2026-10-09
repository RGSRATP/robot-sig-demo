"""Vues thématiques : carbone de toutes les maquettes, calculé en arrière-plan et gardé dans le cache de chaque
maquette ; une maquette sans index des propriétés est ignorée sans relancer le calcul indéfiniment."""
import time

import pytest

from serveur.sig import carbone as carb_sig
from serveur.sig import maquettes as maq_sig
from serveur.sig import themes


@pytest.fixture
def cfgm(tmp_path, monkeypatch):
    for id_ in ("a", "b"):
        d = tmp_path / id_
        d.mkdir()
        if id_ == "a":
            (d / "proprietes.sqlite").write_bytes(b"")
    monkeypatch.setattr(maq_sig, "fichiers", lambda cfgm: [("a", "L1/Gare/a.ifc", None), ("b", "L1/Gare/b.ifc", None)])
    monkeypatch.setattr(maq_sig, "dossier_cache", lambda cfgm, id_: tmp_path / id_)
    appels = []

    def bilan(index, cfg):
        appels.append(index)
        return {"total_tco2e": 1234.56, "volume_compte_m3": 3000.0, "part_volume_materiau_declare": 0.4}
    monkeypatch.setattr(carb_sig, "bilan", bilan)
    themes.reinitialiser()
    yield {"carbone": {"materiaux": []}, "_appels": appels}
    themes.reinitialiser()


def attendre(cfgm):
    for _ in range(100):
        r = themes.carbone(cfgm)
        if r["complet"]:
            return r
        time.sleep(0.02)
    raise AssertionError("calcul non terminé")


def test_carbone_de_toutes_les_maquettes_puis_cache(cfgm, tmp_path):
    r = attendre(cfgm)
    assert r["valeurs"] == {"a": {"tco2e": 1234.6, "volume_m3": 3000.0, "part_declaree": 0.4}}
    assert r["total"] == 2 and r["calcules"] == 1 and (tmp_path / "a" / "carbone.json").is_file()
    # nouveau démarrage : relu dans le cache de la maquette, sans recalcul ; la maquette sans index ne relance rien
    themes.reinitialiser()
    r = attendre(cfgm)
    assert r["valeurs"]["a"]["tco2e"] == 1234.6 and len(cfgm["_appels"]) == 1
    assert themes.carbone(cfgm)["complet"]

"""Lecture et contrôle de config/sig.yaml."""
import pytest

from serveur.sig import config as cfg_sig


def test_config_du_depot_valide():
    cfg = cfg_sig.charger_config()
    carte = cfg_sig.config_carte(cfg)
    assert carte["fond_defaut"] in [f["id"] for f in carte["fonds"]]
    assert {"plan", "ortho"} <= {f["id"] for f in carte["fonds"]}


def test_fonds_ign_lus_depuis_la_config():
    cfg = cfg_sig.charger_config()
    for f in cfg_sig.config_carte(cfg)["fonds"]:
        assert f["url"] == cfg["fonds_ign"][f["id"]]["url"]
        assert "data.geopf.fr" in f["url"]


def test_centre_ile_de_france():
    lon, lat = cfg_sig.charger_config()["carte"]["centre_wgs84"]
    assert 1.4 < lon < 3.6 and 48.1 < lat < 49.3


def test_fichier_absent(tmp_path):
    with pytest.raises(cfg_sig.ConfigInvalide, match="introuvable"):
        cfg_sig.charger_config(tmp_path / "absent.yaml")


def test_yaml_invalide(tmp_path):
    p = tmp_path / "sig.yaml"
    p.write_text("carte: [\n", encoding="utf-8")
    with pytest.raises(cfg_sig.ConfigInvalide, match="YAML"):
        cfg_sig.charger_config(p)


@pytest.mark.parametrize(
    "contenu, attendu",
    [
        ("fonds_ign: {}\n", "carte"),
        ("carte: {centre_wgs84: [2.35], zoom_initial: 10}\n", "centre_wgs84"),
        ("carte: {centre_wgs84: [2.35, 48.85], zoom_initial: 40}\n", "zoom_initial"),
        ("carte: {centre_wgs84: [2.35, 48.85], zoom_initial: 10}\n", "fonds_ign"),
        (
            "carte: {centre_wgs84: [2.35, 48.85], zoom_initial: 10}\n"
            "fonds_ign: {defaut: ortho, plan: {url: 'https://x/{z}/{x}/{y}'}}\n",
            "defaut",
        ),
        (
            "carte: {centre_wgs84: [2.35, 48.85], zoom_initial: 10}\n"
            "fonds_ign: {defaut: plan, plan: {url: 'https://x/tuile'}}\n",
            "url",
        ),
    ],
)
def test_config_incomplete_message_clair(tmp_path, contenu, attendu):
    p = tmp_path / "sig.yaml"
    p.write_text(contenu, encoding="utf-8")
    with pytest.raises(cfg_sig.ConfigInvalide, match=attendu):
        cfg_sig.charger_config(p)

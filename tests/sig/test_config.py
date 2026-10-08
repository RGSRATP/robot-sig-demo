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


# ---------------------------------------------------------------- relief (lot 5)

def test_relief_du_depot_expose_a_l_interface():
    relief = cfg_sig.config_carte(cfg_sig.charger_config())["relief"]
    assert relief["defaut"] in [s["id"] for s in relief["sources"]]
    for s in relief["sources"]:
        assert "data.geopf.fr" in s["url"] and "{bbox-epsg-3857}" in s["url"]
        assert s["secours"] is None or s["secours"] in [x["id"] for x in relief["sources"]]


def _config_relief(tmp_path, relief: str):
    """Configuration du dépôt dont la section « relief » est remplacée."""
    import yaml
    cfg = yaml.safe_load(cfg_sig.CHEMIN_CONFIG.read_text(encoding="utf-8"))
    cfg.pop("relief", None)
    p = tmp_path / "sig.yaml"
    p.write_text(yaml.safe_dump(cfg, allow_unicode=True) + relief, encoding="utf-8")
    return p


def test_relief_facultatif(tmp_path):
    cfg = cfg_sig.charger_config(_config_relief(tmp_path, ""))
    assert cfg_sig.config_carte(cfg)["relief"] is None


URL_BIL = "https://x/wms?BBOX={bbox-epsg-3857}&WIDTH=256&HEIGHT=256&FORMAT=image/x-bil;bits=32"


@pytest.mark.parametrize(
    "relief, attendu",
    [
        ("relief: oui\n", "section"),
        ("relief: {actif: false, ombrage: true, zoom_max: 15, valeur_absente_sous: -1000, defaut: a, sources: {}}\n",
         "au moins une source"),
        (f"relief: {{actif: false, ombrage: true, zoom_max: 15, valeur_absente_sous: -1000, defaut: b, sources: {{a: {{url: '{URL_BIL}'}}}}}}\n",
         "defaut"),
        (f"relief: {{actif: oui, ombrage: true, zoom_max: 15, valeur_absente_sous: -1000, defaut: a, sources: {{a: {{url: '{URL_BIL}'}}}}}}\n",
         "actif"),
        (f"relief: {{actif: false, ombrage: true, zoom_max: 25, valeur_absente_sous: -1000, defaut: a, sources: {{a: {{url: '{URL_BIL}'}}}}}}\n",
         "zoom_max"),
        ("relief: {actif: false, ombrage: true, zoom_max: 15, valeur_absente_sous: -1000, defaut: a, sources: {a: {url: 'https://x/{z}/{x}/{y}.png'}}}\n",
         "bbox-epsg-3857"),
        ("relief: {actif: false, ombrage: true, zoom_max: 15, valeur_absente_sous: -1000, defaut: a, sources: {a: {url: 'https://x?BBOX={bbox-epsg-3857}&FORMAT=image/png'}}}\n",
         "x-bil"),
        (f"relief: {{actif: false, ombrage: true, zoom_max: 15, valeur_absente_sous: -1000, defaut: a, sources: {{a: {{url: '{URL_BIL}', secours: a}}}}}}\n",
         "secours"),
    ],
)
def test_relief_incomplet_message_clair(tmp_path, relief, attendu):
    with pytest.raises(cfg_sig.ConfigInvalide, match=attendu):
        cfg_sig.charger_config(_config_relief(tmp_path, relief))


@pytest.mark.reseau
@pytest.mark.parametrize("source", ["rgealti", "lidarhd"])
def test_relief_tuile_reelle(source):
    """Une tuile d'altitude de chaque source, à travers le proxy (Massy, z14) : pytest -m reseau"""
    import struct
    from serveur.sig import reseau
    cfg = cfg_sig.charger_config()
    url = cfg["relief"]["sources"][source]["url"].replace(
        "{bbox-epsg-3857}", "250468.4,6232369.5,252914.4,6234815.5")
    with reseau.client(cfg) as c:
        r = c.get(url)
    assert r.status_code == 200 and len(r.content) == 256 * 256 * 4, r.text[:200]
    h = struct.unpack("<65536f", r.content)
    assert 40 < min(h) and max(h) < 200   # altitudes NGF plausibles autour de Massy

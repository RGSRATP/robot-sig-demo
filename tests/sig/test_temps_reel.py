"""Temps réel : conversion de chaque type de source en GeoJSON, cache, repli sur la dernière réponse, clé IDFM
lue dans l'environnement (jamais exposée), routes. Faux serveurs (httpx MockTransport) : aucun accès réseau."""
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from serveur.sig import config as cfg_sig
from serveur.sig import temps_reel as tr

CLE = "cle-secrete-de-test-123"


@pytest.fixture(autouse=True)
def propre():
    tr.reinitialiser()
    yield
    tr.reinitialiser()


@pytest.fixture
def cfg():
    return cfg_sig.charger_config()


def dans(minutes):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def repondeur(table, appels=None):
    """Faux serveur : premier motif de `table` contenu dans l'URL → (statut, JSON)."""
    def gerer(req: httpx.Request):
        if appels is not None:
            appels.append(req)
        for motif, (statut, corps) in table.items():
            if motif in str(req.url):
                return httpx.Response(statut, json=corps)
        return httpx.Response(404, json={})
    return lambda cfg: httpx.Client(transport=httpx.MockTransport(gerer))


VELIB = {
    "station_information": (200, {"data": {"stations": [
        {"station_id": 1, "name": "Gare de Massy", "lat": 48.72, "lon": 2.26, "capacity": 30}]}}),
    "station_status": (200, {"last_updated": 1791491576, "data": {"stations": [
        {"station_id": 1, "num_bikes_available": 4, "num_bikes_available_types": [{"mechanical": 1}, {"ebike": 3}],
         "num_docks_available": 26, "is_installed": 1, "is_renting": 1, "last_reported": 1791491500},
        {"station_id": 99, "num_bikes_available": 2}]}}),   # station inconnue : ignorée
}


def test_gbfs_velos_disponibles(cfg):
    d = tr.donnees(cfg, "velib", fabrique=repondeur(VELIB))
    assert d["erreur"] is None and len(d["features"]) == 1
    p = d["features"][0]["properties"]
    assert (p["nom"], p["velos"], p["velos_mecaniques"], p["velos_electriques"], p["places_libres"]) == ("Gare de Massy", 4, 1, 3, 26)
    assert d["features"][0]["geometry"]["coordinates"] == [2.26, 48.72]
    assert d["date_donnees"].startswith("2026-")


def test_hubeau_derniere_observation_par_station(cfg):
    table = {
        "referentiel/stations": (200, {"data": [{"code_station": "F7", "libelle_station": "La Seine à Paris - Austerlitz",
                                                  "libelle_cours_eau": "La Seine"}]}),
        "observations_tr": (206, {"data": [   # tri décroissant
            {"code_station": "F7", "longitude": 2.36, "latitude": 48.84, "date_obs": "2026-10-08T21:00:00Z", "resultat_obs": 1830.0},
            {"code_station": "F7", "longitude": 2.36, "latitude": 48.84, "date_obs": "2026-10-08T20:55:00Z", "resultat_obs": 1825.0}]}),
    }
    d = tr.donnees(cfg, "hubeau", fabrique=repondeur(table))
    assert len(d["features"]) == 1
    p = d["features"][0]["properties"]
    assert p["hauteur_m"] == 1.83 and p["cours_d_eau"] == "La Seine" and p["observation"] == "2026-10-08T21:00:00Z"


def test_meteo_grille_et_libelles(cfg):
    src = tr.source(cfg, "meteo")
    o, s, e, n = src["emprise_wgs84"]
    nb = (round((e - o) / src["pas_deg"]) + 1) * (round((n - s) / src["pas_deg"]) + 1)
    rep = [{"current": {"time": "2026-10-08T23:15", "temperature_2m": 7.7, "precipitation": 0.2,
                        "wind_speed_10m": 4.3, "wind_direction_10m": 275, "weather_code": 61}}] * nb
    d = tr.donnees(cfg, "meteo", fabrique=repondeur({"forecast": (200, rep)}))
    assert len(d["features"]) == nb
    p = d["features"][0]["properties"]
    assert p["temperature_c"] == 7.7 and p["temps"] == "Pluie faible" and d["date_donnees"] == "2026-10-08T23:15"


ZONES = {"zones-d-arrets": (200, {"results": [
    {"zdaid": "58774", "zdaname": "Massy - Palaiseau", "zdatown": "Massy", "zdaxepsg2154": 645354, "zdayepsg2154": 6847448}]})}
LIGNES = {"referentiel-des-lignes": (200, {"results": [{"id_line": "C01742", "shortname_line": "A", "transportmode": "rail"}]})}


def siri(*visites):
    return {"Siri": {"ServiceDelivery": {"StopMonitoringDelivery": [
        {"ResponseTimestamp": "2026-10-08T21:00:00Z", "MonitoredStopVisit": list(visites)}]}}}


def visite(minutes, dest="Boissy-Saint-Léger"):
    return {"MonitoredVehicleJourney": {"LineRef": {"value": "STIF:Line::C01742:"},
                                        "DestinationName": [{"value": dest}],
                                        "MonitoredCall": {"ExpectedDepartureTime": dans(minutes), "DepartureStatus": "onTime"}}}


def test_idfm_cle_absente_message_clair(cfg, monkeypatch):
    monkeypatch.setattr(tr.reseau, "lire_env", lambda nom: None)
    d = tr.donnees(cfg, "idfm", fabrique=repondeur({}))
    assert d["features"] == [] and "clé absente" in d["erreur"] and "IDFM_PRIM_CLE" in d["erreur"]


def test_idfm_prochains_passages_cle_en_entete(cfg, monkeypatch):
    monkeypatch.setattr(tr.reseau, "lire_env", lambda nom: CLE if nom == "IDFM_PRIM_CLE" else None)
    monkeypatch.setitem(tr.source(cfg, "idfm"), "zones_arret", ["58774"])
    appels = []
    table = {**ZONES, **LIGNES, "stop-monitoring": (200, siri(visite(12), visite(3), visite(200), visite(-30)))}
    d = tr.donnees(cfg, "idfm", fabrique=repondeur(table, appels))
    assert d["erreur"] is None
    p = d["features"][0]["properties"]
    assert p["gare"] == "Massy - Palaiseau" and p["nb_passages"] == 2   # hors horizon et passés exclus
    assert [x["dans_min"] for x in p["passages"]] == [3, 12] and p["passages"][0]["ligne"] == "RER/Train A"
    lon, lat = d["features"][0]["geometry"]["coordinates"]
    assert 2.25 < lon < 2.27 and 48.72 < lat < 48.73   # Lambert 93 du référentiel converti en WGS84
    sm = [a for a in appels if "stop-monitoring" in str(a.url)][0]
    assert sm.headers["apikey"] == CLE and CLE not in str(sm.url)   # clé en en-tête, jamais dans l'URL
    assert sm.url.params["MonitoringRef"] == "STIF:StopArea:SP:58774:"
    assert CLE not in str(d)


def test_idfm_cle_refusee(cfg, monkeypatch):
    monkeypatch.setattr(tr.reseau, "lire_env", lambda nom: CLE)
    monkeypatch.setitem(tr.source(cfg, "idfm"), "zones_arret", ["58774"])
    d = tr.donnees(cfg, "idfm", fabrique=repondeur({**ZONES, "stop-monitoring": (401, {})}))
    assert "accès refusé" in d["erreur"] and CLE not in d["erreur"]


def test_cache_puis_repli_sur_derniere_reponse(cfg):
    t = [1000.0]
    horloge = lambda: t[0]
    appels = []
    d1 = tr.donnees(cfg, "velib", fabrique=repondeur(VELIB, appels), horloge=horloge)
    n = len(appels)
    t[0] += 10   # dans la période : pas de nouvelle lecture
    assert tr.donnees(cfg, "velib", fabrique=repondeur(VELIB, appels), horloge=horloge) == d1 and len(appels) == n
    t[0] += 120  # période écoulée, service en panne : dernière réponse gardée, avec le message
    d3 = tr.donnees(cfg, "velib", fabrique=repondeur({}), horloge=horloge)
    assert d3["features"] == d1["features"] and d3["date_maj"] == d1["date_maj"] and "HTTP 404" in d3["erreur"]


def test_service_injoignable_ne_bloque_pas(cfg):
    def panne(cfg):
        def gerer(req):
            raise httpx.ConnectError("injoignable")
        return httpx.Client(transport=httpx.MockTransport(gerer))
    d = tr.donnees(cfg, "hubeau", fabrique=panne)
    assert d["features"] == [] and "injoignable" in d["erreur"]


def test_sources_publiques_sans_cle(cfg, monkeypatch):
    monkeypatch.setattr(tr.reseau, "lire_env", lambda nom: CLE)
    s = {x["id"]: x for x in tr.sources_publiques(cfg)}
    assert set(s) == {"velib", "hubeau", "meteo", "idfm"}
    assert s["idfm"]["cle"] == {"variable": "IDFM_PRIM_CLE", "presente": True}
    assert CLE not in str(s)


def test_routes_sources(client_test, cfg_test, monkeypatch):
    monkeypatch.setitem(cfg_test, "temps_reel", cfg_sig.charger_config()["temps_reel"])
    assert [x["id"] for x in client_test.get("/api/sig/temps-reel").json()["sources"]][0] == "velib"


def test_routes_donnees(client_test, cfg_test, monkeypatch):
    monkeypatch.setitem(cfg_test, "temps_reel", cfg_sig.charger_config()["temps_reel"])
    origine = tr.donnees
    monkeypatch.setattr(tr, "donnees", lambda cfg, id_: origine(cfg, id_, fabrique=repondeur(VELIB)))
    r = client_test.get("/api/sig/temps-reel/velib")
    assert r.status_code == 200 and r.json()["features"][0]["properties"]["velos"] == 4
    assert client_test.get("/api/sig/temps-reel/inconnue").status_code == 404


def test_env_lu_avec_bom(tmp_path, monkeypatch):
    """Fichier .env enregistré par le Bloc-notes (UTF-8 avec BOM) : la première ligne est bien lue."""
    (tmp_path / ".env").write_text("IDFM_PRIM_CLE=abc\n", encoding="utf-8-sig")
    monkeypatch.setattr(tr.reseau, "RACINE", tmp_path)
    monkeypatch.delenv("IDFM_PRIM_CLE", raising=False)
    assert tr.reseau.lire_env("IDFM_PRIM_CLE") == "abc"


@pytest.mark.parametrize("modif, attendu", [
    ({"type": "inconnu"}, "type"),
    ({"cle": "abc"}, "aucune clé"),
    ({"url_etat": None}, "url_etat"),
])
def test_config_temps_reel_invalide(tmp_path, modif, attendu):
    import yaml
    cfg = yaml.safe_load(cfg_sig.CHEMIN_CONFIG.read_text(encoding="utf-8"))
    cfg["temps_reel"][0].update(modif)
    p = tmp_path / "sig.yaml"
    p.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    with pytest.raises(cfg_sig.ConfigInvalide, match=attendu):
        cfg_sig.charger_config(p)


@pytest.mark.reseau
@pytest.mark.parametrize("id_", ["velib", "hubeau", "meteo"])
def test_source_reelle(cfg, id_):
    """Vrais services, à travers le proxy : pytest -m reseau"""
    d = tr.donnees(cfg, id_)
    assert d["erreur"] is None and len(d["features"]) > 10

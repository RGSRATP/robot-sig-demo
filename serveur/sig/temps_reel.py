"""Données en temps réel (config/sig.yaml, section « temps_reel ») : interrogées par le serveur à travers le proxy,
converties en GeoJSON (WGS84), gardées en mémoire pendant « periode_s ».

Ne jamais bloquer : une source injoignable garde sa dernière réponse, datée, avec un message ; une gare IDFM en
erreur n'empêche pas les autres. Les clés (IDFM) sont lues dans l'environnement ou .env, jamais dans la
configuration, et n'apparaissent ni dans les messages ni dans les réponses.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

import httpx
from pyproj import Transformer
from shapely.geometry import box, mapping, shape

from . import reseau

# Codes météo de l'OMM (WMO) renvoyés par Open-Meteo : libellés (table de la norme, pas un paramètre métier).
METEO_WMO = {
    0: "Ciel dégagé", 1: "Plutôt dégagé", 2: "Partiellement nuageux", 3: "Couvert", 45: "Brouillard",
    48: "Brouillard givrant", 51: "Bruine légère", 53: "Bruine", 55: "Bruine forte", 56: "Bruine verglaçante",
    57: "Bruine verglaçante forte", 61: "Pluie faible", 63: "Pluie", 65: "Pluie forte", 66: "Pluie verglaçante",
    67: "Pluie verglaçante forte", 71: "Neige faible", 73: "Neige", 75: "Neige forte", 77: "Grains de neige",
    80: "Averses faibles", 81: "Averses", 82: "Averses violentes", 85: "Averses de neige", 86: "Fortes averses de neige",
    95: "Orage", 96: "Orage avec grêle", 99: "Orage avec forte grêle",
}
REFERENTIEL_HEURES = 24   # noms des stations Hub'Eau, positions des gares et noms des lignes IDFM : relus chaque jour

_l93_vers_wgs84 = Transformer.from_crs(2154, 4326, always_xy=True)


class ErreurSource(Exception):
    """Source injoignable ou réponse inexploitable ; le message est affiché tel quel."""


def _point(lon: float, lat: float, proprietes: dict) -> dict:
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lon, 7), round(lat, 7)]},
            "properties": proprietes}


def _maintenant() -> datetime:
    return datetime.now(timezone.utc)


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds") if ts else None


def _json(client: httpx.Client, url: str, **kw):
    try:
        r = client.get(url, **kw)
    except httpx.HTTPError as e:
        raise ErreurSource(reseau.message_erreur(e)) from e
    if r.status_code in (401, 403):
        raise ErreurSource(f"accès refusé (HTTP {r.status_code}) : clé absente, invalide ou sans abonnement à ce service")
    if r.status_code == 429:
        raise ErreurSource("trop de requêtes : le service limite le débit, nouvel essai à la prochaine actualisation")
    if r.status_code not in (200, 206):
        raise ErreurSource(f"réponse HTTP {r.status_code}")
    try:
        return r.json()
    except ValueError as e:
        raise ErreurSource("réponse illisible (JSON attendu)") from e


# ---------------------------------------------------------------- types de sources

def _gbfs(src: dict, client: httpx.Client, ref: dict) -> tuple[list, str | None, list]:
    """Vélos en libre-service (GBFS) : position et capacité des stations, état en temps réel."""
    infos = {s["station_id"]: s for s in _json(client, src["url_stations"])["data"]["stations"]}
    etat = _json(client, src["url_etat"])
    feats, plus_recent = [], 0
    for s in etat["data"]["stations"]:
        i = infos.get(s.get("station_id"))
        if not i or i.get("lat") is None:
            continue
        types = {k: v for t in s.get("num_bikes_available_types") or [] for k, v in t.items()}
        plus_recent = max(plus_recent, s.get("last_reported") or 0)
        feats.append(_point(i["lon"], i["lat"], {
            "nom": i.get("name"), "velos": s.get("num_bikes_available"), "velos_mecaniques": types.get("mechanical"),
            "velos_electriques": types.get("ebike"), "places_libres": s.get("num_docks_available"),
            "capacite": i.get("capacity"), "en_service": bool(s.get("is_installed") and s.get("is_renting")),
            "releve": _iso(s.get("last_reported")),
        }))
    return feats, _iso(etat.get("last_updated") or plus_recent), []


def _hubeau(src: dict, client: httpx.Client, ref: dict) -> tuple[list, str | None, list]:
    """Hauteurs d'eau (grandeur H, en mm) : observation la plus récente de chaque station de l'emprise."""
    o, s_, e, n = src["emprise_wgs84"]
    bbox = f"{o},{s_},{e},{n}"
    if "stations" not in ref:
        st = _json(client, f"{src['url']}/referentiel/stations", params={
            "bbox": bbox, "format": "json", "size": 5000,
            "fields": "code_station,libelle_station,libelle_cours_eau"})
        ref["stations"] = {x["code_station"]: x for x in st.get("data", [])}
    debut = (_maintenant() - timedelta(hours=src["heures"])).isoformat(timespec="seconds")
    obs = _json(client, f"{src['url']}/observations_tr", params={
        "bbox": bbox, "grandeur_hydro": "H", "date_debut_obs": debut, "size": 20000, "sort": "desc",
        "fields": "code_station,longitude,latitude,date_obs,resultat_obs"})
    derniere = {}
    for x in obs.get("data", []):   # tri décroissant : la première vue est la plus récente
        if x.get("code_station") not in derniere and x.get("longitude") is not None:
            derniere[x["code_station"]] = x
    feats = []
    for code, x in derniere.items():
        st = ref["stations"].get(code, {})
        feats.append(_point(x["longitude"], x["latitude"], {
            "station": st.get("libelle_station") or code, "cours_d_eau": st.get("libelle_cours_eau"), "code": code,
            "hauteur_m": round(x["resultat_obs"] / 1000, 3) if x.get("resultat_obs") is not None else None,
            "observation": x.get("date_obs"),
        }))
    date = max((x["date_obs"] for x in derniere.values() if x.get("date_obs")), default=None)
    return feats, date, []


def _meteo(src: dict, client: httpx.Client, ref: dict) -> tuple[list, str | None, list]:
    """Météo actuelle (Open-Meteo) sur une grille de points couvrant l'emprise, en une requête."""
    o, s_, e, n = src["emprise_wgs84"]
    pas = src["pas_deg"]
    points = [(round(o + i * pas, 4), round(s_ + j * pas, 4))
              for j in range(int(round((n - s_) / pas)) + 1) for i in range(int(round((e - o) / pas)) + 1)]
    rep = _json(client, src["url"], params={
        "latitude": ",".join(str(p[1]) for p in points), "longitude": ",".join(str(p[0]) for p in points),
        "current": "temperature_2m,precipitation,wind_speed_10m,wind_direction_10m,weather_code",
        "timezone": "Europe/Paris"})
    rep = rep if isinstance(rep, list) else [rep]
    feats, date = [], None
    for (lon, lat), r in zip(points, rep):
        c = r.get("current") or {}
        date = c.get("time") or date
        feats.append(_point(lon, lat, {
            "temperature_c": c.get("temperature_2m"), "precipitation_mm": c.get("precipitation"),
            "vent_kmh": c.get("wind_speed_10m"), "vent_direction_deg": c.get("wind_direction_10m"),
            "temps": METEO_WMO.get(c.get("weather_code"), f"code {c.get('weather_code')}"),
            "heure_locale": c.get("time"),
        }))
    return feats, date, []


def _idfm(src: dict, client: httpx.Client, ref: dict) -> tuple[list, str | None, list]:
    """Prochains passages (PRIM, SIRI Lite « stop-monitoring ») aux zones d'arrêt déclarées."""
    cle = reseau.lire_env(src["jeton_env"])
    if not cle:
        raise ErreurSource(f"clé absente : ajoutez la ligne {src['jeton_env']}=votre_clé au fichier .env du projet "
                           "(prise en compte à la prochaine actualisation)")
    if "zones" not in ref:   # positions et noms des zones d'arrêt (référentiel ouvert d'IDFM, sans clé)
        ids = ", ".join(f'"{z}"' for z in src["zones_arret"])
        r = _json(client, src["url_zones"], params={
            "where": f"zdaid in ({ids})", "select": "zdaid,zdaname,zdatown,zdaxepsg2154,zdayepsg2154", "limit": 100})
        ref["zones"] = {str(z["zdaid"]): z for z in r.get("results", [])}
    avert = [f"zone d'arrêt {z} introuvable dans le référentiel IDFM" for z in src["zones_arret"] if str(z) not in ref["zones"]]
    maintenant = _maintenant()
    feats, plus_recent = [], None
    for zid in src["zones_arret"]:
        z = ref["zones"].get(str(zid))
        if not z:
            continue
        lon, lat = _l93_vers_wgs84.transform(z["zdaxepsg2154"], z["zdayepsg2154"])
        props = {"gare": z.get("zdaname"), "commune": z.get("zdatown"), "zone_arret": str(zid)}
        try:
            siri = _json(client, src["url"], params={"MonitoringRef": f"STIF:StopArea:SP:{zid}:"}, headers={"apikey": cle})
            livraison = (((siri.get("Siri") or {}).get("ServiceDelivery") or {}).get("StopMonitoringDelivery") or [{}])[0]
            plus_recent = livraison.get("ResponseTimestamp") or plus_recent
            passages = []
            for v in livraison.get("MonitoredStopVisit") or []:
                mvj = v.get("MonitoredVehicleJourney") or {}
                appel = mvj.get("MonitoredCall") or {}
                heure = appel.get("ExpectedDepartureTime") or appel.get("ExpectedArrivalTime") \
                    or appel.get("AimedDepartureTime") or appel.get("AimedArrivalTime")
                if not heure:
                    continue
                t = datetime.fromisoformat(heure.replace("Z", "+00:00"))
                minutes = (t - maintenant).total_seconds() / 60
                if minutes < -1 or minutes > src["horizon_min"]:
                    continue
                ligne = (mvj.get("LineRef") or {}).get("value", "")
                dest = (mvj.get("DestinationName") or [{}])[0].get("value") or \
                    (appel.get("DestinationDisplay") or [{}])[0].get("value")
                passages.append({"ligne_ref": ligne, "destination": dest, "heure": heure,
                                 "dans_min": max(0, round(minutes)),
                                 "statut": appel.get("DepartureStatus") or appel.get("ArrivalStatus")})
            passages.sort(key=lambda p: p["heure"])
            _noms_lignes(src, client, ref, passages)
            props.update(passages=passages, nb_passages=len(passages),
                         prochain_min=passages[0]["dans_min"] if passages else None)
        except ErreurSource as e:
            props["erreur"] = str(e)
            avert.append(f"{z.get('zdaname')} : {e}")
        feats.append(_point(lon, lat, props))
    if feats and all("erreur" in f["properties"] for f in feats):
        raise ErreurSource(feats[0]["properties"]["erreur"])
    return feats, plus_recent, avert


def _noms_lignes(src: dict, client: httpx.Client, ref: dict, passages: list) -> None:
    """Nom court des lignes (« RER A ») d'après le référentiel ouvert d'IDFM ; le code reste si introuvable."""
    noms = ref.setdefault("lignes", {})
    codes = {p["ligne_ref"].split(":")[-2] for p in passages if p["ligne_ref"].count(":") >= 2} - set(noms)
    if codes and src.get("url_lignes"):
        try:
            ids = ", ".join(f'"{c}"' for c in codes)
            r = _json(client, src["url_lignes"], params={"where": f"id_line in ({ids})",
                                                         "select": "id_line,shortname_line,transportmode", "limit": 100})
            for x in r.get("results", []):
                mode = {"rail": "RER/Train", "metro": "Métro", "tram": "Tram", "bus": "Bus"}.get(x.get("transportmode"), "")
                noms[x["id_line"]] = f"{mode} {x.get('shortname_line')}".strip()
        except ErreurSource:
            pass
    for p in passages:
        code = p["ligne_ref"].split(":")[-2] if p["ligne_ref"].count(":") >= 2 else p["ligne_ref"]
        p["ligne"] = noms.get(code, code)


NIVEAUX_VIGILANCE = {1: "Vert", 2: "Jaune", 3: "Orange", 4: "Rouge"}   # nomenclature Vigicrues (pas un paramètre métier)


def _vigicrues(src: dict, client: httpx.Client, ref: dict) -> tuple[list, str | None, list]:
    """Vigilance crues par tronçon (Vigicrues, GeoJSON national) : tronçons qui recoupent l'emprise, simplifiés."""
    d = _json(client, src["url"], follow_redirects=True)
    zone = box(*src["emprise_wgs84"])
    tol = src.get("simplification_deg", 0.0005)
    feats, avert = [], []
    for f in d.get("features", []):
        p = f.get("properties") or {}
        try:
            g = shape(f["geometry"])
        except (KeyError, TypeError, ValueError, AttributeError):
            avert.append(f"tronçon {p.get('lbentcru') or p.get('CdEntCru')} : géométrie illisible, ignoré")
            continue
        if not g.intersects(zone):
            continue
        niveau = p.get("NivInfViCr")
        feats.append({"type": "Feature", "geometry": mapping(g.simplify(tol)), "properties": {
            "troncon": p.get("lbentcru"), "code": p.get("CdEntCru"), "niveau": niveau,
            "vigilance": NIVEAUX_VIGILANCE.get(niveau, "inconnue")}})
    return feats, None, avert


LECTEURS = {"vigicrues_vigilance": _vigicrues, "gbfs": _gbfs, "hubeau_hydrometrie": _hubeau, "open_meteo": _meteo, "idfm_prochains_passages": _idfm}


# ---------------------------------------------------------------- cache et accès

_verrou = threading.Lock()
_etats: dict[str, dict] = {}        # id -> dernière réponse publiée
_referentiels: dict[str, dict] = {}  # id -> données lentes (noms, positions) et date de lecture
_verrous: dict[str, threading.Lock] = {}


def reinitialiser() -> None:
    with _verrou:
        _etats.clear()
        _referentiels.clear()


def source(cfg: dict, id_: str) -> dict | None:
    return next((s for s in cfg.get("temps_reel") or [] if s["id"] == id_), None)


def sources_publiques(cfg: dict) -> list[dict]:
    """Sources déclarées, pour l'interface (aucune clé, seulement sa présence)."""
    out = []
    for s in cfg.get("temps_reel") or []:
        d = {k: s.get(k) for k in ("id", "type", "libelle", "periode_s", "attribution")}
        d["visible"] = bool(s.get("visible", False))
        if s.get("jeton_env"):
            d["cle"] = {"variable": s["jeton_env"], "presente": bool(reseau.lire_env(s["jeton_env"]))}
        out.append(d)
    return out


def donnees(cfg: dict, id_: str, fabrique=reseau.client, horloge=time.time) -> dict:
    """Dernière réponse de la source, relue si elle a plus de « periode_s » secondes. Jamais d'exception :
    en cas d'échec, la réponse précédente (ou une collection vide) avec le message d'erreur."""
    src = source(cfg, id_)
    if src is None:
        raise KeyError(id_)
    with _verrou:
        verrou = _verrous.setdefault(id_, threading.Lock())
    with verrou:   # une seule lecture à la fois par source ; les autres attendent et reprennent son résultat
        etat = _etats.get(id_)
        if etat and horloge() - etat["_lu_a"] < src["periode_s"]:
            return _publique(etat)
        ref = _referentiels.get(id_)
        if not ref or horloge() - ref["_lu_a"] > REFERENTIEL_HEURES * 3600:
            ref = _referentiels[id_] = {"_lu_a": horloge()}
        try:
            with fabrique(cfg) as client:
                feats, date, avert = LECTEURS[src["type"]](src, client, ref)
            etat = {"features": feats, "date_donnees": date, "avertissements": avert, "erreur": None,
                    "date_maj": _iso(horloge()), "_lu_a": horloge()}
        except ErreurSource as e:
            precedent = _etats.get(id_) or {"features": [], "date_donnees": None, "date_maj": None, "avertissements": []}
            etat = {**precedent, "erreur": str(e), "_lu_a": horloge()}
        except (KeyError, TypeError, ValueError) as e:   # format de réponse inattendu
            precedent = _etats.get(id_) or {"features": [], "date_donnees": None, "date_maj": None, "avertissements": []}
            etat = {**precedent, "erreur": f"réponse dans un format inattendu ({type(e).__name__} : {e})", "_lu_a": horloge()}
        _etats[id_] = etat
        return _publique(etat)


def _publique(etat: dict) -> dict:
    return {"type": "FeatureCollection", "features": etat["features"], "date_donnees": etat["date_donnees"],
            "date_maj": etat["date_maj"], "erreur": etat["erreur"], "avertissements": etat["avertissements"]}

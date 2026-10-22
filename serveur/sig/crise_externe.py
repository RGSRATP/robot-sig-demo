"""Carte de crise (lot C3) : recherche d'adresse (géocodage IGN, base de la BAN) et vent au point de l'événement
(Open-Meteo, source « open_meteo » déjà déclarée dans « temps_reel »).

Ne jamais bloquer : un service injoignable renvoie un message, la recherche des stations et l'analyse continuent.
"""
from __future__ import annotations

import threading
import time

import httpx

from . import reseau

_verrou = threading.Lock()
_vents: dict = {}   # (lon, lat arrondis) -> (instant, réponse)


def controler(c: dict, erreur) -> None:
    g = c.get("geocodage")
    if g is not None:
        if not (isinstance(g, dict) and isinstance(g.get("url"), str) and g["url"].startswith("http")):
            raise erreur("« crise.geocodage » : « url » attendue (service de géocodage de l'IGN)")
        e = g.get("emprise_wgs84")
        if e is not None and not (isinstance(e, list) and len(e) == 4 and e[0] < e[2] and e[1] < e[3]):
            raise erreur("« crise.geocodage.emprise_wgs84 » : [ouest, sud, est, nord] attendu")
    v = c.get("vent")
    if v is not None:
        if not (isinstance(v, dict) and isinstance(v.get("source"), str)):
            raise erreur("« crise.vent » : « source » attendue (identifiant d'une source temps réel open_meteo)")
        s = v.get("secteur_deg", 60)
        if not (isinstance(s, (int, float)) and not isinstance(s, bool) and 0 < s < 180):
            raise erreur("« crise.vent.secteur_deg » doit être compris entre 0 et 180")


# ---------------------------------------------------------------- adresses

def adresses(cfg: dict, q: str, fabrique=reseau.client) -> tuple[list[dict], str | None]:
    """Adresses et lieux-dits dont le libellé correspond au texte, dans l'emprise déclarée ; (résultats, anomalie)."""
    g = cfg["crise"].get("geocodage")
    if not g or len(q.strip()) < 3:
        return [], None
    params = {"q": q.strip(), "limit": int(g.get("limite", 6)) * 2}   # double : une partie peut être hors emprise
    if g.get("autour_wgs84"):   # priorité aux réponses proches (Paris), sans exclure les autres
        params["lon"], params["lat"] = g["autour_wgs84"]
    try:
        with fabrique(cfg) as client:
            r = client.get(g["url"], params=params)
        if r.status_code != 200:
            return [], f"recherche d'adresse indisponible (réponse HTTP {r.status_code})"
        features = r.json().get("features") or []
    except httpx.HTTPError as e:
        return [], f"recherche d'adresse indisponible ({reseau.message_erreur(e)})"
    except (ValueError, AttributeError):
        return [], "recherche d'adresse indisponible (réponse illisible)"
    o, s, e, n = g.get("emprise_wgs84") or [-180, -90, 180, 90]
    sortie = []
    for f in features:
        try:
            lon, lat = (float(x) for x in f["geometry"]["coordinates"][:2])
        except (KeyError, TypeError, ValueError):
            continue
        if not (o <= lon <= e and s <= lat <= n):
            continue
        p = f.get("properties") or {}
        sortie.append({"type": "Adresse" if p.get("type") in ("housenumber", "street") else "Lieu",
                       "libelle": p.get("label") or p.get("name") or "?", "position": [round(lon, 6), round(lat, 6)]})
    return sortie[:int(g.get("limite", 6))], None


# ---------------------------------------------------------------- vent

def vent(cfg: dict, lon: float, lat: float, fabrique=reseau.client) -> dict:
    """Vent actuel au point : direction d'où il vient (degrés), vitesse et rafales (km/h), heure de la mesure.

    Indication simple pour la cellule de crise, pas un modèle de dispersion."""
    c = cfg["crise"].get("vent")
    if not c:
        return {"disponible": False, "message": "vent non configuré (« crise.vent »)"}
    src = next((s for s in cfg.get("temps_reel", []) if s.get("id") == c["source"]), None)
    if src is None or src.get("type") != "open_meteo":
        return {"disponible": False, "message": f"source « {c['source']} » absente ou pas de type open_meteo"}
    cle = (round(lon, 2), round(lat, 2))   # ~1 km : la grille du modèle est plus large
    duree = float(c.get("cache_minutes", 10)) * 60
    with _verrou:
        deja = _vents.get(cle)
    if deja and time.monotonic() - deja[0] < duree:
        return deja[1]
    try:
        with fabrique(cfg) as client:
            r = client.get(src["url"], params={"latitude": lat, "longitude": lon, "timezone": "Europe/Paris",
                                               "current": "wind_speed_10m,wind_direction_10m,wind_gusts_10m"})
        if r.status_code != 200:
            return {"disponible": False, "message": f"météo indisponible (réponse HTTP {r.status_code})"}
        cur = r.json().get("current") or {}
    except httpx.HTTPError as e:
        return {"disponible": False, "message": f"météo indisponible ({reseau.message_erreur(e)})"}
    except (ValueError, AttributeError):
        return {"disponible": False, "message": "météo indisponible (réponse illisible)"}
    if cur.get("wind_direction_10m") is None or cur.get("wind_speed_10m") is None:
        return {"disponible": False, "message": "météo : vent absent de la réponse"}
    rep = {"disponible": True, "direction_deg": float(cur["wind_direction_10m"]),
           "vitesse_kmh": float(cur["wind_speed_10m"]),
           "rafales_kmh": float(cur["wind_gusts_10m"]) if cur.get("wind_gusts_10m") is not None else None,
           "heure": cur.get("time"), "secteur_deg": float(c.get("secteur_deg", 60)),
           "source": src.get("libelle"), "attribution": src.get("attribution")}
    with _verrou:
        _vents[cle] = (time.monotonic(), rep)
    return rep


def reinitialiser() -> None:
    with _verrou:
        _vents.clear()

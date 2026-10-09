"""Contexte d'une maquette (lot 5, croisements) : risques autour de l'ouvrage (Géorisques), bâti dans une bande
autour de son emprise (BDNB : âge, niveaux, matériaux, argiles), niveau actuel du cours d'eau le plus proche
(Hub'Eau, converti en NGF) et crues historiques (Vigicrues), comparés au point bas et aux niveaux de la maquette.

Interrogé par le serveur à la demande, à travers le proxy ; résultat gardé en cache (« cache_heures »). Ne jamais
bloquer : chaque interrogation est indépendante, une source en panne devient un message, les autres s'affichent.
"""
from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from pyproj import Transformer
from shapely.geometry import box, mapping, shape
from shapely.ops import transform

from . import reseau
from .config import RACINE

_vers_l93 = Transformer.from_crs(4326, 2154, always_xy=True)
_vers_wgs84 = Transformer.from_crs(2154, 4326, always_xy=True)
# Champs BDNB demandés (la table en a 138) : géométrie, âge, gabarit, matériaux, usage, sol.
CHAMPS_BDNB = ("batiment_groupe_id,geom_groupe,annee_construction,nb_niveau,hauteur_mean,mat_mur_txt,mat_toit_txt,"
               "usage_niveau_1_txt,alea_argile,denomination_monument_historique,altitude_sol_mean")
PAGE_BDNB = 10   # taille de page imposée par l'API publique de la BDNB
CELLULE_BDNB_M = 100   # la bande est interrogée par carrés : un tunnel long et en biais ne ramène pas tout son rectangle


class ErreurContexte(Exception):
    """Source injoignable ou réponse inattendue ; message affiché tel quel."""


def _get(client: httpx.Client, url: str, **kw):
    try:
        r = client.get(url, **kw)
    except httpx.HTTPError as e:
        raise ErreurContexte(reseau.message_erreur(e)) from e
    if r.status_code not in (200, 206):
        raise ErreurContexte(f"réponse HTTP {r.status_code}")
    try:
        return r.json()
    except ValueError as e:
        raise ErreurContexte("réponse illisible (JSON attendu)") from e


def _point(lon, lat, proprietes) -> dict:
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": proprietes}


# ---------------------------------------------------------------- risques (Géorisques)

def risques(cx: dict, client: httpx.Client, lon: float, lat: float) -> tuple[list, list, list]:
    """Synthèse des risques au centre de la maquette et points à risque dans le rayon : (risques, points, erreurs)."""
    base, rayon = cx["georisques"], cx["rayon_m"]
    ll = f"{lon},{lat}"
    lignes, points, erreurs = [], [], []

    def essai(nom, f):
        try:
            f()
        except ErreurContexte as e:
            erreurs.append(f"Géorisques ({nom}) : {e}")
        except (KeyError, TypeError, ValueError) as e:
            erreurs.append(f"Géorisques ({nom}) : réponse inattendue ({type(e).__name__})")

    def argiles():
        d = _get(client, f"{base}/rga", params={"latlon": ll})
        niveau = {"3": "élevé", "2": "moyen", "1": "faible"}.get(str(d.get("codeExposition")), "info")
        lignes.append({"theme": "Retrait-gonflement des argiles", "niveau": niveau, "detail": d.get("exposition") or "non renseigné"})

    def sismicite():
        d = _get(client, f"{base}/zonage_sismique", params={"latlon": ll})
        zones = sorted({x.get("zone_sismicite") for x in d.get("data", []) if x.get("zone_sismicite")})
        code = max((int(x.get("code_zone") or 0) for x in d.get("data", [])), default=0)
        lignes.append({"theme": "Sismicité", "niveau": "faible" if code <= 2 else "moyen" if code == 3 else "élevé",
                       "detail": ", ".join(zones) or "non renseignée"})
        insee = sorted({x["code_insee"] for x in d.get("data", []) if x.get("code_insee")})
        if insee:
            r = _get(client, f"{base}/radon", params={"code_insee": insee[0]})
            classe = max((int(x.get("classe_potentiel") or 0) for x in r.get("data", [])), default=0)
            lignes.append({"theme": "Potentiel radon (commune)", "niveau": {1: "faible", 2: "moyen", 3: "élevé"}.get(classe, "info"),
                           "detail": f"classe {classe}" if classe else "non renseigné"})

    def inondation():
        d = _get(client, f"{base}/gaspar/azi", params={"latlon": ll, "rayon": cx["rayon_inondation_m"]})
        azi = sorted({x.get("libelle_azi") for x in d.get("data", []) if x.get("libelle_azi")})
        # un atlas à proximité ne dit pas que l'ouvrage est en zone inondable : à vérifier sur la carte de l'atlas
        lignes.append({"theme": f"Zones inondables (atlas, {cx['rayon_inondation_m']} m)", "niveau": "à vérifier" if azi else "faible",
                       "detail": f"atlas à proximité : {', '.join(azi)} (l'ouvrage n'est pas forcément en zone inondable)"
                       if azi else "aucun atlas de zone inondable à proximité"})

    def ponctuels(nom, chemin, categorie, libelle, extraire):
        def f():
            d = _get(client, f"{base}/{chemin}", params={"latlon": ll, "rayon": rayon, "page_size": 100})
            n = d.get("results") or 0
            for x in d.get("data", []):
                pos = extraire(x)
                if pos:
                    points.append(_point(pos[0], pos[1], {"categorie": categorie, **libelle(x)}))
            lignes.append({"theme": f"{nom} ({rayon} m)", "niveau": "moyen" if n else "faible", "detail": f"{n} recensé(s)"})
        essai(nom, f)

    lonlat = lambda x: (x["longitude"], x["latitude"]) if x.get("longitude") is not None else None
    essai("argiles", argiles)
    essai("sismicité", sismicite)
    essai("inondation", inondation)
    ponctuels("Cavités souterraines", "cavites", "Cavité souterraine",
              lambda x: {"nom": x.get("nom_cavite") or x.get("type_cavite") or "cavité", "detail": x.get("type_cavite")}, lonlat)
    ponctuels("Mouvements de terrain", "mvt", "Mouvement de terrain",
              lambda x: {"nom": x.get("type") or "mouvement", "detail": f"{x.get('lieu') or ''} {x.get('date_debut') or ''}".strip()}, lonlat)
    ponctuels("Installations classées", "installations_classees", "Installation classée",
              lambda x: {"nom": x.get("raisonSociale"), "detail": x.get("statutSeveso")}, lonlat)
    ponctuels("Sites potentiellement pollués (CASIAS)", "ssp/casias", "Site potentiellement pollué",
              lambda x: {"nom": x.get("nom_etablissement") or x.get("adresse") or "site", "detail": x.get("statut")},
              lambda x: tuple(x["geom"]["coordinates"][:2]) if (x.get("geom") or {}).get("type") == "Point" else None)
    return lignes, points, erreurs


# ---------------------------------------------------------------- bâti (BDNB)

def _pas(a: float, b: float) -> list[float]:
    n = max(1, math.ceil((b - a) / CELLULE_BDNB_M))
    return [a + i * CELLULE_BDNB_M for i in range(n)]


def _pages_bdnb(cx: dict, client: httpx.Client, cellule):
    """Pages de bâtiments BDNB d'un carré (Lambert 93)."""
    x1, y1, x2, y2 = cellule.bounds
    offset = 0
    while True:
        page = _get(client, cx["bdnb"], params={"xmin": round(x1, 1), "ymin": round(y1, 1), "xmax": round(x2, 1),
                                                "ymax": round(y2, 1), "select": CHAMPS_BDNB, "limit": PAGE_BDNB, "offset": offset})
        if not isinstance(page, list) or not page:
            return
        yield page
        if len(page) < PAGE_BDNB:
            return
        offset += PAGE_BDNB


def _entite_bdnb(b: dict, zone, emprise_l93, cx: dict) -> dict | None:
    """Bâtiment BDNB dans la bande → entité GeoJSON (WGS84) ; None hors de la bande ou sans géométrie lisible."""
    try:
        g = shape({k: v for k, v in b["geom_groupe"].items() if k != "crs"})
    except (ValueError, TypeError, KeyError, AttributeError):
        return None
    if not g.intersects(zone):
        return None
    annee = b.get("annee_construction")
    return {"type": "Feature", "geometry": mapping(transform(_vers_wgs84.transform, g)), "properties": {
        "id": b["batiment_groupe_id"], "annee_construction": annee,
        "ancien": bool(annee and annee < cx["annee_bati_ancien"]),
        "au_droit": bool(g.intersects(emprise_l93)), "distance_m": round(g.distance(emprise_l93), 1),
        "nb_niveaux": b.get("nb_niveau"), "hauteur_m": b.get("hauteur_mean"), "murs": b.get("mat_mur_txt"),
        "toiture": b.get("mat_toit_txt"), "usage": b.get("usage_niveau_1_txt"), "alea_argile": b.get("alea_argile"),
        "monument_historique": b.get("denomination_monument_historique"), "altitude_sol_m": b.get("altitude_sol_mean"),
    }}


def batiments(cx: dict, client: httpx.Client, emprise_l93) -> tuple[list, dict, list]:
    """Bâtiments BDNB dans la bande « tampon_bati_m » autour de l'emprise, interrogée par carrés de
    CELLULE_BDNB_M : (entités WGS84, synthèse, erreurs)."""
    zone = emprise_l93.buffer(cx["tampon_bati_m"])
    bx1, by1, bx2, by2 = zone.bounds
    cellules = [c for c in (box(x, y, x + CELLULE_BDNB_M, y + CELLULE_BDNB_M) for x in _pas(bx1, bx2) for y in _pas(by1, by2))
                if c.intersects(zone)]
    feats, erreurs, vus = [], [], set()

    def parcourir() -> bool:
        """Vrai si la lecture s'arrête au plafond « max_batiments »."""
        lus = 0
        for cellule in cellules:
            for page in _pages_bdnb(cx, client, cellule):
                lus += len(page)
                for b in page:
                    if b.get("batiment_groupe_id") in vus:
                        continue
                    vus.add(b.get("batiment_groupe_id"))
                    f = _entite_bdnb(b, zone, emprise_l93, cx)
                    if f:
                        feats.append(f)
                if lus >= cx["max_batiments"]:
                    return True
        return False

    tronque = False
    try:
        tronque = parcourir()
    except ErreurContexte as e:
        erreurs.append(f"BDNB : {e} (liste incomplète)")
    if tronque:
        erreurs.append(f"BDNB : lecture arrêtée à {cx['max_batiments']} bâtiments lus (« contexte.max_batiments ») : liste incomplète")
    p = [f["properties"] for f in feats]
    synthese = {"nb": len(p), "anciens": sum(x["ancien"] for x in p), "au_droit": sum(x["au_droit"] for x in p),
                "anciens_au_droit": sum(x["ancien"] and x["au_droit"] for x in p),
                "sans_annee": sum(x["annee_construction"] is None for x in p),
                "monuments": sum(bool(x["monument_historique"]) for x in p), "tronque": tronque,
                "tampon_m": cx["tampon_bati_m"], "annee_seuil": cx["annee_bati_ancien"]}
    return feats, synthese, erreurs


# ---------------------------------------------------------------- crue (Hub'Eau, Vigicrues)

def crues_historiques(cx: dict, client: httpx.Client, stations: list[dict], cours_d_eau: str | None) -> tuple[dict | None, list]:
    """Crues historiques publiées par Vigicrues (hauteurs à l'échelle) pour la station la plus proche qui en
    publie, de préférence sur le même cours d'eau ; converties en NGF avec le zéro d'échelle actuel de la station
    (Hub'Eau). Seules les stations de prévision de Vigicrues en publient."""
    if not cx.get("vigicrues"):
        return None, []
    candidates = [s for s in stations if s.get("altitude_ref_alti_station") is not None]
    candidates.sort(key=lambda s: (s.get("libelle_cours_eau") != cours_d_eau, s["_d"]))
    erreurs = []
    for s in candidates[:cx.get("max_stations_vigicrues", 4)]:
        try:
            d = _get(client, cx["vigicrues"], params={"CdStationHydro": s["code_station"]})
        except ErreurContexte as e:
            erreurs.append(f"Vigicrues : {e}")
            break
        liste = ((d or {}).get("VigilanceCrues") or {}).get("CruesHistoriques") or []
        zero = s["altitude_ref_alti_station"]
        crues = [{"libelle": c.get("LbUsuel") or "crue", "hauteur_m": float(c["ValHauteur"]),
                  "niveau_ngf": round(zero + float(c["ValHauteur"]), 2), "debit_m3s": c.get("ValDebit")}
                 for c in liste if isinstance(c.get("ValHauteur"), (int, float))]
        if crues:
            crues.sort(key=lambda c: -c["niveau_ngf"])
            return {"station": s["libelle_station"], "cours_d_eau": s.get("libelle_cours_eau"), "code": s["code_station"],
                    "distance_km": round(s["_d"] / 1000, 2), "zero_echelle_ngf": zero, "crues": crues,
                    "meme_cours_d_eau": s.get("libelle_cours_eau") == cours_d_eau}, erreurs
    return None, erreurs


def cours_d_eau(cx: dict, client: httpx.Client, lon: float, lat: float, point_bas_ngf: float | None) -> tuple[dict | None, list]:
    """Station hydrométrique la plus proche (hauteur d'eau H), niveau actuel converti en NGF (zéro d'échelle de la
    station + hauteur) et marge par rapport au point bas de la maquette ; crues historiques (Vigicrues)."""
    r = cx["rayon_station_km"]
    dlat, dlon = r / 111.0, r / (111.0 * math.cos(math.radians(lat)))
    try:
        st = _get(client, f"{cx['hubeau']}/referentiel/stations", params={
            "bbox": f"{lon - dlon},{lat - dlat},{lon + dlon},{lat + dlat}", "en_service": "true", "size": 200,
            "fields": "code_station,libelle_station,libelle_cours_eau,longitude_station,latitude_station,"
                      "altitude_ref_alti_station,code_systeme_alti_site"})
        stations = [s for s in st.get("data", []) if s.get("longitude_station") is not None]
        if not stations:
            return None, [f"Hub'Eau : aucune station hydrométrique à moins de {r} km"]
        x0, y0 = _vers_l93.transform(lon, lat)
        for s in stations:
            sx, sy = _vers_l93.transform(s["longitude_station"], s["latitude_station"])
            s["_d"] = math.hypot(sx - x0, sy - y0)
        stations.sort(key=lambda s: s["_d"])
        erreurs = []
        # la plus proche qui publie une hauteur récente et l'altitude de son zéro d'échelle (niveau NGF calculable)
        proches = sorted(stations[:8], key=lambda s: (s.get("altitude_ref_alti_station") is None, s["_d"]))
        for s in proches:
            obs = _get(client, f"{cx['hubeau']}/observations_tr", params={
                "code_entite": s["code_station"], "grandeur_hydro": "H", "size": 1, "sort": "desc"})
            if not obs.get("data"):
                continue
            o = obs["data"][0]
            h = o["resultat_obs"] / 1000
            zero = s.get("altitude_ref_alti_station")
            niveau = round(zero + h, 2) if zero is not None else None
            if zero is None:
                erreurs.append(f"Hub'Eau : altitude du zéro d'échelle inconnue pour {s['libelle_station']} : niveau NGF non calculable")
            historiques, err = crues_historiques(cx, client, stations[:8], s.get("libelle_cours_eau"))
            return {"station": s["libelle_station"], "cours_d_eau": s.get("libelle_cours_eau"), "code": s["code_station"],
                    "distance_km": round(s["_d"] / 1000, 2), "hauteur_m": round(h, 3), "zero_echelle_ngf": zero,
                    "niveau_ngf": niveau, "observation": o.get("date_obs"), "point_bas_maquette_ngf": point_bas_ngf,
                    "marge_m": round(point_bas_ngf - niveau, 2) if niveau is not None and point_bas_ngf is not None else None,
                    "lon": s["longitude_station"], "lat": s["latitude_station"], "historiques": historiques}, erreurs + err
        return None, ["Hub'Eau : aucune hauteur d'eau récente aux stations voisines"]
    except ErreurContexte as e:
        return None, [f"Hub'Eau : {e}"]
    except (KeyError, TypeError, ValueError) as e:
        return None, [f"Hub'Eau : réponse inattendue ({type(e).__name__})"]


def face_a_l_eau(crue: dict | None, niveaux: dict) -> dict:
    """Croisement « crue × niveaux » : chaque niveau de la maquette (altitude NGF du plancher) comparé au niveau
    actuel du cours d'eau et aux crues historiques. Marge positive : plancher au-dessus du niveau de l'eau.
    Comparaison d'altitudes seulement : ni protections, ni cheminement de l'eau, ni distance au cours d'eau."""
    refs = []
    if crue and crue.get("niveau_ngf") is not None:
        refs.append({"libelle": "Niveau actuel", "niveau_ngf": crue["niveau_ngf"], "date": crue.get("observation")})
    h = (crue or {}).get("historiques")
    if h:
        refs += [{"libelle": c["libelle"], "niveau_ngf": c["niveau_ngf"]} for c in h["crues"]]
    lignes = []
    for n in niveaux.get("niveaux", []):
        marges = [round(n["altitude_ngf"] - r["niveau_ngf"], 2) for r in refs]
        lignes.append({**n, "marges_m": marges, "sous": [r["libelle"] for r, m in zip(refs, marges) if m < 0]})
    return {"references": refs, "niveaux": lignes, "source": niveaux.get("source"), "message": niveaux.get("message")}


# ---------------------------------------------------------------- assemblage et cache

def _cache(cfg: dict, id_: str) -> Path:
    d = Path(cfg["contexte"]["dossier_cache"])
    return (d if d.is_absolute() else RACINE / d) / f"{id_}.json"


def contexte(cfg: dict, maquette: dict, actualiser: bool = False, fabrique=reseau.client, horloge=time.time) -> dict:
    """Contexte d'une maquette positionnée (description complète : emprise WGS84, centre, hauteurs, terrain)."""
    cx = cfg["contexte"]
    chemin = _cache(cfg, maquette["id"])
    if not actualiser and chemin.is_file():
        try:
            d = json.loads(chemin.read_text(encoding="utf-8"))
            if horloge() - d["_lu_a"] < cx["cache_heures"] * 3600:
                return d
        except (ValueError, KeyError):
            pass
    pos = maquette["positionnement"]
    lon, lat = pos["centre_wgs84"]
    emprise = transform(_vers_l93.transform, shape(pos["emprise_geojson"]))
    sol, h = pos.get("altitude_terrain"), pos.get("hauteurs_affichees")
    point_bas = round(sol + h[0], 2) if sol is not None and h else None
    with fabrique(cfg) as client:
        lignes, points, err1 = risques(cx, client, lon, lat)
        bats, synthese, err2 = batiments(cx, client, emprise)
        crue, err3 = cours_d_eau(cx, client, lon, lat, point_bas)
    d = {"maquette": {"id": maquette["id"], "nom": maquette["nom"]}, "risques": lignes,
         "points": {"type": "FeatureCollection", "features": points},
         "batiments": {"type": "FeatureCollection", "features": bats}, "bati": synthese, "crue": crue,
         "erreurs": err1 + err2 + err3, "date": datetime.now(timezone.utc).isoformat(timespec="seconds"), "_lu_a": horloge()}
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return d


COLONNES_BATI = [("id", "Identifiant BDNB"), ("annee_construction", "Année de construction"), ("ancien", "Bâti ancien"),
                 ("au_droit", "Au droit de l'ouvrage"), ("distance_m", "Distance à l'emprise (m)"),
                 ("nb_niveaux", "Niveaux"), ("hauteur_m", "Hauteur (m)"), ("murs", "Murs"), ("toiture", "Toiture"),
                 ("usage", "Usage"), ("alea_argile", "Aléa argiles"), ("monument_historique", "Monument historique")]


def lignes_bati(d: dict) -> list[dict]:
    return [f["properties"] for f in d["batiments"]["features"]]


def tableau_niveaux(eau: dict) -> tuple[list, list[dict]]:
    """Colonnes et lignes de l'export CSV « niveaux face à l'eau » : une colonne de marge par référence."""
    colonnes = [("nom", "Niveau"), ("source", "Origine de l'altitude"), ("altitude_ngf", "Altitude (m NGF)")]
    colonnes += [(f"ref{i}", f"Marge / {r['libelle']} ({r['niveau_ngf']} m NGF)") for i, r in enumerate(eau["references"])]
    lignes = [{"nom": n["nom"], "source": n["source"], "altitude_ngf": n["altitude_ngf"],
               **{f"ref{i}": m for i, m in enumerate(n["marges_m"])}} for n in eau["niveaux"]]
    return colonnes, lignes

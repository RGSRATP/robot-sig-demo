"""Contrôle du calage vertical d'une maquette (lot 5, croisement 6).

Le calage pose la maquette par rapport au terrain RGE ALTI lu en un seul point (centre de l'emprise). Le contrôle
interroge, sur des points de l'emprise (sommets, centre, grille régulière), le RGE ALTI et une seconde source plus
fine (MNT LiDAR HD) au service d'altimétrie de l'IGN, puis signale :
- l'écart entre les deux sources au centre (point du calage) ;
- l'amplitude du terrain sur l'emprise (un calage sur un seul point est approximatif si le terrain varie) ;
- le niveau de la maquette le plus proche du terrain et son écart au terrain LiDAR (repère de vraisemblance des
  altitudes de la maquette : un rez-de-chaussée attendu près du sol, une erreur de dizaines de mètres se voit).

Ne jamais bloquer : une source indisponible devient un message ; ce contrôle ne modifie pas le calage.
"""
from __future__ import annotations

import math

import httpx
from pyproj import Transformer
from shapely.geometry import Point, shape
from shapely.ops import transform

from . import reseau

_vers_l93 = Transformer.from_crs(4326, 2154, always_xy=True)
_vers_wgs84 = Transformer.from_crs(2154, 4326, always_xy=True)
ABSENTE_SOUS = -1000   # valeur « sans donnée » du service (-99999)


def points_emprise(emprise_wgs84: dict, centre_wgs84, points_max: int) -> list[tuple[float, float]]:
    """Centre, sommets puis grille régulière dans l'emprise, au plus points_max points (WGS84)."""
    e = transform(_vers_l93.transform, shape(emprise_wgs84))
    pts = [tuple(_vers_l93.transform(*centre_wgs84))]
    sommets = []
    for g in getattr(e, "geoms", [e]):
        sommets += list(g.exterior.coords)[:-1]
    reste = max(0, points_max - 1)
    pas_sommets = max(1, math.ceil(len(sommets) / max(1, reste // 3)))   # au plus un tiers du budget pour les sommets
    pts += sommets[::pas_sommets]
    reste = points_max - len(pts)
    if reste > 0 and e.area > 0:
        pas = math.sqrt(e.area / reste)
        x1, y1, x2, y2 = e.bounds
        y = y1 + pas / 2
        grille = []
        while y < y2:
            x = x1 + pas / 2
            while x < x2:
                if e.contains(Point(x, y)):
                    grille.append((x, y))
                x += pas
            y += pas
        pts += grille[:reste]
    return [tuple(round(v, 7) for v in _vers_wgs84.transform(x, y)) for x, y in pts[:points_max]]


def altitudes(client: httpx.Client, url: str, ressource: str, pts) -> list[float | None]:
    """Altitudes (m) des points à une ressource du service d'altimétrie, None là où elle n'a pas de donnée."""
    r = client.get(url, params={"lon": "|".join(f"{p[0]:.7f}" for p in pts), "lat": "|".join(f"{p[1]:.7f}" for p in pts),
                                "resource": ressource, "zonly": "true", "delimiter": "|"})
    r.raise_for_status()
    z = r.json().get("elevations") or []
    if len(z) != len(pts):
        raise ValueError(f"{len(z)} altitudes pour {len(pts)} points")
    return [float(v) if isinstance(v, (int, float)) and v > ABSENTE_SOUS else None for v in z]


def _stats(valeurs) -> dict | None:
    v = [x for x in valeurs if x is not None]
    if not v:
        return None
    return {"min": round(min(v), 2), "max": round(max(v), 2), "moyenne": round(sum(v) / len(v), 2), "nb": len(v)}


def controler(cfg: dict, cfgm: dict, m: dict, niveaux: dict | None = None, fabrique=None) -> dict:
    """Contrôle du calage d'une maquette positionnée (description complète). niveaux : sortie de niveaux_ngf."""
    alti = cfgm["altimetrie"]
    ctl = alti["controle"]
    pos = m["positionnement"]
    pts = points_emprise(pos["emprise_geojson"], pos["centre_wgs84"], int(ctl["points_max"]))
    sortie = {"maquette": {"id": m["id"], "nom": m["nom"]}, "calage": pos.get("calage_vertical"),
              "terrain_calage": pos.get("altitude_terrain"), "source_calage": alti.get("ressource"),
              "source_comparaison": ctl.get("libelle_comparaison") or ctl["ressource_comparaison"],
              "nb_points": len(pts), "erreurs": [], "constats": [],
              "ecart_tolere_m": ctl["ecart_tolere_m"], "amplitude_signalee_m": ctl["amplitude_signalee_m"]}
    z = {}
    with (fabrique or reseau.client)(cfg) as client:
        for cle, ressource in (("reference", alti["ressource"]), ("comparaison", ctl["ressource_comparaison"])):
            try:
                z[cle] = altitudes(client, alti["url"], ressource, pts)
            except httpx.HTTPError as e:
                sortie["erreurs"].append(f"altimétrie ({ressource}) : {reseau.message_erreur(e)}")
            except ValueError as e:
                sortie["erreurs"].append(f"altimétrie ({ressource}) : réponse inattendue ({e})")
    ref, comp = z.get("reference"), z.get("comparaison")
    sortie["reference"] = _stats(ref) if ref else None
    sortie["comparaison"] = _stats(comp) if comp else None
    sortie["points"] = [{"lon": p[0], "lat": p[1], "reference": ref[i] if ref else None, "comparaison": comp[i] if comp else None}
                        for i, p in enumerate(pts)]
    centre_ref, centre_comp = (ref[0] if ref else None), (comp[0] if comp else None)
    sortie["centre"] = {"reference": centre_ref, "comparaison": centre_comp,
                        "ecart_m": round(centre_ref - centre_comp, 2) if centre_ref is not None and centre_comp is not None else None}
    ecarts = [a - b for a, b in zip(ref or [], comp or []) if a is not None and b is not None]
    sortie["ecarts"] = _stats(ecarts)
    c = sortie["constats"]

    def constat(niveau, texte):
        c.append({"niveau": niveau, "texte": texte})

    if sortie["centre"]["ecart_m"] is not None:
        e = sortie["centre"]["ecart_m"]
        constat("à vérifier" if abs(e) > ctl["ecart_tolere_m"] else "conforme",
                f"Au centre (point du calage), RGE ALTI {e:+.2f} m par rapport au {sortie['source_comparaison']}"
                + (f" : au-delà de la tolérance de {ctl['ecart_tolere_m']} m" if abs(e) > ctl["ecart_tolere_m"] else ""))
    elif comp is not None and centre_comp is None:
        constat("info", f"{sortie['source_comparaison']} sans donnée au centre de la maquette (zone non couverte)")
    st = sortie["reference"]   # amplitude sur la source du calage
    if st:
        amplitude = round(st["max"] - st["min"], 2)
        sortie["amplitude_terrain_m"] = amplitude
        constat("à vérifier" if amplitude > ctl["amplitude_signalee_m"] else "conforme",
                f"Terrain RGE ALTI de {st['min']:.2f} à {st['max']:.2f} m NGF sur l'emprise (amplitude {amplitude:.2f} m)"
                + (" : le calage sur le seul point central est approximatif" if amplitude > ctl["amplitude_signalee_m"] else ""))
    if ecarts:
        hors = [e for e in ecarts if abs(e) > ctl["amplitude_signalee_m"]]
        sortie["points_ecart_fort"] = len(hors)
        if hors:
            pire = max(hors, key=abs)
            constat("à vérifier", f"{len(hors)} point(s) sur {len(ecarts)} où les deux sources diffèrent de plus de "
                    f"{ctl['amplitude_signalee_m']} m (jusqu'à {pire:+.2f} m, RGE ALTI moins {sortie['source_comparaison']}) : "
                    "terrain modifié entre les deux relevés (chantier, fouille, remblai) ou donnée erronée")
    sol = centre_comp if centre_comp is not None else centre_ref
    if niveaux and niveaux.get("niveaux") and sol is not None:
        proche = min(niveaux["niveaux"], key=lambda n: abs(n["altitude_ngf"] - sol))
        ecart = round(proche["altitude_ngf"] - sol, 2)
        sortie["niveau_proche_terrain"] = {**proche, "ecart_terrain_m": ecart}
        douteux = abs(ecart) > 5 and niveaux.get("source") != "modèle 3D"   # un tunnel n'a pas de niveau près du sol
        constat("à vérifier" if douteux else "info",
                f"Niveau le plus proche du terrain : {proche['nom']} à {proche['altitude_ngf']:.2f} m NGF, {ecart:+.2f} m par rapport au "
                f"terrain au centre" + (" : altitudes de la maquette à vérifier (aucun niveau près du sol)" if douteux else ""))
    if pos.get("calage_vertical", "").startswith("base posée au sol"):
        constat("info", "Maquette posée au sol faute d'altitude du terrain : ses altitudes ne sont pas des altitudes NGF")
    return sortie

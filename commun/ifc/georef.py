"""Interprétation du géoréférencement d'une maquette (fonction pure, à partir de lecture.georef_brut()).

Repère retenu : les coordonnées « locales » de la maquette sont ses coordonnées monde (mètres) moins
l'origine du site (`decalage`). Le repère dit comment passer des coordonnées locales au système projeté :

    projeté = origine + echelle · R(rotation) · local       (x, y)
    altitude = origine[2] + local z

Méthodes, par ordre de priorité (SPEC lot 3) :
1. IfcMapConversion + IfcProjectedCRS (IFC4) ;
2. IfcSite :
   a. coordonnées projetées portées par le placement du site (coordonnées partagées des logiciels
      auteurs) ; le système n'est jamais supposé : il est identifié par la cohérence avec
      RefLatitude / RefLongitude parmi les systèmes candidats, ou déclaré par l'appelant ;
   b. RefLatitude / RefLongitude seules (point + nord géographique), si elles tombent dans l'emprise plausible ;
3. saisie manuelle transmise par l'appelant (utilisée en dernier, ou en premier si « forcer »).
"""
from __future__ import annotations

import math

from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError

SYSTEME_REFERENCE = "EPSG:2154"   # système des saisies manuelles et du positionnement par latitude / longitude

LIBELLES = {
    "ifc_map_conversion": "IfcMapConversion + IfcProjectedCRS",
    "ifc_site_projete": "IfcSite : coordonnées projetées du site",
    "ifc_site_latlon": "IfcSite : RefLatitude / RefLongitude",
    "manuel": "Saisie manuelle",
}


def _crs(texte: str) -> CRS | None:
    try:
        return CRS.from_user_input(texte)
    except (CRSError, TypeError):
        return None


def _nom(crs: CRS) -> str:
    epsg = crs.to_epsg(min_confidence=70)
    return f"EPSG:{epsg}" if epsg else crs.name


def _dans_domaine(crs: CRS, x: float, y: float, marge_deg: float = 0.5) -> bool:
    zone = crs.area_of_use
    if zone is None:
        return False
    try:
        lon, lat = Transformer.from_crs(crs, 4326, always_xy=True).transform(x, y)
    except Exception:
        return False
    if not (math.isfinite(lon) and math.isfinite(lat)):
        return False
    return (zone.west - marge_deg <= lon <= zone.east + marge_deg
            and zone.south - marge_deg <= lat <= zone.north + marge_deg)


def _dans_emprise(lon: float, lat: float, emprise) -> bool:
    if not emprise:
        return True
    return emprise[0] <= lon <= emprise[2] and emprise[1] <= lat <= emprise[3]


def vers_projete(repere: dict, x: float, y: float, z: float = 0.0) -> tuple[float, float, float]:
    """Coordonnées locales (mètres) → coordonnées dans le système du repère."""
    a = math.radians(repere["rotation_deg"])
    s = repere["echelle"]
    ox, oy, oz = repere["origine"]
    return (ox + s * (math.cos(a) * x - math.sin(a) * y),
            oy + s * (math.sin(a) * x + math.cos(a) * y),
            oz + z)


def _azimut_nord_vrai(crs: CRS, lon: float, lat: float) -> float:
    """Angle (degrés, sens horaire) du nord géographique par rapport au nord du quadrillage au point donné."""
    t = Transformer.from_crs(4326, crs, always_xy=True)
    x0, y0 = t.transform(lon, lat)
    x1, y1 = t.transform(lon, lat + 0.001)
    return math.degrees(math.atan2(x1 - x0, y1 - y0))


def _non_positionnee(raison: str, avert: list[str], **extra) -> dict:
    return {"statut": "non_positionnee", "methode": None, "libelle": "Non positionnée", "raison": raison,
            "systeme": None, "repere": None, "avertissements": avert, **extra}


def _positionnee(methode: str, systeme: str, repere: dict, avert: list[str], precision: str, **extra) -> dict:
    return {"statut": "positionnee", "methode": methode, "libelle": LIBELLES[methode], "raison": None,
            "systeme": systeme, "repere": repere, "avertissements": avert, "precision": precision, **extra}


def _manuel(manuel: dict, decalage, avert) -> dict:
    try:
        x, y = float(manuel["x"]), float(manuel["y"])
    except (KeyError, TypeError, ValueError):
        return _non_positionnee("saisie manuelle incomplète : « x » et « y » (Lambert 93) attendus", avert)
    systeme = manuel.get("systeme", SYSTEME_REFERENCE)
    crs = _crs(systeme)
    if crs is None:
        return _non_positionnee(f"saisie manuelle : système « {systeme} » non reconnu", avert)
    if not _dans_domaine(crs, x, y):
        return _non_positionnee(f"saisie manuelle : point ({x:.1f}, {y:.1f}) hors du domaine de {_nom(crs)}", avert)
    repere = {"origine": [x, y, float(manuel.get("altitude", 0.0))],
              "rotation_deg": float(manuel.get("rotation_deg", 0.0)), "echelle": 1.0}
    return _positionnee("manuel", _nom(crs), repere, avert, "saisie", decalage=decalage)


def resoudre(brut: dict, systemes_candidats=(), tolerance_m: float = 2000.0, systeme_declare: str | None = None,
             emprise_plausible_wgs84=None, manuel: dict | None = None) -> dict:
    """Positionnement d'une maquette : méthode, système, repère, avertissements (ou raison du non-positionnement).

    - systemes_candidats : systèmes parmi lesquels identifier celui des coordonnées projetées du site ;
    - tolerance_m : écart maximal entre RefLatitude/RefLongitude et le placement du site pour identifier un système ;
    - systeme_declare : système des coordonnées projetées du site, déclaré par l'appelant ;
    - emprise_plausible_wgs84 : [ouest, sud, est, nord] ; une latitude/longitude hors de cette emprise est rejetée
      (valeur par défaut du logiciel auteur) ;
    - manuel : {"x", "y", "altitude", "rotation_deg", "systeme" (défaut Lambert 93), "forcer"}.
    """
    avert: list[str] = []
    site = brut.get("site")
    decalage = list(site["origine"]) if site else [0.0, 0.0, 0.0]

    if manuel and manuel.get("forcer"):
        return _manuel(manuel, decalage, avert)

    # 1. IfcMapConversion + IfcProjectedCRS
    mc = brut.get("map_conversion")
    if mc:
        crs = _crs(mc.get("systeme") or "")
        if crs is None:
            avert.append(f"IfcMapConversion présent mais système « {mc.get('systeme')} » non reconnu : ignoré")
        else:
            rotation = math.degrees(math.atan2(mc["axe_x_ordonnee"], mc["axe_x_abscisse"]))
            r = {"origine": [mc["est"], mc["nord"], mc["hauteur"]], "rotation_deg": rotation, "echelle": mc["echelle"]}
            # local = monde - décalage : l'origine du repère intègre le décalage
            ox, oy, _ = vers_projete(r, decalage[0], decalage[1])
            repere = {"origine": [ox, oy, mc["hauteur"] + decalage[2]], "rotation_deg": rotation, "echelle": mc["echelle"]}
            if _dans_domaine(crs, ox, oy):
                return _positionnee("ifc_map_conversion", _nom(crs), repere, avert, "fichier", decalage=decalage)
            avert.append(f"IfcMapConversion : point ({ox:.0f}, {oy:.0f}) hors du domaine de {_nom(crs)} : ignoré")

    if site is None:
        if manuel:
            return _manuel(manuel, decalage, avert)
        return _non_positionnee("aucun IfcSite ni IfcMapConversion, et pas de saisie manuelle", avert)

    # 2a. Coordonnées projetées portées par le placement du site
    x, y, z = site["origine"]
    lat, lon = site.get("latitude"), site.get("longitude")
    candidats = [c for c in (_crs(s) for s in systemes_candidats) if c is not None]
    domaine = [c for c in candidats if _dans_domaine(c, x, y)]
    ecarts = {}
    if lat is not None and lon is not None:
        for c in domaine:
            px, py = Transformer.from_crs(4326, c, always_xy=True).transform(lon, lat)
            ecarts[_nom(c)] = math.hypot(px - x, py - y)
    coherents = [n for n, d in ecarts.items() if d <= tolerance_m]
    detail = {"ecarts_latlon_m": {k: round(v, 1) for k, v in ecarts.items()}}

    if domaine:
        if len(coherents) == 1:
            if systeme_declare and _crs(systeme_declare) and _nom(_crs(systeme_declare)) != coherents[0]:
                avert.append(f"système déclaré {systeme_declare} différent du système identifié {coherents[0]} : "
                             "le système identifié est retenu")
            repere = {"origine": [x, y, z], "rotation_deg": 0.0, "echelle": 1.0}
            return _positionnee("ifc_site_projete", coherents[0], repere, avert, "fichier",
                                decalage=decalage, systeme_source="identifié (RefLatitude/RefLongitude)", **detail)
        if len(coherents) > 1:
            avert.append(f"plusieurs systèmes compatibles ({', '.join(coherents)}) : système à déclarer")
        if systeme_declare:
            crs = _crs(systeme_declare)
            if crs is None:
                return _non_positionnee(f"système déclaré « {systeme_declare} » non reconnu", avert, **detail)
            if not _dans_domaine(crs, x, y):
                return _non_positionnee(f"coordonnées du site ({x:.0f}, {y:.0f}) hors du domaine du système déclaré "
                                        f"{_nom(crs)}", avert, **detail)
            if ecarts and not coherents:
                plus_proche = min(ecarts.values())
                avert.append(f"RefLatitude/RefLongitude incohérentes avec le placement du site (écart {plus_proche / 1000:.1f} km) : "
                             "ignorées (valeur par défaut du logiciel auteur ?)")
            repere = {"origine": [x, y, z], "rotation_deg": 0.0, "echelle": 1.0}
            return _positionnee("ifc_site_projete", _nom(crs), repere, avert, "fichier",
                                decalage=decalage, systeme_source="déclaré (configuration)", **detail)
        if manuel:
            return _manuel(manuel, decalage, avert)
        noms = ", ".join(_nom(c) for c in domaine)
        motif = ("RefLatitude/RefLongitude absentes" if not ecarts
                 else f"RefLatitude/RefLongitude incohérentes (écart minimal {min(ecarts.values()) / 1000:.1f} km)")
        return _non_positionnee(f"le site porte des coordonnées projetées ({x:.0f}, {y:.0f}) compatibles avec {noms}, "
                                f"mais {motif} : système à déclarer", avert, **detail)

    # 2b. RefLatitude / RefLongitude seules
    if lat is not None and lon is not None and not (lat == 0 and lon == 0):
        if _dans_emprise(lon, lat, emprise_plausible_wgs84):
            crs = _crs(SYSTEME_REFERENCE)
            px, py = Transformer.from_crs(4326, crs, always_xy=True).transform(lon, lat)
            nord = brut.get("nord_geographique") or [0.0, 1.0]
            # angle (sens trigonométrique) dont il faut tourner la maquette pour aligner son nord sur le nord vrai,
            # puis correction de la convergence des méridiens du système projeté
            rot_nord = math.degrees(math.atan2(nord[0], nord[1]))
            rotation = rot_nord - _azimut_nord_vrai(crs, lon, lat)
            altitude = site.get("altitude") or 0.0
            repere = {"origine": [px, py, altitude], "rotation_deg": rotation, "echelle": 1.0}
            avert.append("position approchée : point RefLatitude/RefLongitude du site et nord géographique ; à vérifier")
            if manuel:
                return _manuel(manuel, decalage, avert)
            return _positionnee("ifc_site_latlon", _nom(crs), repere, avert, "approchée", decalage=decalage)
        avert.append(f"RefLatitude/RefLongitude ({lat:.4f}, {lon:.4f}) hors de l'emprise plausible : "
                     "valeur par défaut du logiciel auteur ? ignorées")

    if manuel:
        return _manuel(manuel, decalage, avert)
    raison = avert[-1] if avert else "ni coordonnées projetées, ni RefLatitude/RefLongitude exploitables"
    return _non_positionnee(raison, avert[:-1] if avert else [])

"""Plans Locaux d'Aide à l'Intervention (PLAI) de la RATP : un PDF vectoriel par station (lot 5, 2026-10-15).

Chaque plan identifie, près d'un accès numéroté, le point RATP (bureau de station), le point de rassemblement
police / RATP, l'accès principal pompiers et l'accès le plus proche des quais de chaque ligne. Les PDF ne sont pas
géoréférencés ; leur lecture se fait en trois temps :

1. lecture du plan (pdfplumber, lecture seule) : station, lignes, adresse de l'accès principal, étiquettes des accès
   (numéro, nom, position sur le plan) ; pictogrammes de la légende découpés (ils servent aussi de symboles sur la
   carte) puis recherchés dans le plan par corrélation d'images (couleurs comprises, plusieurs tailles) ; épingles
   des quais repérées par leur tracé rouge et le numéro de ligne qu'elles portent ;
2. rattachement : chaque pictogramme à l'étiquette d'accès la plus proche sur le plan ; la station retrouvée par son
   nom dans les gares IDFM, ses accès dans les accès IDFM (même numéro et même nom, dans un rayon de la station) ;
3. positionnement : chaque pictogramme à la position IDFM de son accès (choix de l'utilisateur) ; quand le plan peut
   être calé sur au moins deux accès retrouvés, l'écart entre l'endroit dessiné et l'accès est mesuré.

Ne jamais bloquer : tout ce qui n'est pas retrouvé est listé en anomalie, station par station.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

TYPES = {
    "ratp": "Accès le plus proche du point RATP (bureau de station)",
    "rassemblement": "Point de rassemblement police / RATP",
    "pompiers": "Accès principal pompiers",
    "quais": "Accès le plus proche des quais d'une ligne (ligne indiquée sur le PLAI)",
}
# Libellé de la légende qui désigne chaque pictogramme (le pictogramme est dessiné à sa gauche)
LEGENDE = {"ratp": "du point RATP", "rassemblement": "Point de rassemblement", "pompiers": "pompiers",
           "quais": "des quais de la ligne"}
VERSION_LECTURE = 1          # à relever si la lecture change : les plans sont relus
DPI = 96                     # pictogrammes du plan d'environ 30 px (à 72 dpi, l'accès pompiers est parfois manqué)
ECHELLES = (0.8, 0.75, 0.85, 0.9, 1.0)   # pictogrammes du plan plus petits que ceux de la légende
SEUIL_CORRELATION = 0.7
SEUIL_VOISIN = 0.55          # accepté s'il est groupé avec un pictogramme sûr (ils sont dessinés ensemble)
DISTANCE_GROUPE_PT = 60
MOTIF_VOIE = re.compile(
    r"^(?:(?P<num>\d{1,2})\s+)?(?P<nom>(?:r\.|rue|bd|boulevard|av\.|avenue|pl\.|place|sq\.|square|quai|qu\.|cours|"
    r"all\.|allée|imp\.|impasse|pass\.|passage|chée|chaussée|rte|route|parvis|esplanade|carr\.|carrefour|rd-pt|"
    r"rond-point|porte|pte|cité|villa|galerie|gal\.|centre|ctre|gare|parc|jardin|pont|port|promenade|prom\.|"
    r"sente|sentier|voie|terrasse|chemin|ch\.)\s.*)$", re.IGNORECASE)


def normaliser(texte: str) -> str:
    t = unicodedata.normalize("NFD", str(texte).lower())
    return re.sub(r"[^a-z0-9]+", " ", "".join(c for c in t if unicodedata.category(c) != "Mn")).strip()


# ---------------------------------------------------------------- lecture du plan

@dataclass
class Lecture:
    fichier: str
    station: str | None = None
    lignes: list[str] = field(default_factory=list)
    adresse: str | None = None
    acces: list[dict] = field(default_factory=list)      # {numero, nom, x, y} (points PDF, origine en haut à gauche)
    pictos: list[dict] = field(default_factory=list)     # {type, x, y, score, ligne}
    anomalies: list[str] = field(default_factory=list)
    version: int = VERSION_LECTURE


def _lignes_texte(page) -> list[dict]:
    """Mots regroupés en lignes (même hauteur et même corps), coupées aux grands espaces ; hors page ignorés."""
    mots = [m for m in page.extract_words(extra_attrs=["size"])
            if 0 <= m["x0"] <= page.width and 0 <= m["top"] <= page.height]
    groupes: dict[tuple, list] = {}
    for m in mots:
        groupes.setdefault((round(m["top"] * 2) / 2, round(m["size"], 1)), []).append(m)
    lignes = []
    for ms in groupes.values():
        ms.sort(key=lambda m: m["x0"])
        morceau = [ms[0]]
        for m in ms[1:]:
            if m["x0"] - morceau[-1]["x1"] > max(6.0, m["size"] * 1.2):
                lignes.append(morceau)
                morceau = [m]
            else:
                morceau.append(m)
        lignes.append(morceau)
    return [{"texte": " ".join(m["text"] for m in ms), "x0": ms[0]["x0"], "x1": ms[-1]["x1"],
             "top": min(m["top"] for m in ms), "bottom": max(m["bottom"] for m in ms), "size": ms[0]["size"]}
            for ms in lignes]


def _cadre_legende(lignes: list[dict]) -> tuple[dict, list[float] | None]:
    """Carrés des pictogrammes de la légende (côté 28 pt, à gauche du libellé, en haut de l'entrée) et cadre
    de la légende entière (exclu de la recherche)."""
    boites = {}
    for type_, cle in LEGENDE.items():
        l = next((l for l in lignes if cle in l["texte"]), None)
        if l is None:
            continue
        premiere = min((m["top"] for m in lignes if abs(m["x0"] - l["x0"]) < 2 and l["top"] - 14 <= m["top"] <= l["top"]),
                       default=l["top"])
        boites[type_] = ([l["x0"] - 35, premiere, l["x0"] - 7, premiere + 28], l)
    if not boites:
        return {}, None
    textes = [l for l in lignes if any(abs(l["x0"] - b[1]["x0"]) < 2 for b in boites.values())
              and min(b[0][1] for b in boites.values()) - 15 <= l["top"] <= max(b[0][3] for b in boites.values()) + 30]
    cadre = [min(b[0][0] for b in boites.values()) - 15, min(b[0][1] for b in boites.values()) - 15,
             max([l["x1"] for l in textes] + [b[0][2] for b in boites.values()]) + 10,
             max([b[0][3] for b in boites.values()] + [l["bottom"] for l in textes]) + 15]
    return {t: b[0] for t, b in boites.items()}, cadre


class _Correlateur:
    """Corrélation normalisée (moyenne retirée, trois couleurs) d'un gabarit sur l'image du plan, par FFT ;
    transformées de l'image calculées une fois par plan. Les fenêtres presque uniformes sont ignorées."""

    def __init__(self, image: np.ndarray):
        self.H, self.W, _ = image.shape
        self.fs = (self.H + 64, self.W + 64)
        self.F = [np.fft.rfft2(image[:, :, c], self.fs) for c in range(3)]
        self.F2 = [np.fft.rfft2(image[:, :, c] ** 2, self.fs) for c in range(3)]

    def carte(self, gabarit: np.ndarray) -> np.ndarray:
        h, w, _ = gabarit.shape
        H, W, fs = self.H, self.W, self.fs
        t = gabarit - gabarit.mean(axis=(0, 1))
        norme = math.sqrt(float((t ** 2).sum())) or 1e-9
        Fun = np.fft.rfft2(np.ones((h, w)), fs)
        num = np.zeros((H - h + 1, W - w + 1))
        var = np.zeros_like(num)
        for c in range(3):
            num += np.fft.irfft2(self.F[c] * np.fft.rfft2(t[::-1, ::-1, c], fs), fs)[h - 1:H, w - 1:W]
            s1 = np.fft.irfft2(self.F[c] * Fun, fs)[h - 1:H, w - 1:W]
            s2 = np.fft.irfft2(self.F2[c] * Fun, fs)[h - 1:H, w - 1:W]
            var += s2 - s1 * s1 / (h * w)
        r = num / (np.sqrt(np.maximum(var, 1e-9)) * norme)
        r[var < 0.01 * h * w] = 0
        return r


def _redimensionner(img: np.ndarray, e: float) -> np.ndarray:
    from PIL import Image
    h, w, _ = img.shape
    im = Image.fromarray((img * 255).astype(np.uint8)).resize((max(6, round(w * e)), max(6, round(h * e))), Image.LANCZOS)
    return np.asarray(im, dtype=np.float32) / 255


def _chercher(corr: _Correlateur, gabarit: np.ndarray, exclusions: list[list[float]]) -> list[tuple[float, float, float]]:
    """Occurrences (score, x, y du centre en points) du gabarit, hors zones exclues ; l'échelle la plus probable
    (0,8) d'abord, les autres seulement si rien de net n'est trouvé."""
    trouves = []
    for e in ECHELLES:
        t = _redimensionner(gabarit, e)
        r = corr.carte(t)
        h, w, _ = t.shape
        for x0, y0, x1, y1 in exclusions:
            r[max(0, int(y0) - h):max(0, int(y1)), max(0, int(x0) - w):max(0, int(x1))] = -1
        for _ in range(3):
            i = np.unravel_index(int(np.argmax(r)), r.shape)
            score = float(r[i])
            if score < SEUIL_VOISIN:
                break
            trouves.append((score, (i[1] + w / 2) * 72 / DPI, (i[0] + h / 2) * 72 / DPI))
            r[max(0, i[0] - h):i[0] + h, max(0, i[1] - w):i[1] + w] = -1
        if trouves and max(s for s, _, _ in trouves) >= 0.8:
            break
    # dédoublonnage (même pictogramme vu à plusieurs échelles), le meilleur score d'abord
    trouves.sort(reverse=True)
    garde = []
    for s, x, y in trouves:
        if all(math.hypot(x - gx, y - gy) > 12 for _, gx, gy in garde) and s >= trouves[0][0] - 0.12:
            garde.append((s, x, y))
    return garde


def _rouge(couleur) -> bool:
    """Rouge des épingles des quais (CMJN ≈ 0 / 88 / 92 / 0, ou RVB équivalent)."""
    if not isinstance(couleur, (tuple, list)):
        return False
    if len(couleur) == 4:
        c, m, j, n = couleur
        return c < 0.15 and m > 0.7 and j > 0.6 and n < 0.15
    if len(couleur) == 3:
        r, v, b = couleur
        return r > 0.8 and v < 0.3 and b < 0.35
    return False


def _dans(x, y, boite, marge=0.0) -> bool:
    return boite[0] - marge <= x <= boite[2] + marge and boite[1] - marge <= y <= boite[3] + marge


def lire(chemin: Path) -> tuple[Lecture, dict[str, bytes]]:
    """Lecture d'un PLAI (première page) ; rend aussi les pictogrammes de la légende en PNG (symboles de la carte)."""
    import pdfplumber

    lec = Lecture(fichier=chemin.name)
    icones: dict[str, bytes] = {}
    with pdfplumber.open(chemin) as pdf:
        page = pdf.pages[0]
        lignes = _lignes_texte(page)

        # En-tête : station (plus grand corps entre le bandeau et « Accès Principal »), lignes, adresse
        principal = next((l for l in lignes if l["texte"].lower().startswith("accès principal")), None)
        haut = principal["top"] if principal else page.height / 3
        candidats = [l for l in lignes if 60 < l["top"] < haut and not re.match(r"^(plan|identification)", l["texte"], re.I)]
        titre = max((l for l in candidats if re.search(r"[a-zA-Z]{2}", l["texte"])), key=lambda l: l["size"], default=None)
        if titre:
            lec.station = titre["texte"]   # lignes : pastilles dessinées (pas du texte), prises dans les gares IDFM
        else:
            lec.anomalies.append("nom de station introuvable dans l'en-tête")
        if principal:
            suite = sorted((l for l in lignes if 0 < l["top"] - principal["bottom"] < 16), key=lambda l: l["top"])
            lec.adresse = suite[0]["texte"] if suite else None

        boites, cadre = _cadre_legende(lignes)
        if not boites:
            lec.anomalies.append("légende des pictogrammes introuvable : plan non conforme au modèle PLAI")
            return lec, icones
        for type_ in TYPES:
            if type_ not in boites:
                lec.anomalies.append(f"pictogramme « {TYPES[type_]} » absent de la légende")

        # Étiquettes des accès : « [numéro] voie », numéro éventuellement dans une case à gauche
        chiffres = [l for l in lignes if re.fullmatch(r"\d{1,2}", l["texte"]) and l["size"] < 8.5]
        for l in lignes:
            if cadre and _dans(l["x0"], l["top"], cadre) or l["top"] < haut or l["size"] > 9.5:
                continue
            m = MOTIF_VOIE.match(l["texte"])
            if not m:
                continue
            numero = m.group("num")
            if numero is None:
                voisin = min((c for c in chiffres if 0 <= l["x0"] - c["x1"] <= 14
                              and abs((c["top"] + c["bottom"]) / 2 - (l["top"] + l["bottom"]) / 2) < 6),
                             key=lambda c: l["x0"] - c["x1"], default=None)
                numero = voisin["texte"] if voisin else None
            lec.acces.append({"numero": int(numero) if numero else None, "nom": m.group("nom").strip(),
                              "x": (l["x0"] + l["x1"]) / 2, "y": (l["top"] + l["bottom"]) / 2})
        if not lec.acces:
            lec.anomalies.append("aucune étiquette d'accès lue sur le plan")

        # Pictogrammes carrés : gabarits découpés dans la légende, cherchés dans le plan
        rendu = page.to_image(resolution=DPI).original.convert("RGB")
        image = np.asarray(rendu, dtype=np.float32) / 255
        grand = page.to_image(resolution=288).original.convert("RGB")
        corr = _Correlateur(image)
        k = DPI / 72
        exclusions = [[v * k for v in cadre]] if cadre else []
        exclusions.append([0, 0, image.shape[1], haut * k])   # en-tête
        candidats_types = {}
        for type_, b in boites.items():
            png = io.BytesIO()
            grand.crop(tuple(round(v * 4) for v in b)).save(png, format="PNG")
            icones[type_] = png.getvalue()
            if type_ != "quais":
                gabarit = image[int(b[1] * k):int(b[3] * k), int(b[0] * k):int(b[2] * k)]
                candidats_types[type_] = _chercher(corr, gabarit, exclusions)
        surs = [(x, y) for c in candidats_types.values() for s, x, y in c if s >= SEUIL_CORRELATION]
        for type_, trouves in candidats_types.items():
            retenus = [t for t in trouves if t[0] >= SEUIL_CORRELATION]
            if not retenus:
                # moins sûr, mais groupé avec un pictogramme reconnu : les pictogrammes sont dessinés ensemble
                retenus = [t for t in trouves[:1] if any(DISTANCE_GROUPE_PT >= math.hypot(t[1] - x, t[2] - y) > 5
                                                         for x, y in surs)]
            if not retenus:
                lec.anomalies.append(f"pictogramme « {TYPES[type_]} » non trouvé sur le plan")
            for s, x, y in retenus[:2]:
                lec.pictos.append({"type": type_, "x": x, "y": y, "score": round(s, 3), "ligne": None})

        # Épingles des quais : tracé rouge en forme de goutte, numéro de ligne à l'intérieur
        vus = []
        for o in page.curves:
            w, h = o["width"], o["height"]
            if not (14 <= w <= 34 and 18 <= h <= 40 and h > w * 1.05 and _rouge(o.get("non_stroking_color"))):
                continue
            cx, cy = (o["x0"] + o["x1"]) / 2, (o["top"] + o["bottom"]) / 2
            if not (0 <= cx <= page.width and haut <= cy <= page.height) or (cadre and _dans(cx, cy, cadre, 40)):
                continue
            if any(math.hypot(cx - vx, cy - vy) < 8 for vx, vy in vus):
                continue
            vus.append((cx, cy))
            # Le numéro de ligne de l'épingle est dessiné (tracés, pas du texte) : non lu, renvoi au plan.
            # La pointe de l'épingle désigne l'accès : bas du tracé.
            lec.pictos.append({"type": "quais", "x": cx, "y": o["bottom"], "score": None, "ligne": None})
        if "quais" in boites and not any(p["type"] == "quais" for p in lec.pictos):
            lec.anomalies.append("aucune épingle « accès le plus proche des quais » trouvée sur le plan")
    return lec, icones


# ---------------------------------------------------------------- rattachement et positionnement

def _rattacher(lec: Lecture) -> None:
    """Chaque pictogramme à l'étiquette d'accès la plus proche sur le plan (distance en points)."""
    for p in lec.pictos:
        if not lec.acces:
            p["acces"] = None
            continue
        i, a = min(enumerate(lec.acces), key=lambda ia: math.hypot(ia[1]["x"] - p["x"], ia[1]["y"] - p["y"]))
        p["acces"] = i
        p["distance_plan_pt"] = round(math.hypot(a["x"] - p["x"], a["y"] - p["y"]), 1)


def _transformation(src: list[tuple], dst: list[tuple]):
    """Plan (points PDF, y vers le bas) → Lambert 93 : affine (3 accès ou plus) ou similitude (2 accès),
    moindres carrés ; rend (fonction, résidu moyen en m) ou (None, None)."""
    if len(src) < 2:
        return None, None
    S = np.array([(x, -y) for x, y in src], dtype=float)
    D = np.array(dst, dtype=float)
    if len(src) >= 3:
        A = np.hstack([S, np.ones((len(S), 1))])
        coef, *_ = np.linalg.lstsq(A, D, rcond=None)
        f = lambda x, y: tuple(np.array([x, -y, 1.0]) @ coef)
    else:
        # similitude : z' = a z + b en nombres complexes
        zs = S[:, 0] + 1j * S[:, 1]
        zd = D[:, 0] + 1j * D[:, 1]
        a = (zd[1] - zd[0]) / (zs[1] - zs[0]) if zs[1] != zs[0] else None
        if a is None:
            return None, None
        b = zd[0] - a * zs[0]
        f = lambda x, y: ((a * (x - 1j * y) + b).real, (a * (x - 1j * y) + b).imag)
    residus = [math.hypot(*(np.array(f(x, y)) - np.array(d))) for (x, y), d in zip(src, dst)]
    return f, float(np.mean(residus))


def positionner(lec: Lecture, gares, acces_idfm, rayon_m: float) -> tuple[list[dict], list[str]]:
    """Points des pictogrammes (Lambert 93) et anomalies. `gares`, `acces_idfm` : GeoDataFrame IDFM en Lambert 93."""
    anomalies = list(lec.anomalies)
    _rattacher(lec)
    if not lec.station:
        return [], anomalies
    nom = normaliser(lec.station)
    sel = gares[gares["nom_gares"].map(normaliser) == nom]
    if sel.empty:
        proches = sorted(((SequenceMatcher(None, nom, normaliser(g)).ratio(), g) for g in set(gares["nom_gares"].dropna())), reverse=True)
        if proches and proches[0][0] >= 0.85:
            sel = gares[gares["nom_gares"] == proches[0][1]]
    if sel.empty:
        anomalies.append(f"station « {lec.station} » introuvable dans les gares IDFM")
        return [], anomalies
    centre = sel.geometry.union_all().centroid
    lec.station = str(sel["nom_gares"].iloc[0])   # graphie IDFM (le plan écrit parfois « Réaumur-sébastopol »)
    if "indice_lig" in sel:
        lec.lignes = sorted({str(v) for v in sel["indice_lig"].dropna()}, key=lambda v: (len(v), v))
    candidats = acces_idfm[acces_idfm.distance(centre) <= rayon_m]

    # Étiquettes du plan → accès IDFM : nom (approché) et numéro ; le plus proche de la station en cas d'égalité
    appariement: dict[int, tuple] = {}
    for i, a in enumerate(lec.acces):
        meilleur = None
        for _, c in candidats.iterrows():
            score = SequenceMatcher(None, normaliser(a["nom"]), normaliser(c.get("accname", ""))).ratio()
            num_c = c.get("accshortname")
            if a["numero"] is not None and num_c is not None and not (isinstance(num_c, float) and math.isnan(num_c)):
                score += 0.5 if int(float(num_c)) == a["numero"] else -0.5
            cle = (score, -c.geometry.distance(centre))
            if score >= 0.8 and (meilleur is None or cle > meilleur[0]):
                meilleur = (cle, c)
        if meilleur:
            appariement[i] = (meilleur[1].geometry.x, meilleur[1].geometry.y, meilleur[1])
    for i, a in enumerate(lec.acces):
        if i not in appariement and any(p.get("acces") == i for p in lec.pictos):
            anomalies.append(f"accès « {a['numero'] or '?'} {a['nom']} » du plan introuvable dans les accès IDFM "
                             f"(à moins de {rayon_m:g} m de la station)")

    paires = [((lec.acces[i]["x"], lec.acces[i]["y"]), (x, y)) for i, (x, y, _) in appariement.items()]
    calage, residu = _transformation([p[0] for p in paires], [p[1] for p in paires])

    points = []
    for p in lec.pictos:
        i = p.get("acces")
        a = lec.acces[i] if i is not None else {}
        dessine = calage(p["x"], p["y"]) if calage else None
        if i in appariement:
            x, y, c = appariement[i]
            methode = "position IDFM de l'accès"
        elif dessine is not None:
            (x, y), c = dessine, None
            methode = "position dessinée sur le plan (accès non retrouvé dans IDFM)"
        else:
            anomalies.append(f"« {TYPES[p['type']]} » non positionné : accès non retrouvé et plan non calable")
            continue
        ecart = round(math.hypot(dessine[0] - x, dessine[1] - y)) if (dessine is not None and c is not None) else None
        if calage is None:
            note = "écart non mesurable (moins de 2 accès du plan retrouvés dans IDFM)"
        elif ecart is None:
            note = f"placé à l'endroit dessiné (calage sur {len(paires)} accès, résidu {residu:.0f} m)"
        else:
            note = (f"dessiné à {ecart} m de l'accès sur le plan (calage sur {len(paires)} accès, "
                    f"incertitude ~{max(residu, 5):.0f} m)")
        points.append({
            "type": p["type"], "libelle_type": TYPES[p["type"]] + (f" — ligne {p['ligne']}" if p.get("ligne") else ""),
            "station": lec.station, "lignes": " ".join(lec.lignes), "adresse_acces_principal": lec.adresse,
            "acces_numero": a.get("numero"), "acces_nom": a.get("nom"),
            "acces_idfm": str(c["accid"]) if c is not None and "accid" in c else None,
            "ligne_quais": p.get("ligne"), "methode": methode, "ecart_dessin_m": ecart, "note_ecart": note,
            "score_reconnaissance": p.get("score"), "fichier_plai": lec.fichier, "x": float(x), "y": float(y),
        })
    for type_ in ("ratp", "rassemblement", "pompiers"):
        if not any(pt["type"] == type_ for pt in points) and not any(type_ in an for an in anomalies):
            anomalies.append(f"« {TYPES[type_]} » non positionné")
    return points, anomalies


# ---------------------------------------------------------------- couche de l'application (connecteur « PLAI »)

def dossier_plans(service: dict) -> Path:
    from .config import RACINE
    p = Path(service["dossier"])
    return p if p.is_absolute() else RACINE / p


def dossier_cache(cfg: dict) -> Path:
    from .esri import dossier_cache as cache_services
    return cache_services(cfg).parent / "plai"


def identifiant(service: dict) -> str:
    from . import inventaire as inv_sig
    return inv_sig.identifiant(f"plai:{service['dossier']}", None)


def _couche_idfm(cfg: dict, libelle: str):
    """Couche IDFM de référence (gares, accès), lue par son connecteur (cache) puis en Lambert 93."""
    from . import esri, objets
    trouve = next(((s, c) for s, c in esri.services_declares(cfg) if s["libelle"] == libelle), None)
    if trouve is None:
        raise esri.ErreurService(f"couche de référence « {libelle} » non déclarée")
    service, connecteur = trouve
    geo, _ = objets._lire(esri.couche_service(cfg, service, rang=0, connecteur=connecteur))
    return geo.to_crs(2154)


def _lire_un(args):
    """Lecture d'un plan dans un processus séparé (lecture parallèle des centaines de PLAI)."""
    chemin, cache = args
    try:
        return lire_avec_cache(Path(chemin), Path(cache))
    except Exception as e:   # plan illisible : anomalie, les autres continuent
        return Lecture(fichier=Path(chemin).name, anomalies=[f"plan illisible ({type(e).__name__} : {e})"]), {}


def telecharger(service: dict, cfg: dict, client=None):
    """Couche des pictogrammes de tous les PLAI du dossier (Lambert 93) ; tableau de contrôle et icônes en cache."""
    import time
    from concurrent.futures import ProcessPoolExecutor
    from datetime import datetime

    import geopandas as gpd
    from shapely.geometry import Point

    from .esri import ErreurService

    debut = time.monotonic()
    dossier = dossier_plans(service)
    plans = sorted(p for p in dossier.glob("*.pdf")) if dossier.is_dir() else []
    if not plans:
        raise ErreurService(f"aucun PLAI (PDF) dans {service['dossier']}")
    gares = _couche_idfm(cfg, service["couche_gares"])
    acces = _couche_idfm(cfg, service["couche_acces"])
    cache = dossier_cache(cfg)
    cache.mkdir(parents=True, exist_ok=True)

    taches = [(str(p), str(cache / "lectures")) for p in plans]
    if len(taches) > 4:
        with ProcessPoolExecutor(max_workers=int(service.get("processus", 4))) as ex:
            lectures = list(ex.map(_lire_un, taches))
    else:
        lectures = [_lire_un(t) for t in taches]

    points, controle = [], []
    icones_ecrites = set()
    for lec, icones in lectures:
        for type_, png in icones.items():
            if type_ not in icones_ecrites:
                (cache / f"icone_{type_}.png").write_bytes(png)
                icones_ecrites.add(type_)
        pts, anomalies = positionner(lec, gares, acces, float(service.get("rayon_m", 400)))
        points += pts
        controle.append({"fichier": lec.fichier, "station": lec.station, "lignes": " ".join(lec.lignes),
                         "acces_lus": len(lec.acces), "pictogrammes_trouves": len(lec.pictos),
                         "points_positionnes": len(pts),
                         **{f"{t}_positionne": any(p["type"] == t for p in pts) for t in TYPES},
                         "anomalies": " ; ".join(anomalies)})
    (cache / "controle.json").write_text(json.dumps(controle, ensure_ascii=False, indent=1), encoding="utf-8")

    if not points:
        raise ErreurService(f"aucun pictogramme positionné sur {len(plans)} PLAI (voir le tableau de contrôle)")
    for p in points:
        p["plai_pdf"] = f"/api/sig/plai/pdf/{p['fichier_plai']}"
    gdf = gpd.GeoDataFrame([{k: v for k, v in p.items() if k not in ("x", "y")} for p in points],
                           geometry=[Point(p["x"], p["y"]) for p in points], crs="EPSG:2154")
    en_anomalie = [c for c in controle if c["anomalies"]]
    avert = [f"{len(en_anomalie)} PLAI sur {len(controle)} avec anomalies (tableau de contrôle : /api/sig/plai/controle)"] if en_anomalie else []
    meta = {
        "url": service["dossier"], "where": f"{len(plans)} PLAI", "copyright": "RATP — documents internes (PLAI)",
        "date_maj": datetime.now().isoformat(timespec="seconds"), "nb_objets": len(gdf), "nb_objets_service": len(gdf),
        "tronque": False, "avertissements": avert, "pagination": "aucune",
        "duree_secondes": round(time.monotonic() - debut, 1),
    }
    return gdf, meta


# ---------------------------------------------------------------- cache des lectures

def lire_avec_cache(chemin: Path, dossier_cache: Path) -> tuple[Lecture, dict[str, bytes]]:
    """Un plan n'est relu que s'il change (taille, date) ou si la lecture évolue (VERSION_LECTURE)."""
    st = chemin.stat()
    cle = hashlib.sha1(f"{chemin.name}|{st.st_size}|{st.st_mtime_ns}|{VERSION_LECTURE}".encode()).hexdigest()[:16]
    fiche = dossier_cache / f"{cle}.json"
    if fiche.is_file():
        d = json.loads(fiche.read_text(encoding="utf-8"))
        icones = {t: (dossier_cache / f"{cle}_{t}.png").read_bytes() for t in d.pop("icones", [])
                  if (dossier_cache / f"{cle}_{t}.png").is_file()}
        return Lecture(**d), icones
    lec, icones = lire(chemin)
    dossier_cache.mkdir(parents=True, exist_ok=True)
    for t, png in icones.items():
        (dossier_cache / f"{cle}_{t}.png").write_bytes(png)
    fiche.write_text(json.dumps({**asdict(lec), "icones": list(icones)}, ensure_ascii=False), encoding="utf-8")
    return lec, icones

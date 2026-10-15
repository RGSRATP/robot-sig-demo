"""PLAI synthétique (PDF vectoriel écrit en Python pur) : les PLAI de la RATP ne vont jamais dans Git.

Reprend le modèle des vrais plans : en-tête (station, « Accès principal » et adresse), étiquettes d'accès
numérotées, pictogrammes carrés dessinés à 80 % de leur taille de légende, épingle rouge des quais, légende en
bas à droite (pictogramme à gauche de son libellé). Coordonnées données « depuis le haut » comme pdfplumber.
"""
from __future__ import annotations

from pathlib import Path

LARGEUR, HAUTEUR = 595, 842
ACCES = [(1, "rue Alpha", 100, 300), (2, "bd Beta", 400, 300), (3, "av. Gamma", 250, 600)]
# pictogrammes du plan (centre, depuis le haut), près de l'accès 1
PICTOS = {"ratp": (90, 330), "pompiers": (118, 330), "rassemblement": (146, 330)}
EPINGLE = (400, 285)   # pointe de l'épingle des quais, près de l'accès 2
LEGENDE = [("ratp", "Accès le plus proche du point RATP"), ("rassemblement", "Point de rassemblement police / RATP"),
           ("pompiers", "Entrée des pompiers"), ("quais", "Accès le plus proche des quais de la ligne")]
X_LEGENDE, T_LEGENDE = 400, 700


def _texte(s: str) -> str:
    b = s.encode("cp1252")
    return "".join(chr(c) if 32 <= c < 127 and chr(c) not in "()\\" else f"\\{c:03o}" for c in b)


def _ecrire(x: float, t: float, taille: float, s: str) -> str:
    """Texte dont le haut des capitales est à `t` (depuis le haut de la page)."""
    return f"BT /F1 {taille} Tf {x} {HAUTEUR - t - taille * 0.718:.2f} Td ({_texte(s)}) Tj ET\n"


def _pictogramme(type_: str, x0: float, t0: float, cote: float) -> str:
    """Carré coloré et motifs blancs propres à chaque type (dessin unitaire mis à l'échelle)."""
    fond = {"ratp": "0 0.6 0.5", "pompiers": "0.85 0.1 0.1", "rassemblement": "0.1 0.1 0.1", "quais": "1 1 1"}[type_]
    motifs = {"ratp": [(0.15, 0.55, 0.7, 0.25)],
              "pompiers": [(0.15, 0.15, 0.25, 0.7), (0.55, 0.15, 0.1, 0.3)],
              "rassemblement": [(0.1, 0.1, 0.3, 0.3), (0.6, 0.6, 0.3, 0.3), (0.6, 0.1, 0.1, 0.1)],
              "quais": []}[type_]
    ops = f"q {cote} 0 0 {cote} {x0} {HAUTEUR - t0 - cote} cm {fond} rg 0 0 1 1 re f 1 1 1 rg\n"
    ops += "".join(f"{x} {y} {w} {h} re f\n" for x, y, w, h in motifs)
    if type_ == "quais":
        ops += "Q\n" + _epingle(x0 + cote / 2, t0 + cote - 2, cote * 0.7)
        return ops
    return ops + "Q\n"


def _epingle(cx: float, pointe_t: float, hauteur: float = 28) -> str:
    """Goutte rouge (courbes de Bézier), pointe en bas."""
    y = HAUTEUR - pointe_t
    l, m = hauteur * 0.36, y + hauteur * 0.62   # demi-largeur, hauteur des flancs (pdfplumber : boîte des points)
    return (f"0.95 0.1 0.1 rg {cx} {y} m {cx - l * 0.5} {y + hauteur * 0.3} {cx - l} {m - l * 0.5} {cx - l} {m} c "
            f"{cx - l} {m + l * 0.6} {cx - l * 0.55} {y + hauteur} {cx} {y + hauteur} c "
            f"{cx + l * 0.55} {y + hauteur} {cx + l} {m + l * 0.6} {cx + l} {m} c "
            f"{cx + l} {m - l * 0.5} {cx + l * 0.5} {y + hauteur * 0.3} {cx} {y} c h f\n")


def contenu(avec_legende: bool = True, station: str = "STATION TEST") -> str:
    c = _ecrire(40, 30, 10, "PLAN LOCAL D'AIDE A L'INTERVENTION")
    c += _ecrire(40, 80, 20, station)
    c += _ecrire(40, 120, 9, "Accès principal")
    c += _ecrire(40, 132, 8, "Adresse : 12 rue de la Paix, 75002 Paris")
    for num, nom, x, t in ACCES:
        c += "0.2 0.2 0.2 rg\n" + _ecrire(x - 30, t - 3, 7, f"{num} {nom}")
    for type_, (x, t) in PICTOS.items():
        c += _pictogramme(type_, x - 11.2, t - 11.2, 22.4)
    c += _epingle(*EPINGLE)
    if avec_legende:
        for i, (type_, libelle) in enumerate(LEGENDE):
            t = T_LEGENDE + 32 * i
            c += "0 0 0 rg\n" + _ecrire(X_LEGENDE, t, 7, libelle)
            c += _pictogramme(type_, X_LEGENDE - 35, t, 28)
    return c


def ecrire_plai(chemin: Path, **options) -> Path:
    flux = contenu(**options).encode("latin-1")
    objets = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {LARGEUR} {HAUTEUR}] "
        f"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Length " + str(len(flux)).encode() + b" >>\nstream\n" + flux + b"\nendstream",
    ]
    sortie = bytearray(b"%PDF-1.4\n")
    positions = []
    for i, o in enumerate(objets, 1):
        positions.append(len(sortie))
        sortie += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(sortie)
    sortie += f"xref\n0 {len(objets) + 1}\n0000000000 65535 f \n".encode()
    sortie += b"".join(f"{p:010d} 00000 n \n".encode() for p in positions)
    sortie += f"trailer\n<< /Size {len(objets) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    chemin.write_bytes(bytes(sortie))
    return chemin

"""Génère le jeu de test des couches SIG (tests/donnees/jeu/) à partir de données ouvertes IDFM et SNCF.

Usage : .venv\Scripts\python tests\donnees\generer.py
Les téléchargements bruts sont gardés dans tests/donnees/sources/ (hors Git) ; le jeu produit est versionné,
pour que les tests tournent sans réseau. Sources et licences : tests/donnees/LISEZMOI.md.
"""
from __future__ import annotations

import gzip
import shutil
import sys
import urllib.request
from pathlib import Path

import geopandas as gpd
import pandas as pd

ICI = Path(__file__).resolve().parent
SOURCES = ICI / "sources"
JEU = ICI / "jeu"
PROXY = "http://127.0.0.1:9000"

IDFM = "https://data.iledefrance-mobilites.fr/api/explore/v2.1/catalog/datasets"
SNCF = "https://ressources.data.sncf.com/api/explore/v2.1/catalog/datasets"
TELECHARGEMENTS = {
    "idfm_gares.geojson": f"{IDFM}/emplacement-des-gares-idf/exports/geojson",
    "idfm_traces.geojson": f"{IDFM}/traces-du-reseau-ferre-idf/exports/geojson",
    "sncf_gares.csv": f"{SNCF}/liste-des-gares/exports/csv?delimiter=%3B",
}
DEPARTEMENTS_IDF = {"PARIS", "SEINE-ET-MARNE", "YVELINES", "ESSONNE", "HAUTS-DE-SEINE",
                    "SEINE-SAINT-DENIS", "VAL-DE-MARNE", "VAL-D'OISE"}


def telecharger() -> None:
    SOURCES.mkdir(exist_ok=True)
    ouvreur = urllib.request.build_opener(urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))
    for nom, url in TELECHARGEMENTS.items():
        cible = SOURCES / nom
        if cible.exists():
            continue
        print(f"Téléchargement {nom}…")
        with ouvreur.open(url, timeout=300) as r:
            contenu = r.read()
        if contenu[:2] == bytes([0x1F, 0x8B]):  # le portail IDFM répond en gzip même sans le demander
            contenu = gzip.decompress(contenu)
        cible.write_bytes(contenu)


def generer() -> None:
    if JEU.exists():
        shutil.rmtree(JEU)
    JEU.mkdir()

    gares = gpd.read_file(SOURCES / "idfm_gares.geojson")
    gares = gares[["id_gares", "nom_gares", "res_com", "mode", "exploitant", "geometry"]]
    traces = gpd.read_file(SOURCES / "idfm_traces.geojson")
    traces = traces[[c for c in ("idrefligc", "res_com", "mode", "exploitant") if c in traces.columns] + ["geometry"]]
    metro = traces[traces["mode"] == "METRO"]

    # --- Couches valides, une par format ---
    gares_l93 = gares.to_crs(2154)
    gares_l93.to_file(JEU / "idfm_reseau.gpkg", layer="gares", driver="GPKG")
    traces.to_crs(2154).to_file(JEU / "idfm_reseau.gpkg", layer="traces", driver="GPKG")
    metro.to_crs(2154).to_file(JEU / "idfm_metro_l93.shp", encoding="UTF-8")
    gares[gares["mode"] == "RER"].to_file(JEU / "idfm_gares_rer.geojson", driver="GeoJSON")  # WGS84 (RFC 7946)

    sncf = pd.read_csv(SOURCES / "sncf_gares.csv", sep=";", dtype=str)
    sncf = sncf[["code_uic", "libelle", "commune", "departemen", "voyageurs", "fret", "x_l93", "y_l93", "x_wgs84", "y_wgs84"]]
    sncf[sncf["departemen"].isin(DEPARTEMENTS_IDF)].to_csv(JEU / "sncf_gares_idf.csv", sep=";", index=False)
    sncf.to_csv(JEU / "sncf_gares_france.csv", sep=";", index=False)  # grosse couche (filtre d'emprise)

    # --- Couches signalées : système autre que Lambert 93 (affichée avec avertissement) ---
    gares_l93.to_crs(3949).to_file(JEU / "autre_systeme_cc49.gpkg", layer="gares_cc49", driver="GPKG")

    # --- Jeu piégé ---
    echant = gares_l93.head(50)
    echant.to_file(JEU / "piege_sans_prj.shp", encoding="UTF-8")
    (JEU / "piege_sans_prj.prj").unlink()
    echant.to_file(JEU / "piege_sans_shx.shp", encoding="UTF-8")
    (JEU / "piege_sans_shx.shx").unlink()
    contenu = (JEU / "idfm_reseau.gpkg").read_bytes()
    (JEU / "piege_corrompu.gpkg").write_bytes(contenu[:4096])
    (JEU / "piege_vide.geojson").write_bytes(b"")
    (JEU / "piege_texte.geojson").write_text("ceci n'est pas du GeoJSON", encoding="utf-8")
    sncf.head(20).to_csv(JEU / "piege_csv_non_declare.csv", sep=";", index=False)
    invalide = sncf.head(20).copy()
    invalide.loc[invalide.index[:5], "x_l93"] = "inconnu"
    invalide.to_csv(JEU / "piege_csv_xy_invalides.csv", sep=";", index=False)
    sans_colonnes = sncf.head(20).drop(columns=["x_l93", "y_l93"])
    sans_colonnes.to_csv(JEU / "piege_csv_sans_colonnes.csv", sep=";", index=False)
    # Coordonnées Lambert 93 déclarées en WGS84 : incohérence à détecter (cas réel rencontré sur le poste).
    faux = echant.set_crs(4326, allow_override=True)
    faux.to_file(JEU / "piege_systeme_incoherent.gpkg", layer="gares", driver="GPKG")
    # Même fichier, mais le vrai système est déclaré dans la config de test : doit s'afficher.
    faux.to_file(JEU / "systeme_declare_config.gpkg", layer="gares", driver="GPKG")
    # Fichiers non géographiques : ignorés et listés.
    (JEU / "notes.txt").write_text("fichier non géographique", encoding="utf-8")

    taille = sum(p.stat().st_size for p in JEU.iterdir()) / 1e6
    print(f"Jeu généré dans {JEU} ({len(list(JEU.iterdir()))} fichiers, {taille:.1f} Mo)")


if __name__ == "__main__":
    telecharger()
    generer()
    sys.exit(0)

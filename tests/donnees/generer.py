r"""Génère le jeu de test des couches SIG (tests/donnees/jeu/) à partir de données ouvertes IDFM et SNCF.

Usage : .venv\Scripts\python tests\donnees\generer.py
Les téléchargements bruts sont gardés dans tests/donnees/sources/ (hors Git) ; le jeu produit est versionné,
pour que les tests tournent sans réseau. Sources et licences : tests/donnees/LISEZMOI.md.
"""
from __future__ import annotations

import gzip
import json
import shutil
import sys
import urllib.request
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely import MultiPolygon
from shapely.affinity import translate

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


def _esri_json(gdf: gpd.GeoDataFrame, epsg: int, tronque: bool) -> str:
    """Export ArcGIS « query » (f=json) de polylignes, mis en page comme les exports InfraGéo."""
    objets = [{"attributes": {"objectid": i + 1, "nom": nom},
               "geometry": {"paths": [[list(xy) for xy in g.coords]]}}
              for i, (nom, g) in enumerate(zip(gdf["nom"], gdf.geometry))]
    export = {"displayFieldName": "nom", "geometryType": "esriGeometryPolyline",
              "spatialReference": {"wkid": epsg, "latestWkid": epsg},
              "fields": [{"name": "objectid", "type": "esriFieldTypeOID", "alias": "objectid"},
                         {"name": "nom", "type": "esriFieldTypeString", "alias": "nom", "length": 40}],
              "features": objets}
    if tronque:
        export["exceededTransferLimit"] = True
    return json.dumps(export, indent=1)


def exports_infrageo(gares_l93: gpd.GeoDataFrame) -> None:
    """Cas rencontrés sur les exports InfraGéo (donnees/sig/Infragéo/CANA, 2026-10-08)."""
    # Polygon et MultiPolygon mêlés : GDAL déclare un type « Unknown ».
    zones = gares_l93.head(20)[["nom_gares", "geometry"]].copy()
    zones["geometry"] = [g.buffer(80) if i % 3 else MultiPolygon([g.buffer(60), translate(g.buffer(60), 400, 0)])
                         for i, g in enumerate(zones.geometry)]
    zones.to_crs(4326).to_file(JEU / "infrageo_polygones_mixtes.geojson", driver="GeoJSON")
    # Copie identique sous un autre nom.
    shutil.copyfile(JEU / "infrageo_polygones_mixtes.geojson", JEU / "infrageo_copie.json")
    # Exports ArcGIS (Esri JSON) en CC49 : complet, arrêté à la limite de transfert, coupé en cours d'écriture.
    lignes = gares_l93.head(12).to_crs(3949)
    lignes = gpd.GeoDataFrame({"nom": lignes["nom_gares"].tolist()},
                              geometry=[g.buffer(50).exterior for g in lignes.geometry], crs=3949)
    (JEU / "infrageo_esri.json").write_text(_esri_json(lignes, 3949, False), encoding="utf-8")
    # Même couche exportée aussi en GeoJSON (WGS84) : même nom de fichier, autre extension.
    lignes.to_crs(4326).to_file(JEU / "infrageo_esri.geojson", driver="GeoJSON")
    (JEU / "infrageo_esri_tronque.json").write_text(_esri_json(lignes, 3949, True), encoding="utf-8")
    complet = _esri_json(lignes, 3949, False)
    (JEU / "piege_json_coupe.json").write_text(complet[: len(complet) // 2], encoding="utf-8")


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

    exports_infrageo(gares_l93)

    taille = sum(p.stat().st_size for p in JEU.iterdir()) / 1e6
    print(f"Jeu généré dans {JEU} ({len(list(JEU.iterdir()))} fichiers, {taille:.1f} Mo)")


if __name__ == "__main__":
    telecharger()
    generer()
    sys.exit(0)

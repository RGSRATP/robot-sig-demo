# Jeu de test des couches SIG

`jeu/` est produit par `generer.py` à partir de données ouvertes, puis versionné pour que les tests tournent sans réseau.
Pour le régénérer : `.venv\Scripts\python tests\donnees\generer.py` (téléchargements via le proxy, gardés dans `sources/`, hors Git).
`sig_test.yaml` est la configuration utilisée par les tests (même structure que `config/sig.yaml`).

## Sources

| Fichier source | Producteur | Licence |
|---|---|---|
| Gares et stations du réseau ferré d'Île-de-France (`emplacement-des-gares-idf`) | Île-de-France Mobilités | Licence Ouverte v2.0 (Etalab) |
| Tracés du réseau de transport ferré d'Île-de-France (`traces-du-reseau-ferre-idf`) | Île-de-France Mobilités | Licence Ouverte v2.0 (Etalab) |
| Liste des gares (`liste-des-gares`) | SNCF | Open Database License (ODbL) |

## Contenu

| Fichier | Rôle |
|---|---|
| `idfm_reseau.gpkg` (couches `gares`, `traces`) | GeoPackage multi-couches, Lambert 93 |
| `idfm_metro_l93.shp` | Shapefile complet (lignes de métro), Lambert 93 |
| `idfm_gares_rer.geojson` | GeoJSON, WGS84 (avertissement attendu) |
| `sncf_gares_idf.csv` | CSV X/Y Lambert 93 déclaré dans la config ; contient aussi le WGS84 de la SNCF (contrôle de conversion) |
| `sncf_gares_france.csv` | Grosse couche (6 469 objets) pour le filtre d'emprise (seuil de test : 1 000) |
| `autre_systeme_cc49.gpkg` | Système CC49 (EPSG:3949) : converti, avec avertissement |
| `systeme_declare_config.gpkg` | Système faux dans le fichier, corrigé par la clé `systeme` de la config |
| `piege_*` | Jeu piégé : corrompu, vide, faux GeoJSON, `.prj` ou `.shx` manquant, CSV non déclaré / sans colonnes / coordonnées invalides, système incohérent |
| `notes.txt` | Fichier non géographique, listé comme ignoré |

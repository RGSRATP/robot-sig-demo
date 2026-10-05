# Données (lecture seule, exclues de Git)

- `sig/` : couches SIG en GeoPackage, Shapefile (avec .shp, .shx, .dbf, .prj), GeoJSON ou CSV. Au moins un fichier de chaque format utilisé, en Lambert 93 de préférence.
- `ifc/` : deux maquettes de test (ex. une avec les locaux, une avec le génie civil). Idéalement l'une géoréférencée, l'autre non, pour tester les deux cas.
- `esri/` : exports de couches InfraGéo, si les services ne sont pas joignables depuis le poste.

Vérifie la classification des fichiers avant de les utiliser avec Claude Code.

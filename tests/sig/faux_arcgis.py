"""Faux serveur ArcGIS REST (httpx.MockTransport) : métadonnées, comptage, pagination par décalage
ou par identifiants, erreurs et panne. Aucun accès réseau pendant les tests."""
from __future__ import annotations

from urllib.parse import parse_qs

import httpx

RACINE = "https://faux.arcgis.test/arcgis/rest/services"
L93 = {"wkid": 102110, "latestWkid": 2154}

# Points en Lambert 93 (gares de Massy, Orsay, Palaiseau, Versailles, Saclay)
GARES = [
    {"attributes": {"OBJECTID": i + 1, "FID": 100 + i, "nom": nom, "mise_en_service": ms},
     "geometry": {"x": x, "y": y}}
    for i, (nom, x, y, ms) in enumerate([
        ("Massy-Palaiseau", 646310.0, 6852080.0, 1_356_998_400_000),
        ("Orsay-Ville", 641200.0, 6849370.0, None),
        ("Palaiseau", 643480.0, 6850970.0, 0),
        ("Versailles-Chantiers", 635250.0, 6856240.0, 946_684_800_000),
        ("Saclay", 639000.0, 6851500.0, None),
    ])
]

# Polygones (sens Esri : extérieur horaire, trou antihoraire) : un polygone troué, un multipolygone, un vide
_carre = lambda x, y, c: [[x, y], [x, y + c], [x + c, y + c], [x + c, y], [x, y]]       # horaire
_trou = lambda x, y, c: [[x, y], [x + c, y], [x + c, y + c], [x, y + c], [x, y]]        # antihoraire
COMMUNES = [
    {"attributes": {"OBJECTID": 1, "nom": "Commune trouée"},
     "geometry": {"rings": [_carre(640000, 6850000, 1000), _trou(640200, 6850200, 200)]}},
    {"attributes": {"OBJECTID": 2, "nom": "Commune en deux parties"},
     "geometry": {"rings": [_carre(642000, 6850000, 500), _carre(643000, 6850000, 500)]}},
    {"attributes": {"OBJECTID": 3, "nom": "Commune sans géométrie"}, "geometry": None},
]

COUCHES = {
    "Gares/FeatureServer/0": {
        "meta": {"name": "gares", "type": "Feature Layer", "geometryType": "esriGeometryPoint",
                 "objectIdField": "OBJECTID", "maxRecordCount": 2, "capabilities": "Query",
                 "advancedQueryCapabilities": {"supportsPagination": True},
                 "extent": {"spatialReference": L93},
                 "fields": [{"name": "OBJECTID", "type": "esriFieldTypeOID"},
                            {"name": "FID", "type": "esriFieldTypeInteger"},
                            {"name": "nom", "type": "esriFieldTypeString"},
                            {"name": "mise_en_service", "type": "esriFieldTypeDate"}]},
        "features": GARES,
    },
    "Communes/MapServer/1": {
        "meta": {"name": "communes", "type": "Feature Layer", "geometryType": "esriGeometryPolygon",
                 "maxRecordCount": 2, "capabilities": "Map,Query,Data",
                 "extent": {"spatialReference": {"wkid": 2154}},
                 "fields": [{"name": "OBJECTID", "type": "esriFieldTypeOID"},
                            {"name": "nom", "type": "esriFieldTypeString"}]},
        "features": COMMUNES,
    },
    "Groupe/MapServer/0": {"meta": {"name": "groupe", "type": "Group Layer"}, "features": []},
    "SansSysteme/FeatureServer/0": {
        "meta": {"name": "sans", "type": "Feature Layer", "geometryType": "esriGeometryPoint",
                 "objectIdField": "OBJECTID", "maxRecordCount": 10, "capabilities": "Query"},
        "features": GARES[:1],
    },
}


class FauxArcGIS:
    """Serveur simulé. `panne` : chemins (ou "*") qui lèvent une erreur de connexion ;
    `jeton` : jeton exigé ; `reponse_html` : chemins qui répondent une page HTML."""

    def __init__(self, panne=(), jeton=None, reponse_html=()):
        self.panne, self.jeton, self.reponse_html = set(panne), jeton, set(reponse_html)
        self.requetes: list[httpx.Request] = []

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._repondre)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=self.transport)

    def nb_requetes(self, debut_chemin: str = "") -> int:
        return sum(1 for r in self.requetes if r.url.path.split("/services/")[-1].startswith(debut_chemin))

    def _repondre(self, requete: httpx.Request) -> httpx.Response:
        self.requetes.append(requete)
        chemin = requete.url.path.split("/arcgis/rest/services/")[-1].rstrip("/")
        couche, _, action = chemin.partition("/query") if chemin.endswith("/query") else (chemin, "", "")
        est_query = chemin.endswith("/query")
        if "*" in self.panne or any(couche.startswith(p) for p in self.panne):
            raise httpx.ConnectError("connexion refusée", request=requete)
        if couche in self.reponse_html:
            return httpx.Response(200, text="<html>Portail : identifiez-vous</html>")
        p = {k: v[0] for k, v in parse_qs(requete.content.decode()).items()}
        if self.jeton and p.get("token") != self.jeton:
            return httpx.Response(200, json={"error": {"code": 499, "message": "Token Required", "details": []}})
        if couche not in COUCHES:
            return httpx.Response(200, json={"error": {"code": 400, "message": "Invalid URL", "details": []}})
        d = COUCHES[couche]
        if not est_query:
            return httpx.Response(200, json=d["meta"])
        feats = d["features"]
        if p.get("returnCountOnly") == "true":
            return httpx.Response(200, json={"count": len(feats)})
        if p.get("returnIdsOnly") == "true":
            return httpx.Response(200, json={"objectIdFieldName": "OBJECTID",
                                             "objectIds": [f["attributes"]["OBJECTID"] for f in feats]})
        maxi = d["meta"]["maxRecordCount"]
        if "objectIds" in p:
            ids = {int(v) for v in p["objectIds"].split(",")}
            lot, depasse = [f for f in feats if f["attributes"]["OBJECTID"] in ids], False
        elif "resultOffset" in p:
            debut = int(p["resultOffset"])
            n = min(int(p.get("resultRecordCount") or maxi), maxi)
            lot, depasse = feats[debut:debut + n], debut + n < len(feats)
        else:
            lot, depasse = feats[:maxi], len(feats) > maxi
        sr = d["meta"].get("extent", {}).get("spatialReference")
        corps = {"features": lot, "fields": d["meta"].get("fields", []), "exceededTransferLimit": depasse}
        if sr:
            corps["spatialReference"] = sr
        return httpx.Response(200, json=corps)

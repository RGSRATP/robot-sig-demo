"""Faux serveur WFS 2.0 et GeoJSON par URL (httpx.MockTransport) : pagination COUNT / STARTINDEX, emprise,
exception OGC, page HTML, export volumineux, panne. Aucun accès réseau pendant les tests."""
from __future__ import annotations

import gzip
import json

import httpx

WFS = "https://faux.wfs.test/wfs/ows"
GEOJSON = "https://faux.ods.test/api/explore/v2.1/catalog/datasets/gares/exports/geojson"
GEOJSON_L93 = "https://faux.ods.test/l93.geojson"
GEOJSON_HTML = "https://faux.ods.test/portail"
GEOJSON_GROS = "https://faux.ods.test/gros.geojson"
GEOJSON_GZIP = "https://faux.ods.test/compresse.geojson"

# Zonages en Lambert 93 autour de Massy (5 objets) : pagination par 2
ZONES = [
    {"type": "Feature", "id": f"zone_urba.{i}",
     "geometry": {"type": "Polygon", "coordinates": [[[x, y], [x + 100, y], [x + 100, y + 100], [x, y + 100], [x, y]]]},
     "properties": {"libelle": f"U{i}", "typezone": "U", "fid": i}}
    for i, (x, y) in enumerate([(646000, 6849000), (646200, 6849000), (646400, 6849000), (646600, 6849000), (646800, 6849000)])
]

# Gares en WGS84 : deux en Essonne, une à Paris, une sans géométrie, une avec un attribut imbriqué
GARES = [
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [2.2611, 48.7247]},
     "properties": {"nom": "Massy-Palaiseau", "lignes": ["B", "C"]}},
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [2.2134, 48.7158]},
     "properties": {"nom": "Palaiseau", "lignes": ["B"]}},
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [2.3470, 48.8620]},
     "properties": {"nom": "Châtelet-Les Halles", "adresse": {"rue": "Rambuteau", "cp": "75001"}}},
    {"type": "Feature", "geometry": None, "properties": {"nom": "Gare sans position"}},
]


class FauxFlux:
    def __init__(self, panne=False):
        self.panne = panne
        self.requetes: list[httpx.Request] = []

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self._repondre))

    def _repondre(self, requete: httpx.Request) -> httpx.Response:
        self.requetes.append(requete)
        if self.panne:
            raise httpx.ConnectError("connexion refusée", request=requete)
        url = str(requete.url)
        if url.startswith(WFS):
            return self._wfs(requete)
        if url == GEOJSON:
            return httpx.Response(200, json={"type": "FeatureCollection", "features": GARES})
        if url == GEOJSON_L93:
            return httpx.Response(200, json={"type": "FeatureCollection", "features": ZONES[:2]})
        if url == GEOJSON_HTML:
            return httpx.Response(200, text="<html>Portail : identifiez-vous</html>")
        if url == GEOJSON_GZIP:   # comme les API Explore : export compressé
            corps = gzip.compress(json.dumps({"type": "FeatureCollection", "features": GARES}).encode())
            return httpx.Response(200, headers={"content-encoding": "gzip", "content-type": "application/geo+json"}, content=corps)
        if url == GEOJSON_GROS:
            return httpx.Response(200, content=json.dumps({"type": "FeatureCollection", "features": ZONES * 2000}).encode())
        return httpx.Response(404, text="introuvable")

    def _wfs(self, requete: httpx.Request) -> httpx.Response:
        p = dict(requete.url.params)
        if p.get("TYPENAMES") != "wfs_du:zone_urba":
            return httpx.Response(400, headers={"content-type": "application/xml"}, text=(
                '<ows:ExceptionReport><ows:Exception exceptionCode="InvalidParameterValue">'
                f"<ows:ExceptionText>Feature type {p.get('TYPENAMES')} unknown</ows:ExceptionText>"
                "</ows:Exception></ows:ExceptionReport>"))
        debut, nb = int(p.get("STARTINDEX", 0)), int(p.get("COUNT", 1000))
        return httpx.Response(200, json={
            "type": "FeatureCollection", "numberMatched": len(ZONES), "numberReturned": len(ZONES[debut:debut + nb]),
            "features": ZONES[debut:debut + nb],
            "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::2154"}}})

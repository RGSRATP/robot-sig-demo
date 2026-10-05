"""Fabrique de maquettes IFC de test, une par méthode de positionnement (livrée avec le module).

Chaque maquette contient un site, un bâtiment, un étage et deux murs de 10 m × 0,2 m × 3 m avec un Pset
« Données d'identification ». Coordonnées : point de test au nord de Massy (Lambert 93 646 310 / 6 852 080),
converties avec pyproj en WGS84 et en CC49.
"""
from __future__ import annotations

from pathlib import Path

import ifcopenshell
import ifcopenshell.api as api
import ifcopenshell.api.owner.settings
from pyproj import Transformer

L93 = (646310.0, 6852080.0)
LON, LAT = Transformer.from_crs(2154, 4326, always_xy=True).transform(*L93)
CC49 = Transformer.from_crs(2154, 3949, always_xy=True).transform(*L93)


def dms(v: float) -> tuple[int, int, int, int]:
    signe = -1 if v < 0 else 1
    v = abs(v)
    d = int(v)
    m = int((v - d) * 60)
    s = (v - d - m / 60) * 3600
    return (signe * d, signe * m, signe * int(s), signe * int(round((s - int(s)) * 1e6)))


def maquette(schema: str, nom: str, origine_site=(0.0, 0.0, 0.0), latlon=None, elevation=None):
    f = ifcopenshell.file(schema=schema)
    if schema == "IFC2X3":   # l'historique de propriétaire est obligatoire en IFC2X3
        personne = api.run("owner.add_person", f)
        organisation = api.run("owner.add_organisation", f)
        utilisateur = api.run("owner.add_person_and_organisation", f, person=personne, organisation=organisation)
        application = api.run("owner.add_application", f)
        ifcopenshell.api.owner.settings.get_user = lambda _f: utilisateur
        ifcopenshell.api.owner.settings.get_application = lambda _f: application
    projet = api.run("root.create_entity", f, ifc_class="IfcProject", name=nom)
    metre = api.run("unit.add_si_unit", f, unit_type="LENGTHUNIT")   # mètres, comme les maquettes réelles
    api.run("unit.assign_unit", f, units=[metre])
    contexte = api.run("context.add_context", f, context_type="Model")
    corps = api.run("context.add_context", f, context_type="Model", context_identifier="Body",
                    target_view="MODEL_VIEW", parent=contexte)
    site = api.run("root.create_entity", f, ifc_class="IfcSite", name="Site")
    batiment = api.run("root.create_entity", f, ifc_class="IfcBuilding", name="Bâtiment")
    etage = api.run("root.create_entity", f, ifc_class="IfcBuildingStorey", name="N00")
    api.run("aggregate.assign_object", f, products=[site], relating_object=projet)
    api.run("aggregate.assign_object", f, products=[batiment], relating_object=site)
    api.run("aggregate.assign_object", f, products=[etage], relating_object=batiment)
    m = __import__("numpy").eye(4)
    m[:3, 3] = origine_site
    api.run("geometry.edit_object_placement", f, product=site, matrix=m, is_si=True)
    for p in (batiment, etage):
        api.run("geometry.edit_object_placement", f, product=p, matrix=m, is_si=True)
    if latlon:
        site.RefLatitude, site.RefLongitude = dms(latlon[0]), dms(latlon[1])
    if elevation is not None:
        site.RefElevation = elevation
    for i, (x, y, rot) in enumerate([(0.0, 0.0, 0.0), (0.0, 0.0, 90.0)]):
        mur = api.run("root.create_entity", f, ifc_class="IfcWall", name=f"Mur {i + 1}")
        api.run("spatial.assign_container", f, products=[mur], relating_structure=etage)
        mm = __import__("numpy").eye(4)
        import math
        a = math.radians(rot)
        mm[:2, :2] = [[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]]
        mm[:3, 3] = (origine_site[0] + x, origine_site[1] + y, origine_site[2])
        api.run("geometry.edit_object_placement", f, product=mur, matrix=mm, is_si=True)
        rep = api.run("geometry.add_wall_representation", f, context=corps, length=10.0, height=3.0, thickness=0.2)
        api.run("geometry.assign_representation", f, product=mur, representation=rep)
        pset = api.run("pset.add_pset", f, product=mur, name="Données d'identification")
        api.run("pset.edit_pset", f, pset=pset, properties={"SGP_DI_Sous_Ouvrage": f"SO-{i + 1:02d}"})
    return f, site, contexte


def ecrire_jeu(DOSSIER: Path) -> dict:
    """Écrit le jeu de maquettes dans DOSSIER ; renvoie les coordonnées de référence."""
    DOSSIER = Path(DOSSIER)
    DOSSIER.mkdir(parents=True, exist_ok=True)

    # 1. IFC4 : IfcMapConversion + IfcProjectedCRS (Lambert 93), coordonnées du projet locales
    f, _, _ = maquette("IFC4", "Map conversion")
    api.run("georeference.add_georeferencing", f)
    api.run("georeference.edit_georeferencing", f,
            projected_crs={"Name": "EPSG:2154"},
            coordinate_operation={"Eastings": L93[0], "Northings": L93[1], "OrthogonalHeight": 80.0,
                                  "XAxisAbscissa": 1.0, "XAxisOrdinate": 0.0, "Scale": 1.0})
    f.write(str(DOSSIER / "methode1_map_conversion_ifc4.ifc"))

    # 2a. IFC2X3 : coordonnées CC49 dans le placement du site, RefLatitude/RefLongitude cohérentes
    f, _, _ = maquette("IFC2X3", "Site projeté identifié", origine_site=(CC49[0], CC49[1], 80.0), latlon=(LAT, LON))
    f.write(str(DOSSIER / "methode2a_site_cc49_identifie.ifc"))

    # 2a bis. Même chose, mais RefLatitude/RefLongitude par défaut de Revit (Boston) : système à déclarer
    f, _, _ = maquette("IFC2X3", "Site projeté Boston", origine_site=(CC49[0], CC49[1], 80.0),
                       latlon=(42.4146, -71.2582))
    f.write(str(DOSSIER / "methode2a_site_cc49_boston.ifc"))

    # 2b. IFC2X3 : coordonnées locales, RefLatitude/RefLongitude seules
    f, _, _ = maquette("IFC2X3", "Latitude longitude", latlon=(LAT, LON), elevation=80.0)
    f.write(str(DOSSIER / "methode2b_latlon.ifc"))

    # 3. Sans géoréférencement (saisie manuelle dans la configuration de test)
    f, _, _ = maquette("IFC2X3", "Manuelle")
    f.write(str(DOSSIER / "methode3_manuelle.ifc"))

    # Sans géoréférencement ni saisie : « non positionnée »
    f, _, _ = maquette("IFC2X3", "Sans géoréférencement")
    f.write(str(DOSSIER / "sans_georeferencement.ifc"))

    # Piège : fichier qui n'est pas un IFC
    (DOSSIER / "piege_pas_un_ifc.ifc").write_text("ceci n'est pas une maquette\n", encoding="utf-8")
    return {"l93": L93, "wgs84": (LON, LAT), "cc49": CC49}



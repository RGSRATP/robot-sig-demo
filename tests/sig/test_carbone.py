"""Carbone d'une maquette : volume (un seul champ par objet, par priorité), matériau déclaré / déduit du nom /
supposé d'après le type IFC, volumes douteux écartés, configuration contrôlée."""
import json
import sqlite3

import pytest

from serveur.sig import carbone as K
from serveur.sig import maquettes as maq

CFG = {
    "champs_volume": ["NetVolume", "Volume", "GrossVolume"],
    "champs_materiau": ["SGP_DI_Materiau", "Matériau structurel"],
    "volume_max_objet_m3": 1000,
    "materiaux": [{"nom": "Béton armé", "facteur_kgco2e_m3": 350, "motifs": ["b[ée]ton", "concrete", r"\bc\d\d/\d\d\b"]},
                  {"nom": "Acier", "facteur_kgco2e_m3": 14000, "motifs": ["acier", "steel"]},
                  {"nom": "Maçonnerie", "facteur_kgco2e_m3": 300, "motifs": ["parpaing"]}],
    "par_type_ifc": {"IfcSlab": "Béton armé", "IfcMember": "Acier"},
}


def index(tmp_path, objets):
    p = tmp_path / "proprietes.sqlite"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE objets (guid TEXT PRIMARY KEY, type_ifc TEXT, nom TEXT, fiche TEXT)")
    for i, (t, nom, psets) in enumerate(objets):
        c.execute("INSERT INTO objets VALUES (?, ?, ?, ?)", (f"g{i}", t, nom, json.dumps({"type_ifc": t, "nom": nom, "psets": psets})))
    c.commit()
    c.close()
    return p


def test_bilan(tmp_path):
    p = index(tmp_path, [
        # volume déclaré deux fois (NetVolume prioritaire), matériau déclaré
        ("IfcWall", "Voile", {"BaseQuantities": {"NetVolume": 10.0, "GrossVolume": 12.0}, "Données": {"SGP_DI_Materiau": "C30/37"}}),
        ("IfcWall", "Mur:Parpaing_200", {"Cotes": {"Volume": 4.0}}),                 # matériau déduit du nom
        ("IfcSlab", "Dalle", {"Cotes": {"Volume": 20.0}}),                           # supposé : béton (type IFC)
        ("IfcMember", "Profil", {"Cotes": {"Volume": 0.5}, "M": {"Matériau structurel": "Steel S355"}}),
        ("IfcBuildingElementProxy", "", {"Cotes": {"Volume": 3.0}}),                 # non classé
        ("IfcDoor", "Porte", {}),                                                    # sans volume
        ("IfcSlab", "Radier", {"Cotes": {"Volume": 5000.0}}),                        # douteux (> 1 000 m³)
        ("IfcBeam", "Poutre", {"Cotes": {"Volume": 0}}),                             # volume nul : sans volume
    ])
    b = K.bilan(p, CFG)
    m = {x["materiau"]: x for x in b["materiaux"]}
    assert m["Béton armé"]["volume_m3"] == 30.0 and m["Béton armé"]["volume_déclaré"] == 10.0
    assert m["Béton armé"]["volume_supposé (type IFC)"] == 20.0 and m["Béton armé"]["tco2e"] == 10.5
    assert m["Maçonnerie"]["volume_déduit du nom"] == 4.0
    assert m["Acier"]["tco2e"] == 7.0
    assert b["nb_sans_volume"] == 2 and b["nb_volume_douteux"] == 1 and b["non_classes"] == {"IfcBuildingElementProxy": 1}
    assert b["total_tco2e"] == pytest.approx(10.5 + 1.2 + 7.0)
    assert b["materiaux"][0]["materiau"] == "Béton armé"   # tri par émissions décroissantes


def test_config_du_depot_valide():
    c = maq.charger_config()["carbone"]
    assert {m["nom"] for m in c["materiaux"]} >= {"Béton armé", "Acier"}


@pytest.mark.parametrize("modif, attendu", [
    (lambda c: c["materiaux"][0].update(motifs=["(non fermé"]), "motif invalide"),
    (lambda c: c["par_type_ifc"].update(IfcWall="Pierre"), "inconnu"),
    (lambda c: c.pop("champs_volume"), "champs_volume"),
])
def test_config_carbone_invalide(tmp_path, modif, attendu):
    import yaml
    cfg = yaml.safe_load(maq.CHEMIN_GEOREF.read_text(encoding="utf-8"))
    modif(cfg["carbone"])
    p = tmp_path / "g.yaml"
    p.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    with pytest.raises(maq.ConfigMaquettesInvalide, match=attendu):
        maq.charger_config(p)

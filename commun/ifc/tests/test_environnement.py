"""Le module IFC dispose d'IfcOpenShell et peut produire un fichier IFC en mémoire."""
import ifcopenshell

import commun.ifc as module_ifc


def test_ifcopenshell_installe():
    assert module_ifc.version_ifcopenshell() is not None


def test_ifcopenshell_cree_un_fichier_ifc4():
    f = ifcopenshell.file(schema="IFC4")
    f.createIfcProject(ifcopenshell.guid.new(), Name="Essai")
    assert f.schema == "IFC4"
    assert len(f.by_type("IfcProject")) == 1


def test_module_sans_dependance_vers_sig_ni_config():
    """Isolation pour la fusion : commun/ifc/ n'importe pas serveur/ et ne lit pas les fichiers de config/."""
    import re
    from pathlib import Path

    racine = Path(module_ifc.__file__).parent
    for py in racine.rglob("*.py"):
        if "tests" in py.parts:
            continue
        texte = py.read_text(encoding="utf-8")
        assert not re.search(r"^\s*(from|import)\s+serveur\b", texte, re.M), py
        assert "sig.yaml" not in texte and "georef_maquettes.yaml" not in texte, py

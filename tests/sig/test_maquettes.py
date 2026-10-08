"""Lot 3 — maquettes sur la carte : configuration, traitements, positionnement, calage vertical, API.

Jeu : tests/donnees/ifc/ (une maquette par méthode, une sans géoréférencement, un faux IFC).
Altimétrie : faux service (altitude du terrain 60 m). Recette : les maquettes géoréférencées sont
positionnées et leur modèle 3D est servi ; la maquette sans géoréférencement est « non positionnée ».
"""
import copy
import json
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from serveur.sig import maquettes as maq
from serveur.sig import routes, travail_ifc
from serveur.sig.app import app

CONFIG = Path(__file__).resolve().parents[1] / "donnees" / "georef_test.yaml"
ALTITUDE_TERRAIN = 60.0


@pytest.fixture(scope="module")
def cache_traite(tmp_path_factory):
    """Cache rempli une fois pour le module (traitement direct, sans sous-processus)."""
    cfgm = maq.charger_config(CONFIG)
    cfgm["dossier_cache"] = str(tmp_path_factory.mktemp("cache_ifc"))
    t = cfgm["traitement"]
    for id_, _rel, chemin in maq.fichiers(cfgm):
        sortie = maq.dossier_cache(cfgm, id_)
        params = maq.parametres_traitement(chemin, cfgm)
        p = sortie / "parametres.json"
        sortie.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(params), encoding="utf-8")
        travail_ifc.main([str(chemin), str(sortie), str(p)])
    return cfgm


@pytest.fixture
def cfgm(cache_traite, monkeypatch):
    cfgm = copy.deepcopy(cache_traite)
    appels = []

    def repondre(requete):
        appels.append(requete)
        return httpx.Response(200, json={"elevations": [ALTITUDE_TERRAIN]})

    monkeypatch.setattr(maq, "fabrique_client", lambda cfg: httpx.Client(transport=httpx.MockTransport(repondre)))
    cfgm["_appels_altimetrie"] = appels
    for f in Path(cfgm["dossier_cache"]).glob("*/sol.json"):
        f.unlink()
    return cfgm


@pytest.fixture
def inv(cfg_test, cfgm, monkeypatch):
    monkeypatch.setattr(maq.traitements, "assurer", lambda *a, **k: None)
    return {m["nom"]: m for m in maq.inventaire(cfg_test, cfgm)["maquettes"]}


# ---------------------------------------------------------------- configuration

def test_config_projet_valide():
    cfgm = maq.charger_config()
    assert cfgm["identification"]["systemes_candidats"] and cfgm["altimetrie"]["url"]


def test_config_incomplete(tmp_path):
    p = tmp_path / "g.yaml"
    p.write_text("dossier_ifc: x\ndossier_cache: y\n", encoding="utf-8")
    with pytest.raises(maq.ConfigMaquettesInvalide, match="traitement"):
        maq.charger_config(p)
    with pytest.raises(maq.ConfigMaquettesInvalide, match="introuvable"):
        maq.charger_config(tmp_path / "absent.yaml")


def test_config_rendu(tmp_path):
    """Réglages du rendu 3D : valeurs par défaut si absents, message clair si invalides."""
    import yaml
    from serveur.sig.maquettes import CHEMIN_GEOREF
    cfg = yaml.safe_load(CHEMIN_GEOREF.read_text(encoding="utf-8"))
    cfg["affichage_3d"].pop("rendu", None)
    p = tmp_path / "g.yaml"
    p.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    assert maq.charger_config(p)["affichage_3d"]["rendu"]["aretes"] is True
    cfg["affichage_3d"]["rendu"] = {"aretes": "oui"}
    p.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    with pytest.raises(maq.ConfigMaquettesInvalide, match="rendu"):
        maq.charger_config(p)


def test_config_saisie_manuelle_incomplete(tmp_path):
    texte = CONFIG.read_text(encoding="utf-8").replace("    y: 6860000.0\n", "")
    p = tmp_path / "g.yaml"
    p.write_text(texte, encoding="utf-8")
    with pytest.raises(maq.ConfigMaquettesInvalide, match="vont ensemble"):
        maq.charger_config(p)


# ---------------------------------------------------------------- inventaire et positionnement (recette)

def test_inventaire_toutes_les_maquettes(inv):
    assert set(inv) == {"methode1_map_conversion_ifc4", "methode2a_site_cc49_boston", "methode2a_site_cc49_identifie",
                        "methode2b_latlon", "methode3_manuelle", "piege_pas_un_ifc", "sans_georeferencement"}


def test_recette_maquettes_positionnees(inv):
    attendu = {"methode1_map_conversion_ifc4": "ifc_map_conversion", "methode2a_site_cc49_identifie": "ifc_site_projete",
               "methode2b_latlon": "ifc_site_latlon", "methode3_manuelle": "manuel"}
    for nom, methode in attendu.items():
        pos = inv[nom]["positionnement"]
        assert pos["statut"] == "positionnee" and pos["methode"] == methode, nom
        assert pos["emprise_geojson"]["type"] in ("Polygon", "MultiPolygon")


def test_recette_maquette_non_positionnee(inv):
    for nom in ("sans_georeferencement", "methode2a_site_cc49_boston"):
        pos = inv[nom]["positionnement"]
        assert pos["statut"] == "non_positionnee" and pos["raison"], nom


def test_faux_ifc_en_erreur_sans_bloquer(inv):
    e = inv["piege_pas_un_ifc"]["etat"]
    assert e["phase"] == "erreur" and "ISO-10303-21" in e["message"]


def test_position_au_bon_endroit(inv):
    # même point (nord de Massy) pour les méthodes 1, 2a, 2b : centres à moins d'un mètre (murs identiques)
    centres = [inv[n]["positionnement"]["centre_l93"] for n in
               ("methode1_map_conversion_ifc4", "methode2a_site_cc49_identifie", "methode2b_latlon")]
    for c in centres[1:]:
        assert abs(c[0] - centres[0][0]) < 1 and abs(c[1] - centres[0][1]) < 1
    lon, lat = inv["methode1_map_conversion_ifc4"]["positionnement"]["centre_wgs84"]
    assert 2.26 < lon < 2.28 and 48.76 < lat < 48.78


def test_calage_vertical_sur_le_terrain(inv, cfgm):
    pos = inv["methode2a_site_cc49_identifie"]["positionnement"]
    # site à 80 m NGF, murs de 0 à 3 m, terrain à 60 m : 20 à 23 m au-dessus du terrain
    assert pos["altitude_terrain"] == ALTITUDE_TERRAIN
    assert pos["hauteurs_affichees"] == pytest.approx([20.0, 23.0], abs=0.01)
    assert pos["affichage"]["decalage_z_m"] == pytest.approx(20.0)
    manuel = inv["methode3_manuelle"]["positionnement"]
    assert manuel["hauteurs_affichees"] == pytest.approx([31.5, 34.5], abs=0.01)   # 90 - 60 + 1,5
    assert "décalage manuel" in manuel["calage_vertical"]


def test_altimetrie_mise_en_cache(cfg_test, cfgm, monkeypatch):
    monkeypatch.setattr(maq.traitements, "assurer", lambda *a, **k: None)
    maq.inventaire(cfg_test, cfgm)
    n = len(cfgm["_appels_altimetrie"])
    maq.inventaire(cfg_test, cfgm)
    assert n > 0 and len(cfgm["_appels_altimetrie"]) == n


def test_altimetrie_indisponible_base_au_sol(cfg_test, cfgm, monkeypatch):
    def panne(requete):
        raise httpx.ConnectError("refus", request=requete)
    monkeypatch.setattr(maq, "fabrique_client", lambda cfg: httpx.Client(transport=httpx.MockTransport(panne)))
    monkeypatch.setattr(maq.traitements, "assurer", lambda *a, **k: None)
    inv = {m["nom"]: m for m in maq.inventaire(cfg_test, cfgm)["maquettes"]}
    pos = inv["methode2a_site_cc49_identifie"]["positionnement"]
    assert pos["statut"] == "positionnee" and pos["hauteurs_affichees"][0] == pytest.approx(0.0, abs=0.01)
    assert any("non calée" in a for a in pos["avertissements"])


def test_altitudes_deja_absolues_detectees(cfg_test, cfgm, monkeypatch):
    """Site à 80 m et objets portant déjà leur altitude NGF : l'altitude du site n'est pas comptée deux fois."""
    def repondre(requete):
        return httpx.Response(200, json={"elevations": [-75.0]})   # terrain fictif proche des objets seuls
    monkeypatch.setattr(maq, "fabrique_client", lambda cfg: httpx.Client(transport=httpx.MockTransport(repondre)))
    monkeypatch.setattr(maq.traitements, "assurer", lambda *a, **k: None)
    cfgm["altimetrie"]["ecart_max_terrain_m"] = 100
    inv = {m["nom"]: m for m in maq.inventaire(cfg_test, cfgm)["maquettes"]}
    pos = inv["methode2a_site_cc49_identifie"]["positionnement"]
    assert any(a.startswith("altitude du site") and "ignorée" in a for a in pos["avertissements"])


def test_declaration_par_motif(cfg_test, cfgm, monkeypatch):
    monkeypatch.setattr(maq.traitements, "assurer", lambda *a, **k: None)
    cfgm["systemes_declares"] = [{"motif": "methode2a_*", "systeme": "EPSG:3949"}]
    inv = {m["nom"]: m for m in maq.inventaire(cfg_test, cfgm)["maquettes"]}
    pos = inv["methode2a_site_cc49_boston"]["positionnement"]
    assert pos["statut"] == "positionnee" and "methode2a_*" in pos["systeme_source"]


# ---------------------------------------------------------------- file de traitement (sous-processus réel)

def test_file_de_traitement_sous_processus(cfgm, tmp_path):
    cfgm = copy.deepcopy(cfgm)
    cfgm["dossier_cache"] = str(tmp_path / "cache")
    file = maq.Traitements()
    choix = [m for m in maq.fichiers(cfgm) if m[1] in ("methode1_map_conversion_ifc4.ifc", "piege_pas_un_ifc.ifc")]
    file.assurer(cfgm, choix)
    debut = time.time()
    while file.occupee() and time.time() - debut < 120:
        time.sleep(0.2)
    ok, ko = choix if choix[0][1].startswith("methode") else choix[::-1]

    def diagnostic(m):
        d = maq.dossier_cache(cfgm, m[0])
        return {"historique": file.historique, "fichiers": sorted(p.name for p in d.iterdir()),
                **{n: (d / n).read_text(encoding="utf-8", errors="replace")[-800:]
                   for n in ("erreur.json", "journal.txt", "trace.txt") if (d / n).is_file()}}

    assert maq.resultat(cfgm, ok[0], maq.cle_traitement(ok[2], cfgm)) is not None, diagnostic(ok)
    assert (maq.dossier_cache(cfgm, ok[0]) / "modele.glb").is_file()
    e = maq.erreur(cfgm, ko[0], maq.cle_traitement(ko[2], cfgm))
    assert e and "ISO-10303-21" in e["message"], diagnostic(ko)
    # relance : le résultat est effacé puis recalculé
    file.relancer(cfgm, ok[0], ok[2])
    while file.occupee() and time.time() - debut < 180:
        time.sleep(0.2)
    assert maq.resultat(cfgm, ok[0], maq.cle_traitement(ok[2], cfgm)) is not None, diagnostic(ok)


# ---------------------------------------------------------------- API

@pytest.fixture
def client(cfg_test, cfgm, monkeypatch):
    monkeypatch.setattr(maq.traitements, "assurer", lambda *a, **k: None)
    app.dependency_overrides[routes._config_ou_erreur] = lambda: cfg_test
    app.dependency_overrides[routes._config_maquettes] = lambda: cfgm
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_api_maquettes_et_emprises(client):
    r = client.get("/api/sig/maquettes").json()
    assert len(r["maquettes"]) == 7 and r["complet"] and r["total"] == 7
    em = client.get("/api/sig/maquettes/emprises").json()
    assert em["type"] == "FeatureCollection" and len(em["features"]) == 4 and not em["tronque"]
    # liste allégée : ni emprises ni repère ni métadonnées (des milliers de maquettes) ; rectangle et affichage 3D
    pos = [m["positionnement"] for m in r["maquettes"] if (m.get("positionnement") or {}).get("statut") == "positionnee"]
    assert pos and all(not set(p) & set(maq.CLES_LOURDES) for p in pos)
    assert all(len(p["emprise_wgs84"]) == 4 and "origine" in p["affichage"] for p in pos)
    assert all("metadonnees" not in m for m in r["maquettes"])


def test_api_liste_par_difference(client, cfgm):
    r = client.get("/api/sig/maquettes").json()
    # rien n'a changé : liste vide, même jeton
    r2 = client.get("/api/sig/maquettes", params={"depuis": r["jeton"]}).json()
    assert not r2["complet"] and r2["maquettes"] == [] and r2["jeton"] == r["jeton"]
    # jeton d'une autre session du serveur (redémarrage) : liste complète
    r3 = client.get("/api/sig/maquettes", params={"depuis": "autre:3"}).json()
    assert r3["complet"] and len(r3["maquettes"]) == 7
    # une maquette disparaît du cache : seule sa ligne est renvoyée au balayage suivant
    m = next(x for x in r["maquettes"] if x["nom"] == "methode2b_latlon")
    (maq.dossier_cache(cfgm, m["id"]) / "resultat.json").rename(maq.dossier_cache(cfgm, m["id"]) / "resultat.bak")
    try:
        r4 = client.get("/api/sig/maquettes", params={"depuis": r["jeton"], "actualiser": True}).json()
    finally:
        (maq.dossier_cache(cfgm, m["id"]) / "resultat.bak").rename(maq.dossier_cache(cfgm, m["id"]) / "resultat.json")
    assert [x["id"] for x in r4["maquettes"]] == [m["id"]] and r4["maquettes"][0]["etat"]["phase"] != "pret"


def test_api_emprises_par_zone(client):
    r = client.get("/api/sig/maquettes").json()
    pos = [m["positionnement"] for m in r["maquettes"] if (m.get("positionnement") or {}).get("statut") == "positionnee"]
    x1, y1, x2, y2 = pos[0]["emprise_wgs84"]
    em = client.get("/api/sig/maquettes/emprises", params={"bbox": f"{x1},{y1},{x2},{y2}"}).json()
    assert 1 <= len(em["features"]) <= 4
    assert client.get("/api/sig/maquettes/emprises", params={"bbox": "0,0,1,1"}).json()["features"] == []
    assert client.get("/api/sig/maquettes/emprises", params={"bbox": "a,b"}).status_code == 422


def test_api_description_complete(client):
    r = client.get("/api/sig/maquettes").json()
    m = next(x for x in r["maquettes"] if x["nom"] == "methode1_map_conversion_ifc4")
    d = client.get(f"/api/sig/maquettes/{m['id']}").json()
    assert d["metadonnees"]["schema"] == "IFC4" and d["positionnement"]["repere"]
    assert "emprise_geojson" not in d["positionnement"]
    assert client.get("/api/sig/maquettes/inconnue").status_code == 404


def test_api_modele_glb_et_objet(client):
    r = client.get("/api/sig/maquettes").json()
    m = next(x for x in r["maquettes"] if x["nom"] == "methode1_map_conversion_ifc4")
    glb = client.get(f"/api/sig/maquettes/{m['id']}/modele.glb")
    assert glb.status_code == 200 and glb.content[:4] == b"glTF"
    assert glb.headers["content-type"] == "model/gltf-binary"
    import struct
    n = struct.unpack("<I", glb.content[12:16])[0]
    guid = json.loads(glb.content[20:20 + n])["scenes"][0]["extras"]["guids"][0]
    fiche = client.get(f"/api/sig/maquettes/{m['id']}/objets/{guid}").json()
    assert fiche["type_ifc"] == "IfcWall" and "Données d'identification" in fiche["psets"]
    assert client.get(f"/api/sig/maquettes/{m['id']}/objets/inconnu").status_code == 404


def test_api_maquette_inconnue_ou_en_erreur(client):
    assert client.get("/api/sig/maquettes/inconnue/modele.glb").status_code == 404
    r = client.get("/api/sig/maquettes").json()
    piege = next(x for x in r["maquettes"] if x["nom"] == "piege_pas_un_ifc")
    assert client.get(f"/api/sig/maquettes/{piege['id']}/modele.glb").status_code == 409


def test_api_config_maquettes_invalide_503(cfg_test, monkeypatch):
    def invalide():
        raise maq.ConfigMaquettesInvalide("georef_maquettes.yaml : essai")
    monkeypatch.setattr(maq, "config", invalide)
    app.dependency_overrides[routes._config_ou_erreur] = lambda: cfg_test
    with TestClient(app) as c:
        r = c.get("/api/sig/maquettes")
    app.dependency_overrides.clear()
    assert r.status_code == 503 and "essai" in r.json()["detail"]


def test_api_types_et_familles(client):
    r = client.get("/api/sig/maquettes").json()
    m = next(x for x in r["maquettes"] if x["nom"] == "methode1_map_conversion_ifc4")
    t = client.get(f"/api/sig/maquettes/{m['id']}/types").json()
    # deux murs : type IfcWall, famille « Génie civil » (type IFC déclaré)
    assert [t["types"][i] for i in t["objets"]] == ["IfcWall", "IfcWall"]
    assert [t["familles"][i]["nom"] for i in t["famille"]] == ["Génie civil", "Génie civil"]
    assert t["familles"][0]["nb"] == 2 and t["familles"][0]["couleur"].startswith("#")
    # deuxième appel : relu du cache
    assert client.get(f"/api/sig/maquettes/{m['id']}/types").json() == t


def test_familles_par_domaine_puis_type_puis_fichier(cfgm):
    cfgm = copy.deepcopy(cfgm)
    cfgm["familles_objets"] = [{"nom": "Lot", "fichiers": ["methode1*"]}, {"nom": "Rien", "types_ifc": ["IfcDoor"]}]
    m = next(x for x in maq.fichiers(cfgm) if x[1] == "methode1_map_conversion_ifc4.ifc")
    t = maq.types_objets(cfgm, *m)
    assert [t["familles"][i]["nom"] for i in t["famille"]] == ["Lot", "Lot"]
    cfgm["familles_objets"] = [{"nom": "Lot", "fichiers": ["methode1*"]}, {"nom": "Structure", "types_ifc": ["IfcWall"]}]
    t = maq.types_objets(cfgm, *m)
    assert [t["familles"][i]["nom"] for i in t["famille"]] == ["Structure", "Structure"]


def test_ecriture_etat_refusee_par_windows_n_interrompt_pas(tmp_path, monkeypatch):
    """Le serveur lit etat.json pendant que le travailleur le remplace : « Accès refusé » sous Windows."""
    from serveur.sig import travail_ifc
    refus = {"n": 0}
    remplacer = Path.replace

    def replace_capricieux(self, cible):
        if refus["n"] < 3:
            refus["n"] += 1
            raise PermissionError(5, "Accès refusé")
        return remplacer(self, cible)

    monkeypatch.setattr(Path, "replace", replace_capricieux)
    travail_ifc._ecrire(tmp_path / "resultat.json", {"a": 1})          # réussit après trois refus
    assert json.loads((tmp_path / "resultat.json").read_text(encoding="utf-8")) == {"a": 1}
    monkeypatch.setattr(Path, "replace", lambda self, cible: (_ for _ in ()).throw(PermissionError(5, "Accès refusé")))
    travail_ifc._ecrire_etat(tmp_path / "etat.json", {"phase": "x"})   # toujours refusé : ignoré, pas d'exception

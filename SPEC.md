# Démonstrateur SIG + lecture BIM — Cahier des charges

## 1. Objet

Construire une application web locale qui affiche des données SIG hétérogènes sur un fond IGN et positionne les maquettes IFC sur la carte, avec ouverture de la maquette en 3D.

Ce projet est **autonome**, mais il est conçu comme un **module du `demonstrateur-patrimoine`** dans lequel il sera fusionné plus tard (section 8). Il en reprend le socle technique, les conventions et la méthode lot par lot.

Hors périmètre pour l'instant : lien BDM, moteur de règles L01-L12, édition des données.

## 2. Données d'entrée

| Source | Formats | Emplacement |
|---|---|---|
| Couches SIG fichiers | GeoPackage, Shapefile, GeoJSON, CSV avec colonnes X/Y | `donnees/sig/` |
| Couches SIG services | Services Esri (ArcGIS REST, FeatureServer / MapServer) issus d'InfraGéo | URL déclarées dans `config/sig.yaml` |
| Maquettes | IFC (2x3 ou 4) | `donnees/ifc/` |

- Système de référence attendu des données : Lambert 93 (EPSG:2154). Tout autre système est détecté et signalé, jamais supposé.
- Un CSV sans système déclaré dans `config/sig.yaml` n'est pas chargé : il est listé en anomalie.
- Le contenu de `donnees/` est en **lecture seule**.

## 3. Fond de carte

Géoplateforme IGN, appelée par le navigateur à travers le proxy du poste :
- Plan IGN v2 (défaut) et orthophotographies, sélectionnables ;
- paramètres (URL WMTS, couches, formats) dans `config/sig.yaml`, jamais en dur ;
- si le fond ne répond pas, l'application reste utilisable sans fond et affiche un message clair.

À vérifier au lot 0 : que le navigateur du poste atteint bien `data.geopf.fr`.

## 4. Fonctionnalités par lot

### Lot 0 — Socle
- Environnement Python et Node dans l'espace utilisateur, via le proxy.
- Serveur FastAPI minimal (`/api/sig/sante`), interface MapLibre avec le fond IGN.
- Dépôt Git initialisé, `.gitignore` excluant `donnees/`, `.env` et les environnements.

**Recette** : la carte s'affiche centrée sur l'Île-de-France avec le fond IGN ; `pytest` passe.

### Lot 1 — Couches SIG fichiers
- Inventaire automatique de `donnees/sig/` : nom, format, géométrie, système, nombre d'objets, emprise. Les fichiers illisibles sont listés en anomalie, sans bloquer les autres.
- Conversion côté serveur en WGS84 (EPSG:4326) pour l'affichage ; les coordonnées Lambert 93 d'origine restent consultables.
- Gestionnaire de couches : afficher/masquer, ordre, opacité, style simple défini dans `config/sig.yaml` (avec style par défaut si absent).
- Clic sur un objet : panneau des attributs.
- Recherche texte simple sur les attributs d'une couche.
- Grosses couches : chargement limité à l'emprise affichée (filtre bbox) au-delà d'un seuil paramétrable.

**Recette** : au moins un fichier de chaque format s'affiche au bon endroit ; un fichier corrompu du jeu piégé apparaît en anomalie ; tests par format.

### Lot 2 — Services Esri
- Connecteur ArcGIS REST : lecture des couches déclarées dans `config/sig.yaml` (requête `query` en GeoJSON, pagination).
- Authentification éventuelle via `.env` (jamais dans le code ni dans Git).
- Cache local des réponses, avec date de mise à jour visible.
- Si le service est injoignable depuis le poste : repli sur un export fichier, et message clair.

**Recette** : une couche InfraGéo s'affiche, ou le repli est documenté dans `DECISIONS.md` si l'accès est impossible.

### Lot 3 — Maquettes IFC sur la carte
- Lecture des maquettes de `donnees/ifc/` avec le module partagé `commun/ifc/` (section 7).
- Positionnement, par ordre de priorité :
  1. `IfcMapConversion` + `IfcProjectedCRS` (IFC4) ;
  2. coordonnées de l'`IfcSite` (RefLatitude / RefLongitude) ;
  3. saisie manuelle dans `config/georef_maquettes.yaml` (point d'insertion en Lambert 93, rotation, altitude).
- La méthode utilisée est affichée pour chaque maquette ; une maquette sans géoréférencement ni saisie manuelle est listée « non positionnée ».
- Emprise au sol de chaque maquette affichée comme une couche ; liste des maquettes avec zoom.
- Clic sur une emprise : fiche (nom, schéma IFC, méthode de positionnement) et bouton d'ouverture en 3D dans un panneau.

**Recette** : les deux maquettes de test sont positionnées et s'ouvrent en 3D ; une maquette sans géoréférencement apparaît « non positionnée » ; tests sur chaque méthode.

### Lot 4 — Interrogation croisée
- Pour une maquette : liste des objets SIG des couches visibles qui intersectent son emprise.
- Pour un objet SIG : maquettes qui le recouvrent.
- Liste des locaux d'une maquette avec leur code (Pset « Données d'identification », `SGP_DI_Sous_Ouvrage`).
- Export CSV des résultats.

**Recette** : résultats vérifiés à la main sur un cas connu ; test d'intersection.

### Lot 5 — Fonctionnalités spécifiques
À définir avec l'utilisateur après le lot 4.

## 5. Principes

- Ne jamais bloquer, toujours tracer : une donnée défectueuse est signalée, les autres continuent de s'afficher.
- Aucun paramètre métier en dur : couches, styles, seuils, URL et géoréférencement manuel vivent dans `config/`.
- Pas de PostGIS ni de Docker en local : GeoPandas / pyogrio, et DuckDB spatial si les volumes l'exigent (choix à tracer dans `DECISIONS.md`).

## 6. Pile technique

Identique au `demonstrateur-patrimoine` : Python + FastAPI (serveur), interface Vite avec MapLibre GL JS (carte) et la visionneuse IFC existante (3D), pytest. Lecture IFC côté serveur avec IfcOpenShell.

## 7. Lecture IFC partagée

Il n'existe pas de module IFC à reprendre du `demonstrateur-patrimoine`. Un module de lecture IFC **autonome** est créé dans `commun/ifc/`, avec IfcOpenShell, pour être repris **tel quel** par le démonstrateur patrimoine lors de la fusion.

Fonctions attendues :
- **Métadonnées** : nom du projet, schéma IFC (2x3, 4…), application auteur, date, unités de longueur.
- **Géoréférencement** : détection de la méthode disponible (`IfcMapConversion` + `IfcProjectedCRS`, coordonnées `RefLatitude` / `RefLongitude` de l'`IfcSite`, ou aucune) et paramètres associés (système, translation, rotation, échelle, altitude).
- **Emprise au sol** : polygone 2D de la maquette, en coordonnées du projet, et transformation en coordonnées projetées à partir de paramètres de géoréférencement (issus du fichier ou fournis par l'appelant).
- **Locaux** : `IfcSpace` avec identifiant, nom, nom long, étage de rattachement.
- **Psets** : lecture générique des jeux de propriétés d'un objet (dont « Données d'identification », `SGP_DI_Sous_Ouvrage`).

Règles de conception :
- Aucune dépendance vers `serveur/sig/`, `interface/`, FastAPI ni `config/` : le module reçoit des chemins et des paramètres explicites (le géoréférencement manuel de `config/georef_maquettes.yaml` est lu par `serveur/sig/` puis transmis au module).
- Sorties en structures Python simples, sérialisables en JSON.
- Ne jamais bloquer : une maquette illisible ou incomplète renvoie des anomalies explicites au lieu d'interrompre le traitement.
- Ne jamais supposer un système de coordonnées : un système absent ou autre que Lambert 93 est signalé.
- Tests propres au module, livrables avec lui.

Toute évolution du module reste compatible avec cet usage partagé et est notée dans `DECISIONS.md`.

## 8. Préparation de la fusion

- Code métier SIG uniquement dans `serveur/sig/` et `interface/sig/`.
- Toutes les routes sous `/api/sig/`.
- Configuration dans `config/sig.yaml` et `config/georef_maquettes.yaml`, distincts de `regles.yaml`.
- Mêmes versions de Python, Node et des dépendances communes que le démonstrateur ; tout écart est noté.
- Au moment de la fusion : copie des dossiers `sig/`, des deux fichiers de config, reprise du module `commun/ifc/`, ajout d'un onglet « Carte » dans l'interface du démonstrateur.

## 9. Structure du dépôt

```
demonstrateur-sig/
  SPEC.md  CLAUDE.md  DECISIONS.md  DEMARRAGE.md
  donnees/   sig/  ifc/  esri/      (lecture seule, hors Git)
  config/    sig.yaml  georef_maquettes.yaml
  commun/    ifc/                    (module IFC autonome, repris par le démonstrateur)
  serveur/   sig/                    (FastAPI)
  interface/ sig/                    (MapLibre + visionneuse)
  tests/     donnees/                (jeu piégé)
```

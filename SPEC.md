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
- *(Évolution validée le 2026-10-05, voir `DECISIONS.md`)* : la 3D est affichée **dans la carte**, au milieu des couches SIG (même écran, même caméra), et non dans un panneau séparé ; clic sur un objet de maquette : fiche IFC (type, étage, Psets).

**Recette** : les deux maquettes de test sont positionnées et s'ouvrent en 3D ; une maquette sans géoréférencement apparaît « non positionnée » ; tests sur chaque méthode.

### Lot 4 — Interrogation croisée
- Pour une maquette : liste des objets SIG des couches visibles qui intersectent son emprise.
- Pour un objet SIG : maquettes qui le recouvrent.
- Liste des locaux d'une maquette avec leur code (Pset « Données d'identification », `SGP_DI_Sous_Ouvrage`).
- Export CSV des résultats.

**Recette** : résultats vérifiés à la main sur un cas connu ; test d'intersection.

### Lot 5 — Fonctionnalités spécifiques
À définir avec l'utilisateur après le lot 4.

### Lot C1 — Carte de gestion de crise : périmètre et analyse *(ajouté le 2026-10-21)*

Cahier des charges complet : `SIG_Viewer/CDC_Carte_Gestion_Crise.md` (hors dépôt, lots C1 à C4). Cas d'usage n°1 de l'atelier InfraGéo. POC local ; utilisateur : la cellule de crise. L'outil aide à décider, il ne décide pas.

- **Événement** : type (liste paramétrable), titre, heure, description, mode réel ou **exercice** (bandeau visible). Localisation par clic sur la carte, recherche d'une station ou d'un accès, ou coordonnées.
- **Périmètre au rayon libre**, intuitif :
  - clic puis glisser pour tracer le cercle, rayon affiché en direct ;
  - poignée sur le bord pour ajuster, centre déplaçable, saisie au clavier ;
  - raccourcis 500 m, 600 m et 1 km (paramétrables) ;
  - plusieurs cercles concentriques, polygone dessiné ;
  - rôles de zone : évacuation (rouge), fermeture (orange), vigilance (jaune), paramétrables.
- **Cercles calculés en Lambert 93**, jamais dans la projection de la carte.
- **Analyse par zone** (la zone la plus grave l'emporte) :
  - stations (touchées par leur point ou par un de leurs accès, avec la cause) et accès ;
  - tronçons de ligne ;
  - maquettes et ouvrages annexes (signalés comme puits) ;
  - écoles, chantiers et travaux, défibrillateurs, réseaux de gaz, points PLAI ;
  - un point de rassemblement en zone rouge ou orange est déclaré inutilisable, et le plus proche hors zone est proposé.

  Chaque élément donne sa distance, sa direction, sa source et la date de sa donnée. Les compteurs se mettent à jour pendant le glissement.
- **Mise en évidence** : zones en aplats, extérieur assombri, pictogrammes d'état, lignes touchées, légende de crise, vue « Crise ».
- **Listes** par zone et par catégorie, avec un export CSV.
- **Configuration** : section `crise` de `config/sig.yaml` (types, rayons, rôles, couleurs, couches analysées et leur catégorie, règles de proposition).

**Recette** : trois scénarios vérifiés à la main sur la carte.
- **A — Place de la Bastille** : stations, accès, lignes, écoles, chantiers.
- **B — Place de la République** : points PLAI ; point de rassemblement inutilisable et remplacement proposé.
- **C — Gare Stade de France (L15)** : gare 64SDF, tunnels 3364I et 6465I, ouvrage annexe 6301P.

S'y ajoutent :
- un test de distance (accès à 495 m dans le cercle de 500 m, à 505 m hors du cercle) ;
- une couche injoignable signalée sans bloquer l'analyse ;
- un recalcul en moins de 2 s pour 1 km dans Paris ;
- `pytest`.

### Lot C2 — Carte de gestion de crise : décider et tracer *(ajouté le 2026-10-21)*

- **Statut de chaque élément** : proposé (défaut), fermé, sans arrêt, évacué, confiné, à surveiller, ouvert, levé (liste paramétrable, par catégorie) ; actions en masse par zone ; statut visible sur la carte (creux : à décider, plein : décidé, croix : fermé ou évacué).
- **Main courante** : journal horodaté de toutes les actions avec leur auteur (session Windows par défaut, modifiable), notes libres ; enregistrement automatique de l'événement à chaque action.
- **Évolution du périmètre** : une version par modification ; changements affichés (« +3 accès, +1 école ») et inscrits au journal ; un élément décidé sorti du périmètre garde son statut (« hors zone désormais »).
- **Exports** (fichiers remis à l'utilisateur, jamais envoyés) : PDF A4 ou A3 (carte affichée, légende, chiffres par zone, bandeau réel ou exercice, sources et dates, page de synthèse des décisions et de la main courante) ; Excel (synthèse, une feuille par catégorie avec les statuts, main courante) ; GeoJSON du périmètre.

**Recette** : scénario Bastille dans l'interface — élargissement de 500 m à 600 m avec les changements affichés, accès de la zone d'évacuation fermés en masse, note au journal, exports PDF, Excel et GeoJSON relus ; tests des statuts, du journal, des versions et des exports.

### Lot C3 — Carte de gestion de crise : enrichir l'analyse *(ajouté le 2026-10-22)*

Toutes les nouvelles sources sont sur `data.geopf.fr` (domaine déjà validé), sauf le vent (Open-Meteo, déjà connecté).

- **Sources autour de l'événement** : les couches nationales trop volumineuses pour l'Île-de-France entière (tronçons de route et bâtiments de la BD TOPO, population carroyée INSEE) sont demandées au WFS par carreaux de Lambert 93 qui couvrent un disque autour du point, gardées en cache par carreau, chargées en arrière-plan sans bloquer l'analyse (« chargement en cours » affiché, analyse relancée). Au-delà du rayon chargé, les résultats sont signalés incomplets.
- **Rues touchées** : voies regroupées par nom (nom à gauche, sinon à droite ; voies sans nom regroupées par nature), longueur dans la zone, tracé sur la carte.
- **Points de bouclage** : points où les voies coupent la limite extérieure des zones bouclées (évacuation et fermeture par défaut), voies principales en tête (importance BD TOPO).
- **Estimations** : bâtiments et logements (BD TOPO), habitants, ménages, enfants de 0 à 3 ans et personnes de 80 ans et plus (Filosofi, carreaux de 200 m au prorata de la surface dans la zone), par zone, suivies en direct pendant le glissement, datées.
- **Établissements sensibles** : hôpitaux, établissements hospitaliers et maisons de retraite (BD TOPO), à côté des écoles.
- **Recherche d'adresse** (géocodage de l'IGN, base de la BAN), avec les stations et les accès.
- **Vent** au point (Open-Meteo) : flèche, secteur indicatif « sous le vent » en option, inscrit à la main courante et au PDF. Indication simple, pas un modèle de dispersion.
- **Couloir** le long d'une ligne (canalisation, voie, cortège) : axe tracé sur la carte, tampon de part et d'autre.
- **Import d'un périmètre** GeoJSON : aller-retour exact avec l'export (cercles, polygones, couloirs) ; polygones et lignes d'un autre SIG ; tout autre système que WGS84 est refusé, jamais supposé.
- Les estimations, le vent et les couloirs figurent dans les exports PDF et Excel.

Non fait dans ce lot : trafic temps réel (PRIM IDFM, clé en attente).

**Recette** : scénario « fuite de gaz rue de la Roquette » placé par l'adresse (rues, points de bouclage, estimations, vent), couloir tracé, import du périmètre exporté au lot C2 ; recalcul en moins de 2 s pour 1 km dans Paris une fois les données chargées ; tests des carreaux (cache, échec, refus de débit), des rues, du bouclage, du dénombrement (prorata, polygone, couloir), du géocodage, du vent et de l'import.

## 5. Principes

- Ne jamais bloquer, toujours tracer : une donnée défectueuse est signalée, les autres continuent de s'afficher.
- Aucun paramètre métier en dur : couches, styles, seuils, URL et géoréférencement manuel vivent dans `config/`.
- Pas de PostGIS ni de Docker en local : GeoPandas / pyogrio, et DuckDB spatial si les volumes l'exigent (choix à tracer dans `DECISIONS.md`).

## 6. Pile technique

Identique au `demonstrateur-patrimoine` : Python + FastAPI (serveur), interface Vite avec MapLibre GL JS (carte) et la visionneuse IFC existante (3D), pytest. Lecture IFC côté serveur avec IfcOpenShell ; conversion 3D par web-ifc, le moteur de la visionneuse (évolution du 2026-10-06, voir `DECISIONS.md`).

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

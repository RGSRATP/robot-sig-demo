# Consignes permanentes pour Claude Code

Tu construis le démonstrateur décrit dans `SPEC.md`, sur le poste Windows de l'utilisateur, lot par lot. Réponds, commente et documente en français.

## Avant toute action

1. Lis `SPEC.md` en entier, puis ce fichier, puis `DECISIONS.md`.
2. Vérifie le contenu de `donnees/` (voir `donnees/LISEZMOI.md`). S'il manque ce qu'un lot exige, arrête-toi et dis quoi.
3. Demande quel lot réaliser si l'utilisateur ne l'a pas précisé. Un seul lot à la fois.
4. Présente ton plan du lot et attends la validation avant de coder.

## Règles non négociables

- **Ne jamais modifier, déplacer ni supprimer** un fichier de `donnees/`. Les jeux de test dérivés vont dans `tests/donnees/`.
- **Ne jamais bloquer, toujours tracer** : une couche ou une maquette défectueuse est signalée en anomalie, le reste s'affiche.
- **Aucun paramètre en dur** : couches, styles, URL, seuils, géoréférencement manuel sont dans `config/`.
- **Aucun secret dans le code ni dans Git** : identifiants éventuels dans `.env`, exclu par `.gitignore`.
- **Isolation pour la fusion** : code SIG uniquement dans `serveur/sig/` et `interface/sig/`, routes sous `/api/sig/`. `commun/ifc/` est un module IFC autonome (SPEC section 7), sans dépendance vers `sig/` ni `config/`, destiné à être repris tel quel par le démonstrateur patrimoine ; toute évolution est notée dans `DECISIONS.md`.
- **Systèmes de coordonnées** : ne jamais supposer un système. Lambert 93 (EPSG:2154) attendu ; tout autre cas est signalé.

## Environnement du poste

- Pas de droits administrateur : tout s'installe dans l'espace utilisateur (environnement virtuel Python, `npm` local au projet). Pas de Docker, pas de PostGIS.
- pip et npm passent par le proxy `http://127.0.0.1:9000`.
- Le fond IGN est appelé par le navigateur, à travers le proxy système du poste. Aucune autre ressource Internet à l'exécution (bibliothèques servies localement).

## Méthode

- Écris les tests (pytest) en même temps que le code : un test par format de données, par méthode de positionnement IFC et par critère de recette.
- Commit Git à chaque étape qui fonctionne, avec un message clair en français.
- Tiens `DECISIONS.md` à jour (date, choix, raison, impact sur la fusion).
- Si `SPEC.md` est ambigu ou irréalisable, signale l'écart et propose des options au lieu de trancher seul.

## Fin de lot

Arrête-toi et fournis :
1. un compte rendu (fait, non fait, anomalies, décisions) ;
2. les commandes de recette : activation de l'environnement, lancement du serveur, lancement de l'interface, `pytest` ;
3. ce que l'utilisateur doit vérifier à l'écran.

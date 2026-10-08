# Démarrage

## 1. Préparer le dossier
1. Décompresse l'archive **hors OneDrive** (ex. `C:\Users\<toi>\projets\demonstrateur-sig`).
2. Copie tes données (voir `donnees/LISEZMOI.md`) :
   - couches SIG dans `donnees\sig\` ;
   - deux maquettes IFC de test dans `donnees\ifc\` ;
   - éventuels exports Esri dans `donnees\esri\`.
3. Rien à copier dans `commun\ifc\` : le module de lecture IFC y est créé par Claude Code (SPEC section 7).
4. Complète `config\sig.yaml` : couches CSV (colonnes X/Y, système), URL des services Esri si tu les connais.

## 2. Lancer Claude Code
Dans un terminal, depuis le dossier :
```
claude
```

## 3. Premier message
> Lis SPEC.md, CLAUDE.md et DECISIONS.md. Vérifie le contenu de donnees/. Puis présente ton plan du lot 0, sans rien coder avant ma validation.

## 4. Ensuite
Un lot à la fois : plan → validation → code → recette → lot suivant. Utilise `/clear` entre deux lots.

## 5. Clés des services (fichier `.env`)
Certaines sources demandent une clé personnelle, par exemple IDFM PRIM pour les prochains passages en gare.
La clé ne va **jamais** dans `config\sig.yaml` ni dans le code. Elle va dans un fichier `.env` à la racine du
projet, que Git ignore (il n'est jamais envoyé ni partagé).

1. Ouvre le **Bloc-notes** (touche Windows, tape « Bloc-notes », Entrée).
2. Écris une seule ligne, sans espace ni guillemets autour du signe `=` :
   ```
   IDFM_PRIM_CLE=colle_ta_clé_ici
   ```
3. **Fichier › Enregistrer sous** :
   - dossier : la racine du projet (celui qui contient `lancer.cmd`) ;
   - **Type : « Tous les fichiers (\*.\*) »** (sinon le Bloc-notes ajoute `.txt`) ;
   - **Nom : `.env`** (avec le point au début, rien après) ;
   - Encodage : UTF-8. Clique sur Enregistrer.
4. Vérifie dans l'Explorateur (menu Affichage › cocher « Extensions de noms de fichiers ») que le fichier
   s'appelle bien `.env` et pas `.env.txt`.
5. Inutile de relancer : la clé est lue à la prochaine actualisation (une minute au plus). Dans le volet
   « Temps réel », le message « Clé absente » disparaît et les gares affichent leurs prochains départs.

Si un message « accès refusé » apparaît : la clé est mal recopiée, expirée, ou ton compte PRIM n'est pas abonné à
l'API « Prochains passages (requête unitaire) ». Si la clé a été collée ailleurs (message, fichier partagé),
régénère-la sur le portail PRIM.

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

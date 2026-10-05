# Journal des décisions

| Date | Décision | Raison | Impact fusion |
|---|---|---|---|
| (initial) | Projet SIG séparé, fusionné ensuite dans `demonstrateur-patrimoine` | Tester le volet SIG de façon autonome | Isolation `sig/`, routes `/api/sig/` |
| (initial) | Fond de carte : Géoplateforme IGN via le proxy | Choix utilisateur | Paramètres dans `config/sig.yaml` |
| (initial) | Données SIG mixtes : fichiers + services Esri | Choix utilisateur | Connecteurs dans `serveur/sig/` |
| 2026-10-05 | Pas de module IFC à copier : création d'un module de lecture IFC autonome dans `commun/ifc/` (IfcOpenShell : métadonnées, géoréférencement, emprise, locaux, Psets). SPEC section 7 mise à jour | Le démonstrateur patrimoine ne fournit pas de module IFC réutilisable | Module repris tel quel par le démonstrateur patrimoine ; aucune dépendance vers `serveur/sig/` ni `config/` |

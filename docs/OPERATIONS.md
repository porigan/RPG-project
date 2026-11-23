# Administration et récupération

Les commandes de jeu passent par les règles et la vue du personnage. Les commandes suivantes sont des outils d'auteur ou de diagnostic ; `history` inclut des informations réservées au MJ.

## Vérifier et sauvegarder

```powershell
python -m rpg --db partie.db audit
python -m rpg --db partie.db backup sauvegarde.db
```

L'audit compare rejeu et projections, les hash du journal, les index historiques, les reçus d'idempotence, les références SQLite et les invariants métier intégrés. Les validateurs de modules personnalisés s'exécutent sur leurs écritures ; leurs audits spécialisés restent à ajouter au besoin.

La sauvegarde utilise l'API SQLite, avec fermeture explicite du fichier destination. Elle refuse d'écraser une destination existante. Pour restaurer, utilisez la sauvegarde comme fichier `--db`, ou copiez-la vers un nouvel emplacement après fermeture de l'application.

Ne remplacez pas une base ouverte en copiant seulement son fichier principal : le mode WAL peut encore contenir des transactions confirmées.

## Reconstruire l'état dérivé

```powershell
python -m rpg --db partie.db rebuild
```

Cette opération reconstruit les projections, les index de changements et les reçus manquants à partir du journal, sans lancer de règle ni de tirage. Elle n'efface aucun événement. Un reçu existant incohérent n'est pas écrasé : l'audit le signale et une analyse est nécessaire.

Une chaîne de journal incompatible, une précondition impossible ou une cause située dans le futur sont des erreurs bloquantes, pas des occasions d'inventer une justification narrative.

## Créer une autre chronologie

Consultez d'abord une révision de commande confirmée dans `history` ou dans son résultat technique :

```powershell
python -m rpg --db partie.db history --last 10
python -m rpg --db partie.db fork alternative --at 1 --reason 'Explorer une autre décision'
python -m rpg --db partie.db --branch alternative play
```

La révision 1 correspond à la création de l'univers. Les frontières au milieu d'une commande atomique sont refusées. La branche d'origine reste consultable et jouable.

## Plans interrompus

```powershell
python -m rpg --db partie.db plans
python -m rpg --db partie.db plans --resume IDENTIFIANT
```

La reprise concerne les plans `running` interrompus techniquement. Si la dernière étape avait déjà été confirmée, son reçu empêche de la rejouer avant de remettre le curseur à jour.

Un plan `awaiting_decision` ne poursuit jamais de lui-même ses étapes restantes. Le joueur exprime une nouvelle intention. L'API `PlanRunner.cancel` peut fermer un ancien plan ; une édition automatique de ses décisions n'est pas fournie.

Les tours de `Session.turn` possèdent aussi un identifiant réutilisable. Un tour terminé renvoie sa réponse enregistrée. Un tour dont l'interprétation IA a échoué peut être resoumis par l'application avec le même texte et le même identifiant. L'interface terminal ne fournit pas encore de navigateur de tours en attente.

## Déclencheurs et corrections

```powershell
python -m rpg --db partie.db schedule declencheur.json --reason 'Départ convenu du PNJ'
python -m rpg --db partie.db cancel-trigger depart-du-scribe --reason 'Départ annulé'
python -m rpg --db partie.db correct correction.json --revision 12 --reason 'Erreur initiale identifiée'
```

Une correction JSON est une liste d'objets `{category, key, before, after}`. Elle doit fournir les anciennes valeurs exactes et traiter tous les invariants concernés dans un même événement. Déplacer un personnage sans les objets qu'il porte est refusé. Les suppressions structurelles et les corrections des collections système ne sont pas proposées par cette commande.

Le résultat précise que les conséquences historiques ne sont pas recalculées automatiquement. Si elles sont compromises, préférez une branche au point approprié puis réexécutez explicitement les décisions encore valides.

Une correction de croyance ajoute une acquisition datée du moment courant, avec provenance `correction`, et conserve l'ancienne entrée historique. Les métadonnées de date et de provenance sont donc produites par le moteur, même si le fichier de correction contient celles de l'ancienne valeur.

## Erreurs et limites

- `Conflict` : révision périmée, identifiant réutilisé ou passé incompatible. Relire les faits avant de proposer une nouvelle résolution.
- `InvalidAction` : règle, référence ou argument invalide. La commande n'est pas partiellement confirmée.
- `LimitExceeded` : budget de résolution, profondeur ou sélection dépassé. La commande courante est annulée et le travail reste en attente.
- `ModelError` : configuration, transport ou réponse IA incompatible. Aucun résultat probabiliste n'est relancé par une simple erreur de narration.

La base doit être sauvegardée avant toute évolution de schéma. La version initiale utilise `PRAGMA user_version=1` et refuse les versions inconnues ; aucun outil de migration future n'est encore livré.

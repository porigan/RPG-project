# Architecture et invariants

Les objectifs du cahier des charges sont conservés : causalité, persistance, décisions du joueur respectées, connaissances individuelles et mécanismes propres aux univers. Les choix ci-dessous constituent l'architecture implémentée de la version 0.1.

## Responsabilités

| Fichier | Responsabilité |
| --- | --- |
| `model.py` | Commandes, erreurs, vue transactionnelle paresseuse et résolution des dépendances de modules. |
| `store.py` | SQLite, transactions, journal, projections, reçus, ascendance des branches, audit et sauvegarde. |
| `engine.py` | Validation des commandes, événements, causalité, hasard déterministe et échéances. |
| `modules.py` | Mécanismes facultatifs ; aucun niveau, XP ou point de vie dans le contrat du noyau. |
| `knowledge.py` | Perception, croyances, relations, souvenirs et actualisation hors champ. |
| `universe.py` | Chargement JSON contrôlé et validation atomique de l'état initial. |
| `plans.py` | Exécution progressive et persistante des intentions composées. |
| `ai.py` | Transport HTTP, schéma de proposition, contexte autorisé et narration facultative. |
| `session.py` | Orchestration d'un tour jouable avec identifiant idempotent. |
| `cli.py` | Jeu terminal et commandes techniques d'administration. |

## Écriture d'une commande

1. Identifier la commande et calculer l'empreinte de son acteur, type et arguments.
2. Ouvrir `BEGIN IMMEDIATE`, puis rechercher un reçu existant.
3. Renvoyer ce reçu s'il correspond exactement ; rejeter une réutilisation avec un autre contenu.
4. Vérifier la révision attendue, les droits de l'acteur et l'activation du module.
5. Exécuter le gestionnaire contre une vue isolée, en construisant des événements contenant les changements avant/après.
6. Valider les invariants, résoudre les conséquences réactives et les frontières temporelles.
7. Attacher le reçu complet au dernier événement de la commande.
8. Enregistrer journal, projections, index historiques, reçu et tête de branche dans la même transaction.
9. Produire la narration après commit.

Une erreur avant commit annule toute la commande. Une interruption normale peut au contraire confirmer le temps et les conséquences effectivement produits : `status=interrupted`. Les étapes précédentes d'un plan restent validées.

Aucun appel réseau ou modèle n'est réalisé sous verrou d'écriture SQLite.

## Journal et projections

Le journal est la source de reconstruction. Chaque changement durable contient sa valeur avant et sa valeur après. Les projections `records` servent les lectures courantes ; elles sont des objets JSON validés, avec identité stable et colonnes indexées pour acteur, sujet, lieu, statut, mode et échéance. Les mécanismes possèdent leurs collections logiques.

Les références `event_refs` servent les recherches historiques individuelles. Les reçus complets sont aussi présents dans les événements : la reconstruction restaure les reçus manquants sans réexécuter la commande.

Le rejeu applique les résultats enregistrés, pas les règles courantes. Les schémas d'événements et les versions de modules sont explicites. Un changement incompatible exige une migration conçue et testée ; cette version ne possède pas encore de migration vers un autre schéma.

Le journal et les reçus sont protégés contre UPDATE/DELETE par des triggers SQL. Une chaîne de hash et les préconditions avant/après détectent les incohérences. Il ne s'agit pas d'une protection contre l'administrateur du fichier, qui peut retirer ces protections ou réécrire toute la chaîne.

## Invariants métier

- Les ressources restent entières et non négatives ; les caractéristiques respectent leurs bornes déclarées.
- Un objet possède un propriétaire et un détenteur distincts. L'index du détenteur et le lieu sont cohérents avec le personnage qui le porte.
- Un déplacement traite les objets portés et les offres actives attachées au personnage dans le même événement.
- Un achat transfère l'objet et les fonds ensemble, uniquement dans les limites du consentement exprimé.
- Les références de personnages, lieux et informations doivent exister.
- Une croyance possède une source et une date ; sa valeur n'est pas assimilée à la vérité globale.
- Une commande IA ne peut changer d'acteur, écrire des projections, programmer un déclencheur ou appeler une opération d'administration.
- Une révision périmée ne peut être confirmée en tant que nouvelle commande.

Les administrateurs et les modules Python sont du code de confiance. Le registre n'est pas une sandbox pour du Python tiers hostile.

## Deux temporalités

`seq` est l'ordre d'enregistrement et de rejeu ; `effective_at` est la date fictive ; `recorded_at` est une date UTC informatique. Un événement établi tardivement peut avoir une date fictive plus ancienne que la ligne précédente.

Les liens de causalité doivent pointer vers des événements déjà enregistrés dont la date fictive n'est pas postérieure. Le moteur refuse d'appliquer rétrospectivement une modification à un enregistrement déjà modifié plus tard dans la chronologie fictive.

Le temps est exprimé en secondes entières depuis l'origine de la partie. Les actions longues rencontrent successivement les échéances pertinentes. À une même échéance, l'ordre temporel est `(due, priority, id)`. Les expirations de capacités ont une priorité de -100 et sont traitées avant la fin d'une action à cet instant. Les réactions événementielles sont traitées immédiatement après l'événement qui les déclenche.

Les conditions sont réévaluées sur les modifications de leur dépendance déclarée. Une condition déjà vraie au moment de sa configuration n'est pas une échéance immédiate : elle sera évaluée lors du prochain changement de cette dépendance. Les déclencheurs d'événement et de condition sont à usage unique. Les récurrences temporelles imposent un intervalle strictement positif.

Le noyau revalide statut et version à l'exécution. Il n'utilise pas une ancienne copie de déclencheur après remplacement. Un dépassement de budget annule la commande en cours ; il ne consomme pas les échéances restantes.

## Hors-champ

Les occasions de communication sont des fenêtres déclarées : participants, lieu, information, début, fin, politique relationnelle éventuelle et probabilité. Avant une interaction avec un PNJ, les sources nécessaires sont actualisées jusqu'au début de la fenêtre.

Le moteur vérifie les connaissances historiques et la présence des participants pendant toute la fenêtre, y compris un départ suivi d'un retour. Le résultat probabiliste dépend de l'identité de l'occasion, pas de la requête qui déclenche son calcul. Les croyances acquises sont datées de la fin de l'occasion et conservées.

Une observation déjà établie après cette date bloque une transmission rétrospective susceptible de la contredire. Une relation révisée après la fenêtre bloque aussi un jugement historique qui ne serait plus justifiable par la valeur courante.

La production autonome est une politique périodique fixe, avec capacité, calculée arithmétiquement. Ce n'est pas un planificateur général de vie des PNJ. Aucun parcours incontrôlé du graphe social n'est réalisé.

Les sélections sont indexées et bornées à 2 000 résultats ; un historique individuel à 1 000 versions ; les dépendances différées à 16 niveaux ; le budget de résolution à 1 000 unités par défaut. Une limite produit une erreur explicite. Le rejeu et l'audit globaux restent linéaires et nécessiteront des snapshots pour les campagnes très longues.

## Branches et corrections

Une branche conserve un parent et une révision de départ. Elle possède ses projections et ses nouveaux événements ; le préfixe du journal est partagé par ascendance. Les reçus des commandes complètement héritées sont conservés. Le résultat d'un reçu hérité garde les métadonnées de la branche d'origine.

Une branche ne peut pas couper au milieu d'une commande atomique. La profondeur est limitée à 64 branches. Les corrections locales sont des événements explicites avec motif, révision attendue et anciennes valeurs ; elles ne recalculent pas automatiquement toutes les conséquences passées.

Les observations ou souvenirs réels du joueur humain ne peuvent évidemment pas être effacés par une bifurcation informatique.

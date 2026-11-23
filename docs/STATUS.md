# État d'avancement

Version de développement : 0.1.0. Le moteur, les deux univers de configuration, l'interface terminal et les tests sont présents dans le dépôt. Ce bilan distingue les primitives fonctionnelles d'un moteur RPG universel abouti.

## Implémenté et couvert par les tests

| Domaine | Comportement exercé |
| --- | --- |
| Persistance | Achat atomique, conservation des fonds, rollback en cas d'erreur d'écriture, arrêt brutal d'un processus, réouverture et sauvegarde. |
| Idempotence | Requête répétée, refus d'une empreinte différente, reprise de plan et reconstruction d'un reçu manquant. |
| Journal | Rejeu avant/après, hash, projections, index, invariants intégrés et protections append-only. |
| Concurrence | Deux écrivains réels avec la même révision : une seule commande confirmée. |
| Temps | Expiration exacte, interruption dépendante, poursuite indépendante, échéances remplacées/annulées et récurrences bornées. |
| Hasard | Tirage enregistré, nouvelle tentative distincte, bornes 0 et 1, absence de relance au rejeu. |
| Mémoire | Perception, croyance fausse transmissible, vérité globale indépendante, contexte joueur sans secrets cachés. |
| Hors-champ | Présence historique pendant une fenêtre, indépendance du requérant, résultat persistant et refus d'une contradiction avec une observation. |
| Intentions | Plan partiellement accompli, arrêt au prix trop élevé et préservation des étapes déjà réalisées. |
| Mécanismes | Progression, évolution descriptive de personnalité, combat élémentaire, compagnons et quêtes à objectif persistant. |
| Chronologies | Bifurcation, indépendance des projections, héritage des reçus et corrections locales explicites. |
| Interface | Partie en français dans un véritable sous-processus, réouverture de sa sauvegarde, grammaire locale clairement identifiée. |
| IA | HTTP localhost réel pour les deux protocoles, réponses contrôlées, filtrage des propositions, révision périmée, échec de narration sans annulation du jeu. |

Les tests générant des séquences d'actions sont déterministes. Ils complètent les scénarios ciblés ; ils ne constituent pas une preuve exhaustive de toutes les combinaisons de règles possibles.

## Limites réelles

1. **Aucun modèle IA réel validé dans cet environnement.** Il manque un endpoint et un modèle configurés, ainsi qu'une clé si le fournisseur l'exige. Les tests HTTP utilisent des réponses contrôlées.
2. **Autonomie déclarative.** Les PNJ ont des occasions de communication, des productions périodiques et des déplacements programmés. Ils n'ont pas encore de planificateur général d'objectifs ou de dialogue libre piloté par IA.
3. **Catalogue d'actions fini.** L'IA interprète les actions implémentées ; elle n'invente pas de nouvelles règles ou de faits durables. Les nouvelles mécaniques nécessitent un module.
4. **Narration libre faillible.** Les références sont contrôlées, pas la vérité de chaque phrase. La narration factuelle reste le mode par défaut.
5. **Perception simple.** Le lieu partagé est la frontière de visibilité et de conversation. Distance, obstacles, déguisements, acoustique et sens particuliers ne sont pas simulés.
6. **Déplacements abstraits.** Le personnage reste associé au lieu de départ jusqu'à l'arrivée ; il n'y a pas encore de position intermédiaire ni de scène de trajet persistante. L'interruption consomme le temps écoulé.
7. **Hors-champ limité aux occasions explicites.** Le moteur ne déduit pas des rencontres arbitraires à partir du graphe social. Les ressources partagées exigent une actualisation avant utilisation ; une politique de production complexe avec coûts ou décisions multiples reste à développer.
8. **Corrections prudentes.** Pas de réparation causale générale automatique, de fusion de branches ni de recalcul silencieux de conséquences.
9. **Échelle bornée.** Pas encore de snapshots périodiques, pagination générale ou base distribuée. Les recherches ont des plafonds explicites ; l'audit et le rejeu parcourent le journal.
10. **Modules introductifs.** Le combat ne gère pas initiative, défenses, tours complets ou ripostes autonomes. Les créatures n'ont pas d'ordres ou d'évolutions. Les quêtes ont un seul objectif de test persistant. Aucune règle Pokémon ou One Piece complète n'est prétendue implémentée.
11. **Triggers sémantiques différés.** Les conditions structurées sont fonctionnelles ; l'évaluation de contexte sémantique et les abonnements persistants complexes ne sont pas livrés.
12. **Contrat de déploiement local.** Un service écrivain SQLite, sans serveur multijoueur, authentification distante, interface graphique ni chiffrement de sauvegarde. Un seul contrôleur de plans doit piloter une partie à la fois ; la coordination de plusieurs sessions éditant le même plan reste à concevoir.
13. **Évolution des données.** Version de schéma explicite et refus des versions incompatibles, mais pas encore de migration entre versions publiées.
14. **Jugements et psychologie.** Les probabilités sont définies par les règles ; aucune estimation IA de difficulté n'est automatiquement promue en vérité. La personnalité évolue par expériences déclarées, sans simulateur psychologique ni oubli artificiel.

## Améliorations prioritaires

1. Valider un vrai modèle sur un corpus d'intentions ambiguës, d'informations cachées et de décisions non autorisées ; mesurer les erreurs d'interprétation.
2. Ajouter un contexte de dialogue persistant et des réactions de PNJ avec propositions de divulgation contrôlées.
3. Définir des trajets comme états persistants avec positions ou étapes intermédiaires et événements locaux.
4. Étendre les politiques autonomes à des objectifs bornés, en gardant les mêmes transitions en simulation active et différée.
5. Introduire snapshots et pagination à partir de mesures de campagnes longues.
6. Développer les audits causaux ciblés et les outils de réparation avec aperçu des conséquences affectées.
7. Ajouter les règles d'un premier univers complet sous forme de modules, sans les généraliser au noyau.

Les détails techniques et les commandes de reprise se trouvent dans [HANDOFF.md](HANDOFF.md).

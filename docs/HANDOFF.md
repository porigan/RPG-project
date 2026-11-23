# Reprise du développement

## Point de départ

Le dépôt initial contenait uniquement un README. La branche `codex/rpg-engine` ajoute un moteur Python sans dépendance d'exécution externe, une suite `unittest`, deux univers JSON, un terminal et des transports IA configurables.

La demande utilisateur autorise la poursuite autonome de l'ensemble du moteur, les tests, la documentation et une branche/pull request. Ne réduire pas systématiquement le travail au prochain petit incrément et ne demander une décision que si elle change substantiellement l'expérience souhaitée ou nécessite une autorisation externe.

## Retrouver et vérifier l'état

```powershell
git status --short --branch
python --version
python -W error::ResourceWarning -m unittest discover -v
python -m rpg --help
```

Pour les vérifications de style, utiliser Ruff 0.17.6, puis `ruff check rpg tests` et `ruff format --check rpg tests`. Les tests HTTP doivent pouvoir ouvrir localhost ; dans un environnement restreint, demander l'autorisation d'exécuter ces tests hors du bac à sable au lieu de les présenter comme réussis ou de les retirer.

L'environnement de développement initial est Windows avec Python 3.13.15. La CI est configurée sur Linux/Windows et Python 3.12/3.13/3.14 ; vérifier ses résultats réels dans GitHub avant d'affirmer que cette matrice passe.

## Architecture à préserver

- Toute transition durable doit passer par un événement et la transaction du moteur.
- Un reçu est stocké à la fois dans le journal et dans sa table d'accès rapide.
- `seq` et `effective_at` ne sont pas interchangeables ; ne trier jamais le journal par date fictive pour le rejouer.
- Ne rappeler ni l'IA ni le générateur de hasard pendant un rejeu.
- Une intention composée est une suite de commandes atomiques, pas une seule transaction géante.
- Les vues de personnages sélectionnent explicitement leurs champs ; ne sérialiser jamais un événement global dans un prompt joueur.
- Les limites de budget laissent une résolution en attente ; elles ne justifient pas une absence d'événement.
- Une correction locale ne constitue pas un recalcul causal général.
- Un nouveau module déclare ses collections et validateurs ; il reste du code de confiance.

## Navigation du code

Commencer par `engine.py` et `store.py`, puis `knowledge.py` pour le hors-champ. `modules.py` donne les transitions concrètes. `plans.py` et `session.py` gèrent les reprises. Les détails du protocole IA sont dans `ai.py`, sans dépendance à un SDK fournisseur.

`tests/test_adversarial.py` couvre les cas de corruption, de concurrence et de temps les plus sensibles. Les tests de transport sont explicitement distingués des tests avec un vrai modèle dans `tests/test_ai_and_interface.py`.

## Prochaine avancée utile

Lancer une partie avec les commandes du README, puis configurer un véritable modèle si disponible. Ne pas chercher une clé dans les fichiers personnels : le programme attend uniquement les variables `RPG_AI_*` documentées.

Après validation de l'interprétation réelle, travailler les réactions de PNJ, les états de trajet et les politiques autonomes. Ajouter des tests de non-régression avant d'élargir une règle temporelle ou une actualisation différée.

Pour une fonctionnalité non présente, consulter [STATUS.md](STATUS.md) ; ne pas reprendre ses descriptions comme si elle existait déjà. Les sauvegardes d'utilisateurs doivent être copiées avant toute évolution de schéma.

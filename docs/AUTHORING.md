# Créer un univers ou un module

## Univers de données

Un univers est un fichier JSON avec `schema: 1`, un identifiant, un titre, le personnage joueur, les modules activés et les collections initiales. Les deux fichiers de `examples/universes/` sont exécutables, pas de simples illustrations.

`harbor.json` associe inventaire, économie, mémoire, probabilités, capacités, progression et combat. `clockwork.json` utilise créatures et quêtes, sans attribut de combat ou niveau.

Les identifiants de personnages et les noms de ressources/caractéristiques, ainsi que ceux des faits, capacités, tests, quêtes et expériences, acceptent lettres, chiffres, tiret, point et soulignement. Le séparateur `:` est réservé aux clés composées internes, par exemple `player:coin`. Les libellés et descriptions restent du texte libre.

Les doublons de clés JSON, nombres non finis, références absentes, durées invalides et dépendances désactivées sont rejetés. La création d'une partie est atomique : une erreur ne laisse pas un demi-univers.

Une collection initiale doit appartenir à un module activé. Pour désactiver un mécanisme, retirez également ses données initiales. Les collections d'exécution telles que les reçus, plans, effets ou compagnons sont créées par le moteur ; ne les injectez pas dans les collections initiales standard.

## Modules fournis

| Module | Dépendances | Actions |
| --- | --- | --- |
| `core` | — | `observe`, `wait`, `travel` |
| `inventory` | `core` | `give` |
| `economy` | `inventory` | `buy` |
| `chance` | `core` | `attempt` |
| `abilities` | `core` | `activate` |
| `memory` | `core` | `talk`, `tell`, `experience` |
| `progression` | `core` | `train` |
| `combat` | `core` | `attack` |
| `creatures` | `core` | `befriend` |
| `quests` | `chance` | `accept_quest`, `claim_quest` |

Les ressources et caractéristiques sont des enregistrements facultatifs, avec noms et bornes déclarés. Rien n'exige une statistique `hp`, un niveau ou de l'XP. Le combat fourni utilise conventionnellement `hp` ; cette convention appartient à ce module.

Un compagnon désigne actuellement un lien persistant avec une créature. Il ne la téléporte pas avec le joueur et n'implémente pas ses ordres de combat. Les quêtes fournies portent sur un objectif de test persistant, accepté volontairement, avec récompense à réclamer une seule fois.

## Ajouter du code de règles

Un module Python possède un nom, une version, des dépendances, des collections, des validateurs et des gestionnaires. Enregistrez-le explicitement dans un `Registry` ; un fichier d'univers ne peut pas importer ou exécuter du Python.

Exemple de structure :

```python
from rpg.model import InvalidAction, Registry
from rpg.modules import RuleModule, default_registry, require_fields

def validate_counter(state, record):
    if type(record.get("value")) is not int or record["value"] < 0:
        raise InvalidAction("Compteur invalide.")

def record_visit(ctx, actor, args):
    require_fields(args, ())
    key = actor["id"]
    record = ctx.state.get("visit_counts", key, required=False) or {
        "id": key, "actor": key, "value": 0
    }
    record["value"] += 1
    ctx.emit("visit.recorded", {"actor": key}, [("visit_counts", key, record)])
    return {"visits": record["value"]}

visits = RuleModule(
    "visits", {"record_visit": record_visit}, ("core",),
    collections=("visit_counts",),
    validators={"visit_counts": validate_counter},
)
registry = Registry([*default_registry().modules.values(), visits])
```

Passez ce registre à `Engine(store, registry)`, puis ajoutez `visits` à la configuration de l'univers. Les collections déclarées peuvent aussi recevoir des données initiales validées. Les dépendances cycliques et les doubles déclarations d'actions ou de collections sont rejetées.

Les gestionnaires utilisent `ctx.emit` ; ils n'écrivent pas directement dans SQLite. Leurs validateurs sont appelés à l'initialisation et sur chaque changement de leurs collections. Les modules sont du code de confiance : l'interface n'empêche pas techniquement un module hostile de contourner ses conventions.

Pour exposer une nouvelle action à une IA ou à la grammaire locale, ajoutez également son contrat d'arguments et sa sélection de contexte dans l'adaptateur concerné. Une action Python n'est pas automatiquement une commande en langage naturel.

## Déclencheurs

Exemple temporel dans un fichier JSON utilisé par `schedule` :

```json
{
  "id": "depart-du-scribe",
  "mode": "time",
  "due": 1200,
  "status": "active",
  "action": {"kind": "npc.move", "actor": "scribe", "destination": "hill"}
}
```

Les autres modes sont `event`, avec `subject` égal au type d'événement, et `condition`, avec une dépendance explicite :

```json
{
  "id": "reaction-au-paiement",
  "mode": "condition",
  "status": "active",
  "condition": {
    "category": "resources", "key": "player:coin",
    "field": "value", "op": "lte", "value": 75
  },
  "action": {
    "kind": "resource.delta", "actor": "player", "resource": "stamina",
    "amount": 1, "reason": "conséquence explicitement définie par l'univers"
  }
}
```

Ces conséquences sont des règles d'auteur explicites. Le modèle ne peut pas fabriquer spontanément une récompense ou un déplacement du joueur. Les actions de déclencheur disponibles sont expiration d'effet, variation de ressource et déplacement de PNJ. Les conditions sémantiques libres ne sont pas encore prises en charge.

## Mémoire et hors-champ

Une information globale (`facts`) ne crée aucune connaissance automatiquement. Une perception autorisée ou une communication produit une croyance (`beliefs`) et son entrée historique (`belief_history`). Une croyance initiale doit déclarer sa source et sa date ; elle peut être fausse.

La liste `shareable_facts` d'un PNJ autorise la divulgation lors d'une question. Elle n'implique pas qu'il connaît déjà ces informations. `topics` indique seulement les sujets proposés publiquement à la conversation.

Une occasion hors champ doit préciser une fenêtre de présence réelle. Une relation ne crée pas une rencontre. La production autonome ne possède qu'une politique par personnage et ressource. Les consommateurs intégrés de ressources de PNJ actualisent cette production avant de modifier le solde.

Pour les nouveaux modules consommant une ressource autonome, actualisez le personnage concerné **avant** de lire les valeurs nécessaires et de préparer des changements. Ne prélevez pas sur un état différé ni sur une ancienne copie obtenue avant une actualisation.

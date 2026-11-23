# Intégration IA

Pour utiliser **ChatGPT dans l'application Windows**, l'adaptateur HTTP de cette page n'est pas nécessaire : voir [l'interface MCP](CHATGPT.md). Elle laisse ChatGPT interpréter et narrer dans la conversation, sans exécuter de second modèle côté moteur.

## Contrat

Le moteur accepte tout objet implémentant :

```python
def complete(messages: list[dict], schema: dict) -> dict:
    ...
```

`HTTPModel` fournit deux transports :

- `ollama` : endpoint complet `/api/chat`, messages, schéma dans `format`, `stream=false` ;
- `chat-completions` : endpoint compatible, messages et `response_format={"type":"json_object"}`.

Le deuxième transport utilise le mode JSON, puis les mêmes validations locales ; il ne suppose pas que tous les fournisseurs prennent en charge un schéma strict. Certains modèles ou serveurs pourront refuser ce mode : leur compatibilité doit être testée.

Références de protocole : [Ollama chat](https://docs.ollama.com/api/chat), [sorties structurées Ollama](https://docs.ollama.com/capabilities/structured-outputs), [Chat Completions](https://developers.openai.com/api/reference/resources/chat).

## Propositions et validation

L'interpréteur reçoit l'intention et une vue sélectionnée du joueur : scène, personnages visibles, objets détenus, offres accessibles, connaissances du personnage et catalogue d'actions activées.

Il renvoie soit une clarification, soit au maximum huit étapes. Chaque étape doit citer une portion exacte de l'intention source. Les références de lieux d'action, objets, interlocuteurs, capacités et connaissances sont vérifiées contre le contexte autorisé. Les paramètres sont contrôlés à nouveau par le module au moment de l'exécution.

L'acteur et la révision attendue sont choisis par l'application, jamais par le modèle. Les opérations d'administration ne figurent pas dans le catalogue. Une réponse IA ne contient aucun patch directement applicable à la base.

La citation d'une intention ne prouve pas à elle seule que son interprétation est correcte. Un modèle peut produire une proposition techniquement valide mais mal comprendre le joueur. La version actuelle ne possède pas encore d'évaluation sémantique approfondie ni de politique de confirmation dédiée aux décisions irréversibles du jeu.

## Informations et narration

Les secrets globaux, les événements MJ et les croyances cachées des PNJ sont absents de la vue joueur. Les sujets de conversation publics peuvent être visibles sans exposer leur réponse.

La narration par défaut utilise les résultats confirmés du moteur. La narration IA facultative reçoit seulement la scène et un compte rendu factuel des actions réalisées. Les anciens souvenirs ne lui sont pas envoyés comme rappels spontanés. « Mes notes » et « Mes souvenirs » sont des demandes explicites qui permettent leur restitution.

Le contrôle des références empêche de citer un identifiant de fait absent, mais ne prouve pas que chaque phrase du texte est vraie. La prose IA ne devient jamais une modification d'état. Cette limite doit être conservée dans toute présentation du projet.

Les dialogues libres de PNJ, l'invention autonome de faits durables et la création de nouvelles règles par le modèle ne sont pas encore implémentés. Les politiques de divulgation des PNJ sont actuellement déclarées dans l'univers.

## Configuration

Variables d'environnement :

| Variable | Valeur |
| --- | --- |
| `RPG_AI_ENDPOINT` | URL complète du serveur. |
| `RPG_AI_MODEL` | Identifiant du modèle déjà disponible. |
| `RPG_AI_PROTOCOL` | `ollama` par défaut, ou `chat-completions`. |
| `RPG_AI_API_KEY` | Jeton facultatif, envoyé en Authorization Bearer. |

HTTPS est obligatoire hors de localhost. Les redirections sont refusées et les corps d'erreurs distantes ne sont pas affichés. La réponse est limitée à 1 Mo et le délai HTTP à 30 secondes par défaut. Le programme n'effectue aucune relance réseau automatique susceptible de doubler une facturation.

## Ce qui a été testé

Les tests automatisés lancent un vrai serveur HTTP local avec des réponses contrôlées, vérifient les deux enveloppes de protocole, les erreurs HTTP, le refus des redirections, les propositions invalides et l'exécution dans le moteur.

Ce serveur est une fixture, pas un modèle de langage. Aucun endpoint, modèle ni jeton RPG n'était configuré dans l'environnement de développement. Une validation de bout en bout avec un modèle réel reste à effectuer après configuration, sans modifier le noyau du moteur.

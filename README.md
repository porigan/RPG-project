# RPG-project

Moteur RPG persistant et modulaire en Python, avec une interface terminal en français et une frontière explicite entre propositions IA, règles et modifications du monde.

Il s'agit d'une version fonctionnelle de développement. **Pour jouer avec ChatGPT comme maître du jeu, sans API de modèle supplémentaire, utilisez l'[intégration MCP locale Windows](docs/CHATGPT.md).** Le protocole MCP est testé avec le client officiel ; la visibilité dans votre conversation nécessite encore la connexion du client et son contrôle après redémarrage.

L'adaptateur de modèle HTTP et le jeu terminal ci-dessous restent des options distinctes, non nécessaires à l'intégration ChatGPT.

## Commencer à jouer

Prérequis : Python 3.12 ou plus récent, avec SQLite et son support JSON. Aucune bibliothèque externe n'est nécessaire pour exécuter le code depuis le dépôt.

Depuis le dossier du projet :

```powershell
python -m rpg --db partie.db init --universe examples/universes/harbor.json
python -m rpg --db partie.db play
```

Écrivez par exemple :

```text
Je regarde autour de moi
J'achète la dague
inventaire
J'attends 5 minutes
Je parle à Nora de la destination
J'active le vol
J'emprunte le passage aérien
mes notes
quitter
```

La dernière traversée exige quinze minutes de vol alors que le pouvoir dure douze minutes : elle s'interrompt à l'expiration. Le monde utilise ici une localisation par lieux, sans positions intermédiaires sur les routes.

Pour une autre expérience, sans points de vie, niveaux, combat ni monnaie imposés :

```powershell
python -m rpg --db jardin.db init --universe examples/universes/clockwork.json
python -m rpg --db jardin.db play
```

Essayez « J'accepte la quête », « Je tente de déchiffrer le signal », « Je réclame la récompense », « J'approche Cuivre ». Les échecs consomment les coûts et durées déclarés ; une nouvelle tentative peut être nécessaire.

Le mode local est une petite grammaire française documentée, pas un modèle de langage. En cas d'expression non reconnue, il demande de préciser au lieu d'inventer une action.

## Activer une véritable IA

Le même moteur accepte un serveur Ollama local ou un endpoint compatible Chat Completions. Le programme ne télécharge aucun modèle et ne crée aucun compte.

Exemple PowerShell avec un modèle déjà installé dans Ollama :

```powershell
$env:RPG_AI_PROTOCOL = 'ollama'
$env:RPG_AI_ENDPOINT = 'http://localhost:11434/api/chat'
$env:RPG_AI_MODEL = 'nom-du-modele-installe'
python -m rpg --db partie.db play --ai
```

Pour un service compatible Chat Completions, utilisez `RPG_AI_PROTOCOL=chat-completions`, son URL complète HTTPS et, si nécessaire, `RPG_AI_API_KEY`. Ne placez pas la clé dans l'URL ou dans les fichiers de configuration des univers.

Avec `--ai`, le modèle interprète l'intention ; les réponses restent issues de gabarits factuels. `--ai --ai-narration` active aussi une reformulation littéraire facultative. Celle-ci peut se tromper malgré le contrôle des références et n'est jamais appliquée à la base de données. Si elle échoue techniquement, la réponse factuelle reste disponible.

Voir [l'intégration IA](docs/AI.md), notamment la distinction entre tests de transport HTTP et validation avec un vrai modèle.

## Fonctionnalités implémentées

- Journal append-only, état courant, reçus idempotents et index historiques enregistrés dans une même transaction SQLite.
- Rejeu sans nouveau tirage ni appel IA, audit, reconstruction et sauvegarde cohérente.
- Chronologies alternatives conservant leur ascendance et l'ancienne branche.
- Temps fictif entier, effets temporaires et déclencheurs temporels, conditionnels et événementiels.
- Plans persistants, interruptions et reprise après un arrêt entre une étape et l'avancement du curseur.
- Propriété, possession, localisation, ressources et caractéristiques bornées.
- Modules facultatifs d'économie, hasard, capacités, mémoire, progression, combat, créatures et quêtes.
- Croyances distinctes des vérités globales, transmission, familiarité asymétrique, souvenirs et évolution descriptive de personnalité.
- Hors-champ borné : occasions explicites de communication et production périodique agrégée.
- Interface naturelle locale, adaptateur HTTP IA, outils d'administration séparés du contexte joueur.

Les comportements autonomes sont des politiques déclarées. Ce moteur n'est pas encore un MJ capable d'inventer et de valider seul toutes les règles de n'importe quel univers. Les [limites et priorités restantes](docs/STATUS.md) font partie du contrat de cette version.

## Tests et diagnostics

```powershell
python -W error::ResourceWarning -m unittest discover -v
python -m rpg --db partie.db audit
python -m rpg --db partie.db backup sauvegarde.db
```

Les tests n'utilisent aucune clé API. Ils exécutent notamment des achats réels, des écritures concurrentes, un processus interrompu brutalement, des résolutions temporelles, des transmissions différées, des branches et des requêtes HTTP vers un serveur de test local. Ils ne prouvent pas la qualité d'un véritable modèle IA.

Les tests HTTP ont besoin d'une connexion loopback autorisée ; un bac à sable qui interdit le réseau peut bloquer cette partie de la suite.

La CI configure Linux et Windows avec Python 3.12, 3.13 et 3.14. Son résultat effectif se consulte dans les vérifications GitHub de la pull request.

Pour installer aussi la commande `rpg` dans un environnement virtuel :

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\rpg.exe --help
```

L'installation du paquet peut télécharger setuptools pour sa construction. Le moteur installé n'a aucune dépendance Python externe d'exécution. Sous Linux, utilisez `.venv/bin/python` et `.venv/bin/rpg`.

## Documentation

- [Connecter le moteur à ChatGPT sur Windows](docs/CHATGPT.md).
- [Architecture et invariants](docs/ARCHITECTURE.md).
- [Créer un univers ou un module](docs/AUTHORING.md).
- [Intégration et limites IA](docs/AI.md).
- [Administration et récupération](docs/OPERATIONS.md).
- [État d'avancement et limites](docs/STATUS.md).
- [Vérifications techniques](docs/VALIDATION.md).
- [Reprendre le développement](docs/HANDOFF.md).

Les fichiers `.db`, clés et environnements virtuels sont exclus de Git. Conservez vos sauvegardes séparément du code.

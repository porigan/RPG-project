# Validation technique

## Interface ChatGPT, version 0.2

La suite comprend désormais 80 tests, dont 16 tests de la frontière ChatGPT/moteur et quatre tests MCP avec le SDK officiel 2.3.0. La validation complète locale utilise l'environnement virtuel équipé de `.[mcp]` :

```powershell
.\.venv\Scripts\python.exe -W error::ResourceWarning -m unittest discover -q
```

Les tests MCP lancent un serveur réel dans un processus séparé, découvrent les outils, exécutent un achat, vérifient l'idempotence et initialisent une connexion Streamable HTTP authentifiée. Les propositions altérées, périmées ou rattachées à une autre sauvegarde sont refusées. Les lectures utilisent SQLite en mode lecture seule. Un test interdit les connexions réseau pendant la résolution du moteur, pour vérifier l'absence de second modèle.

La suite a été exécutée hors du bac à sable Windows, qui bloquait les communications entre certains sous-processus. Un succès du SDK MCP ne prouve pas la disponibilité des outils dans une conversation ChatGPT : ce dernier contrôle nécessite le redémarrage du client configuré et un appel depuis la conversation choisie.

La CI installe maintenant `.[mcp]` pour exécuter aussi ces tests sur ses six configurations. Consulter le résultat associé au commit courant de la pull request.

L'entrée `rpg-engine` a également été enregistrée dans le client local Windows avec sa sauvegarde existante et une copie préalable. `tools/check_mcp.py` a utilisé cette configuration exacte : quatre outils découverts, appel d'état réussi, révision identique avant et après. Ce contrôle est un appel du SDK officiel, pas un appel du modèle ChatGPT dans la conversation. Le redémarrage et la vérification dans la conversation restent nécessaires.

## Première livraison, version 0.1

Vérifications locales du 23 novembre 2026, sur Windows avec Python 3.13.15.

| Vérification | Résultat observé |
| --- | --- |
| `python -W error::ResourceWarning -m unittest discover -q` | 60 tests réussis. |
| `ruff check rpg tests tools` avec Ruff 0.17.6 | Aucun diagnostic. |
| `ruff format --check rpg tests tools` | 21 fichiers conformes. |
| Construction et installation du paquet dans `.venv` | Wheel 0.1.0 construit et installé. |
| `python tools/check_install.py --python .venv/Scripts/python.exe` | Achat, fermeture, réouverture et audit du paquet installé depuis un autre dossier. |
| Modèle IA réel | Non testé : endpoint et modèle RPG non configurés. |

Les tests de transport exécutent de vraies requêtes HTTP sur localhost vers une fixture contrôlée. Ils ont été lancés hors du bac à sable après que celui-ci a bloqué le loopback. Ils ne sont pas des tests de qualité d'un modèle de langage.

La matrice GitHub Actions configure six combinaisons Linux/Windows et Python 3.12/3.13/3.14. Les résultats de ces exécutions sont ceux de la pull request ; la configuration de la matrice ne suffit pas à démontrer son succès.

Les limites fonctionnelles sont détaillées dans [STATUS.md](STATUS.md).

# Validation technique

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

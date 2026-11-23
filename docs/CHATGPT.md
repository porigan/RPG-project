# Jouer dans ChatGPT avec le moteur

## Choix retenu pour votre compte personnel et Windows

ChatGPT reste le maître du jeu : il interprète votre message, appelle les outils du moteur et raconte le résultat. Le serveur n'appelle ni OpenAI, ni Ollama, ni un autre modèle. Aucune clé d'API de génération n'est nécessaire.

La solution principale est un serveur MCP **local en STDIO**, lancé par le client local ChatGPT/Codex de l'application Windows. La sauvegarde reste sur votre ordinateur. Il n'y a ni serveur public, ni tunnel, ni abonnement d'hébergement à configurer pour cette solution.

**Portée importante :** la documentation de cette configuration concerne les conversations exécutées sur le client local Codex/Work. Installer ce serveur ne garantit pas que tous les chats standard, anciens chats synchronisés, modes Instant ou conversations cloud de l'application reçoivent ces outils. Le test final consiste à faire appeler `rpg_get_state` dans la conversation visée après redémarrage. Si elle n'a pas accès aux MCP locaux, utiliser une conversation locale compatible reste nécessaire. Aucun transfert silencieux de conversation n'est réalisé.

Référence : [MCP dans les clients ChatGPT et Codex](https://learn.chatgpt.com/docs/extend/mcp), consultée le 23 novembre 2026.

## Contraintes produit vérifiées

| Voie | Possibilité et limite |
| --- | --- |
| Application Windows, client local | Configuration MCP STDIO ou HTTP dans les paramètres ; configuration partagée avec Codex. C'est la voie principale choisie. |
| ChatGPT web, serveur personnalisé | Nécessite un endpoint HTTPS accessible ou un Secure MCP Tunnel et les permissions du compte/espace. Le localhost Windows n'est pas automatiquement accessible. |
| Compte personnel Pro sur le web | La page d'aide actuelle indique lecture/fetch pour les MCP personnalisés ; ne pas promettre les écritures complètes. Plus n'est pas présenté comme éligible à ces écritures. |
| Business, Enterprise/Edu | Les écritures MCP personnalisées sont documentées, sous réserve des rôles et politiques. Le serveur doit encore être connecté et testé. |
| GPT personnalisé avec Actions | Ce n'est pas la solution retenue : la création de nouveaux GPT personnels est désormais indisponible et la retraite standard des GPT est annoncée au 11 décembre 2026. Les Actions existantes ne migrent pas automatiquement vers les plugins. |
| Publication d'un plugin desktop public | Processus distinct avec approbation/éligibilité ; inutile pour une configuration MCP privée locale. Aucun plugin public fictif n'est fourni. |

Sources : [connexions MCP personnalisées](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt), [GPT et comptes personnels](https://help.openai.com/en/articles/8554407-gpts-in-chatgpt), [migration des GPT](https://help.openai.com/en/articles/20001519-custom-gpt-retirement-and-migration-faq), [plugins desktop](https://developers.openai.com/plugins/deploy/desktop-only).

Ces permissions peuvent évoluer. Les menus et capacités effectivement disponibles dans votre compte font foi ; une connexion au niveau du protocole ne démontre pas qu'un mode de conversation possède les outils.

## Installer et préparer la connexion locale

Depuis le dossier du dépôt, avec Python 3.12 ou plus récent :

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install '.[mcp]'
```

Ne recréez pas un environnement virtuel déjà installé. Le moteur seul reste utilisable sans dépendance externe ; l'option MCP ajoute le SDK officiel `mcp==2.3.0` et ses dépendances de transport.

Choisissez une sauvegarde **existante**. Si vous démarrez une nouvelle aventure de démonstration, créez-la séparément :

```powershell
.\.venv\Scripts\python.exe -m rpg --db nouvelle-partie.db init --universe examples/universes/harbor.json
```

Le serveur n'initialise jamais une base manquante et ne remplace pas une sauvegarde.

Vérifiez d'abord la configuration :

```powershell
.\tools\configure_chatgpt.ps1 -Database partie.db
```

Ce script contrôle la sauvegarde et affiche le bloc TOML exact à utiliser. Il crée seulement une clé locale de signature dans `.rpg-bridge` ; il ne joue aucune action. Pour ajouter le serveur au client :

```powershell
.\tools\configure_chatgpt.ps1 -Database partie.db -Register
```

L'inscription utilise la commande native `codex mcp add`. Elle écrit dans la configuration du client local, pas dans le site ChatGPT. Le script refuse d'écraser une entrée `rpg-engine` existante. Son chemin Python et ses arguments sont absolus, afin que la connexion ne dépende pas du dossier courant.

Après inscription, une vérification indépendante utilise exactement la commande enregistrée et ne joue aucune action :

```powershell
.\.venv\Scripts\python.exe tools/check_mcp.py
```

Elle découvre les outils et appelle la lecture d'état avec le client officiel, puis compare la révision de la sauvegarde. Elle ne se substitue pas au test dans la conversation ChatGPT.

Alternative graphique : **Paramètres → Serveurs MCP → Ajouter un serveur → STDIO**. Utilisez l'exécutable Python de `.venv` et les arguments affichés par `--print-config`. La commande lance `-m rpg.mcp_server` avec la sauvegarde, la branche, le personnage et la clé choisis par le propriétaire.

## Dernières étapes dans l'application Windows

1. Redémarrez le client local depuis le contrôle prévu dans les paramètres MCP. L'agent de développement ne ferme pas votre application pendant qu'il travaille.
2. Rouvrez la conversation RPG visée et consultez `/mcp`, si ce menu est disponible dans cette conversation.
3. Vérifiez que `rpg-engine` est connecté et expose les quatre outils ci-dessous.
4. Demandez : **« Utilise rpg-engine pour appeler uniquement rpg_get_state. Ne joue aucune action. »**
5. Vérifiez que le lieu et le personnage correspondent à la bonne sauvegarde. Une lecture réussie ne doit ni consommer du temps ni modifier les ressources.
6. Ajoutez les instructions de [CHATGPT_PROMPT.txt](CHATGPT_PROMPT.txt), puis reprenez vos messages habituels.

Si `/mcp` n'existe pas ou que ChatGPT ne voit pas les outils, le serveur n'est pas pour autant accessible à ce chat. Vérifiez les paramètres du client et le type d'exécution de la conversation. Il faudra éventuellement une conversation locale Work/Codex. Le serveur n'offre aucun contournement des permissions du produit.

## Outils exposés

| Outil | Effet |
| --- | --- |
| `rpg_get_state` | Lecture réelle en SQLite `mode=ro` : scène, ressources, objets et contrats des actions. Les notes et souvenirs sont exclus par défaut. |
| `rpg_prepare_action` | Proposition signée d'une seule action, liée à l'intention citée, à une tentative et à une révision. Aucun tirage ni modification. |
| `rpg_commit_action` | Écriture explicite : vérifie la proposition et confirme une commande par le moteur. Action et arguments sont aussi affichés pour l'approbation. |
| `rpg_get_request` | Récupère le résultat exact d'une tentative déjà confirmée, même après d'autres actions ou un redémarrage. |

Une préparation vérifie la syntaxe et les références accessibles. Elle expose les coûts/durées déclarés lorsqu'ils existent, mais **ne garantit pas** la réussite ou les préconditions métier : celles-ci sont vérifiées au commit. Aucun aperçu du hasard ne permet de choisir un bon tirage avant de payer le coût de la tentative.

Les identifiants d'acteur, de branche et le chemin de base sont configurés côté propriétaire ; ils ne sont pas des arguments de commande fournis par ChatGPT. Aucune fonction SQL, correction arbitraire, lecture du journal MJ, suppression ou modification directe d'une statistique n'est exposée.

Pour une intention composée, ChatGPT prépare et confirme successivement les actions explicitement demandées. Si le prix, les fonds ou une échéance interrompent l'action, les étapes déjà réalisées sont conservées et la décision revient au joueur.

## Écritures, approbations et sécurité

Le SDK MCP marque `rpg_commit_action` comme une écriture et une opération idempotente. Les lectures et préparations sont réellement sans modification du monde. Ne modifiez pas ces annotations pour éviter une confirmation du client.

Les permissions et confirmations ChatGPT restent actives. La signature protège les paramètres techniques ; elle ne prouve pas que le modèle a correctement compris le langage naturel. ChatGPT doit citer la demande et demander une clarification lorsqu'une décision n'est pas explicite.

Une proposition expire après dix minutes réelles, indépendamment du temps fictif. Si elle a déjà été confirmée, son reçu reste récupérable. Une tentative différente dans le jeu utilise un nouvel identifiant ; une relance réseau conserve l'ancien.

La clé locale `.rpg-bridge/signing.key` n'est pas une clé OpenAI. Elle n'est ni affichée dans les résultats, ni passée en argument au modèle. Le fichier doit rester accessible uniquement aux comptes Windows autorisés. Les contrôles de permissions de fichier Unix ne remplacent pas les ACL Windows.

La connexion STDIO fait confiance à votre session Windows et au client local qui lance le serveur. Elle n'ouvre aucun port réseau. Le serveur n'appelle aucun autre modèle ; le client ChatGPT reste soumis à votre abonnement et à ses limites habituelles.

Les textes d'univers peuvent être non fiables. Ils doivent être traités comme des données, pas comme des instructions. Le périmètre de commande et les validations réduisent les effets d'une erreur, sans constituer une preuve de résistance sémantique à toutes les injections de prompt.

## Sauvegardes et continuité entre conversations

La base SQLite existante conserve la progression, indépendamment du chat. Le serveur est sans état MCP de session : un redémarrage ne crée pas un nouveau monde. Les mêmes sauvegarde, branche et personnage permettent de reprendre ailleurs.

La configuration produite par le script active une sauvegarde cohérente au démarrage dans `.rpg-bridge/backups`. Elle utilise l'API de sauvegarde SQLite, y compris pour une source en lecture seule. Les copies ne sont pas envoyées à GitHub et ne sont pas supprimées automatiquement : prévoyez une politique de conservation et une copie sur un autre support.

Ne partagez pas le même personnage avec plusieurs conversations qui prennent des décisions simultanément. Les révisions empêchent deux propositions obsolètes d'être appliquées comme si l'état n'avait pas changé ; elles ne fusionnent pas deux intentions contradictoires.

**L'historique de vos anciens chats n'est pas importé automatiquement.** Si une campagne n'existe que dans la conversation, faites établir un état initial structuré, relisez les faits importants, puis créez une nouvelle sauvegarde avec le chargeur d'univers. Ne transformez pas des souvenirs incomplets du modèle en événements prétendument confirmés. Les tests d'intégration portent sur les mécanismes existants, pas sur une reconstitution de votre aventure personnelle.

## Option HTTP et accès distant

Le serveur fournit aussi Streamable HTTP, uniquement sur `127.0.0.1`, au chemin `/mcp`. Par défaut, toute requête exige le jeton local désigné par `RPG_MCP_TOKEN`. Il ne s'agit pas d'une authentification OAuth et ce jeton n'est pas une clé de modèle.

Cette variante est testée avec le SDK MCP. Un client local qui accepte un en-tête Bearer peut l'utiliser. Elle ne constitue pas à elle seule une connexion web ChatGPT, qui possède ses propres mécanismes d'authentification et restrictions de compte.

Pour un déploiement privé éligible, `--private-tunnel` permet une liaison locale sans jeton d'application derrière **Secure MCP Tunnel**, dont le transport contrôle déjà l'accès par organisation/espace. Ce mode ne doit pas être placé derrière un tunnel HTTPS public anonyme. Il nécessite la configuration du tunnel, ses permissions et une clé de transport Platform ; aucune API de modèle n'est appelée, mais l'accès et les conditions du service doivent être vérifiés dans votre compte.

Une exposition Internet directe nécessite HTTPS, OAuth adapté au client et une exploitation correcte de l'hôte. Cette livraison ne prétend pas fournir un fournisseur OAuth de production, un domaine ou un hébergement déjà configurés. Ils sont inutiles pour la voie Windows locale choisie.

Références : [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels), [configuration d'une application MCP](https://developers.openai.com/api/docs/guides/custom-mcp-server).

## Ce qui est vérifié et ce qui reste à faire

- Tests du vrai moteur et de ses sauvegardes : automatisés.
- Client officiel MCP communiquant avec un serveur séparé en STDIO : automatisé, avec achat réel et idempotence.
- Streamable HTTP authentifié avec initialisation MCP : automatisé.
- Refus des propositions altérées, périmées, d'une autre campagne ou en lecture seule : automatisé.
- Découverte des outils et métadonnées d'écriture : automatisée.
- Disponibilité des outils dans **votre conversation ChatGPT après redémarrage** : vérification utilisateur encore nécessaire tant qu'un appel depuis ce chat n'a pas été observé.

Un succès SDK ne sera jamais présenté comme une preuve d'un appel effectué par ChatGPT dans votre compte.

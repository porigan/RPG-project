"""Public action contracts shared by conversational adapters; no model dependency."""

from .model import InvalidAction, integer

ACTION_ARGS = {
    "observe": (),
    "wait": ("seconds",),
    "travel": ("route",),
    "buy": ("offer", "max_price"),
    "give": ("item", "target"),
    "attempt": ("check",),
    "activate": ("ability",),
    "talk": ("target", "topic"),
    "tell": ("target", "fact"),
    "train": ("training",),
    "experience": ("experience",),
    "attack": ("target", "move"),
    "befriend": ("encounter",),
    "accept_quest": ("quest",),
    "claim_quest": ("quest",),
}


def validate_action(action, args, view):
    if (
        not isinstance(action, str)
        or action not in ACTION_ARGS
        or action not in view["actions"]
        or not isinstance(args, dict)
    ):
        raise InvalidAction("Action non autorisée.")
    if set(args) - set(ACTION_ARGS[action]):
        raise InvalidAction("Arguments non autorisés.")
    required = set(ACTION_ARGS[action]) - ({"topic"} if action == "talk" else set())
    if required - set(args):
        raise InvalidAction("Arguments manquants.")
    for key, value in args.items():
        if key in {"seconds", "max_price"}:
            integer(value, key, 1 if key == "seconds" else 0, 604800 if key == "seconds" else 10**12)
        elif not isinstance(value, str) or not value or len(value) > 200:
            raise InvalidAction("Identifiant d'action invalide.")
    references = {
        "route": "routes",
        "offer": "offers",
        "check": "checks",
        "ability": "abilities",
        "training": "training",
        "experience": "experiences",
        "move": "moves",
        "encounter": "encounters",
        "quest": "quests",
    }
    for key, collection in references.items():
        if key in args and args[key] not in {entry["id"] for entry in view[collection]}:
            raise InvalidAction("Élément absent du contexte autorisé.")
    if "target" in args and args["target"] not in {entry["id"] for entry in view["nearby"]}:
        raise InvalidAction("Personnage inaccessible.")
    if "fact" in args and args["fact"] not in {entry["id"] for entry in view["beliefs"]}:
        raise InvalidAction("Le personnage ne possède pas cette connaissance.")
    if "topic" in args:
        target = next(entry for entry in view["nearby"] if entry["id"] == args["target"])
        topics = set(target.get("topics", [])) | {entry["id"] for entry in view["beliefs"]}
        if args["topic"] not in topics:
            raise InvalidAction("Sujet absent du contexte autorisé.")
    if "item" in args:
        known = {entry["id"] for entry in view["inventory"]} | {entry["item"] for entry in view["offers"]}
        if args["item"] not in known:
            raise InvalidAction("Objet inconnu.")

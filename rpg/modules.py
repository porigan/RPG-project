"""Optional mechanics. Handlers propose changes through the kernel's event boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .engine import effect_active
from .model import InvalidAction, Registry, identifier, integer, probability


@dataclass
class RuleModule:
    name: str
    actions: dict[str, Callable]
    dependencies: tuple[str, ...] = ()
    version: str = "1.0"
    collections: tuple[str, ...] = ()
    validators: dict[str, Callable] = field(default_factory=dict)


def require_fields(args, allowed, required=()):
    if set(args) - set(allowed) or set(required) - set(args):
        raise InvalidAction(
            f"Arguments attendus : {', '.join(allowed)} ; obligatoires : {', '.join(required)}."
        )


def wait(ctx, actor, args):
    require_fields(args, ("seconds",), ("seconds",))
    seconds = integer(args["seconds"], "durée", 1, 604800)
    ctx.advance(seconds)
    return {"waited": seconds}


def travel(ctx, actor, args):
    require_fields(args, ("route",), ("route",))
    route = ctx.state.get("routes", identifier(args["route"]))
    if actor["location"] != route["origin"]:
        raise InvalidAction("Cette route ne part pas du lieu actuel.")
    required = route.get("required_ability")
    effect = None
    if required:
        active = [
            e
            for e in ctx.state.find("effects", actor=actor["id"], status="active")
            if e["ability"] == required and effect_active(ctx.state, e["id"])
        ]
        if not active:
            raise InvalidAction("Le déplacement exige une capacité actuellement active.")
        effect = active[0]["id"]
    start = ctx.state.now
    ctx.emit("travel.started", {"actor": actor["id"], "route": route["id"]})
    completed = ctx.advance(route["duration"], effect)
    actor = ctx.state.get("entities", actor["id"])
    if not completed:
        ctx.emit(
            "travel.interrupted",
            {"actor": actor["id"], "route": route["id"], "reason": "required_effect_expired"},
        )
        return {
            "status": "interrupted",
            "reason": "required_effect_expired",
            "elapsed": ctx.state.now - start,
        }
    actor["location"] = route["destination"]
    updates = [("entities", actor["id"], actor)]
    for item in ctx.state.find("items", actor=actor["id"]):
        item["location"] = actor["location"]
        updates.append(("items", item["id"], item))
    ctx.emit("travel.completed", {"actor": actor["id"], "destination": actor["location"]}, updates)
    return {
        "destination": actor["location"],
        "destination_name": ctx.state.get("locations", actor["location"])["name"],
        "elapsed": ctx.state.now - start,
    }


def buy(ctx, actor, args):
    require_fields(args, ("offer", "max_price"), ("offer", "max_price"))
    offer = ctx.state.get("offers", identifier(args["offer"]))
    from .knowledge import materialize

    materialize(ctx, offer["actor"])
    offer = ctx.state.get("offers", offer["id"])
    maximum = integer(args["max_price"], "prix maximal")
    seller = ctx.state.get("entities", offer["actor"])
    if seller["id"] == actor["id"]:
        raise InvalidAction("Un personnage ne peut pas acheter son propre objet.")
    if seller["location"] != actor["location"] or offer["status"] != "active":
        raise InvalidAction("Offre inaccessible.")
    if offer["price"] > maximum:
        ctx.emit(
            "purchase.price_discovered", {"actor": actor["id"], "offer": offer["id"], "price": offer["price"]}
        )
        return {"status": "interrupted", "reason": "price_exceeds_consent", "price": offer["price"]}
    currency = offer["currency"]
    balance = ctx.state.get("resources", f"{actor['id']}:{currency}")
    if balance["value"] < offer["price"]:
        ctx.emit(
            "purchase.declined", {"actor": actor["id"], "offer": offer["id"], "reason": "insufficient_funds"}
        )
        return {"status": "interrupted", "reason": "insufficient_funds", "price": offer["price"]}
    item = ctx.state.get("items", offer["item"])
    if item["owner"] != seller["id"] or item["holder"] != seller["id"]:
        raise InvalidAction("Le vendeur ne peut pas transférer cet objet.")
    item.update(owner=actor["id"], holder=actor["id"], actor=actor["id"], location=actor["location"])
    offer["status"] = "sold"
    ctx.emit(
        "purchase.completed",
        {
            "buyer": actor["id"],
            "seller": seller["id"],
            "item": item["id"],
            "price": offer["price"],
            "currency": currency,
        },
        [
            ctx.resource_delta(actor["id"], currency, -offer["price"]),
            ctx.resource_delta(seller["id"], currency, offer["price"]),
            ("items", item["id"], item),
            ("offers", offer["id"], offer),
        ],
    )
    return {"item": item["id"], "item_name": item["name"], "price": offer["price"]}


def give(ctx, actor, args):
    require_fields(args, ("item", "target"), ("item", "target"))
    target = ctx.state.get("entities", identifier(args["target"]))
    item = ctx.state.get("items", identifier(args["item"]))
    if target["id"] == actor["id"] or target["location"] != actor["location"]:
        raise InvalidAction("Destinataire inaccessible.")
    if item["owner"] != actor["id"] or item["holder"] != actor["id"]:
        raise InvalidAction("Cet objet ne peut pas être donné.")
    item.update(owner=target["id"], holder=target["id"], actor=target["id"])
    updates = [("items", item["id"], item)]
    for offer in ctx.state.find("offers", subject=item["id"], status="active"):
        offer["status"] = "cancelled"
        updates.append(("offers", offer["id"], offer))
    ctx.emit("item.given", {"actor": actor["id"], "target": target["id"], "item": item["id"]}, updates)
    return {"item": item["id"], "target": target["id"]}


def attempt(ctx, actor, args):
    require_fields(args, ("check",), ("check",))
    check = ctx.state.get("checks", identifier(args["check"]))
    if check["location"] != actor["location"]:
        raise InvalidAction("Cette tentative n'est pas possible ici.")
    if check.get("once") and ctx.state.get("achievements", f"{actor['id']}:{check['id']}", required=False):
        raise InvalidAction("Cette action a déjà réussi.")
    updates = []
    if check.get("cost"):
        cost = check["cost"]
        updates.append(ctx.resource_delta(actor["id"], cost["resource"], -cost["amount"]))
    ctx.emit("attempt.started", {"actor": actor["id"], "check": check["id"]}, updates)
    ctx.advance(check["duration"])
    roll = ctx.draw(check["id"], probability(check["probability"]))
    updates = []
    if roll["success"] and check.get("once"):
        key = f"{actor['id']}:{check['id']}"
        updates.append(("achievements", key, {"id": key, "actor": actor["id"], "subject": check["id"]}))
    if roll["success"] and check.get("reward"):
        reward = check["reward"]
        updates.append(ctx.resource_delta(actor["id"], reward["resource"], reward["amount"]))
    ctx.emit("attempt.resolved", {"actor": actor["id"], "check": check["id"], "roll": roll}, updates)
    return {"success": roll["success"], "check": check["id"]}


def activate(ctx, actor, args):
    require_fields(args, ("ability",), ("ability",))
    ability = ctx.state.get("abilities", identifier(args["ability"]))
    if actor["id"] not in ability["actors"]:
        raise InvalidAction("Ce personnage ne possède pas cette capacité.")
    effect_id = f"{actor['id']}:{ability['id']}"
    if effect_active(ctx.state, effect_id):
        raise InvalidAction("Capacité déjà active.")
    expiry = ctx.state.now + ability["duration"]
    effect = {
        "id": effect_id,
        "actor": actor["id"],
        "ability": ability["id"],
        "status": "active",
        "expires_at": expiry,
    }
    trigger_id = f"expiry:{effect_id}"
    previous = ctx.state.get("triggers", trigger_id, required=False)
    trigger = {
        "id": trigger_id,
        "status": "active",
        "mode": "time",
        "due": expiry,
        "version": previous["version"] + 1 if previous else 1,
        "priority": -100,
        "action": {"kind": "effect.expire", "effect": effect_id},
    }
    updates = [("effects", effect_id, effect), ("triggers", trigger_id, trigger)]
    if ability.get("cost"):
        cost = ability["cost"]
        updates.append(ctx.resource_delta(actor["id"], cost["resource"], -cost["amount"]))
    ctx.emit(
        "ability.activated", {"actor": actor["id"], "ability": ability["id"], "expires_at": expiry}, updates
    )
    return {"ability": ability["id"], "expires_at": expiry}


def train(ctx, actor, args):
    require_fields(args, ("training",), ("training",))
    definition = ctx.state.get("training", identifier(args["training"]))
    if definition["location"] != actor["location"]:
        raise InvalidAction("Entraînement inaccessible.")
    ctx.emit("training.started", {"actor": actor["id"], "training": definition["id"]})
    ctx.advance(definition["duration"])
    key = f"{actor['id']}:{definition['attribute']}"
    stat = ctx.state.get("attributes", key)
    stat["value"] = min(stat.get("maximum", 10**12), stat["value"] + definition["gain"])
    ctx.emit(
        "training.completed",
        {"actor": actor["id"], "attribute": definition["attribute"]},
        [("attributes", key, stat)],
    )
    return {"attribute": definition["attribute"], "value": stat["value"]}


def attack(ctx, actor, args):
    require_fields(args, ("target", "move"), ("target", "move"))
    target = ctx.state.get("entities", identifier(args["target"]))
    move = ctx.state.get("moves", identifier(args["move"]))
    if (
        actor["id"] not in move["actors"]
        or target["id"] == actor["id"]
        or target["location"] != actor["location"]
    ):
        raise InvalidAction("Attaque inaccessible.")
    own_hp = ctx.state.get("attributes", f"{actor['id']}:hp")
    hp = ctx.state.get("attributes", f"{target['id']}:hp")
    if own_hp["value"] == 0 or hp["value"] == 0:
        raise InvalidAction("Un participant est hors combat.")
    cost = move["cost"]
    ctx.emit(
        "combat.started",
        {"actor": actor["id"], "target": target["id"]},
        [ctx.resource_delta(actor["id"], cost["resource"], -cost["amount"])],
    )
    ctx.advance(move["duration"])
    # Re-read after timed effects: a target may have moved or changed state.
    target = ctx.state.get("entities", target["id"])
    if target["location"] != ctx.state.get("entities", actor["id"])["location"]:
        ctx.emit("combat.interrupted", {"actor": actor["id"], "reason": "target_left"})
        return {"status": "interrupted", "reason": "target_left"}
    hp = ctx.state.get("attributes", f"{target['id']}:hp")
    roll = ctx.draw(f"attack:{move['id']}:{target['id']}", move["probability"])
    if roll["success"]:
        hp["value"] = max(0, hp["value"] - move["damage"])
    ctx.emit(
        "combat.resolved",
        {
            "actor": actor["id"],
            "target": target["id"],
            "roll": roll,
            "damage": move["damage"] if roll["success"] else 0,
        },
        [("attributes", hp["id"], hp)],
    )
    return {"success": roll["success"], "target": target["id"], "hp": hp["value"]}


def default_registry():
    from .knowledge import experience, observe, talk, tell

    return Registry(
        [
            RuleModule(
                "core",
                {"wait": wait, "travel": travel, "observe": observe},
                collections=(
                    "world",
                    "locations",
                    "entities",
                    "routes",
                    "resources",
                    "attributes",
                    "triggers",
                    "observations",
                    "plans",
                    "turns",
                ),
            ),
            RuleModule("inventory", {"give": give}, ("core",), collections=("items",)),
            RuleModule("economy", {"buy": buy}, ("inventory",), collections=("offers",)),
            RuleModule("chance", {"attempt": attempt}, ("core",), collections=("checks", "achievements")),
            RuleModule("abilities", {"activate": activate}, ("core",), collections=("abilities", "effects")),
            RuleModule(
                "memory",
                {"tell": tell, "talk": talk, "experience": experience},
                ("core",),
                collections=(
                    "facts",
                    "beliefs",
                    "belief_history",
                    "relations",
                    "opportunities",
                    "behaviors",
                    "experiences",
                    "experiences_done",
                ),
            ),
            RuleModule("progression", {"train": train}, ("core",), collections=("training",)),
            RuleModule("combat", {"attack": attack}, ("core",), collections=("moves",)),
            RuleModule(
                "creatures", {"befriend": befriend}, ("core",), collections=("encounters", "companions")
            ),
            RuleModule(
                "quests",
                {"accept_quest": accept_quest, "claim_quest": claim_quest},
                ("chance",),
                collections=("quests", "quest_progress"),
            ),
        ]
    )


def befriend(ctx, actor, args):
    require_fields(args, ("encounter",), ("encounter",))
    encounter = ctx.state.get("encounters", identifier(args["encounter"]))
    creature = ctx.state.get("entities", encounter["subject"])
    if encounter["location"] != actor["location"] or creature["location"] != actor["location"]:
        raise InvalidAction("Cette créature est inaccessible.")
    if ctx.state.find("companions", subject=creature["id"]):
        raise InvalidAction("Cette créature a déjà un compagnon.")
    cost = encounter["cost"]
    ctx.emit(
        "creature.approach_started",
        {"actor": actor["id"], "creature": creature["id"]},
        [ctx.resource_delta(actor["id"], cost["resource"], -cost["amount"])],
    )
    ctx.advance(encounter["duration"])
    if ctx.state.get("entities", creature["id"])["location"] != actor["location"]:
        ctx.emit("creature.approach_interrupted", {"actor": actor["id"], "reason": "target_left"})
        return {"status": "interrupted", "reason": "target_left"}
    roll = ctx.draw(f"befriend:{encounter['id']}", encounter["probability"])
    updates = []
    if roll["success"]:
        key = f"companion:{creature['id']}"
        updates.append(("companions", key, {"id": key, "actor": actor["id"], "subject": creature["id"]}))
    ctx.emit(
        "creature.approach_resolved",
        {"actor": actor["id"], "creature": creature["id"], "roll": roll},
        updates,
    )
    return {"success": roll["success"], "creature": creature["id"]}


def accept_quest(ctx, actor, args):
    require_fields(args, ("quest",), ("quest",))
    quest = ctx.state.get("quests", identifier(args["quest"]))
    if quest["location"] != actor["location"]:
        raise InvalidAction("Cette proposition n'est pas accessible ici.")
    key = f"{actor['id']}:{quest['id']}"
    if ctx.state.get("quest_progress", key, required=False):
        raise InvalidAction("Cette quête a déjà été acceptée.")
    ctx.emit(
        "quest.accepted",
        {"actor": actor["id"], "quest": quest["id"]},
        [
            (
                "quest_progress",
                key,
                {"id": key, "actor": actor["id"], "subject": quest["id"], "status": "accepted"},
            )
        ],
    )
    return {"quest": quest["id"], "quest_status": "accepted"}


def claim_quest(ctx, actor, args):
    require_fields(args, ("quest",), ("quest",))
    quest = ctx.state.get("quests", identifier(args["quest"]))
    key = f"{actor['id']}:{quest['id']}"
    progress = ctx.state.get("quest_progress", key)
    if actor["location"] != quest["location"] or progress["status"] != "accepted":
        raise InvalidAction("Cette quête ne peut pas être rendue ici.")
    if not ctx.state.get("achievements", f"{actor['id']}:{quest['goal_check']}", required=False):
        raise InvalidAction("L'objectif de la quête n'est pas encore accompli.")
    progress["status"] = "completed"
    reward = quest["reward"]
    ctx.emit(
        "quest.completed",
        {"actor": actor["id"], "quest": quest["id"]},
        [
            ("quest_progress", key, progress),
            ctx.resource_delta(actor["id"], reward["resource"], reward["amount"]),
        ],
    )
    return {"quest": quest["id"], "quest_status": "completed"}

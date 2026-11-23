"""Validated, data-only universe loading. Configurations cannot execute Python."""

from __future__ import annotations

import copy
import json
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .engine import validate_changes
from .model import InvalidAction, component, digest, identifier, integer, probability

CATEGORIES = {
    "locations",
    "entities",
    "routes",
    "items",
    "resources",
    "attributes",
    "offers",
    "abilities",
    "checks",
    "facts",
    "beliefs",
    "relations",
    "opportunities",
    "behaviors",
    "training",
    "moves",
    "experiences",
    "triggers",
    "encounters",
    "quests",
}


def validate_trigger(trigger):
    if not isinstance(trigger, dict):
        raise InvalidAction("Déclencheur attendu.")
    identifier(trigger.get("id"))
    if trigger.get("mode") not in {"time", "event", "condition"} or trigger.get("status") not in {
        "active",
        "cancelled",
        "consumed",
    }:
        raise InvalidAction("Mode ou statut de déclencheur invalide.")
    integer(trigger.get("priority", 0), "priorité", -1000, 1000)
    if trigger["mode"] == "time":
        integer(trigger.get("due"), "échéance")
    if trigger.get("interval") is not None:
        integer(trigger["interval"], "intervalle", 1)
        if trigger["mode"] != "time":
            raise InvalidAction("Seuls les déclencheurs temporels peuvent se répéter.")
    if trigger["mode"] == "event":
        identifier(trigger.get("subject"), "type d'événement")
    if trigger["mode"] == "condition":
        condition = trigger.get("condition", {})
        for name in ("category", "key", "field"):
            identifier(condition.get(name))
        if condition.get("op") not in {"eq", "gte", "lte"} or "value" not in condition:
            raise InvalidAction("Condition invalide.")
        trigger["subject"] = f"{condition['category']}/{condition['key']}"
    action = trigger.get("action", {})
    if action.get("kind") == "effect.expire":
        identifier(action.get("effect"))
    elif action.get("kind") == "resource.delta":
        for name in ("actor", "resource", "reason"):
            identifier(action.get(name))
        integer(action.get("amount"), "variation", -(10**9), 10**9)
    elif action.get("kind") == "npc.move":
        identifier(action.get("actor"))
        identifier(action.get("destination"))
    else:
        raise InvalidAction("Action de déclencheur non prise en charge.")
    trigger.setdefault("version", 1)


def load_file(path):
    path = Path(path)
    if path.stat().st_size > 2_000_000:
        raise InvalidAction("Configuration trop volumineuse (2 Mo maximum).")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise InvalidAction(f"Clé JSON dupliquée : {key}.")
            result[key] = value
        return result

    def reject_constant(value):
        raise InvalidAction(f"Nombre JSON invalide : {value}.")

    with path.open(encoding="utf-8-sig") as stream:
        return json.load(stream, object_pairs_hook=unique, parse_constant=reject_constant)


def initialize(engine, config: dict, branch: str = "main", seed: str | None = None):
    try:
        if not isinstance(config, dict):
            raise InvalidAction("Un univers doit être un objet JSON.")
        return _initialize(engine, config, branch, seed)
    except InvalidAction:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError):
        raise InvalidAction("Configuration d'univers incomplète ou mal formée.") from None


def _initialize(engine, config: dict, branch: str = "main", seed: str | None = None):
    config = copy.deepcopy(config)
    if seed is not None:
        identifier(seed, "graine de simulation")
    if config.get("schema") != 1:
        raise InvalidAction("Version d'univers non prise en charge.")
    identifier(config.get("id"))
    identifier(config.get("title"))
    identifier(config.get("player"))
    enabled = config.get("modules", ["core"])
    if not isinstance(enabled, list) or len(enabled) != len(set(enabled)) or "core" not in enabled:
        raise InvalidAction("Liste de modules invalide.")
    for name in enabled:
        if name not in engine.registry.modules:
            raise InvalidAction(f"Module inconnu : {name}.")
        if set(engine.registry.modules[name].dependencies) - set(enabled):
            raise InvalidAction(f"Dépendance désactivée pour {name}.")
    records = config.get("records", {})
    if set(records) - (CATEGORIES | set(engine.registry.owners)):
        raise InvalidAction("Collection d'univers inconnue.")
    for category in records:
        owner = engine.registry.owners.get(category)
        if owner not in enabled:
            raise InvalidAction(f"Le module de la collection {category} n'est pas activé.")
    start = integer(config.get("start_time", 0), "temps initial")
    updates = [
        ("world", "clock", {"id": "clock", "value": start}),
        ("world", "seed", {"id": "seed", "value": seed or secrets.token_hex(32)}),
        (
            "world",
            "config",
            {
                "id": "config",
                "universe": config["id"],
                "title": config["title"],
                "player": config["player"],
                "modules": {n: engine.registry.modules[n].version for n in enabled},
            },
        ),
    ]
    seen = set()
    for category, entries in records.items():
        if not isinstance(entries, list):
            raise InvalidAction("Les collections doivent être des listes.")
        for record in entries:
            identifier(record.get("id"))
            if category in {"facts", "abilities", "checks", "quests", "experiences"}:
                component(record["id"])
            ref = (category, record["id"])
            if ref in seen:
                raise InvalidAction(f"Identifiant dupliqué : {ref}.")
            seen.add(ref)
            updates.append((category, record["id"], record))
    if len(updates) > 10_000:
        raise InvalidAction("Trop d'enregistrements initiaux (10 000 maximum).")
    store = engine.store
    with store.transaction():
        store.create_root(branch)
        state = store.state(branch)
        changes = [{"category": c, "key": k, "before": None, "after": v} for c, k, v in updates]
        state.apply(changes)
        validate_changes(state, changes)
        engine.validate_extensions(state, changes)
        validate_universe(state, records, config["player"], start)
        # Seeded beliefs are historical evidence too, not merely a current cache.
        for belief in records.get("beliefs", []):
            historical = dict(belief, id=f"initial:{belief['id']}", sequence=1)
            changes.append(
                {"category": "belief_history", "key": historical["id"], "before": None, "after": historical}
            )
        event = {
            "id": str(uuid.uuid4()),
            "schema": 1,
            "kind": "world.created",
            "command_id": None,
            "actor": "system",
            "effective_at": start,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "causes": [],
            "data": {"universe": config["id"], "config_hash": digest(config)},
            "changes": changes,
            "rules": state.get("world", "config")["modules"],
        }
        store.commit_events(branch, [event], 0)


def validate_universe(state, records, player, start):
    if state.get("entities", player)["kind"] != "player":
        raise InvalidAction("Le personnage principal doit être un joueur.")
    production_keys = [(b["actor"], b["resource"]) for b in records.get("behaviors", [])]
    if len(production_keys) != len(set(production_keys)):
        raise InvalidAction("Une seule politique de production est autorisée par personnage et ressource.")
    for category, entries in records.items():
        for row in entries:
            if category == "entities":
                identifier(row.get("name"))
                if row.get("kind") not in {"player", "npc", "creature"}:
                    raise InvalidAction("Type de personnage invalide.")
                if not isinstance(row.get("personality", []), list) or not all(
                    isinstance(p, str) for p in row.get("personality", [])
                ):
                    raise InvalidAction("La personnalité doit être une liste descriptive.")
                for fact in row.get("shareable_facts", []):
                    state.get("facts", fact)
            elif category == "locations":
                identifier(row.get("name"))
                if not isinstance(row.get("description"), str):
                    raise InvalidAction("Description du lieu absente.")
            elif category == "items":
                identifier(row.get("name"))
                if row.get("actor") != row.get("holder"):
                    raise InvalidAction("Index du détenteur incohérent.")
                if row.get("holder") and state.get("entities", row["holder"])["location"] != row["location"]:
                    raise InvalidAction("Objet et détenteur ne sont pas au même endroit.")
            elif category == "resources":
                if row["id"] != f"{row['actor']}:{row['resource']}":
                    raise InvalidAction("Identifiant de ressource incohérent.")
            elif category == "attributes":
                if row["id"] != f"{row['actor']}:{row['attribute']}":
                    raise InvalidAction("Identifiant de caractéristique incohérent.")
            elif category == "routes":
                state.get("locations", row["origin"])
                state.get("locations", row["destination"])
                if row.get("location") != row["origin"]:
                    raise InvalidAction("Index de route incohérent.")
                integer(row["duration"], "durée", 1, 604800)
                if row.get("required_ability"):
                    state.get("abilities", row["required_ability"])
            elif category == "offers":
                item = state.get("items", row["item"])
                seller = state.get("entities", row["actor"])
                if (
                    row.get("subject") != row["item"]
                    or item["owner"] != seller["id"]
                    or row["location"] != seller["location"]
                ):
                    raise InvalidAction("Offre incohérente.")
                if row["status"] not in {"active", "sold", "cancelled"}:
                    raise InvalidAction("Statut d'offre invalide.")
                integer(row["price"], "prix")
                state.get("resources", f"{seller['id']}:{row['currency']}")
            elif category in {"abilities", "checks", "training", "moves", "experiences"}:
                integer(row["duration"], "durée", 1, 604800)
                if category in {"checks", "moves"}:
                    probability(row["probability"])
                if category in {"checks", "training", "experiences"}:
                    state.get("locations", row["location"])
                for actor in row.get("actors", []):
                    state.get("entities", actor)
                if row.get("cost"):
                    identifier(row["cost"]["resource"])
                    integer(row["cost"]["amount"], "coût", 1)
                if row.get("reward"):
                    identifier(row["reward"]["resource"])
                    integer(row["reward"]["amount"], "récompense", 1)
                if category == "training":
                    integer(row["gain"], "gain", 1)
                    identifier(row["attribute"])
                if category == "moves":
                    integer(row["damage"], "dégâts")
                    if "cost" not in row:
                        raise InvalidAction("Une attaque doit déclarer son coût.")
                if category == "experiences":
                    if not isinstance(row["personality_add"], list) or not all(
                        isinstance(p, str) for p in row["personality_add"]
                    ):
                        raise InvalidAction("Évolution de personnalité invalide.")
                    identifier(row["memory"], "souvenir")
            elif category == "facts":
                identifier(row["statement"], "énoncé")
                state.get("locations", row["location"])
            elif category == "beliefs":
                fact = state.get("facts", row["subject"])
                state.get("entities", row["actor"])
                if row["id"] != f"{row['actor']}:{row['subject']}" or row["statement"] != fact["statement"]:
                    raise InvalidAction("Croyance incohérente.")
                integer(row["acquired_at"], "date d'apprentissage", 0, start)
                if "source" not in row or "certainty" not in row:
                    raise InvalidAction("Provenance de croyance absente.")
            elif category == "relations":
                if row["id"] != f"{row['actor']}:{row['subject']}":
                    raise InvalidAction("Relation incohérente.")
                row.setdefault("updated_at", start)
            elif category == "opportunities":
                state.get("entities", row["actor"])
                state.get("entities", row["subject"])
                state.get("facts", row["fact"])
                state.get("locations", row["location"])
                integer(row["start"], "début d'occasion", start)
                integer(row["due"], "fin d'occasion", row["start"] + 1)
                probability(row["probability"])
                integer(row.get("minimum_familiarity", 0), "familiarité minimale", 0, 100)
                if row["actor"] == row["subject"] or row["status"] != "pending":
                    raise InvalidAction("Occasion de communication invalide.")
            elif category == "behaviors":
                entity = state.get("entities", row["actor"])
                if entity["kind"] == "player" or row["status"] != "active":
                    raise InvalidAction("Comportement autonome invalide.")
                state.get("resources", f"{row['actor']}:{row['resource']}")
                integer(row["last_at"], "dernière actualisation", 0, start)
                integer(row["interval"], "intervalle", 1)
                integer(row["amount"], "production", 1)
                integer(row["capacity"], "capacité", 1)
            elif category == "triggers":
                validate_trigger(row)
                if row["mode"] == "time" and row["due"] < start:
                    raise InvalidAction("Déclencheur initial dans le passé.")
            elif category == "encounters":
                state.get("locations", row["location"])
                if state.get("entities", row["subject"])["kind"] != "creature":
                    raise InvalidAction("Une rencontre doit désigner une créature.")
                identifier(row["name"])
                probability(row["probability"])
                integer(row["duration"], "durée", 1, 604800)
                integer(row["cost"]["amount"], "coût", 1)
                identifier(row["cost"]["resource"])
            elif category == "quests":
                state.get("locations", row["location"])
                identifier(row["name"])
                identifier(row["description"])
                if not state.get("checks", row["goal_check"]).get("once"):
                    raise InvalidAction("L'objectif doit être un test à réussite persistante.")
                identifier(row["reward"]["resource"])
                integer(row["reward"]["amount"], "récompense", 1)

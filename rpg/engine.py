"""Command boundary, event construction, deterministic clock and causal triggers."""

from __future__ import annotations

import copy
import hashlib
import hmac
import uuid
from datetime import datetime, timezone

from .model import (
    Command,
    Conflict,
    InvalidAction,
    LimitExceeded,
    Registry,
    canonical,
    component,
    digest,
    identifier,
    integer,
    probability,
)
from .store import Store


class Context:
    def __init__(self, engine: Engine, branch: str, command: Command):
        self.engine, self.branch, self.command = engine, branch, command
        self.state = engine.store.state(branch)
        self.events: list[dict] = []
        self._reacted = 0
        self._reacting = False
        self._causes: list[str] = []
        self.work = 0
        self._last_ref: dict[tuple[str, str], str] = {}

    def spend(self, amount: int = 1) -> None:
        self.work += amount
        if self.work > self.engine.budget:
            raise LimitExceeded(
                "Budget de résolution épuisé ; commande annulée, traitement toujours en attente."
            )

    def emit(
        self,
        kind: str,
        data: dict | None = None,
        updates: list[tuple[str, str, dict | None]] | None = None,
        effective_at: int | None = None,
        causes: list[str] | None = None,
    ) -> dict:
        self.spend()
        changes = []
        seen = set()
        for category, key, after in updates or []:
            if category not in self.engine.registry.owners:
                raise InvalidAction(f"Collection non déclarée par les modules : {category}.")
            if self.engine.registry.owners[category] not in self.state.get("world", "config")["modules"]:
                raise InvalidAction(f"Module de collection désactivé : {category}.")
            if (category, key) in seen:
                raise InvalidAction("Un événement ne peut modifier deux fois le même enregistrement.")
            seen.add((category, key))
            if after is not None and after.get("id") != key:
                raise InvalidAction("Identité de projection incohérente.")
            before = self.state.get(category, key, required=False)
            if before != after:
                changes.append(
                    {"category": category, "key": key, "before": before, "after": copy.deepcopy(after)}
                )
        event_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, canonical(["rpg", self.branch, self.command.id, len(self.events)]))
        )
        clock_after = next(
            (c["after"]["value"] for c in changes if c["category"] == "world" and c["key"] == "clock"),
            self.state.now,
        )
        at = (
            clock_after
            if effective_at is None
            else integer(effective_at, "date d'événement", 0, max(clock_after, self.state.now))
        )
        derived_causes = list(self._causes)
        if self.events:
            derived_causes.append(self.events[-1]["id"])
        for change in changes:
            ref = (change["category"], change["key"])
            cause = self._last_ref.get(ref) or self.engine.store.last_event_for(self.branch, *ref)
            if cause:
                local = next((e for e in reversed(self.events) if e["id"] == cause), None)
                dependency_time = (
                    local["effective_at"] if local else self.engine.store.event(cause)["effective_at"]
                )
                if dependency_time > at:
                    raise Conflict(
                        "Une modification rétrospective contredirait un état plus récent ; audit requis."
                    )
                derived_causes.append(cause)

        # Append order is not fictive time order. A later-established historical
        # fact must never cite an event from its own fictive future as a cause.
        def cause_time(event_id):
            local = next((e for e in reversed(self.events) if e["id"] == event_id), None)
            return local["effective_at"] if local else self.engine.store.event(event_id)["effective_at"]

        derived_causes = [c for c in derived_causes if cause_time(c) <= at]
        event = {
            "id": event_id,
            "schema": 1,
            "kind": kind,
            "command_id": self.command.id,
            "actor": self.command.actor,
            "effective_at": at,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "causes": sorted(set(causes if causes is not None else derived_causes)),
            "data": copy.deepcopy(data or {}),
            "changes": changes,
            "rules": self.state.get("world", "config")["modules"],
        }
        self.state.apply(changes)
        validate_changes(self.state, changes)
        self.engine.validate_extensions(self.state, changes)
        self.events.append(event)
        for change in changes:
            self._last_ref[(change["category"], change["key"])] = event_id
        self._react()
        return event

    def draw(self, label: str, chance: float, attempt_id: str | None = None) -> dict:
        """Independent draw per attempt, branch and label; replay uses stored results."""
        chance = probability(chance)
        seed = self.state.get("world", "seed")["value"]
        message = canonical([self.branch, attempt_id or self.command.id, label]).encode()
        number = int.from_bytes(hmac.new(seed.encode(), message, hashlib.sha256).digest()[:8], "big")
        value = (number >> 11) / 2**53
        return {
            "label": label,
            "probability": chance,
            "sample": value,
            "success": value < chance,
            "algorithm": "hmac-sha256-u53-v1",
        }

    def resource_delta(self, actor: str, resource: str, amount: int) -> tuple[str, str, dict]:
        key = f"{actor}:{resource}"
        record = self.state.get("resources", key)
        record["value"] += amount
        return "resources", key, record

    def _react(self) -> None:
        if self._reacting:
            return
        self._reacting = True
        try:
            while self._reacted < len(self.events):
                event = self.events[self._reacted]
                self._reacted += 1
                self.spend()
                candidates = self.state.find("triggers", status="active", mode="event", subject=event["kind"])
                changed = {(c["category"], c["key"]) for c in event["changes"]}
                for category, key in sorted(changed):
                    candidates += self.state.find(
                        "triggers", status="active", mode="condition", subject=f"{category}/{key}"
                    )
                for trigger in sorted(
                    {t["id"]: t for t in candidates}.values(), key=lambda t: (t.get("priority", 0), t["id"])
                ):
                    self.spend()
                    current = self.state.get("triggers", trigger["id"])
                    if current["status"] != "active":
                        continue
                    if current["mode"] == "condition" and not self._condition(current["condition"]):
                        continue
                    self.fire(current, [event["id"]])
        finally:
            self._reacting = False

    def _condition(self, condition: dict) -> bool:
        value = self.state.get(condition["category"], condition["key"], required=False)
        if value is None:
            return False
        actual = value.get(condition["field"])
        expected = condition["value"]
        operation = condition["op"]
        if operation == "eq":
            return actual == expected
        if type(actual) not in (int, float) or type(expected) not in (int, float):
            return False
        return actual >= expected if operation == "gte" else actual <= expected

    def fire(self, trigger: dict, causes: list[str]) -> None:
        self.spend()
        trigger = copy.deepcopy(trigger)
        if trigger.get("interval"):
            trigger["due"] += trigger["interval"]
        else:
            trigger["status"] = "consumed"
        trigger["firings"] = trigger.get("firings", 0) + 1
        previous = self._causes
        # Mark consumed before running the action, preventing reentrant one-shot firings.
        event = self.emit(
            "trigger.fired",
            {"trigger": trigger["id"], "version": trigger["version"]},
            [("triggers", trigger["id"], trigger)],
            causes=causes,
        )
        self._causes = [event["id"]]
        try:
            action = trigger["action"]
            kind = action["kind"]
            if kind == "effect.expire":
                effect = self.state.get("effects", action["effect"], required=False)
                if effect and effect["status"] == "active" and effect["expires_at"] <= self.state.now:
                    effect["status"] = "expired"
                    self.emit(
                        "effect.expired",
                        {"effect": effect["id"], "actor": effect["actor"]},
                        [("effects", effect["id"], effect)],
                    )
            elif kind == "resource.delta":
                from .knowledge import materialize

                if self.state.get("entities", action["actor"])["kind"] != "player":
                    materialize(self, action["actor"])
                self.emit(
                    "resource.changed",
                    {"reason": action["reason"], "actor": action["actor"]},
                    [self.resource_delta(action["actor"], action["resource"], action["amount"])],
                )
            elif kind == "npc.move":
                actor = self.state.get("entities", action["actor"])
                if actor["kind"] == "player":
                    raise InvalidAction("Un déclencheur ne peut décider un déplacement du joueur.")
                self.state.get("locations", action["destination"])
                actor["location"] = action["destination"]
                updates = [("entities", actor["id"], actor)]
                for item in self.state.find("items", actor=actor["id"]):
                    item["location"] = actor["location"]
                    updates.append(("items", item["id"], item))
                for offer in self.state.find("offers", actor=actor["id"], status="active"):
                    offer["location"] = actor["location"]
                    updates.append(("offers", offer["id"], offer))
                self.emit("npc.moved", {"actor": actor["id"]}, updates)
            else:
                raise InvalidAction(f"Action de déclencheur non autorisée : {kind}.")
        finally:
            self._causes = previous

    def advance(self, seconds: int, required_effect: str | None = None) -> bool:
        integer(seconds, "durée", 0, 31_536_000)
        target = self.state.now + seconds
        while True:
            due = self.state.find("triggers", status="active", mode="time", due_lte=target)
            if not due:
                break
            trigger = min(due, key=lambda t: (t["due"], t.get("priority", 0), t["id"]))
            self.spend()
            if trigger["due"] < self.state.now:
                raise Conflict("Échéance en retard : audit requis avant de poursuivre.")
            if self.state.now < trigger["due"]:
                self.emit(
                    "time.advanced",
                    {"to": trigger["due"]},
                    [("world", "clock", {"id": "clock", "value": trigger["due"]})],
                )
            current = self.state.get("triggers", trigger["id"])
            if (
                current["status"] == "active"
                and current["version"] == trigger["version"]
                and current["due"] <= self.state.now
            ):
                self.fire(current, [self.events[-1]["id"]] if self.events else [])
            if required_effect and not effect_active(self.state, required_effect):
                return False
        if self.state.now < target:
            self.emit("time.advanced", {"to": target}, [("world", "clock", {"id": "clock", "value": target})])
        return not required_effect or effect_active(self.state, required_effect)


def effect_active(state, effect_id: str) -> bool:
    effect = state.get("effects", effect_id, required=False)
    return bool(effect and effect["status"] == "active" and effect["expires_at"] > state.now)


def validate_changes(state, changes):
    for change in changes:
        record = change["after"]
        category = change["category"]
        if record is None:
            continue
        if category == "resources":
            component(record["resource"], "nom de ressource")
            integer(record["value"], "ressource")
            state.get("entities", record["actor"])
            if record["id"] != f"{record['actor']}:{record['resource']}":
                raise InvalidAction("Identité de ressource incohérente.")
        elif category == "items":
            for field in ("owner", "holder"):
                if record.get(field):
                    state.get("entities", record[field])
            state.get("locations", record["location"])
            if record.get("actor") != record.get("holder"):
                raise InvalidAction("Index du détenteur incohérent.")
            if (
                record.get("holder")
                and state.get("entities", record["holder"])["location"] != record["location"]
            ):
                raise InvalidAction("Objet et détenteur doivent partager le même lieu.")
        elif category == "entities":
            component(record["id"], "identifiant de personnage")
            state.get("locations", record["location"])
            identifier(record.get("name"))
            if record.get("kind") not in {"player", "npc", "creature"}:
                raise InvalidAction("Type de personnage invalide.")
            if not isinstance(record.get("personality", []), list) or not all(
                isinstance(p, str) for p in record.get("personality", [])
            ):
                raise InvalidAction("Personnalité descriptive invalide.")
            if any(
                item["location"] != record["location"] for item in state.find("items", actor=record["id"])
            ):
                raise InvalidAction("Le déplacement doit aussi traiter les objets portés.")
            if any(
                offer["location"] != record["location"]
                for offer in state.find("offers", actor=record["id"], status="active")
            ):
                raise InvalidAction("Le déplacement doit aussi traiter les offres actives.")
        elif category == "attributes":
            component(record["attribute"], "nom de caractéristique")
            integer(
                record["value"], "caractéristique", record.get("minimum", 0), record.get("maximum", 10**12)
            )
            state.get("entities", record["actor"])
            if record["id"] != f"{record['actor']}:{record['attribute']}":
                raise InvalidAction("Identité de caractéristique incohérente.")
        elif category == "relations":
            integer(record["familiarity"], "familiarité", 0, 100)
            state.get("entities", record["actor"])
            state.get("entities", record["subject"])
            if record["id"] != f"{record['actor']}:{record['subject']}":
                raise InvalidAction("Identité de relation incohérente.")
        elif category in {"beliefs", "belief_history"}:
            state.get("entities", record["actor"])
            fact = state.get("facts", record["subject"])
            if record["statement"] != fact["statement"] or not isinstance(record.get("source"), dict):
                raise InvalidAction("Croyance sans énoncé ou provenance valide.")
            integer(record["acquired_at"], "date d'apprentissage", 0, state.now)
            if category == "beliefs" and record["id"] != f"{record['actor']}:{record['subject']}":
                raise InvalidAction("Identité de croyance incohérente.")
        elif category == "triggers":
            from .universe import validate_trigger

            validate_trigger(record)
            action = record["action"]
            if action["kind"] == "resource.delta":
                state.get("resources", f"{action['actor']}:{action['resource']}")
            elif action["kind"] == "npc.move":
                if state.get("entities", action["actor"])["kind"] == "player":
                    raise InvalidAction("Un déclencheur ne peut déplacer le joueur de sa propre initiative.")
                state.get("locations", action["destination"])
        elif category == "effects":
            state.get("entities", record["actor"])
            state.get("abilities", record["ability"])
            integer(record["expires_at"], "expiration")
            if record["status"] not in {"active", "expired"}:
                raise InvalidAction("Statut d'effet invalide.")
        elif category == "world" and record["id"] == "clock":
            integer(record["value"], "temps")
            if change["before"] and record["value"] < change["before"]["value"]:
                raise InvalidAction("Le temps ne recule que par création de branche.")


class Engine:
    def __init__(self, store: Store, registry: Registry | None = None, budget: int = 1000):
        from .modules import default_registry

        self.store = store
        self.registry = registry or default_registry()
        self.budget = integer(budget, "budget", 10, 100_000)

    def execute(self, command: Command, branch: str = "main") -> dict:
        fingerprint = digest(command.identity())
        with self.store.transaction():
            meta = self.store.branch(branch)
            previous = self.store.receipt(branch, command.id, fingerprint)
            if previous is not None:
                return previous
            if command.expected_revision is not None and command.expected_revision != meta["revision"]:
                raise Conflict("Proposition périmée : l'état du monde a changé.")
            context = Context(self, branch, command)
            actor = context.state.get("entities", command.actor)
            if actor["kind"] != "player":
                raise InvalidAction("Cette interface attend un personnage joueur.")
            config = context.state.get("world", "config")
            if command.kind not in self.registry.actions:
                raise InvalidAction(f"Action inconnue : {command.kind}.")
            module, handler = self.registry.actions[command.kind]
            if module not in config["modules"]:
                raise InvalidAction(f"Module non activé : {module}.")
            if config["modules"][module] != self.registry.modules[module].version:
                raise InvalidAction(f"Version du module {module} incompatible avec la sauvegarde.")
            result = handler(context, actor, command.args)
            if not context.events:
                context.emit("action.resolved", {"kind": command.kind, "result": result})
            result = {
                "command_id": command.id,
                "branch": branch,
                "status": "completed",
                **result,
                "revision": meta["revision"] + len(context.events),
                "time": context.state.now,
                "events": [e["id"] for e in context.events],
            }
            context.events[-1]["receipt"] = {
                "command_id": command.id,
                "fingerprint": fingerprint,
                "result": result,
                "first_seq": meta["revision"] + 1,
                "last_seq": result["revision"],
            }
            self.store.commit_events(branch, context.events, meta["revision"])
            return result

    def validate_extensions(self, state, changes):
        for change in changes:
            validator = self.registry.validators.get(change["category"])
            if validator and change["after"] is not None:
                validator(state, copy.deepcopy(change["after"]))

    def view(self, actor: str, branch: str = "main") -> dict:
        from .knowledge import actor_view

        # A consistent read snapshot, even with another process writing.
        self.store.db.execute("BEGIN")
        try:
            result = actor_view(self.store.state(branch), actor)
            config = self.store.state(branch).get("world", "config")
            result["actions"] = sorted(
                action for action, (module, _) in self.registry.actions.items() if module in config["modules"]
            )
            result["revision"] = self.store.branch(branch)["revision"]
            return result
        finally:
            self.store.db.execute("ROLLBACK")

    def schedule(
        self,
        trigger: dict,
        reason: str,
        command_id: str,
        branch: str = "main",
        expected_revision: int | None = None,
    ) -> dict:
        """Trusted author/administrator interface. Never exposed to the LLM."""
        from .universe import validate_trigger

        identifier(reason, "raison")
        trigger = copy.deepcopy(trigger)
        validate_trigger(trigger)
        return self._admin(
            "trigger.configure",
            command_id,
            {"trigger": trigger, "reason": reason},
            branch,
            expected_revision,
            lambda ctx: self._schedule(ctx, trigger, reason),
        )

    @staticmethod
    def _schedule(ctx, trigger, reason):
        current = ctx.state.get("triggers", trigger["id"], required=False)
        trigger["version"] = (current["version"] + 1) if current else 1
        if trigger["mode"] == "time" and trigger["due"] < ctx.state.now:
            raise InvalidAction("Impossible de programmer dans le passé.")
        ctx.emit("trigger.configured", {"reason": reason}, [("triggers", trigger["id"], trigger)])
        return {"trigger": trigger["id"], "version": trigger["version"]}

    def cancel_trigger(self, trigger_id: str, reason: str, command_id: str, branch: str = "main") -> dict:
        def cancel(ctx):
            trigger = ctx.state.get("triggers", trigger_id)
            trigger.update(status="cancelled", version=trigger["version"] + 1)
            ctx.emit("trigger.cancelled", {"reason": reason}, [("triggers", trigger_id, trigger)])
            return {"trigger": trigger_id}

        return self._admin(
            "trigger.cancel",
            command_id,
            {"id": trigger_id, "reason": identifier(reason)},
            branch,
            None,
            cancel,
        )

    def correct(
        self, changes: list[dict], reason: str, command_id: str, expected_revision: int, branch: str = "main"
    ) -> dict:
        """Explicit local repair with before-values. Dependent history is never erased."""
        identifier(reason, "raison")
        allowed = {"entities", "items", "resources", "attributes", "relations", "beliefs"}

        def repair(ctx):
            updates = []
            for change in changes:
                if change["category"] not in allowed or change["after"] is None:
                    raise InvalidAction("Correction structurelle non prise en charge ; créer une branche.")
                if ctx.state.get(change["category"], change["key"]) != change["before"]:
                    raise Conflict("La valeur à corriger a changé.")
                after = copy.deepcopy(change["after"])
                if change["category"] == "beliefs":
                    after.update(
                        acquired_at=ctx.state.now,
                        source={"kind": "correction", "reason": reason},
                        certainty="corrected",
                        sequence=self.store.branch(branch)["revision"] + len(ctx.events) + 1,
                    )
                    historical = dict(after, id="history:" + digest([branch, command_id, change["key"]]))
                    updates.append(("belief_history", historical["id"], historical))
                updates.append((change["category"], change["key"], after))
            ctx.emit("continuity.corrected", {"reason": reason, "scope": "current_state_only"}, updates)
            return {
                "corrected": len(updates),
                "warning": "Les conséquences historiques ne sont pas recalculées automatiquement.",
            }

        return self._admin(
            "continuity.correct",
            command_id,
            {"changes": changes, "reason": reason},
            branch,
            expected_revision,
            repair,
        )

    def _admin(self, kind, command_id, args, branch, expected_revision, operation):
        command = Command(command_id, "system", kind, args, expected_revision)
        fingerprint = digest(command.identity())
        with self.store.transaction():
            meta = self.store.branch(branch)
            previous = self.store.receipt(branch, command_id, fingerprint)
            if previous is not None:
                return previous
            if expected_revision is not None and meta["revision"] != expected_revision:
                raise Conflict("Révision administrative périmée.")
            ctx = Context(self, branch, command)
            result = operation(ctx)
            if not ctx.events:
                raise InvalidAction("Une opération administrative doit être journalisée.")
            result.update(revision=meta["revision"] + len(ctx.events), events=[e["id"] for e in ctx.events])
            ctx.events[-1]["receipt"] = {
                "command_id": command_id,
                "fingerprint": fingerprint,
                "result": result,
                "first_seq": meta["revision"] + 1,
                "last_seq": result["revision"],
            }
            self.store.commit_events(branch, ctx.events, meta["revision"])
            return result

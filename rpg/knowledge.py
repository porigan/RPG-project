"""Selective beliefs, perception and bounded, evidence-based off-screen updates."""

from __future__ import annotations

from .model import Conflict, InvalidAction, digest, identifier


def learn(ctx, actor: str, fact: str, value, source: dict, at: int | None = None):
    at = ctx.state.now if at is None else at
    definition = ctx.state.get("facts", fact)
    key = f"{actor}:{fact}"
    current = ctx.state.get("beliefs", key, required=False)
    if current and current["value"] == value and current["acquired_at"] <= at:
        return
    history_id = "history:" + digest([ctx.branch, ctx.command.id, len(ctx.events), actor, fact])
    belief = {
        "id": key,
        "actor": actor,
        "subject": fact,
        "statement": definition["statement"],
        "value": value,
        "source": source,
        "acquired_at": at,
        "certainty": "observed" if source.get("kind") == "perception" else "reported",
        "sequence": ctx.engine.store.branch(ctx.branch)["revision"] + len(ctx.events) + 1,
    }
    history = dict(belief, id=history_id)
    updates = [("belief_history", history_id, history)]
    if current is None or current["acquired_at"] <= at:
        updates.append(("beliefs", key, belief))
    ctx.emit("knowledge.acquired", {"actor": actor, "fact": fact, "source": source}, updates, effective_at=at)


def materialize(ctx, actor: str, until: int | None = None, stack: tuple[str, ...] = ()):
    """Resolve declared opportunities and fixed production, never invent encounters.

    Only explicit co-presence windows are candidates. Recursive sources are
    brought up to the start of a window, so chains have strictly earlier times.
    """
    until = ctx.state.now if until is None else until
    if len(stack) >= 16 or actor in stack:
        raise Conflict("Dépendance différée cyclique ou trop profonde ; audit requis.")
    ctx.spend()
    for behavior in ctx.state.find("behaviors", actor=actor, status="active"):
        ctx.spend()
        periods = (until - behavior["last_at"]) // behavior["interval"]
        if periods <= 0:
            continue
        key = f"{actor}:{behavior['resource']}"
        resource = ctx.state.get("resources", key)
        amount = min(periods * behavior["amount"], max(0, behavior["capacity"] - resource["value"]))
        resource["value"] += amount
        behavior["last_at"] += periods * behavior["interval"]
        ctx.emit(
            "npc.production_resolved",
            {"actor": actor, "periods": periods, "amount": amount},
            [("resources", key, resource), ("behaviors", behavior["id"], behavior)],
            effective_at=until,
        )
    candidates = ctx.state.find("opportunities", subject=actor, status="pending", due_lte=until)
    candidates.sort(key=lambda o: (o["due"], o["id"]))
    for opportunity in candidates:
        ctx.spend()
        # A prior recursion may have resolved the same candidate.
        opportunity = ctx.state.get("opportunities", opportunity["id"])
        if opportunity["status"] != "pending":
            continue
        materialize(ctx, opportunity["actor"], opportunity["start"], stack + (actor,))
        past_beliefs = [
            b
            for b in ctx.state.find("belief_history", actor=opportunity["actor"], subject=opportunity["fact"])
            if b["acquired_at"] <= opportunity["start"]
        ]
        belief = (
            max(past_beliefs, key=lambda b: (b["acquired_at"], b.get("sequence", 1)))
            if past_beliefs
            else None
        )
        possible = bool(belief)
        for participant in (opportunity["actor"], actor):
            versions = ctx.engine.store.record_versions(
                ctx.branch, "entities", participant, opportunity["due"]
            )
            for event in ctx.events:
                if event["effective_at"] <= opportunity["due"]:
                    versions += [
                        (event["effective_at"], 10**15 + i, change["after"])
                        for i, change in enumerate(event["changes"])
                        if change["category"] == "entities" and change["key"] == participant
                    ]
            versions.sort(key=lambda entry: (entry[0], entry[1]))
            before = [v for v in versions if v[0] <= opportunity["start"]]
            window = [v for v in versions if opportunity["start"] < v[0] <= opportunity["due"]]
            possible = possible and bool(before) and before[-1][2]["location"] == opportunity["location"]
            possible = possible and all(v[2] and v[2]["location"] == opportunity["location"] for v in window)
        if opportunity.get("minimum_familiarity", 0):
            relation = ctx.state.get("relations", f"{opportunity['actor']}:{actor}", required=False)
            possible = (
                possible and bool(relation) and relation["familiarity"] >= opportunity["minimum_familiarity"]
            )
            if relation and relation.get("updated_at", 0) > opportunity["start"]:
                raise Conflict("Relation passée indéterminée ; résolution différée refusée.")
        roll = ctx.draw(
            f"opportunity:{opportunity['id']}", opportunity["probability"], attempt_id=opportunity["id"]
        )
        transmitted = possible and roll["success"]
        if transmitted and any(
            o["at"] >= opportunity["due"] for o in ctx.state.find("observations", actor=actor)
        ):
            raise Conflict(
                "Une transmission rétrospective pourrait invalider une observation établie ; audit requis."
            )
        opportunity.update(status="resolved", transmitted=bool(transmitted))
        event = ctx.emit(
            "opportunity.resolved",
            {
                "opportunity": opportunity["id"],
                "possible": bool(possible),
                "transmitted": bool(transmitted),
                "roll": roll,
            },
            [("opportunities", opportunity["id"], opportunity)],
            effective_at=opportunity["due"],
        )
        if transmitted:
            learn(
                ctx,
                actor,
                opportunity["fact"],
                belief["value"],
                {"kind": "communication", "actor": opportunity["actor"], "event": event["id"]},
                opportunity["due"],
            )


def observe(ctx, actor, args):
    from .modules import require_fields

    require_fields(args, ())
    materialize(ctx, actor["id"])
    for fact in ctx.state.find("facts", location=actor["location"]):
        if fact.get("public", False):
            learn(
                ctx,
                actor["id"],
                fact["id"],
                fact["value"],
                {"kind": "perception", "location": actor["location"]},
            )
    # Repeated observations at the same time are merged into one current barrier.
    key = f"{actor['id']}:{ctx.state.now}"
    ctx.emit(
        "scene.observed",
        {"actor": actor["id"], "location": actor["location"]},
        [
            (
                "observations",
                key,
                {"id": key, "actor": actor["id"], "location": actor["location"], "at": ctx.state.now},
            )
        ],
    )
    return {"observed": actor["location"]}


def tell(ctx, actor, args):
    from .modules import require_fields

    require_fields(args, ("target", "fact"), ("target", "fact"))
    target = ctx.state.get("entities", identifier(args["target"]))
    if target["location"] != actor["location"] or target["id"] == actor["id"]:
        raise InvalidAction("Interlocuteur inaccessible.")
    materialize(ctx, target["id"])
    belief = ctx.state.get("beliefs", f"{actor['id']}:{identifier(args['fact'])}")
    ctx.emit(
        "communication.spoken", {"actor": actor["id"], "target": target["id"], "fact": belief["subject"]}
    )
    learn(
        ctx, target["id"], belief["subject"], belief["value"], {"kind": "communication", "actor": actor["id"]}
    )
    return {"target": target["id"], "fact": belief["subject"]}


def talk(ctx, actor, args):
    from .modules import require_fields

    require_fields(args, ("target", "topic"), ("target",))
    target = ctx.state.get("entities", identifier(args["target"]))
    if target["location"] != actor["location"] or target["id"] == actor["id"]:
        raise InvalidAction("Interlocuteur inaccessible.")
    materialize(ctx, target["id"])
    updates = []
    for source, dest in ((actor["id"], target["id"]), (target["id"], actor["id"])):
        key = f"{source}:{dest}"
        relation = ctx.state.get("relations", key, required=False) or {
            "id": key,
            "actor": source,
            "subject": dest,
            "familiarity": 0,
            "description": "connaissance",
        }
        relation.update(familiarity=min(100, relation["familiarity"] + 1), updated_at=ctx.state.now)
        updates.append(("relations", key, relation))
    ctx.emit("conversation.started", {"actor": actor["id"], "target": target["id"]}, updates)
    topic = args.get("topic")
    shared = []
    if topic:
        identifier(topic)
        belief = ctx.state.get("beliefs", f"{target['id']}:{topic}", required=False)
        # Disclosure is authored policy, never inferred from a familiarity percentage.
        if belief and topic in target.get("shareable_facts", []):
            learn(ctx, actor["id"], topic, belief["value"], {"kind": "communication", "actor": target["id"]})
            shared.append(topic)
    barrier = f"{target['id']}:{ctx.state.now}"
    ctx.emit(
        "character.observed",
        {"actor": target["id"]},
        [
            (
                "observations",
                barrier,
                {"id": barrier, "actor": target["id"], "location": target["location"], "at": ctx.state.now},
            )
        ],
    )
    return {"target": target["id"], "shared_facts": shared}


def experience(ctx, actor, args):
    from .modules import require_fields

    require_fields(args, ("experience",), ("experience",))
    definition = ctx.state.get("experiences", identifier(args["experience"]))
    if actor["location"] != definition["location"]:
        raise InvalidAction("Expérience inaccessible.")
    key = f"{actor['id']}:{definition['id']}"
    if ctx.state.get("experiences_done", key, required=False):
        raise InvalidAction("Cette expérience a déjà été intégrée.")
    ctx.advance(definition["duration"])
    actor = ctx.state.get("entities", actor["id"])
    actor["personality"] = list(dict.fromkeys(actor.get("personality", []) + definition["personality_add"]))
    ctx.emit(
        "personality.evolved",
        {"actor": actor["id"], "experience": definition["id"]},
        [
            ("entities", actor["id"], actor),
            (
                "experiences_done",
                key,
                {"id": key, "actor": actor["id"], "description": definition["memory"], "at": ctx.state.now},
            ),
        ],
    )
    return {"experience": definition["id"]}


def actor_view(state, actor_id: str):
    actor = state.get("entities", actor_id)
    location = state.get("locations", actor["location"])
    nearby = state.find("entities", location=actor["location"])
    entity_ids = {e["id"] for e in nearby}
    # Explicit field selection: never serialize complete NPC/fact/event records.
    return {
        "actor": {"id": actor["id"], "name": actor["name"], "personality": actor.get("personality", [])},
        "time": state.now,
        "location": {"id": location["id"], "name": location["name"], "description": location["description"]},
        "nearby": [
            {
                "id": e["id"],
                "name": e["name"],
                "description": e.get("description", ""),
                "topics": e.get("topics", []),
            }
            for e in nearby
            if e["id"] != actor_id
        ],
        "inventory": [
            {"id": i["id"], "name": i["name"], "description": i.get("description", ""), "owner": i["owner"]}
            for i in state.find("items", actor=actor_id)
        ],
        "resources": [
            {"name": r["resource"], "value": r["value"]} for r in state.find("resources", actor=actor_id)
        ],
        "attributes": [
            {"name": a["attribute"], "value": a["value"]} for a in state.find("attributes", actor=actor_id)
        ],
        "beliefs": [
            {
                "id": b["subject"],
                "statement": b["statement"],
                "value": b["value"],
                "certainty": b["certainty"],
            }
            for b in state.find("beliefs", actor=actor_id)
        ],
        "memories": [
            {"description": m["description"], "at": m["at"]}
            for m in state.find("experiences_done", actor=actor_id)
        ],
        "relations": [
            {"target": r["subject"], "familiarity": r["familiarity"], "description": r["description"]}
            for r in state.find("relations", actor=actor_id)
        ],
        "routes": [
            {"id": r["id"], "name": r["name"], "duration": r["duration"]}
            for r in state.find("routes", location=actor["location"])
        ],
        "offers": [
            {
                "id": o["id"],
                "item": o["item"],
                "name": state.get("items", o["item"])["name"],
                "price": o["price"],
                "currency": o["currency"],
            }
            for o in state.find("offers", location=actor["location"], status="active")
            if o["actor"] in entity_ids
        ],
        "checks": [
            {"id": c["id"], "description": c["description"]}
            for c in state.find("checks", location=actor["location"])
        ],
        "abilities": [
            {"id": a["id"], "name": a["name"], "duration": a["duration"]}
            for a in state.find("abilities")
            if actor_id in a["actors"]
        ],
        "effects": [
            {"ability": e["ability"], "expires_at": e["expires_at"]}
            for e in state.find("effects", actor=actor_id, status="active")
            if e["expires_at"] > state.now
        ],
        "training": [
            {"id": t["id"], "description": t["description"]}
            for t in state.find("training", location=actor["location"])
        ],
        "experiences": [
            {"id": e["id"], "description": e["description"]}
            for e in state.find("experiences", location=actor["location"])
        ],
        "moves": [{"id": m["id"], "name": m["name"]} for m in state.find("moves") if actor_id in m["actors"]],
        "encounters": [
            {"id": e["id"], "name": e["name"], "creature": e["subject"]}
            for e in state.find("encounters", location=actor["location"])
        ],
        "companions": [
            {"id": c["subject"], "name": state.get("entities", c["subject"])["name"]}
            for c in state.find("companions", actor=actor_id)
        ],
        "quests": [
            {
                "id": q["id"],
                "name": q["name"],
                "description": q["description"],
                "status": (state.get("quest_progress", f"{actor_id}:{q['id']}", required=False) or {}).get(
                    "status", "available"
                ),
            }
            for q in state.find("quests", location=actor["location"])
        ],
    }

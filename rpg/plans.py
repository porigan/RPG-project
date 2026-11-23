"""Durable plans: each step is a separate atomic command and crash-safe receipt."""

from __future__ import annotations

import copy

from .model import Command, InvalidAction, identifier


class PlanRunner:
    def __init__(self, engine):
        self.engine = engine

    def start(self, plan_id: str, actor: str, steps: list[dict], branch="main", expected_revision=None):
        identifier(plan_id)
        identifier(actor)
        if not isinstance(steps, list) or not 1 <= len(steps) <= 8:
            raise InvalidAction("Un plan doit contenir de 1 à 8 étapes.")
        for step in steps:
            if (
                not isinstance(step, dict)
                or set(step) != {"action", "args"}
                or not isinstance(step["args"], dict)
            ):
                raise InvalidAction("Étape de plan invalide.")
            if step["action"] not in self.engine.registry.actions:
                raise InvalidAction("Action de plan inconnue.")
        payload = {"actor": actor, "steps": steps}

        def create(ctx):
            if ctx.state.get("plans", plan_id, required=False):
                raise InvalidAction("Identifiant de plan déjà utilisé.")
            if ctx.state.get("entities", actor)["kind"] != "player":
                raise InvalidAction("Un plan doit appartenir à un joueur.")
            record = {
                "id": plan_id,
                "actor": actor,
                "steps": copy.deepcopy(steps),
                "index": 0,
                "status": "running",
                "results": [],
            }
            ctx.emit("plan.created", {"plan": plan_id}, [("plans", plan_id, record)])
            return {"plan": plan_id}

        self.engine._admin(
            "plan.create", f"plan:{plan_id}:create", payload, branch, expected_revision, create
        )
        return self.run(plan_id, branch)

    def run(self, plan_id: str, branch="main"):
        while True:
            plan = self.engine.store.state(branch).get("plans", plan_id)
            if plan["status"] != "running":
                return plan
            index = plan["index"]
            step = plan["steps"][index]
            try:
                result = self.engine.execute(
                    Command(f"plan:{plan_id}:step:{index}", plan["actor"], step["action"], step["args"]),
                    branch,
                )
                status = "running" if result["status"] == "completed" else "awaiting_decision"
            except InvalidAction as exc:
                result = {"status": "rejected", "reason": str(exc)}
                status = "awaiting_decision"

            def advance(ctx):
                current = ctx.state.get("plans", plan_id)
                if current["index"] != index or current["status"] != "running":
                    raise InvalidAction("Le curseur du plan a changé.")
                current["results"].append(result)
                current["index"] = index + 1
                current["status"] = (
                    "completed" if index + 1 == len(current["steps"]) and status == "running" else status
                )
                ctx.emit(
                    "plan.progressed",
                    {"plan": plan_id, "step": index, "status": current["status"]},
                    [("plans", plan_id, current)],
                )
                return {"plan": plan_id}

            self.engine._admin(
                "plan.advance",
                f"plan:{plan_id}:advance:{index}",
                {"plan": plan_id, "step": index, "result": result},
                branch,
                None,
                advance,
            )

    def cancel(self, plan_id: str, actor: str, branch="main"):
        def cancel(ctx):
            plan = ctx.state.get("plans", plan_id)
            if plan["actor"] != actor:
                raise InvalidAction("Ce plan appartient à un autre personnage.")
            plan["status"] = "cancelled"
            ctx.emit("plan.cancelled", {"plan": plan_id}, [("plans", plan_id, plan)])
            return {"plan": plan_id}

        return self.engine._admin(
            "plan.cancel", f"plan:{plan_id}:cancel", {"plan": plan_id, "actor": actor}, branch, None, cancel
        )

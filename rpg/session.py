"""Playable orchestration: perception, proposal, durable plan, grounded response."""

from __future__ import annotations

import uuid

from .ai import LocalInterpreter, ModelError, factual_narration, normalized
from .model import Command, Conflict, InvalidAction, digest, identifier
from .plans import PlanRunner


class Session:
    def __init__(self, engine, actor: str, branch="main", interpreter=None, narrator=None):
        self.engine, self.actor, self.branch = engine, actor, branch
        self.interpreter = interpreter or LocalInterpreter()
        self.narrator = narrator
        self.plans = PlanRunner(engine)

    def turn(self, text: str, turn_id: str | None = None):
        if not isinstance(text, str) or not text.strip() or len(text) > 8000:
            raise InvalidAction("Une intention doit contenir de 1 à 8 000 caractères.")
        turn_id = identifier(turn_id or str(uuid.uuid4()))
        request_hash = digest({"text": text, "actor": self.actor})
        existing = self.engine.store.state(self.branch).get("turns", turn_id, required=False)
        if existing:
            if existing["request_hash"] != request_hash:
                raise Conflict("Identifiant de tour réutilisé pour une autre intention.")
            if existing["status"] == "completed":
                return existing["response"]
        else:

            def begin(ctx):
                ctx.state.get("entities", self.actor)
                ctx.emit(
                    "turn.started",
                    {"turn": turn_id},
                    [
                        (
                            "turns",
                            turn_id,
                            {
                                "id": turn_id,
                                "actor": self.actor,
                                "request_hash": request_hash,
                                "status": "pending",
                            },
                        )
                    ],
                )
                return {"turn": turn_id}

            self.engine._admin(
                "turn.begin", f"turn:{turn_id}:begin", {"hash": request_hash}, self.branch, None, begin
            )

        plan_id = f"turn:{turn_id}"
        plan = self.engine.store.state(self.branch).get("plans", plan_id, required=False)
        if plan:
            plan = self.plans.run(plan_id, self.branch)
            return self._finish(turn_id, self._response(plan, self.engine.view(self.actor, self.branch)))

        self.engine.execute(Command(f"turn:{turn_id}:observe", self.actor, "observe"), self.branch)
        view = self.engine.view(self.actor, self.branch)
        query = normalized(text.strip().rstrip(".!?"))
        if query in {"inventaire", "mon inventaire", "mes ressources", "mes caracteristiques"}:
            inventory = ", ".join(i["name"] for i in view["inventory"]) or "aucun objet"
            resources = (
                ", ".join(f"{r['name']} : {r['value']}" for r in view["resources"])
                or "aucune ressource chiffrée"
            )
            response = {"text": f"Inventaire : {inventory}. Ressources : {resources}.", "status": "completed"}
        elif query in {"mes notes", "mes souvenirs", "que sais-je"}:
            notes = [str(b["value"]) for b in view["beliefs"]] + [m["description"] for m in view["memories"]]
            response = {"text": " ".join(notes) or "Aucune information consignée.", "status": "completed"}
        elif query in {"aide", "/aide"}:
            response = {
                "text": "Décrivez votre intention : je regarde autour de moi ; j'achète la dague ; "
                "j'attends 5 minutes ; je parle à Nora de la destination ; je vais au phare. "
                "Vous pouvez aussi demander : inventaire, mes notes. Écrivez quitter pour sortir.",
                "status": "completed",
            }
        else:
            proposal = self.interpreter.interpret(text, view)
            if proposal.clarification:
                response = {"text": proposal.clarification, "status": "awaiting_decision"}
            else:
                plan = self.plans.start(plan_id, self.actor, proposal.steps, self.branch, proposal.revision)
                response = self._response(plan, self.engine.view(self.actor, self.branch))
        return self._finish(turn_id, response)

    def _response(self, plan, view):
        text = factual_narration(view, plan["results"])
        warning = None
        if self.narrator:
            # No global events, old memories, hidden NPC state or full database view.
            facts = [
                {"id": "location", "text": view["location"]["description"]},
                {"id": "outcome", "text": text},
            ]
            try:
                text = self.narrator.render(facts)
            except ModelError:
                warning = "La narration IA a échoué ; le résultat factuel du moteur est affiché."
        response = {"text": text, "status": plan["status"], "plan": plan["id"], "time": view["time"]}
        if warning:
            response["warning"] = warning
        return response

    def _finish(self, turn_id, response):
        def finish(ctx):
            record = ctx.state.get("turns", turn_id)
            record.update(status="completed", response=response)
            ctx.emit("turn.completed", {"turn": turn_id}, [("turns", turn_id, record)])
            return response.copy()

        self.engine._admin("turn.finish", f"turn:{turn_id}:finish", response, self.branch, None, finish)
        return response

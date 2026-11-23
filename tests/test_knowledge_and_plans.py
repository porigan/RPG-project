import unittest

from rpg import Conflict, InvalidAction
from rpg.model import Registry
from rpg.modules import RuleModule
from rpg.plans import PlanRunner
from tests.support import command, config, world


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.store, self.engine = world()
        self.addCleanup(self.store.close)

    def test_views_do_not_leak_global_facts_or_npc_beliefs(self):
        from rpg.model import canonical

        view = canonical(self.engine.view("player"))
        self.assertNotIn("SECRET_INACCESSIBLE", view)
        self.assertNotIn("île de Verre", view)
        self.assertEqual(self.engine.view("player")["beliefs"], [])
        command(self.engine, "observe")
        beliefs = self.engine.view("player")["beliefs"]
        self.assertEqual([b["id"] for b in beliefs], ["notice"])
        self.assertNotIn("île de Verre", canonical(self.engine.view("player")))

    def test_false_belief_can_be_communicated_without_changing_truth(self):
        cfg = config()
        cfg["records"]["beliefs"][0]["value"] = "Le navire ne part jamais."
        other, engine = world(configuration=cfg)
        self.addCleanup(other.close)
        command(engine, "talk", {"target": "scribe", "topic": "destination"})
        state = other.state("main")
        self.assertEqual(state.get("beliefs", "player:destination")["value"], "Le navire ne part jamais.")
        self.assertEqual(state.get("facts", "destination")["value"], "Le navire part pour l'île de Verre.")
        command(engine, "tell", {"target": "merchant", "fact": "destination"})
        self.assertEqual(
            other.state("main").get("beliefs", "merchant:destination")["value"], "Le navire ne part jamais."
        )

    def test_offscreen_transmission_happens_before_npc_response_and_only_once(self):
        command(self.engine, "wait", {"seconds": 600})
        self.assertIsNone(self.store.state("main").get("beliefs", "scout:destination", required=False))
        result = command(self.engine, "talk", {"target": "scout", "topic": "destination"})
        self.assertEqual(result["shared_facts"], ["destination"])
        state = self.store.state("main")
        self.assertEqual(state.get("beliefs", "scout:destination")["acquired_at"], 300)
        self.assertEqual(state.get("resources", "scribe:pages")["value"], 1)
        before = len([e for e in self.store.history("main") if e["kind"] == "opportunity.resolved"])
        command(self.engine, "talk", {"target": "scout", "topic": "destination"})
        self.assertEqual(
            before, len([e for e in self.store.history("main") if e["kind"] == "opportunity.resolved"])
        )
        self.assertTrue(self.store.audit("main")["ok"])

    def test_co_presence_constraints_include_departure_inside_window(self):
        self.engine.schedule(
            {
                "id": "leave",
                "mode": "time",
                "due": 150,
                "status": "active",
                "action": {"kind": "npc.move", "actor": "scribe", "destination": "hill"},
            },
            "départ",
            "leave",
        )
        self.engine.schedule(
            {
                "id": "return",
                "mode": "time",
                "due": 250,
                "status": "active",
                "action": {"kind": "npc.move", "actor": "scribe", "destination": "port"},
            },
            "retour",
            "return",
        )
        command(self.engine, "wait", {"seconds": 600})
        result = command(self.engine, "talk", {"target": "scout", "topic": "destination"})
        self.assertEqual(result["shared_facts"], [])
        self.assertFalse(self.store.state("main").get("opportunities", "quay-conversation")["transmitted"])

    def test_familiarity_alone_never_transmits_information(self):
        command(self.engine, "talk", {"target": "scout", "topic": "destination"})
        self.assertIsNone(self.store.state("main").get("beliefs", "scout:destination", required=False))
        self.assertNotEqual(self.store.state("main").get("relations", "player:scout")["familiarity"], 40)

    def test_materialization_does_not_depend_on_request_id(self):
        cfg = config()
        cfg["records"]["opportunities"][0]["probability"] = 0.5
        outcomes = []
        for ident in ("first-materializer", "different-materializer"):
            store, engine = world(configuration=cfg)
            self.addCleanup(store.close)
            command(engine, "wait", {"seconds": 400})
            command(engine, "talk", {"target": "scout"}, ident)
            outcomes.append(
                [e["data"]["roll"] for e in store.history("main") if e["kind"] == "opportunity.resolved"]
            )
        self.assertEqual(outcomes[0], outcomes[1])

    def test_retroactive_transmission_is_blocked_if_observation_exists(self):
        # Fault injection stands for imported/corrupted state; ordinary talk resolves first.
        command(self.engine, "wait", {"seconds": 600})
        self.store._write(
            "main", "observations", "prior", {"id": "prior", "actor": "scout", "location": "port", "at": 400}
        )
        before = self.store.branch("main")["revision"]
        with self.assertRaises(Conflict):
            command(self.engine, "talk", {"target": "scout", "topic": "destination"})
        self.assertEqual(before, self.store.branch("main")["revision"])
        self.assertEqual(
            self.store.state("main").get("opportunities", "quay-conversation")["status"], "pending"
        )


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.store, self.engine = world()
        self.addCleanup(self.store.close)
        self.runner = PlanRunner(self.engine)

    def test_composite_plan_stops_at_price_and_preserves_previous_steps(self):
        plan = self.runner.start(
            "voyage",
            "player",
            [
                {"action": "wait", "args": {"seconds": 15}},
                {"action": "buy", "args": {"offer": "boat-sale", "max_price": 100}},
                {"action": "travel", "args": {"route": "path"}},
            ],
        )
        self.assertEqual(plan["status"], "awaiting_decision")
        self.assertEqual(self.store.state("main").now, 15)
        self.assertEqual(self.store.state("main").get("resources", "player:coin")["value"], 100)
        self.assertEqual(self.store.state("main").get("entities", "player")["location"], "port")
        self.assertEqual(plan["index"], 2)
        self.assertTrue(self.store.audit("main")["ok"])

    def test_crash_between_step_and_cursor_does_not_repeat_step(self):
        from unittest.mock import patch

        original = self.engine._admin

        def crash(kind, *args, **kwargs):
            if kind == "plan.advance":
                raise OSError("process interrupted after committed step")
            return original(kind, *args, **kwargs)

        with patch.object(self.engine, "_admin", crash):
            with self.assertRaises(OSError):
                self.runner.start("recoverable", "player", [{"action": "wait", "args": {"seconds": 10}}])
        self.assertEqual(self.store.state("main").now, 10)
        result = self.runner.run("recoverable")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.store.state("main").now, 10)

    def test_invalid_later_step_preserves_completed_purchase(self):
        result = self.runner.start(
            "partial",
            "player",
            [
                {"action": "buy", "args": {"offer": "dagger-sale", "max_price": 25}},
                {"action": "give", "args": {"item": "dagger", "target": "missing"}},
            ],
        )
        self.assertEqual(result["status"], "awaiting_decision")
        self.assertEqual(self.store.state("main").get("items", "dagger")["owner"], "player")
        self.assertEqual(self.store.state("main").get("resources", "player:coin")["value"], 75)

    def test_module_dependency_cycles_and_disabled_mechanics(self):
        with self.assertRaises(InvalidAction):
            Registry([RuleModule("a", {}, ("b",)), RuleModule("b", {}, ("a",))])
        cfg = config()
        cfg["modules"].remove("combat")
        cfg["records"].pop("moves")
        store, engine = world(configuration=cfg)
        self.addCleanup(store.close)
        with self.assertRaises(InvalidAction):
            command(engine, "attack", {"target": "merchant", "move": "strike"})


if __name__ == "__main__":
    unittest.main()

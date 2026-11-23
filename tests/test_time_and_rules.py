import unittest

from rpg import InvalidAction, LimitExceeded
from tests.support import command, trigger, world


class TimeTests(unittest.TestCase):
    def setUp(self):
        self.store, self.engine = world()
        self.addCleanup(self.store.close)

    def test_effect_expires_at_exact_boundary_and_interrupts_required_action(self):
        command(self.engine, "activate", {"ability": "flight"})
        result = command(self.engine, "travel", {"route": "sky"})
        self.assertEqual(result["time"], 720)
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(self.store.state("main").get("effects", "player:flight")["status"], "expired")
        self.assertEqual(self.store.state("main").get("entities", "player")["location"], "port")
        self.assertTrue(self.store.audit("main")["ok"])

    def test_effect_expiration_does_not_interrupt_unrelated_action(self):
        command(self.engine, "activate", {"ability": "flight"})
        result = command(self.engine, "travel", {"route": "path"})
        self.assertEqual(result["time"], 900)
        self.assertEqual(result["destination"], "hill")
        self.assertEqual(self.engine.view("player")["effects"], [])

    def test_cancelled_and_replaced_triggers_do_not_fire_old_effect(self):
        self.engine.schedule(trigger(), "première version", "t1")
        self.engine.schedule(trigger(due=20, amount=7), "remplacement", "t2")
        command(self.engine, "wait", {"seconds": 10})
        self.assertEqual(self.store.state("main").get("resources", "player:coin")["value"], 100)
        command(self.engine, "wait", {"seconds": 10})
        self.assertEqual(self.store.state("main").get("resources", "player:coin")["value"], 107)
        self.engine.schedule(trigger(ident="cancelled", due=30), "annulation", "t3")
        self.engine.cancel_trigger("cancelled", "plus nécessaire", "t4")
        command(self.engine, "wait", {"seconds": 10})
        self.assertEqual(self.store.state("main").get("resources", "player:coin")["value"], 107)

    def test_conditional_and_event_triggers_are_one_shot(self):
        self.engine.schedule(
            {
                "id": "condition",
                "status": "active",
                "mode": "condition",
                "condition": {
                    "category": "resources",
                    "key": "player:coin",
                    "field": "value",
                    "op": "lte",
                    "value": 75,
                },
                "action": {
                    "kind": "resource.delta",
                    "actor": "player",
                    "resource": "stamina",
                    "amount": 2,
                    "reason": "condition",
                },
            },
            "test",
            "cond",
        )
        self.engine.schedule(
            {
                "id": "event",
                "status": "active",
                "mode": "event",
                "subject": "purchase.completed",
                "action": {
                    "kind": "resource.delta",
                    "actor": "player",
                    "resource": "coin",
                    "amount": 1,
                    "reason": "event",
                },
            },
            "test",
            "evt",
        )
        command(self.engine, "buy", {"offer": "dagger-sale", "max_price": 25})
        self.assertEqual(self.store.state("main").get("resources", "player:coin")["value"], 76)
        self.assertEqual(self.store.state("main").get("resources", "player:stamina")["value"], 22)
        self.assertEqual(len([e for e in self.store.history("main") if e["kind"] == "trigger.fired"]), 2)

    def test_repeating_trigger_and_budget_rollback(self):
        self.engine.schedule(trigger(due=1, interval=1), "récurrent", "repeat")
        self.engine.budget = 20
        before = self.store.audit("main")
        with self.assertRaises(LimitExceeded):
            command(self.engine, "wait", {"seconds": 100})
        self.assertEqual(before, self.store.audit("main"))
        self.engine.budget = 1000
        command(self.engine, "wait", {"seconds": 3})
        self.assertEqual(self.store.state("main").get("resources", "player:coin")["value"], 103)

    def test_probability_replay_and_new_attempts(self):
        first = command(self.engine, "attempt", {"check": "search"}, "attempt1")
        events_before = self.store.history("main")
        self.assertEqual(first, command(self.engine, "attempt", {"check": "search"}, "attempt1"))
        self.assertEqual(events_before, self.store.history("main"))
        command(self.engine, "attempt", {"check": "search"}, "attempt2")
        rolls = [e["data"]["roll"] for e in self.store.history("main") if e["kind"] == "attempt.resolved"]
        self.assertNotEqual(rolls[0]["sample"], rolls[1]["sample"])
        self.assertEqual(self.store.state("main").now, 60)
        self.assertEqual(self.store.state("main").get("resources", "player:stamina")["value"], 18)
        self.store.rebuild("main")
        self.assertEqual(
            rolls, [e["data"]["roll"] for e in self.store.history("main") if e["kind"] == "attempt.resolved"]
        )

    def test_training_and_personality_are_optional_persistent_mechanics(self):
        command(self.engine, "travel", {"route": "path"})
        command(self.engine, "train", {"training": "meditation"})
        command(self.engine, "experience", {"experience": "watch-sea"})
        self.assertEqual(self.store.state("main").get("attributes", "player:focus")["value"], 2)
        self.assertIn("attentif aux horizons", self.engine.view("player")["actor"]["personality"])
        self.assertEqual(len(self.engine.view("player")["memories"]), 1)
        with self.assertRaises(InvalidAction):
            command(self.engine, "experience", {"experience": "watch-sea"})

    def test_target_moving_during_attack_interrupts_without_refunding_time(self):
        self.engine.schedule(
            {
                "id": "leave",
                "mode": "time",
                "due": 3,
                "status": "active",
                "action": {"kind": "npc.move", "actor": "merchant", "destination": "hill"},
            },
            "départ",
            "depart",
        )
        result = command(self.engine, "attack", {"target": "merchant", "move": "strike"})
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(result["time"], 5)
        self.assertEqual(self.store.state("main").get("resources", "player:stamina")["value"], 19)
        self.assertEqual(self.store.state("main").get("items", "dagger")["location"], "hill")


if __name__ == "__main__":
    unittest.main()

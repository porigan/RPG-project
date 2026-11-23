import unittest

from rpg import Command, Engine, InvalidAction, Store
from rpg.model import Registry
from rpg.modules import RuleModule, default_registry
from rpg.universe import initialize, load_file
from tests.support import ROOT


class OptionalModulesTests(unittest.TestCase):
    def setUp(self):
        self.config = load_file(ROOT / "examples/universes/clockwork.json")
        self.store = Store()
        self.engine = Engine(self.store)
        self.config["records"]["checks"][0]["probability"] = 1
        self.config["records"]["encounters"][0]["probability"] = 1
        initialize(self.engine, self.config, seed="test")
        self.addCleanup(self.store.close)

    def execute(self, kind, args, ident):
        return self.engine.execute(Command(ident, "researcher", kind, args))

    def test_second_universe_has_no_levels_or_hp_imposed(self):
        view = self.engine.view("researcher")
        self.assertEqual(view["attributes"], [])
        self.assertNotIn("attack", view["actions"])
        self.assertNotIn("buy", view["actions"])
        self.assertNotIn("train", view["actions"])
        self.execute("travel", {"route": "to-workshop"}, "travel")
        self.assertEqual(self.engine.view("researcher")["location"]["id"], "workshop")

    def test_creature_companion_is_persistent_and_cannot_be_acquired_twice(self):
        result = self.execute("befriend", {"encounter": "copper-bird"}, "approach")
        self.assertTrue(result["success"])
        self.assertEqual(self.engine.view("researcher")["companions"][0]["id"], "finch")
        with self.assertRaises(InvalidAction):
            self.execute("befriend", {"encounter": "copper-bird"}, "approach-again")
        self.store.rebuild("main")
        self.assertEqual(self.engine.view("researcher")["companions"][0]["id"], "finch")

    def test_quest_reward_requires_real_goal_and_can_be_claimed_only_once(self):
        self.execute("accept_quest", {"quest": "decode"}, "accept")
        with self.assertRaises(InvalidAction):
            self.execute("claim_quest", {"quest": "decode"}, "too-early")
        self.execute("attempt", {"check": "signal"}, "solve")
        self.execute("claim_quest", {"quest": "decode"}, "claim")
        with self.assertRaises(InvalidAction):
            self.execute("claim_quest", {"quest": "decode"}, "claim-again")
        self.assertEqual(self.store.state("main").get("resources", "researcher:notes")["value"], 1)
        self.assertTrue(self.store.audit("main")["ok"])

    def test_external_module_declares_its_collection_and_validates_real_writes(self):
        def validate_counter(state, record):
            if type(record.get("value")) is not int or not 0 <= record["value"] <= 2:
                raise InvalidAction("Compteur hors limites.")

        def visit(ctx, actor, args):
            record = ctx.state.get("visits", actor["id"])
            record["value"] += 1
            ctx.emit("visit.recorded", {}, [("visits", actor["id"], record)])
            return {"visits": record["value"]}

        module = RuleModule(
            "visits",
            {"visit": visit},
            ("core",),
            collections=("visits",),
            validators={"visits": validate_counter},
        )
        registry = Registry([*default_registry().modules.values(), module])
        configuration = {
            "schema": 1,
            "id": "minimal",
            "title": "Minimal",
            "player": "p",
            "modules": ["core", "visits"],
            "records": {
                "locations": [{"id": "room", "name": "Pièce", "description": "Une pièce."}],
                "entities": [{"id": "p", "name": "P", "kind": "player", "location": "room"}],
                "visits": [{"id": "p", "actor": "p", "value": 0}],
            },
        }
        with Store() as store:
            engine = Engine(store, registry)
            initialize(engine, configuration)
            for index in range(2):
                engine.execute(Command(f"visit-{index}", "p", "visit"))
            with self.assertRaises(InvalidAction):
                engine.execute(Command("visit-too-many", "p", "visit"))
            self.assertEqual(store.state("main").get("visits", "p")["value"], 2)
            store.rebuild("main")
            self.assertTrue(store.audit("main")["ok"])

    def test_modules_cannot_silently_redeclare_another_collection(self):
        with self.assertRaises(InvalidAction):
            Registry(
                [RuleModule("one", {}, collections=("same",)), RuleModule("two", {}, collections=("same",))]
            )


if __name__ == "__main__":
    unittest.main()

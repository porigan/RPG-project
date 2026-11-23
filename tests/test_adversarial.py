import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from rpg import Command, Conflict, Engine, InvalidAction, Store
from rpg.ai import validate_proposal
from rpg.engine import Context
from rpg.universe import initialize
from tests.support import command, config, world


class AdversarialTests(unittest.TestCase):
    def test_real_concurrent_writers_with_same_expected_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "concurrent.db"
            store, _ = world(path)
            store.close()
            barrier = threading.Barrier(2)

            def purchase(index):
                with Store(path) as connection:
                    engine = Engine(connection)
                    barrier.wait(timeout=10)
                    try:
                        engine.execute(
                            Command(
                                f"buyer-{index}",
                                "player",
                                "buy",
                                {"offer": "dagger-sale", "max_price": 25},
                                1,
                            )
                        )
                        return "committed"
                    except Conflict:
                        return "conflict"

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(purchase, range(2)))
            self.assertEqual(sorted(results), ["committed", "conflict"])
            with Store(path) as store:
                self.assertEqual(store.state("main").get("resources", "player:coin")["value"], 75)
                self.assertTrue(store.audit("main")["ok"])

    def test_malformed_universes_never_leave_half_created_world(self):
        for mutate in (
            lambda c: c["records"]["resources"][0].update(value=-1),
            lambda c: c["records"]["entities"][0].update(location="missing"),
            lambda c: c["records"]["routes"][0].update(duration=True),
            lambda c: c["records"]["checks"][0].update(probability=float("nan")),
            lambda c: c["records"]["offers"][0].pop("currency"),
        ):
            with self.subTest(mutation=mutate):
                configuration = config()
                mutate(configuration)
                with Store() as store:
                    with self.assertRaises(InvalidAction):
                        initialize(Engine(store), configuration)
                    self.assertEqual(store.db.execute("SELECT count(*) FROM branches").fetchone()[0], 0)
                    self.assertEqual(store.db.execute("SELECT count(*) FROM events").fetchone()[0], 0)

    def test_probability_endpoints_even_for_maximum_hash(self):
        store, engine = world()
        self.addCleanup(store.close)
        ctx = Context(engine, "main", Command("roll", "player", "observe"))

        class MaxHash:
            def digest(self):
                return b"\xff" * 32

        with patch("rpg.engine.hmac.new", return_value=MaxHash()):
            self.assertTrue(ctx.draw("certain", 1)["success"])
            self.assertFalse(ctx.draw("impossible", 0)["success"])
            self.assertLess(ctx.draw("bounded", 0.5)["sample"], 1)

    def test_all_causal_edges_respect_fictive_time_despite_late_recording(self):
        store, engine = world()
        self.addCleanup(store.close)
        command(engine, "wait", {"seconds": 600})
        command(engine, "talk", {"target": "scout", "topic": "destination"})
        events = store.history("main")
        known = {}
        self.assertTrue(
            any(e["effective_at"] < events[i - 1]["effective_at"] for i, e in enumerate(events) if i)
        )
        for event in events:
            for cause in event["causes"]:
                self.assertIn(cause, known)
                self.assertLessEqual(known[cause], event["effective_at"])
            known[event["id"]] = event["effective_at"]
        self.assertTrue(store.audit("main")["ok"])

    def test_unhashable_model_action_is_rejected_cleanly(self):
        store, engine = world()
        self.addCleanup(store.close)
        with self.assertRaises(InvalidAction):
            validate_proposal(
                {"steps": [{"action": ["observe"], "args": {}, "source": "regarde"}], "clarification": None},
                "regarde",
                engine.view("player"),
            )

    def test_correcting_location_without_moving_held_items_is_refused(self):
        store, engine = world()
        self.addCleanup(store.close)
        command(engine, "buy", {"offer": "dagger-sale", "max_price": 25})
        before = store.state("main").get("entities", "player")
        change = {
            "category": "entities",
            "key": "player",
            "before": before,
            "after": dict(before, location="hill"),
        }
        # Audit must detect cross-record inconsistencies, so repairs must include
        # all affected records in one event, not just the directly edited entity.
        with self.assertRaises(InvalidAction):
            engine.correct(
                [change], "correction incomplète", "bad-location-repair", store.branch("main")["revision"]
            )

    def test_idempotency_receipts_are_recoverable_from_journal(self):
        store, engine = world()
        self.addCleanup(store.close)
        result = command(engine, "buy", {"offer": "dagger-sale", "max_price": 25}, "recover-receipt")
        store.db.execute("DROP TRIGGER receipts_no_delete")
        store.db.execute("DELETE FROM receipts")
        self.assertIn("receipt_mismatch", store.audit("main")["errors"])
        store.rebuild("main")
        self.assertEqual(
            result, command(engine, "buy", {"offer": "dagger-sale", "max_price": 25}, "recover-receipt")
        )
        self.assertTrue(store.audit("main")["ok"])

    def test_scheduled_resource_consumption_actualizes_npc_production_first(self):
        configuration = config()
        configuration["records"]["behaviors"][0]["capacity"] = 3
        store, engine = world(configuration=configuration)
        self.addCleanup(store.close)
        engine.schedule(
            {
                "id": "consume-pages",
                "status": "active",
                "mode": "time",
                "due": 300,
                "action": {
                    "kind": "resource.delta",
                    "actor": "scribe",
                    "resource": "pages",
                    "amount": -2,
                    "reason": "travail remis",
                },
            },
            "consommation prévue",
            "schedule-consumption",
        )
        command(engine, "wait", {"seconds": 300})
        self.assertEqual(store.state("main").get("resources", "scribe:pages")["value"], 1)
        command(engine, "wait", {"seconds": 300})
        command(engine, "talk", {"target": "scribe"})
        self.assertEqual(store.state("main").get("resources", "scribe:pages")["value"], 3)
        self.assertTrue(store.audit("main")["ok"])

    def test_rebuild_recovers_historical_reference_index(self):
        store, engine = world()
        self.addCleanup(store.close)
        command(engine, "wait", {"seconds": 600})
        store.db.execute("DELETE FROM event_refs")
        self.assertIn("event_index_mismatch", store.audit("main")["errors"])
        store.rebuild("main")
        self.assertTrue(store.audit("main")["ok"])

    def test_catalog_names_cannot_collide_with_composite_identifiers(self):
        configuration = config()
        configuration["records"]["entities"][0]["id"] = "player:other"
        with Store() as store:
            with self.assertRaises(InvalidAction):
                initialize(Engine(store), configuration)

    def test_commit_boundary_rejects_explicit_cause_from_fictive_future(self):
        store, engine = world()
        self.addCleanup(store.close)
        command(engine, "wait", {"seconds": 100})
        future = store.history("main")[-1]["id"]
        before = store.audit("main")

        def invalid(ctx):
            ctx.emit("invalid.historical", {}, effective_at=0, causes=[future])
            return {}

        with self.assertRaises(InvalidAction):
            engine._admin("invalid", "invalid-cause", {}, "main", None, invalid)
        self.assertEqual(before, store.audit("main"))

    def test_latest_belief_at_same_fictive_time_uses_journal_order(self):
        configuration = config()
        initial = dict(
            configuration["records"]["beliefs"][0],
            id="player:destination",
            actor="player",
            value="Une autre destination.",
        )
        configuration["records"]["beliefs"].append(initial)
        store, engine = world(configuration=configuration)
        self.addCleanup(store.close)
        command(engine, "tell", {"target": "scribe", "fact": "destination"}, "same-time-new-belief")
        command(engine, "wait", {"seconds": 400})
        command(engine, "talk", {"target": "scout", "topic": "destination"})
        self.assertEqual(
            store.state("main").get("beliefs", "scout:destination")["value"], "Une autre destination."
        )
        self.assertTrue(store.audit("main")["ok"])

    def test_belief_correction_preserves_old_knowledge_history(self):
        store, engine = world()
        self.addCleanup(store.close)
        command(engine, "wait", {"seconds": 50})
        before = store.state("main").get("beliefs", "scribe:destination")
        change = {
            "category": "beliefs",
            "key": before["id"],
            "before": before,
            "after": dict(before, value="Destination corrigée."),
        }
        engine.correct(
            [change], "information locale corrigée", "belief-repair", store.branch("main")["revision"]
        )
        history = store.state("main").find("belief_history", actor="scribe", subject="destination")
        self.assertEqual(sorted(b["acquired_at"] for b in history), [0, 50])
        command(engine, "wait", {"seconds": 350})
        command(engine, "talk", {"target": "scout", "topic": "destination"})
        self.assertEqual(
            store.state("main").get("beliefs", "scout:destination")["value"], "Destination corrigée."
        )
        self.assertTrue(store.audit("main")["ok"])


if __name__ == "__main__":
    unittest.main()

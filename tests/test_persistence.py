import random
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rpg import Conflict, Engine, InvalidAction, Store
from tests.support import ROOT, command, world


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.store, self.engine = world()
        self.addCleanup(self.store.close)

    def test_purchase_is_atomic_and_conserves_money(self):
        command(self.engine, "buy", {"offer": "dagger-sale", "max_price": 25})
        state = self.store.state("main")
        self.assertEqual(state.get("resources", "player:coin")["value"], 75)
        self.assertEqual(state.get("resources", "merchant:coin")["value"], 75)
        self.assertEqual(state.get("items", "dagger")["owner"], "player")
        self.assertEqual(state.get("items", "dagger")["holder"], "player")
        self.assertEqual(self.store.history("main")[-1]["kind"], "purchase.completed")
        self.assertEqual(len(self.store.history("main")[-1]["changes"]), 4)
        self.assertTrue(self.store.audit("main")["ok"])

    def test_write_failure_rolls_back_events_projection_and_receipt(self):
        before = self.store.audit("main")
        original = self.store._write
        calls = 0

        def failing(*args):
            nonlocal calls
            calls += 1
            original(*args)
            if calls == 2:
                raise OSError("simulated disk error")

        with patch.object(self.store, "_write", failing):
            with self.assertRaises(OSError):
                command(self.engine, "buy", {"offer": "dagger-sale", "max_price": 25}, "disk-error")
        self.assertEqual(before, self.store.audit("main"))
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM receipts").fetchone()[0], 0)
        command(self.engine, "buy", {"offer": "dagger-sale", "max_price": 25}, "disk-error")

    def test_idempotence_survives_stale_revision_and_conflicting_payload_is_rejected(self):
        args = {"offer": "dagger-sale", "max_price": 25}
        first = command(self.engine, "buy", args, "same", revision=1)
        again = command(self.engine, "buy", args, "same", revision=1)
        self.assertEqual(first, again)
        with self.assertRaises(Conflict):
            command(self.engine, "buy", {"offer": "boat-sale", "max_price": 25}, "same")
        self.assertEqual(self.store.branch("main")["revision"], first["revision"])

    def test_stale_proposal_cannot_write(self):
        command(self.engine, "wait", {"seconds": 1})
        before = self.store.audit("main")
        with self.assertRaises(Conflict):
            command(self.engine, "buy", {"offer": "dagger-sale", "max_price": 25}, revision=1)
        self.assertEqual(before, self.store.audit("main"))

    def test_append_only_guards(self):
        for sql in ("UPDATE events SET hash='x'", "DELETE FROM events"):
            with self.assertRaises(sqlite3.IntegrityError):
                self.store.db.execute(sql)

    def test_rebuild_detects_and_repairs_projection_corruption(self):
        command(self.engine, "buy", {"offer": "dagger-sale", "max_price": 25})
        before = self.store.audit("main")
        self.store.db.execute("DELETE FROM records WHERE category='items'")
        self.assertIn("projection_mismatch", self.store.audit("main")["errors"])
        self.store.rebuild("main")
        self.assertEqual(before, self.store.audit("main"))

    def test_fork_preserves_old_timeline_and_inherited_receipts(self):
        purchase = command(self.engine, "buy", {"offer": "dagger-sale", "max_price": 25}, "purchase")
        command(self.engine, "give", {"item": "dagger", "target": "scout"})
        old = self.store.audit("main")
        self.store.fork("main", purchase["revision"], "alternative", "autre décision")
        self.assertEqual(self.store.state("alternative").get("items", "dagger")["owner"], "player")
        repeated = command(
            self.engine, "buy", {"offer": "dagger-sale", "max_price": 25}, "purchase", "alternative"
        )
        self.assertEqual(repeated, purchase)
        command(self.engine, "travel", {"route": "path"}, branch="alternative")
        self.assertEqual(old, self.store.audit("main"))
        self.assertEqual(self.store.state("main").get("entities", "player")["location"], "port")
        self.assertTrue(self.store.audit("alternative")["ok"])
        self.store.rebuild("alternative")
        self.assertTrue(self.store.audit("alternative")["ok"])

    def test_cannot_fork_inside_one_command(self):
        command(self.engine, "travel", {"route": "path"})
        with self.assertRaises(InvalidAction):
            self.store.fork("main", 2, "invalid", "coupure pendant une commande")

    def test_correction_requires_before_value_and_preserves_original(self):
        before = self.store.state("main").get("resources", "player:coin")
        after = dict(before, value=90)
        change = {"category": "resources", "key": before["id"], "before": before, "after": after}
        revision = self.store.branch("main")["revision"]
        self.engine.correct([change], "erreur initiale documentée", "repair", revision)
        self.assertEqual(
            self.store.history("main")[0]["changes"][
                next(
                    i
                    for i, c in enumerate(self.store.history("main")[0]["changes"])
                    if c["key"] == "player:coin"
                )
            ]["after"]["value"],
            100,
        )
        self.assertEqual(self.store.state("main").get("resources", before["id"])["value"], 90)
        with self.assertRaises(Conflict):
            self.engine.correct(
                [change], "mauvaise précondition", "repair2", self.store.branch("main")["revision"]
            )
        self.assertTrue(self.store.audit("main")["ok"])

    def test_generated_sequences_preserve_invariants_and_replay(self):
        rng = random.Random(730)
        for index in range(100):
            action = rng.choice(["wait", "attempt", "buy"])
            args = {
                "wait": {"seconds": rng.randint(1, 20)},
                "attempt": {"check": "search"},
                "buy": {"offer": "dagger-sale", "max_price": rng.randint(1, 50)},
            }[action]
            before = self.store.audit("main")
            try:
                command(self.engine, action, args, f"generated-{index}")
            except InvalidAction:
                self.assertEqual(before, self.store.audit("main"))
            self.assertTrue(self.store.audit("main")["ok"])
            self.assertTrue(all(r["value"] >= 0 for r in self.store.state("main").find("resources")))

    def test_restart_backup_and_real_process_crash_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "world.db"
            store, engine = world(path)
            result = command(engine, "buy", {"offer": "dagger-sale", "max_price": 25}, "saved")
            store.backup(Path(directory) / "backup.db")
            store.close()
            crash_code = (
                "import sqlite3,os,sys; c=sqlite3.connect(sys.argv[1],isolation_level=None); "
                "c.execute('BEGIN IMMEDIATE'); c.execute(\"DELETE FROM records WHERE category='items'\"); os._exit(17)"
            )
            crashed = subprocess.run(
                [sys.executable, "-c", crash_code, str(path)], cwd=ROOT, capture_output=True
            )
            self.assertEqual(crashed.returncode, 17)
            with Store(path) as reopened:
                self.assertEqual(
                    command(Engine(reopened), "buy", {"offer": "dagger-sale", "max_price": 25}, "saved"),
                    result,
                )
                self.assertTrue(reopened.audit("main")["ok"])
            with Store(Path(directory) / "backup.db") as restored:
                self.assertTrue(restored.audit("main")["ok"])

    def test_two_connections_cannot_sell_same_item_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "world.db"
            store, engine = world(path)
            with Store(path) as second:
                command(engine, "buy", {"offer": "dagger-sale", "max_price": 25}, "first")
                with self.assertRaises(InvalidAction):
                    command(Engine(second), "buy", {"offer": "dagger-sale", "max_price": 25}, "second")
                self.assertEqual(second.state("main").get("resources", "player:coin")["value"], 75)
            store.close()


if __name__ == "__main__":
    unittest.main()

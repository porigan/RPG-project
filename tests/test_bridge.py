import base64
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rpg import Command, Conflict, Engine, InvalidAction, Store
from rpg.bridge import BridgeConfig, RPGBridge, load_or_create_key
from rpg.model import canonical
from tests.support import world


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "save.db"
        store, _ = world(self.path)
        store.close()
        self.now = 1000
        self.bridge = RPGBridge(
            BridgeConfig(self.path, b"test-secret-for-local-proposals!!"), lambda: self.now
        )

    def prepare(self, action="buy", args=None, request="purchase", text="J'achète la dague"):
        return self.bridge.prepare_action(
            request,
            text,
            action,
            args if args is not None else {"offer": "dagger-sale", "max_price": 25},
            self.bridge.get_state()["revision"],
        )

    def audit(self):
        with Store(self.path) as store:
            return store.audit("main")

    def test_state_reads_are_sqlite_readonly_without_magical_notes_or_secrets(self):
        before = self.audit()
        view = self.bridge.get_state()
        self.assertNotIn("beliefs", view)
        self.assertNotIn("memories", view)
        self.assertNotIn("SECRET_INACCESSIBLE", canonical(view))
        self.assertNotIn("île de Verre", canonical(view))
        self.assertEqual(before, self.audit())
        with Store(self.path, readonly=True) as store:
            with self.assertRaises(sqlite3.OperationalError):
                store.db.execute("DELETE FROM records")

    def test_preparing_is_not_execution_or_a_probability_preview(self):
        before = self.audit()
        with patch("rpg.engine.Context.draw", side_effect=AssertionError("preview must not draw")):
            proposal = self.prepare("attempt", {"check": "search"}, text="Je fouille")
        self.assertEqual(proposal["resolution"], "not_executed")
        self.assertNotIn("sample", canonical(proposal))
        self.assertEqual(before, self.audit())

    def test_commit_and_retry_across_process_restarts_return_identical_outcome(self):
        token = self.prepare()["proposal"]
        first = self.bridge.commit_action(token)
        reopened = RPGBridge(self.bridge.config, lambda: self.now)
        self.assertEqual(first, reopened.commit_action(token))
        self.assertEqual(first, reopened.get_request("purchase"))
        self.assertEqual(first["result"]["status"], "completed")
        self.assertEqual(next(r["value"] for r in first["state"]["resources"] if r["name"] == "coin"), 75)
        self.assertTrue(self.audit()["ok"])

    def test_retry_after_other_actions_returns_original_scene_and_information(self):
        first = self.bridge.commit_action(
            self.prepare("observe", {}, "observation", "Je regarde")["proposal"]
        )
        self.bridge.commit_action(
            self.prepare("travel", {"route": "path"}, "travel", "Je vais au phare")["proposal"]
        )
        self.assertEqual(self.bridge.get_state()["location"]["id"], "hill")
        self.assertEqual(self.bridge.get_request("observation"), first)
        self.assertEqual(first["state"]["location"]["id"], "port")
        self.assertEqual(first["new_information"][0]["fact"], "notice")

    def test_forged_payload_and_other_campaign_key_are_rejected(self):
        proposal = self.prepare()["proposal"]
        body, signature = proposal.split(".")
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        payload["arguments"]["max_price"] = 999
        forged_body = base64.urlsafe_b64encode(canonical(payload).encode()).decode().rstrip("=")
        before = self.audit()
        with self.assertRaises(InvalidAction):
            self.bridge.commit_action(forged_body + "." + signature)
        with self.assertRaises(InvalidAction):
            RPGBridge(BridgeConfig(self.path, b"a-different-secret-at-least-32bytes")).commit_action(proposal)
        self.assertEqual(before, self.audit())

    def test_same_key_cannot_apply_proposal_to_different_world_or_branch(self):
        proposal = self.prepare()["proposal"]
        path = Path(self.directory.name) / "other.db"
        store, _ = world(path)
        store.close()
        with self.assertRaises(InvalidAction):
            RPGBridge(BridgeConfig(path, self.bridge.config.secret), lambda: self.now).commit_action(proposal)
        with Store(self.path) as store:
            store.fork("main", 1, "other", "alternative")
        with self.assertRaises(InvalidAction):
            RPGBridge(
                BridgeConfig(self.path, self.bridge.config.secret, branch="other"), lambda: self.now
            ).commit_action(proposal)

    def test_stale_or_expired_proposal_cannot_commit_but_old_receipt_is_recoverable(self):
        proposal = self.prepare()["proposal"]
        self.now += 601
        with self.assertRaises(Conflict):
            self.bridge.commit_action(proposal)
        fresh = self.prepare()["proposal"]
        self.bridge.commit_action(fresh)
        self.now += 601
        self.assertEqual(self.bridge.commit_action(fresh), self.bridge.get_request("purchase"))
        late = self.prepare("wait", {"seconds": 5}, "stale", "J'attends 5 secondes")["proposal"]
        with Store(self.path) as store:
            Engine(store).execute(Command("another-controller", "player", "wait", {"seconds": 1}))
        with self.assertRaises(Conflict):
            self.bridge.commit_action(late)

    def test_reusing_request_id_for_new_text_or_arguments_is_rejected(self):
        self.bridge.commit_action(self.prepare()["proposal"])
        for args, text in (
            ({"offer": "dagger-sale", "max_price": 30}, "J'achète la dague"),
            ({"offer": "dagger-sale", "max_price": 25}, "Une autre intention"),
        ):
            with self.assertRaises(Conflict):
                self.prepare(args=args, text=text)

    def test_readonly_connection_cannot_commit_a_valid_signed_proposal(self):
        proposal = self.prepare()["proposal"]
        readonly = RPGBridge(
            BridgeConfig(self.path, self.bridge.config.secret, writable=False), lambda: self.now
        )
        before = self.audit()
        with self.assertRaises(InvalidAction):
            readonly.commit_action(proposal)
        self.assertEqual(before, self.audit())

    def test_no_actor_override_admin_actions_or_arbitrary_statistics(self):
        for action, args in (
            ("buy", {"offer": "dagger-sale", "max_price": 25, "actor": "scribe"}),
            ("correct", {"value": 999}),
            ("schedule", {}),
            ("buy", {"offer": "dagger-sale", "max_price": True}),
        ):
            with self.subTest(action=action):
                with self.assertRaises(InvalidAction):
                    self.prepare(action, args)

    def test_lost_response_does_not_repeat_probability_or_cost(self):
        proposal = self.prepare("attempt", {"check": "search"}, "check", "Je fouille")["proposal"]
        outcome = self.bridge.commit_action(proposal)
        with patch("rpg.engine.Context.draw", side_effect=AssertionError("retry must not draw")):
            self.assertEqual(outcome, self.bridge.commit_action(proposal))
        self.assertEqual(
            self.bridge.get_request("unknown"), {"status": "not_committed", "request_id": "unknown"}
        )

    def test_player_intention_is_auditable_and_unrelated_secrets_never_return(self):
        outcome = self.bridge.commit_action(self.prepare()["proposal"])
        with Store(self.path) as store:
            provenance = store.history("main")[-1]["provenance"]
            self.assertEqual(provenance["player_text"], "J'achète la dague")
        self.assertNotIn("SECRET_INACCESSIBLE", canonical(outcome))
        self.assertNotIn("île de Verre", canonical(outcome))
        self.assertNotIn(str(self.path), canonical(outcome))

    def test_secret_file_is_persistent_and_not_overwritten(self):
        path = Path(self.directory.name) / "keys" / "signing.key"
        first = load_or_create_key(path)
        self.assertEqual(len(first), 32)
        self.assertEqual(first, load_or_create_key(path))

    def test_visible_commit_arguments_must_match_signed_proposal(self):
        proposal = self.prepare()["proposal"]
        with self.assertRaises(InvalidAction):
            self.bridge.commit_action(proposal, "buy", {"offer": "boat-sale", "max_price": 999})
        self.assertEqual(self.bridge.get_state()["revision"], 1)

    def test_backend_never_calls_a_second_model_or_network(self):
        with patch("socket.socket.connect", side_effect=AssertionError("No outbound connection allowed")):
            proposal = self.prepare("attempt", {"check": "search"}, "no-model", "Je fouille")
            self.assertEqual(proposal["declared_terms"]["cost"], {"resource": "stamina", "amount": 1})
            self.bridge.commit_action(proposal["proposal"])

    def test_missing_save_is_not_created(self):
        path = Path(self.directory.name) / "missing.db"
        with self.assertRaises(InvalidAction):
            BridgeConfig(path, self.bridge.config.secret)
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()

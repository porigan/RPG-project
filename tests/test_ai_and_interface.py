import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from rpg import Conflict, InvalidAction, Store
from rpg.ai import AIInterpreter, AINarrator, HTTPModel, ModelError, validate_proposal
from rpg.model import canonical
from rpg.session import Session
from tests.support import ROOT, command, world


class InterfaceTests(unittest.TestCase):
    def setUp(self):
        self.store, self.engine = world()
        self.addCleanup(self.store.close)

    def test_offline_play_purchase_conversation_and_restart_idempotence(self):
        session = Session(self.engine, "player")
        result = session.turn("J'achète la dague", "natural-buy")
        self.assertIn("Dague", result["text"])
        self.assertEqual(self.store.state("main").get("resources", "player:coin")["value"], 75)
        self.assertEqual(Session(self.engine, "player").turn("J'achète la dague", "natural-buy"), result)
        with self.assertRaises(Conflict):
            session.turn("J'attends 1 minute", "natural-buy")
        session.turn("J'attends 5 minutes", "natural-wait")
        conversation = session.turn("Je parle à Nora de la destination", "natural-talk")
        self.assertIn("île de Verre", conversation["text"])
        self.assertNotIn("SECRET_INACCESSIBLE", canonical(conversation))
        self.assertTrue(self.store.audit("main")["ok"])

    def test_unknown_wording_does_not_upgrade_item(self):
        result = Session(self.engine, "player").turn("J'achète l'épée légendaire", "unknown")
        self.assertEqual(result["status"], "awaiting_decision")
        self.assertEqual(
            self.store.state("main").get("items", "dagger")["description"], "Une dague ordinaire en acier."
        )
        self.assertEqual(self.store.state("main").get("resources", "player:coin")["value"], 100)

    def test_invalid_model_proposals_cannot_write_resources_or_override_actor(self):
        view = self.engine.view("player")
        before = self.store.audit("main")
        invalid = [
            {
                "steps": [{"action": "resource.delta", "args": {}, "source": "je regarde"}],
                "clarification": None,
            },
            {
                "steps": [{"action": "observe", "args": {"actor": "scribe"}, "source": "je regarde"}],
                "clarification": None,
            },
            {
                "steps": [{"action": "observe", "args": {}, "source": "citation inventée"}],
                "clarification": None,
            },
            {
                "steps": [{"action": "travel", "args": {"route": "secret-route"}, "source": "je regarde"}],
                "clarification": None,
            },
            {
                "steps": [
                    {
                        "action": "tell",
                        "args": {"target": "merchant", "fact": "gm-secret"},
                        "source": "je regarde",
                    }
                ],
                "clarification": None,
            },
            {"steps": [], "clarification": None},
        ]
        for proposal in invalid:
            with self.subTest(proposal=proposal):
                with self.assertRaises(InvalidAction):
                    validate_proposal(proposal, "je regarde", view)
        self.assertEqual(before, self.store.audit("main"))

    def test_remote_narration_failure_preserves_committed_action_and_falls_back(self):
        class UnavailableNarrator:
            def render(self, facts):
                serialized = canonical(facts)
                if "SECRET_INACCESSIBLE" in serialized or "île de Verre" in serialized:
                    raise AssertionError("Leaked secret")
                raise ModelError("offline")

        result = Session(self.engine, "player", narrator=UnavailableNarrator()).turn(
            "J'achète la dague", "narrator-failure"
        )
        self.assertIn("warning", result)
        self.assertIn("Dague", result["text"])
        self.assertEqual(self.store.state("main").get("resources", "player:coin")["value"], 75)

    def test_narration_reference_validation(self):
        class BadModel:
            def complete(self, messages, schema):
                return {"text": "Un trésor apparaît.", "references": ["invented"]}

        with self.assertRaises(ModelError):
            AINarrator(BadModel()).render([{"id": "location", "text": "Un quai."}])

    def test_ai_proposal_prepared_before_other_action_is_rejected(self):
        class SlowModel:
            def complete(inner, messages, schema):
                command(self.engine, "wait", {"seconds": 1})
                return {
                    "steps": [
                        {
                            "action": "buy",
                            "args": {"offer": "dagger-sale", "max_price": 25},
                            "source": "J'achète la dague",
                        }
                    ],
                    "clarification": None,
                }

        with self.assertRaises(Conflict):
            Session(self.engine, "player", interpreter=AIInterpreter(SlowModel())).turn(
                "J'achète la dague", "stale-ai"
            )
        self.assertEqual(self.store.state("main").get("resources", "player:coin")["value"], 100)

    def test_cli_is_playable_in_real_subprocess_and_database_reopens(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "game.db")
            environment = dict(os.environ, PYTHONUTF8="1")
            created = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "rpg",
                    "--db",
                    path,
                    "init",
                    "--universe",
                    str(ROOT / "examples/universes/harbor.json"),
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            played = subprocess.run(
                [sys.executable, "-m", "rpg", "--db", path, "play"],
                cwd=ROOT,
                env=environment,
                input="J'achète la dague\ninventaire\nquitter\n",
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(played.returncode, 0, played.stderr)
            self.assertIn("Vous achetez Dague", played.stdout)
            self.assertIn("coin : 75", played.stdout)
            with Store(path) as reopened:
                self.assertEqual(reopened.state("main").get("items", "dagger")["owner"], "player")
                self.assertTrue(reopened.audit("main")["ok"])


class HTTPTransportTests(unittest.TestCase):
    """Actual loopback HTTP; the response server is NOT a real language model."""

    def setUp(self):
        self.requests = []
        self.response = {
            "steps": [{"action": "observe", "args": {}, "source": "Je regarde"}],
            "clarification": None,
        }
        self.protocol = "ollama"
        self.status = 200
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append((data, dict(self.headers)))
                content = canonical(outer.response)
                envelope = (
                    {"message": {"content": content}}
                    if outer.protocol == "ollama"
                    else {"choices": [{"message": {"content": content}}]}
                )
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                if outer.status == 302:
                    self.send_header("Location", "https://example.invalid/steal")
                self.end_headers()
                self.wfile.write(canonical(envelope).encode())

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.endpoint = f"http://127.0.0.1:{self.server.server_port}/chat"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def test_ollama_and_chat_completions_transport(self):
        for protocol in ("ollama", "chat-completions"):
            self.protocol = protocol
            model = HTTPModel(self.endpoint, "test-model", protocol, api_key="test-key")
            output = model.complete([{"role": "user", "content": "test"}], {"type": "object"})
            self.assertEqual(output, self.response)
            body, headers = self.requests[-1]
            self.assertFalse(body["stream"])
            self.assertEqual(headers["Authorization"], "Bearer test-key")
            self.assertEqual(body["model"], "test-model")
            self.assertIn("format" if protocol == "ollama" else "response_format", body)

    def test_http_errors_do_not_disclose_keys_and_redirect_is_refused(self):
        model = HTTPModel(self.endpoint, "test-model", api_key="do-not-log-this")
        for status in (401, 302):
            self.status = status
            with self.assertRaises(ModelError) as raised:
                model.complete([], {})
            self.assertNotIn("do-not-log-this", str(raised.exception))
            self.assertIn(str(status), str(raised.exception))

    def test_real_http_interpretation_and_actual_engine_execution(self):
        store, engine = world()
        self.addCleanup(store.close)
        model = HTTPModel(self.endpoint, "http-fixture")
        result = Session(engine, "player", interpreter=AIInterpreter(model)).turn("Je regarde", "http-turn")
        self.assertIn("quai", result["text"])
        sent = canonical(self.requests[0][0])
        self.assertNotIn("SECRET_INACCESSIBLE", sent)
        self.assertNotIn("île de Verre", sent)
        self.assertTrue(store.audit("main")["ok"])

    def test_missing_configuration_and_unsafe_endpoint(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ModelError):
                HTTPModel.from_environment()
        for endpoint in (
            "http://example.com/chat",
            "https://user:password@example.com/chat",
            "https://example.com/chat?key=secret",
        ):
            with self.assertRaises(ModelError):
                HTTPModel(endpoint, "model")


if __name__ == "__main__":
    unittest.main()

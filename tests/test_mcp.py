"""Real SDK clients over stdio and authenticated Streamable HTTP, no model API."""

import asyncio
import functools
import importlib.util
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from rpg import Store
from tests.support import ROOT, world

HAS_MCP = importlib.util.find_spec("mcp") is not None
if HAS_MCP:
    from mcp import Client, StdioServerParameters


def bounded(method):
    @functools.wraps(method)
    async def wrapped(*args, **kwargs):
        async with asyncio.timeout(35):
            return await method(*args, **kwargs)

    return wrapped


@unittest.skipUnless(HAS_MCP, "Installer .[mcp] pour tester les transports MCP officiels")
class MCPTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "save.db"
        store, _ = world(self.path)
        store.close()
        self.key = Path(self.directory.name) / "signing.key"
        self.key.write_bytes(b"mcp-test-secret-for-proposals-32-bytes")
        self.args = ["-m", "rpg.mcp_server", "--db", str(self.path), "--secret-file", str(self.key)]
        self.env = dict(os.environ, PYTHONUTF8="1", OTEL_SDK_DISABLED="true")

    def stdio(self, *extra):
        return Client(
            StdioServerParameters(command=sys.executable, args=[*self.args, *extra], cwd=ROOT, env=self.env),
            read_timeout_seconds=15,
        )

    @bounded
    async def test_stdio_discovery_and_real_purchase_round_trip(self):
        async with self.stdio() as client:
            tools = await client.list_tools()
            definitions = {tool.name: tool for tool in tools.tools}
            self.assertEqual(
                set(definitions),
                {"rpg_get_state", "rpg_prepare_action", "rpg_get_request", "rpg_commit_action"},
            )
            self.assertTrue(definitions["rpg_get_state"].annotations.read_only_hint)
            self.assertTrue(definitions["rpg_prepare_action"].annotations.read_only_hint)
            self.assertFalse(definitions["rpg_commit_action"].annotations.read_only_hint)
            self.assertTrue(definitions["rpg_commit_action"].annotations.idempotent_hint)
            for definition in definitions.values():
                self.assertNotIn("database", definition.input_schema["properties"])
                self.assertNotIn("actor", definition.input_schema["properties"])
            state = (await client.call_tool("rpg_get_state", {})).structured_content
            prepared = (
                await client.call_tool(
                    "rpg_prepare_action",
                    {
                        "request_id": "sdk-buy",
                        "player_text": "J'achète la dague",
                        "action": "buy",
                        "arguments": {"offer": "dagger-sale", "max_price": 25},
                        "expected_revision": state["revision"],
                    },
                )
            ).structured_content
            self.assertEqual(prepared["resolution"], "not_executed")
            commit_args = {
                "proposal": prepared["proposal"],
                "action": "buy",
                "arguments": {"offer": "dagger-sale", "max_price": 25},
            }
            result = await client.call_tool("rpg_commit_action", commit_args)
            self.assertFalse(result.is_error)
            self.assertEqual(result.structured_content["result"]["status"], "completed")
            again = await client.call_tool("rpg_commit_action", commit_args)
            self.assertEqual(result.structured_content, again.structured_content)
        with Store(self.path) as store:
            self.assertEqual(store.state("main").get("resources", "player:coin")["value"], 75)
            self.assertTrue(store.audit("main")["ok"])

    @bounded
    async def test_stdio_readonly_server_has_no_write_tool(self):
        async with self.stdio("--read-only") as client:
            tools = await client.list_tools()
            self.assertNotIn("rpg_commit_action", {t.name for t in tools.tools})
            result = await client.call_tool("rpg_get_state", {})
            self.assertFalse(result.is_error)
        with Store(self.path) as store:
            self.assertEqual(store.branch("main")["revision"], 1)

    @bounded
    async def test_stdio_invalid_action_returns_tool_error_without_writing(self):
        async with self.stdio() as client:
            result = await client.call_tool(
                "rpg_prepare_action",
                {
                    "request_id": "bad",
                    "player_text": "Je regarde",
                    "action": "correct",
                    "arguments": {"statistics": 999},
                    "expected_revision": 1,
                },
            )
            self.assertTrue(result.is_error)
            self.assertIn("INVALID_ACTION", result.content[0].text)
            tampered = await client.call_tool(
                "rpg_commit_action", {"proposal": "fake.invalid", "action": "observe", "arguments": {}}
            )
            self.assertTrue(tampered.is_error)
        with Store(self.path) as store:
            self.assertEqual(store.branch("main")["revision"], 1)

    @bounded
    async def test_http_requires_authentication_and_sdk_initializes_real_transport(self):
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        env = dict(self.env, RPG_MCP_TOKEN="a-local-http-test-token-at-least-32-characters")
        process = subprocess.Popen(
            [sys.executable, *self.args, "--transport", "streamable-http", "--port", str(port)],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            url = f"http://127.0.0.1:{port}/mcp"
            deadline = time.monotonic() + 20
            while True:
                if process.poll() is not None:
                    raise AssertionError(process.stderr.read().decode("utf-8", errors="replace"))
                try:
                    urllib.request.urlopen(url, timeout=1).close()
                    self.fail("Unauthenticated HTTP must never succeed")
                except urllib.error.HTTPError as exc:
                    code = exc.code
                    exc.close()
                    self.assertEqual(code, 401)
                    break
                except urllib.error.URLError:
                    if time.monotonic() >= deadline:
                        raise AssertionError("HTTP server did not start") from None
                    await asyncio.sleep(0.05)
            import httpx2
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client

            hostile = urllib.request.Request(
                url,
                headers={
                    "Authorization": "Bearer " + env["RPG_MCP_TOKEN"],
                    "Origin": "https://untrusted.example",
                },
            )
            with self.assertRaises(urllib.error.HTTPError) as rejected:
                urllib.request.urlopen(hostile, timeout=3).close()
            self.assertEqual(rejected.exception.code, 403)
            rejected.exception.close()
            async with httpx2.AsyncClient(
                headers={"Authorization": "Bearer " + env["RPG_MCP_TOKEN"]}
            ) as http:
                async with streamable_http_client(url, http_client=http) as (reader, writer):
                    async with ClientSession(reader, writer) as client:
                        await client.initialize()
                        result = await client.call_tool("rpg_get_state", {})
                        self.assertFalse(result.is_error)
                        self.assertEqual(result.structured_content["revision"], 1)
        finally:
            process.terminate()
            process.communicate(timeout=10)


if __name__ == "__main__":
    unittest.main()

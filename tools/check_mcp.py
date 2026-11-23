"""Read-only protocol probe using the exact locally registered MCP launch command."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tomllib
from pathlib import Path


async def check(name: str):
    from mcp import Client, StdioServerParameters

    from rpg import Store

    config_path = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "config.toml"
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    entry = config.get("mcp_servers", {}).get(name)
    if not entry or "command" not in entry or entry.get("enabled") is False:
        raise RuntimeError("Le serveur local demandé n'est pas configuré ou activé.")
    args = entry.get("args", [])
    if "rpg.mcp_server" not in args or "--db" not in args:
        raise RuntimeError("La configuration sélectionnée ne désigne pas le serveur RPG attendu.")
    database = args[args.index("--db") + 1]
    branch = args[args.index("--branch") + 1] if "--branch" in args else "main"
    with Store(database, readonly=True) as store:
        before = store.branch(branch)["revision"]
    environment = dict(os.environ, PYTHONUTF8="1", OTEL_SDK_DISABLED="true", **entry.get("env", {}))
    params = StdioServerParameters(command=entry["command"], args=args, cwd=entry.get("cwd"), env=environment)
    async with asyncio.timeout(35):
        async with Client(params, read_timeout_seconds=15) as client:
            tools = await client.list_tools()
            names = {tool.name for tool in tools.tools}
            if not {"rpg_get_state", "rpg_prepare_action", "rpg_get_request"} <= names:
                raise RuntimeError("Catalogue RPG incomplet.")
            state = await client.call_tool("rpg_get_state", {})
            if state.is_error:
                raise RuntimeError("Le serveur a refusé la lecture d'état.")
            actor = state.structured_content["actor"]["id"]
    with Store(database, readonly=True) as store:
        after = store.branch(branch)["revision"]
    print(
        json.dumps(
            {
                "server": name,
                "sdk_connection": "verified",
                "tools": sorted(names),
                "actor": actor,
                "revision_before": before,
                "revision_after": after,
                "world_unchanged": before == after,
                "chatgpt_conversation_call": "not_verified_by_this_probe",
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default="rpg-engine")
    asyncio.run(check(parser.parse_args().server))

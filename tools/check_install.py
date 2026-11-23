"""Verify the installed package from outside the source checkout."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()
    executable = str(Path(args.python).resolve())
    root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ, PYTHONUTF8="1")
    with tempfile.TemporaryDirectory() as directory:

        def run(*arguments, input=None):
            result = subprocess.run(
                [executable, *arguments],
                cwd=directory,
                env=environment,
                input=input,
                text=True,
                encoding="utf-8",
                capture_output=True,
                timeout=30,
                check=True,
            )
            return result.stdout

        imported = Path(run("-c", "import rpg; print(rpg.__file__)").strip()).resolve()
        if imported == root / "rpg" / "__init__.py":
            raise AssertionError("The source checkout shadowed the installed package")
        database = str(Path(directory) / "installed.db")
        run("-m", "rpg", "--db", database, "init", "--universe", str(root / "examples/universes/harbor.json"))
        played = run("-m", "rpg", "--db", database, "play", input="J'achète la dague\ninventaire\nquitter\n")
        if "coin : 75" not in played:
            raise AssertionError(played)
        report = json.loads(run("-m", "rpg", "--db", database, "audit"))
        if not report["ok"]:
            raise AssertionError(report)
        bridge = json.loads(run("-m", "rpg.mcp_server", "--db", database, "--check"))
        if not bridge["ready"] or bridge["model_api_required"]:
            raise AssertionError(bridge)
        print(
            json.dumps(
                {"installed_package": str(imported), "purchase_and_reopen": "passed", "audit": report},
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()

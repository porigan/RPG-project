from pathlib import Path

from rpg import Command, Engine, Store
from rpg.universe import initialize, load_file

ROOT = Path(__file__).resolve().parents[1]


def config():
    return load_file(ROOT / "examples" / "universes" / "harbor.json")


def world(path=":memory:", seed="reproducible-test-seed", budget=1000, configuration=None):
    store = Store(path)
    engine = Engine(store, budget=budget)
    initialize(engine, configuration or config(), seed=seed)
    return store, engine


def command(engine, kind, args=None, ident=None, branch="main", revision=None):
    if ident is None:
        ident = f"test-{engine.store.branch(branch)['revision']}-{kind}"
    return engine.execute(Command(ident, "player", kind, args or {}, revision), branch)


def trigger(ident="reward", due=10, amount=1, **extra):
    return {
        "id": ident,
        "mode": "time",
        "due": due,
        "status": "active",
        "action": {
            "kind": "resource.delta",
            "actor": "player",
            "resource": "coin",
            "amount": amount,
            "reason": "règle de test",
        },
        **extra,
    }

"""Shared contracts; no universe mechanics are imposed by this module."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


class InvalidAction(ValueError):
    """A proposal cannot be executed; the current transaction is rolled back."""


class Conflict(InvalidAction):
    """The command was prepared against stale state or reuses an identifier."""


class LimitExceeded(InvalidAction):
    """A work budget was exhausted. Nothing in this command was committed."""


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def integer(value: Any, name: str, minimum: int = 0, maximum: int = 10**12) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise InvalidAction(f"{name}: entier attendu entre {minimum} et {maximum}.")
    return value


def identifier(value: Any, name: str = "identifiant") -> str:
    if not isinstance(value, str) or not value or len(value) > 200 or any(ord(c) < 32 for c in value):
        raise InvalidAction(f"{name}: texte non vide attendu (200 caractères maximum).")
    return value


def component(value: Any, name: str = "identifiant de catalogue") -> str:
    identifier(value, name)
    if not all(c.isalnum() or c in "_.-" for c in value):
        raise InvalidAction(f"{name}: utilisez lettres, chiffres, tiret, point ou soulignement.")
    return value


def probability(value: Any) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
        raise InvalidAction("Probabilité attendue entre 0 et 1.")
    return float(value)


@dataclass(frozen=True)
class Command:
    id: str
    actor: str
    kind: str
    args: dict[str, Any] = field(default_factory=dict)
    expected_revision: int | None = None

    def identity(self) -> dict[str, Any]:
        identifier(self.id)
        identifier(self.actor)
        identifier(self.kind)
        if not isinstance(self.args, dict):
            raise InvalidAction("Les arguments doivent être un objet JSON.")
        try:
            canonical(self.args)
        except (ValueError, TypeError, RecursionError):
            raise InvalidAction("Arguments JSON invalides.") from None
        return {"actor": self.actor, "kind": self.kind, "args": self.args}


class Module(Protocol):
    name: str
    version: str
    dependencies: tuple[str, ...]
    actions: dict[str, Callable[..., dict[str, Any]]]
    collections: tuple[str, ...]
    validators: dict[str, Callable]


class State:
    """Lazy, copy-on-read projection with an isolated transaction overlay.

    Changes are produced only through events. Selectors push exact indexed
    fields down to SQLite rather than loading the complete world.
    """

    def __init__(self, get: Callable, find: Callable):
        self._get = get
        self._find = find
        self.overlay: dict[tuple[str, str], dict | None] = {}

    def get(self, category: str, key: str, required: bool = True) -> dict | None:
        ref = (category, key)
        value = self.overlay[ref] if ref in self.overlay else self._get(category, key)
        if value is None and required:
            raise InvalidAction(f"Élément inconnu : {category}/{key}.")
        return copy.deepcopy(value)

    def find(self, category: str, **filters: Any) -> list[dict]:
        rows = {r["id"]: r for r in self._find(category, filters)}
        for (cat, key), value in self.overlay.items():
            if cat != category:
                continue
            rows.pop(key, None)
            if value is not None and all(
                (value.get("due", 10**30) <= v if k == "due_lte" else value.get(k) == v)
                for k, v in filters.items()
            ):
                rows[key] = value
        return copy.deepcopy([rows[k] for k in sorted(rows)])

    def apply(self, changes: list[dict]) -> None:
        for change in changes:
            ref = (change["category"], change["key"])
            if self.get(*ref, required=False) != change["before"]:
                raise Conflict(f"Précondition de changement invalide : {ref}.")
            self.overlay[ref] = copy.deepcopy(change["after"])

    @property
    def now(self) -> int:
        return self.get("world", "clock")["value"]


class Registry:
    def __init__(self, modules: list[Module]):
        self.modules = {m.name: m for m in modules}
        if len(self.modules) != len(modules):
            raise InvalidAction("Module dupliqué.")
        self.actions: dict[str, tuple[str, Callable]] = {}
        self.owners: dict[str, str] = {}
        self.validators: dict[str, Callable] = {}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(name: str) -> None:
            if name not in self.modules:
                raise InvalidAction(f"Dépendance absente : {name}.")
            if name in visiting:
                raise InvalidAction(f"Cycle de dépendances : {name}.")
            if name in visited:
                return
            visiting.add(name)
            module = self.modules[name]
            for dependency in module.dependencies:
                visit(dependency)
            for action, handler in module.actions.items():
                if action in self.actions:
                    raise InvalidAction(f"Action déclarée deux fois : {action}.")
                self.actions[action] = (name, handler)
            for category in module.collections:
                component(category, "collection de module")
                if category in self.owners:
                    raise InvalidAction(f"Collection déclarée deux fois : {category}.")
                self.owners[category] = name
            if set(module.validators) - set(module.collections):
                raise InvalidAction(f"Validateur sans collection déclarée : {name}.")
            self.validators.update(module.validators)
            visiting.remove(name)
            visited.add(name)

        for module_name in self.modules:
            visit(module_name)

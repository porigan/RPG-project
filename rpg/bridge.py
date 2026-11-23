"""ChatGPT is the GM; this service only reads and resolves engine commands.

No language model, network call, sampling preview or arbitrary state patch lives
here. A signed proposal binds one action to one save, actor and revision.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .contracts import ACTION_ARGS, validate_action
from .engine import Engine
from .knowledge import actor_view
from .model import Command, Conflict, InvalidAction, State, canonical, digest, identifier, integer
from .store import Store


@dataclass(frozen=True)
class BridgeConfig:
    database: Path
    secret: bytes = field(repr=False)
    branch: str = "main"
    actor: str | None = None
    name: str = "Ma campagne RPG"
    writable: bool = True
    proposal_ttl: int = 600

    def __post_init__(self):
        path = Path(self.database).resolve()
        if not path.is_file():
            raise InvalidAction("La sauvegarde doit déjà exister. Créez-la avec rpg init si nécessaire.")
        object.__setattr__(self, "database", path)
        if not isinstance(self.secret, bytes) or len(self.secret) < 32:
            raise InvalidAction("Une clé de signature locale d'au moins 32 octets est nécessaire.")
        identifier(self.branch)
        identifier(self.name, "nom de campagne")
        integer(self.proposal_ttl, "durée de proposition", 30, 3600)


def load_or_create_key(path: Path) -> bytes:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(secrets.token_bytes(32))
        path.chmod(0o600)
    except FileExistsError:
        pass
    value = path.read_bytes()
    if len(value) < 32 or len(value) > 4096:
        raise InvalidAction("Clé locale invalide ; ne remplacez pas une clé existante sans raison.")
    return value


class RPGBridge:
    def __init__(self, config: BridgeConfig, clock: Callable[[], float] = time.time):
        self.config, self.clock = config, clock
        with Store(config.database, readonly=True) as store:
            settings = store.state(config.branch).get("world", "config")
            self.actor = config.actor or settings["player"]
            if store.state(config.branch).get("entities", self.actor)["kind"] != "player":
                raise InvalidAction("La connexion doit être liée à un personnage joueur.")
            initial = store.history(config.branch, 1)[0]["id"]
            self.binding = digest([initial, config.branch, self.actor])

    def get_state(self, include_notes: bool = False) -> dict:
        if type(include_notes) is not bool:
            raise InvalidAction("include_notes doit être un booléen.")
        with Store(self.config.database, readonly=True) as store:
            return self._public_view(Engine(store).view(self.actor, self.config.branch), include_notes)

    def _public_view(self, view, include_notes=False):
        view = dict(view)
        if not include_notes:
            for key in ("beliefs", "memories", "relations"):
                view.pop(key, None)
        view["campaign"] = self.config.name
        view["action_contracts"] = {
            action: {
                "arguments": list(ACTION_ARGS[action]),
                "optional": ["topic"] if action == "talk" else [],
            }
            for action in view["actions"]
            if action in ACTION_ARGS
        }
        return view

    def _command_id(self, request_id):
        identifier(request_id, "identifiant de tentative")
        return "chatgpt:" + digest([self.binding, request_id])

    def _command(self, payload):
        return Command(
            self._command_id(payload["request_id"]),
            self.actor,
            payload["action"],
            payload["arguments"],
            payload["revision"],
            {"source": "chatgpt", "player_text": payload["player_text"], "request_id": payload["request_id"]},
        )

    def prepare_action(
        self, request_id: str, player_text: str, action: str, arguments: dict, expected_revision: int
    ) -> dict:
        self._command_id(request_id)
        if not isinstance(player_text, str) or not player_text.strip() or len(player_text) > 8000:
            raise InvalidAction("Citez l'intention du joueur, entre 1 et 8 000 caractères.")
        integer(expected_revision, "révision attendue", 1)
        payload = {
            "schema": 1,
            "binding": self.binding,
            "request_id": request_id,
            "player_text": player_text,
            "action": action,
            "arguments": arguments,
            "revision": expected_revision,
            "expires": int(self.clock()) + self.config.proposal_ttl,
        }
        command = self._command(payload)
        fingerprint = digest(command.identity())
        with Store(self.config.database, readonly=True) as store:
            # Receipt recovery takes precedence over current visibility or stale revision.
            existing = store.receipt(self.config.branch, command.id, fingerprint)
            if existing is not None:
                return {"status": "already_committed", "outcome": self._outcome(store, request_id, existing)}
            view = Engine(store).view(self.actor, self.config.branch)
            if view["revision"] != expected_revision:
                raise Conflict(
                    "L'état a changé : consultez la campagne avant de préparer une nouvelle proposition."
                )
            validate_action(action, arguments, view)
            catalogs = {
                "travel": ("routes", "route"),
                "attempt": ("checks", "check"),
                "activate": ("abilities", "ability"),
                "attack": ("moves", "move"),
                "train": ("training", "training"),
                "experience": ("experiences", "experience"),
                "befriend": ("encounters", "encounter"),
            }
            declared = {}
            if action in catalogs:
                category, key = catalogs[action]
                definition = store.state(self.config.branch).get(category, arguments[key])
                declared = {name: definition[name] for name in ("duration", "cost") if name in definition}
        body = canonical(payload).encode("utf-8")
        if len(body) > 24_000:
            raise InvalidAction("Proposition trop volumineuse.")
        encoded = base64.urlsafe_b64encode(body).decode().rstrip("=")
        signature = hmac.new(self.config.secret, encoded.encode(), hashlib.sha256).hexdigest()
        return {
            "status": "prepared",
            "proposal": encoded + "." + signature,
            "action": action,
            "arguments": arguments,
            "player_text": player_text,
            "revision": expected_revision,
            "expires_at": payload["expires"],
            "resolution": "not_executed",
            "rules_checked": "syntax_and_visible_references_only",
            "declared_terms": declared,
            "notice": "Aucun tirage ni changement effectué. Les préconditions métier seront vérifiées à la confirmation.",
        }

    def _decode(self, proposal):
        if not isinstance(proposal, str) or len(proposal) > 34_000:
            raise InvalidAction("Proposition signée invalide.")
        try:
            encoded, supplied = proposal.split(".")
            expected = hmac.new(self.config.secret, encoded.encode("ascii"), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(supplied, expected):
                raise InvalidAction("Signature de proposition invalide.")
            payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
            if payload["schema"] != 1 or payload["binding"] != self.binding:
                raise InvalidAction("Proposition destinée à une autre campagne, branche ou personnage.")
            self._command(payload).identity()
            return payload
        except (ValueError, KeyError, TypeError, UnicodeError, RecursionError):
            raise InvalidAction("Proposition signée invalide.") from None

    def commit_action(self, proposal: str, action: str | None = None, arguments: dict | None = None) -> dict:
        if not self.config.writable:
            raise InvalidAction("Cette connexion est strictement en lecture seule.")
        payload = self._decode(proposal)
        if action is not None:
            try:
                matches = action == payload["action"] and canonical(arguments) == canonical(
                    payload["arguments"]
                )
            except (TypeError, ValueError, RecursionError):
                matches = False
            if not matches:
                raise InvalidAction("L'action affichée ne correspond pas à la proposition signée.")
        command = self._command(payload)
        with Store(self.config.database) as store:
            previous = store.receipt(self.config.branch, command.id, digest(command.identity()))
            if previous is None and payload["expires"] <= self.clock():
                raise Conflict("Proposition expirée : relisez l'état et préparez une nouvelle proposition.")
            result = Engine(store).execute(command, self.config.branch)
            return self._outcome(store, payload["request_id"], result)

    def get_request(self, request_id: str) -> dict:
        command_id = self._command_id(request_id)
        with Store(self.config.database, readonly=True) as store:
            row = store.db.execute(
                "SELECT result FROM receipts WHERE branch=? AND command_id=?",
                (self.config.branch, command_id),
            ).fetchone()
            if row is None:
                return {"status": "not_committed", "request_id": request_id}
            return self._outcome(store, request_id, json.loads(row[0]))

    def _outcome(self, store, request_id, result):
        # Reconstruct the committed revision, not today's scene: a late retry
        # must return exactly the original facts even after subsequent actions.
        records = store.replay(self.config.branch, result["revision"])
        state = State(
            lambda category, key: records.get((category, key)),
            lambda category, filters: [
                v
                for (c, _), v in records.items()
                if c == category and all(v.get(k) == value for k, value in filters.items())
            ],
        )
        view = actor_view(state, self.actor)
        view["revision"] = result["revision"]
        config = state.get("world", "config")
        registry = Engine(store).registry
        view["actions"] = sorted(a for a, (m, _) in registry.actions.items() if m in config["modules"])
        information = {}
        memories = []
        command_events = set(result["events"])
        for event in store.history(self.config.branch, result["revision"]):
            if event["id"] not in command_events:
                continue
            for change in event["changes"]:
                after = change["after"]
                if not after or after.get("actor") != self.actor:
                    continue
                if change["category"] == "beliefs":
                    information[after["subject"]] = {
                        "fact": after["subject"],
                        "statement": after["statement"],
                        "value": after["value"],
                        "certainty": after["certainty"],
                    }
                elif change["category"] == "experiences_done":
                    memories.append(after["description"])
        safe_result = {k: v for k, v in result.items() if k not in {"events", "branch", "command_id"}}
        return {
            "request_id": request_id,
            "result": safe_result,
            "state": self._public_view(view),
            "new_information": list(information.values()),
            "new_memories": memories,
            "authority": "committed_engine_result",
            "narration": "ChatGPT poursuit la narration à partir de ces faits ; les croyances ne sont pas des vérités garanties.",
        }

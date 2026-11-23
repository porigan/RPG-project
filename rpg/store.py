"""SQLite unit of work, immutable event history, branches and recovery tools."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .model import Conflict, InvalidAction, LimitExceeded, State, canonical, digest, identifier, integer

SCHEMA_VERSION = 1
INDEX_FIELDS = {"actor", "subject", "location", "status", "due", "mode"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS branches (
 id TEXT PRIMARY KEY, parent TEXT REFERENCES branches(id), fork_seq INTEGER NOT NULL,
 revision INTEGER NOT NULL, head_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
 branch TEXT NOT NULL REFERENCES branches(id), seq INTEGER NOT NULL,
 id TEXT NOT NULL UNIQUE, body TEXT NOT NULL, hash TEXT NOT NULL,
 PRIMARY KEY(branch, seq)
);
CREATE TABLE IF NOT EXISTS records (
 branch TEXT NOT NULL REFERENCES branches(id), category TEXT NOT NULL,
 key TEXT NOT NULL, body TEXT NOT NULL,
 actor TEXT GENERATED ALWAYS AS (json_extract(body, '$.actor')) VIRTUAL,
 subject TEXT GENERATED ALWAYS AS (json_extract(body, '$.subject')) VIRTUAL,
 location TEXT GENERATED ALWAYS AS (json_extract(body, '$.location')) VIRTUAL,
 status TEXT GENERATED ALWAYS AS (json_extract(body, '$.status')) VIRTUAL,
 due INTEGER GENERATED ALWAYS AS (json_extract(body, '$.due')) VIRTUAL,
 mode TEXT GENERATED ALWAYS AS (json_extract(body, '$.mode')) VIRTUAL,
 PRIMARY KEY(branch, category, key)
);
CREATE INDEX IF NOT EXISTS records_actor ON records(branch,category,actor);
CREATE INDEX IF NOT EXISTS records_subject ON records(branch,category,subject);
CREATE INDEX IF NOT EXISTS records_pending_subject ON records(branch,category,subject,status,due);
CREATE INDEX IF NOT EXISTS records_location ON records(branch,category,location);
CREATE INDEX IF NOT EXISTS records_due ON records(branch,category,status,mode,due);
CREATE TABLE IF NOT EXISTS receipts (
 branch TEXT NOT NULL REFERENCES branches(id), command_id TEXT NOT NULL,
 fingerprint TEXT NOT NULL, result TEXT NOT NULL, first_seq INTEGER NOT NULL,
 last_seq INTEGER NOT NULL, PRIMARY KEY(branch,command_id)
);
CREATE TABLE IF NOT EXISTS event_refs (
 event_id TEXT NOT NULL REFERENCES events(id), category TEXT NOT NULL,
 key TEXT NOT NULL, effective_at INTEGER NOT NULL,
 PRIMARY KEY(event_id,category,key)
);
CREATE INDEX IF NOT EXISTS refs_history ON event_refs(category,key,effective_at);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
 BEGIN SELECT RAISE(ABORT, 'append-only events'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
 BEGIN SELECT RAISE(ABORT, 'append-only events'); END;
CREATE TRIGGER IF NOT EXISTS receipts_no_update BEFORE UPDATE ON receipts
 BEGIN SELECT RAISE(ABORT, 'immutable receipts'); END;
CREATE TRIGGER IF NOT EXISTS receipts_no_delete BEFORE DELETE ON receipts
 BEGIN SELECT RAISE(ABORT, 'immutable receipts'); END;
"""


class Store:
    def __init__(self, path: str | Path = ":memory:", *, readonly: bool = False):
        self.path = str(path)
        self.readonly = readonly
        if readonly:
            if self.path == ":memory:":
                raise InvalidAction("La lecture seule exige une sauvegarde existante.")
            self.db = sqlite3.connect(
                Path(path).resolve().as_uri() + "?mode=ro", uri=True, isolation_level=None, timeout=5
            )
        else:
            self.db = sqlite3.connect(self.path, isolation_level=None, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        if not readonly:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in ((SCHEMA_VERSION,) if readonly else (0, SCHEMA_VERSION)):
            self.db.close()
            raise InvalidAction(f"Schéma {version} non pris en charge, attendu {SCHEMA_VERSION}.")
        if not readonly:
            self.db.executescript(SCHEMA)
            self.db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def close(self) -> None:
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @contextmanager
    def transaction(self):
        if self.db.in_transaction:
            raise RuntimeError("Nested transaction is forbidden")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def branch(self, name: str) -> dict:
        row = self.db.execute("SELECT * FROM branches WHERE id=?", (name,)).fetchone()
        if row is None:
            raise InvalidAction(f"Chronologie inconnue : {name}.")
        return dict(row)

    def create_root(self, name: str) -> None:
        identifier(name)
        if self.db.execute("SELECT 1 FROM branches WHERE id=?", (name,)).fetchone():
            raise InvalidAction("Cette chronologie existe déjà.")
        self.db.execute("INSERT INTO branches VALUES (?,NULL,0,0,?)", (name, "0" * 64))

    def state(self, branch: str) -> State:
        self.branch(branch)

        def get(category, key):
            row = self.db.execute(
                "SELECT body FROM records WHERE branch=? AND category=? AND key=?", (branch, category, key)
            ).fetchone()
            return json.loads(row[0]) if row else None

        def find(category, filters):
            params = [branch, category]
            clauses = ["branch=?", "category=?"]
            for field, value in filters.items():
                if field == "due_lte":
                    integer(value, "borne temporelle")
                    clauses.append("due<=?")
                    params.append(value)
                    continue
                if field not in INDEX_FIELDS:
                    raise InvalidAction(f"Sélecteur non indexé : {field}.")
                clauses.append(f"{field} IS ?")
                params.append(value)
            sql = "SELECT body FROM records WHERE " + " AND ".join(clauses) + " ORDER BY key"
            rows = self.db.execute(sql + " LIMIT 2001", params).fetchall()
            if len(rows) > 2000:
                raise LimitExceeded(
                    "Sélection trop volumineuse ; une pagination ou un périmètre plus précis est nécessaire."
                )
            return [json.loads(row[0]) for row in rows]

        return State(get, find)

    def receipt(self, branch: str, command_id: str, fingerprint: str) -> dict | None:
        row = self.db.execute(
            "SELECT * FROM receipts WHERE branch=? AND command_id=?", (branch, command_id)
        ).fetchone()
        if row is None:
            return None
        if row["fingerprint"] != fingerprint:
            raise Conflict("Identifiant déjà utilisé avec une autre commande.")
        return json.loads(row["result"])

    def commit_events(self, branch: str, events: list[dict], expected_revision: int) -> list[dict]:
        if not self.db.in_transaction:
            raise RuntimeError("Event commit requires an active transaction")
        meta = self.branch(branch)
        if meta["revision"] != expected_revision:
            raise Conflict("L'état a changé : proposition à réévaluer.")
        seq, previous = meta["revision"], meta["head_hash"]
        ancestry = dict(self._ancestry(branch))
        view = self.state(branch)
        committed = []
        for proposal in events:
            seq += 1
            event = dict(proposal, branch=branch, seq=seq, previous_hash=previous)
            ancestry[branch] = seq - 1
            for cause in event["causes"]:
                row = self.db.execute("SELECT branch,seq,body FROM events WHERE id=?", (cause,)).fetchone()
                if (
                    row is None
                    or row["branch"] not in ancestry
                    or row["seq"] > ancestry[row["branch"]]
                    or json.loads(row["body"])["effective_at"] > event["effective_at"]
                ):
                    raise InvalidAction(
                        "Cause absente, étrangère à la branche ou située dans le futur fictif."
                    )
            view.apply(event["changes"])
            body = canonical(event)
            previous = digest(event)
            self.db.execute(
                "INSERT INTO events VALUES (?,?,?,?,?)", (branch, seq, event["id"], body, previous)
            )
            for change in event["changes"]:
                self.db.execute(
                    "INSERT INTO event_refs VALUES (?,?,?,?)",
                    (event["id"], change["category"], change["key"], event["effective_at"]),
                )
            committed.append(event)
            if "receipt" in event:
                self._write_receipt(branch, event["receipt"])
        for (category, key), value in view.overlay.items():
            self._write(branch, category, key, value)
        self.db.execute("UPDATE branches SET revision=?,head_hash=? WHERE id=?", (seq, previous, branch))
        return committed

    def _write_receipt(self, branch, receipt, missing_only=False):
        verb = "INSERT OR IGNORE" if missing_only else "INSERT"
        self.db.execute(
            f"{verb} INTO receipts VALUES (?,?,?,?,?,?)",
            (
                branch,
                receipt["command_id"],
                receipt["fingerprint"],
                canonical(receipt["result"]),
                receipt["first_seq"],
                receipt["last_seq"],
            ),
        )

    def _write(self, branch, category, key, value):
        if value is None:
            self.db.execute(
                "DELETE FROM records WHERE branch=? AND category=? AND key=?", (branch, category, key)
            )
        else:
            self.db.execute(
                "INSERT INTO records(branch,category,key,body) VALUES (?,?,?,?) "
                "ON CONFLICT(branch,category,key) DO UPDATE SET body=excluded.body",
                (branch, category, key, canonical(value)),
            )

    def history(self, branch: str, upto: int | None = None, _depth: int = 0) -> list[dict]:
        if _depth > 64:
            raise InvalidAction("Profondeur maximale de chronologies atteinte.")
        meta = self.branch(branch)
        bound = meta["revision"] if upto is None else integer(upto, "révision", 0, meta["revision"])
        inherited = []
        if meta["parent"] is not None:
            inherited = self.history(meta["parent"], min(bound, meta["fork_seq"]), _depth + 1)
        own = [
            json.loads(r[0])
            for r in self.db.execute(
                "SELECT body FROM events WHERE branch=? AND seq<=? ORDER BY seq", (branch, bound)
            )
        ]
        return inherited + own

    def _ancestry(self, branch, bound=None):
        result = []
        while True:
            if len(result) > 64:
                raise InvalidAction("Trop de branches imbriquées.")
            meta = self.branch(branch)
            bound = meta["revision"] if bound is None else min(bound, meta["revision"])
            result.append((branch, bound))
            if meta["parent"] is None:
                return list(reversed(result))
            branch, bound = meta["parent"], min(bound, meta["fork_seq"])

    def record_versions(self, branch, category, key, until):
        """Bounded historical lookup through indexed change references."""
        versions = []
        for origin, bound in self._ancestry(branch):
            rows = self.db.execute(
                "SELECT e.body FROM event_refs r JOIN events e ON e.id=r.event_id "
                "WHERE r.category=? AND r.key=? AND r.effective_at<=? AND e.branch=? AND e.seq<=? "
                "ORDER BY r.effective_at,e.seq LIMIT 1001",
                (category, key, until, origin, bound),
            ).fetchall()
            if len(rows) + len(versions) > 1000:
                raise LimitExceeded("Historique individuel trop volumineux pour cette résolution.")
            for row in rows:
                event = json.loads(row[0])
                for change in event["changes"]:
                    if change["category"] == category and change["key"] == key:
                        versions.append((event["effective_at"], event["seq"], change["after"]))
        return sorted(versions, key=lambda v: (v[0], v[1]))

    def last_event_for(self, branch, category, key):
        for origin, bound in reversed(self._ancestry(branch)):
            row = self.db.execute(
                "SELECT e.id FROM event_refs r JOIN events e ON e.id=r.event_id "
                "WHERE r.category=? AND r.key=? AND e.branch=? AND e.seq<=? ORDER BY e.seq DESC LIMIT 1",
                (category, key, origin, bound),
            ).fetchone()
            if row:
                return row[0]
        return None

    def event(self, event_id):
        row = self.db.execute("SELECT body FROM events WHERE id=?", (event_id,)).fetchone()
        if row is None:
            raise InvalidAction("Cause inconnue dans le journal.")
        return json.loads(row[0])

    def replay(self, branch: str, upto: int | None = None) -> dict[tuple[str, str], dict]:
        records = {}
        previous = "0" * 64
        known_events = {}
        for expected_seq, event in enumerate(self.history(branch, upto), 1):
            if event.get("schema") != 1 or event["seq"] != expected_seq or event["previous_hash"] != previous:
                raise InvalidAction("Journal incompatible ou chaîne causale corrompue.")
            previous = digest(event)
            if any(c not in known_events or known_events[c] > event["effective_at"] for c in event["causes"]):
                raise InvalidAction("Cause absente ou située dans le futur fictif.")
            known_events[event["id"]] = event["effective_at"]
            for change in event["changes"]:
                ref = (change["category"], change["key"])
                if records.get(ref) != change["before"]:
                    raise InvalidAction(f"Journal incohérent : {ref}.")
                if change["after"] is None:
                    records.pop(ref, None)
                else:
                    records[ref] = change["after"]
        return records

    def audit(self, branch: str) -> dict:
        with self.transaction():
            history = self.history(branch)
            reconstructed = self.replay(branch)
            actual = {
                (r["category"], r["key"]): json.loads(r["body"])
                for r in self.db.execute("SELECT * FROM records WHERE branch=?", (branch,))
            }
            errors = []
            meta = self.branch(branch)
            if reconstructed != actual:
                errors.append("projection_mismatch")
            from .engine import validate_changes

            try:
                validate_changes(
                    self.state(branch),
                    [{"category": c, "key": k, "before": v, "after": v} for (c, k), v in actual.items()],
                )
            except (InvalidAction, KeyError, TypeError):
                errors.append("domain_invariant_failure")
            expected_hash = digest(history[-1]) if history else "0" * 64
            if meta["head_hash"] != expected_hash or meta["revision"] != len(history):
                errors.append("head_mismatch")
            for event in history:
                row = self.db.execute("SELECT hash FROM events WHERE id=?", (event["id"],)).fetchone()
                if row is None or row[0] != digest(event):
                    errors.append("event_hash_mismatch")
                refs = {
                    (r[0], r[1], r[2])
                    for r in self.db.execute(
                        "SELECT category,key,effective_at FROM event_refs WHERE event_id=?", (event["id"],)
                    )
                }
                if refs != {(c["category"], c["key"], event["effective_at"]) for c in event["changes"]}:
                    errors.append("event_index_mismatch")
            if self.db.execute("PRAGMA foreign_key_check").fetchall():
                errors.append("foreign_key_failure")
            if self.db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                errors.append("sqlite_integrity_failure")
            expected_receipts = {e["receipt"]["command_id"]: e["receipt"] for e in history if "receipt" in e}
            actual_receipts = {
                r["command_id"]: {
                    "command_id": r["command_id"],
                    "fingerprint": r["fingerprint"],
                    "result": json.loads(r["result"]),
                    "first_seq": r["first_seq"],
                    "last_seq": r["last_seq"],
                }
                for r in self.db.execute("SELECT * FROM receipts WHERE branch=?", (branch,))
            }
            if expected_receipts != actual_receipts:
                errors.append("receipt_mismatch")
            return {
                "ok": not errors,
                "errors": sorted(set(errors)),
                "events": len(history),
                "records": len(actual),
                "state_hash": digest(sorted((c, k, v) for (c, k), v in reconstructed.items())),
            }

    def rebuild(self, branch: str) -> None:
        with self.transaction():
            records = self.replay(branch)
            self.db.execute("DELETE FROM records WHERE branch=?", (branch,))
            for (category, key), value in records.items():
                self._write(branch, category, key, value)
            for event in self.history(branch):
                self.db.execute("DELETE FROM event_refs WHERE event_id=?", (event["id"],))
                for change in event["changes"]:
                    self.db.execute(
                        "INSERT INTO event_refs VALUES (?,?,?,?)",
                        (event["id"], change["category"], change["key"], event["effective_at"]),
                    )
                if "receipt" in event:
                    self._write_receipt(branch, event["receipt"], missing_only=True)

    def fork(self, source: str, at: int, name: str, reason: str) -> None:
        identifier(name)
        identifier(reason, "raison")
        with self.transaction():
            integer(at, "révision de départ", 1, self.branch(source)["revision"])
            if len(self._ancestry(source)) >= 64:
                raise InvalidAction("Profondeur maximale de branches atteinte.")
            if self.db.execute("SELECT 1 FROM branches WHERE id=?", (name,)).fetchone():
                raise InvalidAction("Cette chronologie existe déjà.")
            history = self.history(source, at)
            remaining = self.history(source)
            if (
                0 < at < len(remaining)
                and remaining[at - 1]["command_id"] is not None
                and remaining[at - 1]["command_id"] == remaining[at]["command_id"]
            ):
                raise InvalidAction("Une branche doit partir d'une frontière de commande validée.")
            records = self.replay(source, at)
            head = digest(history[-1]) if history else "0" * 64
            self.db.execute("INSERT INTO branches VALUES (?,?,?,?,?)", (name, source, at, at, head))
            for (category, key), value in records.items():
                self._write(name, category, key, value)
            self.db.execute(
                "INSERT INTO receipts SELECT ?,command_id,fingerprint,result,first_seq,last_seq "
                "FROM receipts WHERE branch=? AND last_seq<=?",
                (name, source, at),
            )
            event = {
                "id": str(uuid.uuid4()),
                "schema": 1,
                "kind": "timeline.forked",
                "command_id": None,
                "actor": "system",
                "effective_at": records.get(("world", "clock"), {}).get("value", 0),
                "causes": [history[-1]["id"]] if history else [],
                "data": {"source": source, "at": at, "reason": reason},
                "changes": [],
                "rules": {},
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            }
            self.commit_events(name, [event], at)

    def backup(self, destination: str | Path) -> None:
        target_path = Path(destination).resolve()
        if self.path != ":memory:" and target_path == Path(self.path).resolve():
            raise InvalidAction("La sauvegarde doit utiliser un autre fichier.")
        if target_path.exists():
            raise InvalidAction("La destination de sauvegarde existe déjà.")
        with closing(sqlite3.connect(target_path)) as target:
            self.db.backup(target)

"""Durable world coordinator: compare-and-swap commit, event log and CoW timelines."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from app.simulation.v2.context import TurnContext, WorldSnapshot
from app.simulation.v2.events import WorldEvent
from app.simulation.v2.reducer import validate_snapshot
from app.simulation.v2.values import JsonValue, digest, freeze_mapping, natural
from app.simulation.v2.version import WorldVersion

from .codec import checkpoint, decode, delta, encode, version_from
from .database import Database, IdempotencyConflict, StorageCorruption
from .history import HistoryReader, commit_id, head_row, record_hash
from .objects import ArrayObjectStore
from .observations import save_stage_runs

if TYPE_CHECKING:
    from app.ai.jobs.models import JobSpec

FORMAT = {
    "format": "clade.checkpoint-delta",
    "schema_version": 2,
    "minimum_reader_version": 2,
    "array_codec": "npz-v1",
}


class WorldStore:
    def __init__(self, root: Path, *, checkpoint_interval: int = 50) -> None:
        natural(checkpoint_interval, "checkpoint_interval")
        if checkpoint_interval < 1:
            raise ValueError("Checkpoint interval must be positive")
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        metadata = self.root / "metadata.json"
        if metadata.exists() and decode(metadata.read_text()) != freeze_mapping(FORMAT):
            raise StorageCorruption("Unsupported save format or reader version")
        self.db = Database(self.root / "world.sqlite")
        self.objects = ArrayObjectStore(self.root)
        self.history = HistoryReader(self.db, self.objects)
        self.checkpoint_interval = checkpoint_interval
        if not metadata.exists():
            # A small reconstructable index; SQLite remains the authority for heads.
            import tempfile

            fd, name = tempfile.mkstemp(prefix="metadata-", suffix=".tmp", dir=self.root)
            try:
                with os.fdopen(fd, "w") as stream:
                    stream.write(encode(FORMAT))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(name, metadata)
            finally:
                Path(name).unlink(missing_ok=True)

    def create(self, snapshot: WorldSnapshot, *, seed: int) -> WorldSnapshot:
        natural(seed, "seed")
        if seed > 2**63 - 1:
            raise ValueError("World seed exceeds SQLite int64")
        if snapshot.version.generation != 0 or snapshot.version.revision != 0:
            raise ValueError("Genesis must use generation/revision zero")
        validate_snapshot(snapshot)
        payload = checkpoint(snapshot, self.objects)
        version = snapshot.version
        with self.db.transaction() as connection:
            existing = connection.execute(
                "SELECT seed,manifest FROM worlds WHERE world_id=?", (version.world_id,)
            ).fetchone()
            if existing is not None:
                raise IdempotencyConflict("World already exists; use fork for a new timeline")
            connection.execute(
                "INSERT INTO worlds VALUES (?,?,?)",
                (version.world_id, seed, encode(snapshot.manifest)),
            )
            self._insert_commit(connection, snapshot, None, payload, True, None)
            connection.execute(
                "INSERT INTO timelines VALUES (?,?,?,NULL)",
                (version.world_id, version.timeline_id, commit_id(version)),
            )
            self._boundary(connection, snapshot, snapshot.version)
            self._outbox(connection, snapshot, "WorldCreated", {"state_hash": snapshot.state_hash})
        return snapshot

    def head(self, world_id: str, timeline_id: str) -> WorldSnapshot:
        with self.db.connection() as connection:
            version = version_from(dict(head_row(connection, world_id, timeline_id)))
        return self.history.replay(version)

    def commit(
        self,
        candidate: TurnContext,
        *,
        command_key: str,
        jobs: tuple[JobSpec, ...] = (),
    ) -> WorldSnapshot:
        if (
            not command_key.strip()
            or candidate.errors
            or any(r.errors for r in candidate.stage_results)
        ):
            raise ValueError("Commit requires a key and a valid candidate")
        expected = candidate.world_version
        request = freeze_mapping(
            {
                "active_events": [event.to_dict() for event in candidate.active_events],
                "jobs": [job.input_hash for job in jobs],
                "expected": expected.to_dict(),
                "start": candidate.start_snapshot_id,
                "command": candidate.command,
                "seed": candidate.seed,
                "rng": candidate.rng_namespace,
                "turn": candidate.turn_id,
                "pressures": candidate.external_pressures,
            }
        )
        request_hash = digest(request)
        # Check retries before CAS: a successful request remains successful after head advances.
        with self.db.connection() as connection:
            prior = self._prior_command(connection, expected, command_key, request_hash)
        if prior is not None:
            return self.history.replay(prior)
        if candidate.seed != self.world_seed(expected.world_id):
            raise ValueError("Turn seed must match the saved world seed")
        before = self.history.replay(expected)
        if (
            before.snapshot_id != candidate.start_snapshot_id
            or candidate.turn_id != before.turn_id + 1
        ):
            raise ValueError("Candidate must advance exactly one turn from the recorded snapshot")
        after = replace(candidate.snapshot, version=expected.advance(), turn_id=candidate.turn_id)
        if before.manifest != after.manifest:
            raise ValueError("Model upgrades require an explicit replacement command")
        validate_snapshot(after)
        full = after.turn_id % self.checkpoint_interval == 0
        payload = checkpoint(after, self.objects) if full else delta(before, after, self.objects)
        events = tuple(event for result in candidate.stage_results for event in result.events)
        for event in events:
            after.version.require(event.version)
            if event.turn != after.turn_id:
                raise ValueError("Event has wrong turn")
        for job in jobs:
            after.version.require(job.expected_world_version)
            if job.turn_id != after.turn_id or job.snapshot_id != after.snapshot_id:
                raise ValueError("AI job must reference the committed immutable snapshot")
        committed = after.version
        with self.db.transaction() as connection:
            prior = self._prior_command(connection, expected, command_key, request_hash)
            if prior is not None:
                committed = prior
            else:
                expected.require(
                    version_from(
                        dict(head_row(connection, expected.world_id, expected.timeline_id))
                    )
                )
                self._insert_commit(connection, after, before, payload, full, command_key)
                connection.execute(
                    "INSERT INTO commands VALUES (?,?,?,?,?,?,?)",
                    (
                        expected.world_id,
                        expected.timeline_id,
                        expected.generation,
                        command_key,
                        request_hash,
                        encode(request),
                        commit_id(after.version),
                    ),
                )
                self._move_head(connection, expected, after.version)
                self._boundary(connection, after, after.version)
                self._publish(connection, after, events, candidate.metrics, jobs)
                save_stage_runs(connection, after.version, candidate.stage_results)
        return self.history.replay(committed)

    def fork(self, parent: WorldVersion, timeline_id: str) -> WorldSnapshot:
        before = self.history.replay(parent)
        after = replace(before, version=WorldVersion(parent.world_id, timeline_id))
        payload = delta(before, after, self.objects)
        with self.db.transaction() as connection:
            self._insert_commit(connection, after, before, payload, False, None)
            connection.execute(
                "INSERT INTO timelines VALUES (?,?,?,?)",
                (
                    parent.world_id,
                    timeline_id,
                    commit_id(after.version),
                    commit_id(parent),
                ),
            )
            self._boundary(connection, after, parent)
            self._outbox(connection, after, "TimelineCreated", {"parent": parent.to_dict()})
        return after

    def replace_head(
        self,
        expected: WorldVersion,
        source: WorldVersion,
        *,
        command_key: str,
    ) -> WorldSnapshot:
        """Explicit rewind/load within this world; generation prevents same-turn ABA."""
        if source.world_id != expected.world_id or not command_key.strip():
            raise ValueError("Replacement needs an in-world source and command key")
        request = freeze_mapping(
            {"expected": expected.to_dict(), "source": source.to_dict(), "kind": "replace"}
        )
        request_hash = digest(request)
        with self.db.connection() as connection:
            prior = self._prior_command(connection, expected, command_key, request_hash)
        if prior is not None:
            return self.history.replay(prior)
        before, restored = self.history.replay(expected), self.history.replay(source)
        after = replace(restored, version=expected.advance(replace_generation=True))
        payload = checkpoint(after, self.objects)
        committed = after.version
        with self.db.transaction() as connection:
            prior = self._prior_command(connection, expected, command_key, request_hash)
            if prior is not None:
                committed = prior
            else:
                expected.require(
                    version_from(
                        dict(head_row(connection, expected.world_id, expected.timeline_id))
                    )
                )
                self._insert_commit(connection, after, before, payload, True, command_key)
                self._move_head(connection, expected, after.version)
                connection.execute(
                    "INSERT INTO commands VALUES (?,?,?,?,?,?,?)",
                    (
                        expected.world_id,
                        expected.timeline_id,
                        expected.generation,
                        command_key,
                        request_hash,
                        encode(request),
                        commit_id(after.version),
                    ),
                )
                self._boundary(connection, after, source)
                self._outbox(connection, after, "WorldReplaced", {"source": source.to_dict()})
        return self.history.replay(committed)

    def messages(
        self, world_id: str, timeline_id: str, *, after: int = 0, limit: int = 100
    ) -> tuple[Mapping[str, JsonValue], ...]:
        natural(after, "cursor")
        if not 1 <= limit <= 1000:
            raise ValueError("Invalid page limit")
        with self.db.connection() as connection:
            rows = connection.execute(
                "SELECT cursor,message_id,kind,payload FROM outbox WHERE world_id=? AND "
                "timeline_id=? AND cursor>? ORDER BY cursor LIMIT ?",
                (world_id, timeline_id, after, limit),
            ).fetchall()
        return tuple(
            freeze_mapping(
                {
                    "cursor": row["cursor"],
                    "message_id": row["message_id"],
                    "kind": row["kind"],
                    "payload": decode(row["payload"]),
                }
            )
            for row in rows
        )

    def world_seed(self, world_id: str) -> int:
        with self.db.connection() as connection:
            row = connection.execute(
                "SELECT seed FROM worlds WHERE world_id=?", (world_id,)
            ).fetchone()
            if row is None:
                raise KeyError(world_id)
            return int(row["seed"])

    def command_input(self, version: WorldVersion) -> Mapping[str, JsonValue]:
        with self.db.connection() as connection:
            row = connection.execute(
                "SELECT input_hash,input_payload FROM commands WHERE commit_id=?",
                (commit_id(version),),
            ).fetchone()
        if row is None:
            raise KeyError("Commit has no command input")
        payload = decode(row["input_payload"])
        if digest(payload) != row["input_hash"]:
            raise StorageCorruption("Command input checksum mismatch")
        return payload

    @staticmethod
    def _boundary(
        connection: sqlite3.Connection, snapshot: WorldSnapshot, source: WorldVersion
    ) -> None:
        version = snapshot.version
        prior = connection.execute(
            "SELECT commit_id FROM turn_boundaries WHERE world_id=? AND timeline_id=? "
            "AND generation=? AND turn_id=?",
            (source.world_id, source.timeline_id, source.generation, snapshot.turn_id),
        ).fetchone()
        connection.execute(
            "INSERT INTO turn_boundaries VALUES (?,?,?,?,?)",
            (
                version.world_id,
                version.timeline_id,
                version.generation,
                snapshot.turn_id,
                prior["commit_id"] if prior is not None else commit_id(source),
            ),
        )

    @staticmethod
    def _prior_command(
        connection: sqlite3.Connection, version: WorldVersion, key: str, input_hash: str
    ) -> WorldVersion | None:
        row = connection.execute(
            "SELECT c.*,q.input_hash FROM commands q JOIN commits c ON c.commit_id=q.commit_id "
            "WHERE q.world_id=? AND q.timeline_id=? AND q.generation=? AND q.command_key=?",
            (version.world_id, version.timeline_id, version.generation, key),
        ).fetchone()
        if row is None:
            return None
        if row["input_hash"] != input_hash:
            raise IdempotencyConflict("Same command key with different input")
        return version_from(dict(row))

    @staticmethod
    def _insert_commit(
        connection: sqlite3.Connection,
        after: WorldSnapshot,
        before: WorldSnapshot | None,
        payload: Mapping[str, JsonValue],
        full: bool,
        key: str | None,
    ) -> None:
        version = after.version
        record: dict[str, object] = {
            "commit_id": commit_id(version),
            **version.to_dict(),
            "turn_id": after.turn_id,
            "parent_id": commit_id(before.version) if before else None,
            "base_hash": before.state_hash if before else None,
            "state_hash": after.state_hash,
            "payload": encode(payload),
            "is_checkpoint": int(full),
            "command_key": key,
        }
        record["record_hash"] = record_hash(record)
        connection.execute(
            "INSERT INTO commits VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", tuple(record.values())
        )

    @staticmethod
    def _move_head(
        connection: sqlite3.Connection, expected: WorldVersion, result: WorldVersion
    ) -> None:
        changed = connection.execute(
            "UPDATE timelines SET head_id=? WHERE world_id=? AND timeline_id=? AND head_id=?",
            (commit_id(result), expected.world_id, expected.timeline_id, commit_id(expected)),
        ).rowcount
        if changed != 1:
            raise StorageCorruption("Timeline head CAS failed")

    @staticmethod
    def _outbox(
        connection: sqlite3.Connection, snapshot: WorldSnapshot, kind: str, payload: object
    ) -> None:
        version = snapshot.version
        connection.execute(
            "INSERT INTO outbox(message_id,world_id,timeline_id,commit_id,kind,payload) VALUES "
            "(?,?,?,?,?,?)",
            (
                digest([version.to_dict(), kind]),
                version.world_id,
                version.timeline_id,
                commit_id(version),
                kind,
                encode({"version": version.to_dict(), "turn": snapshot.turn_id, "data": payload}),
            ),
        )

    def _publish(
        self,
        connection: sqlite3.Connection,
        after: WorldSnapshot,
        events: tuple[WorldEvent, ...],
        metrics: Mapping[str, JsonValue],
        jobs: tuple[JobSpec, ...],
    ) -> None:
        for ordinal, event in enumerate(events):
            connection.execute(
                "INSERT INTO events VALUES (?,?,?,?)",
                (event.event_id, commit_id(after.version), ordinal, encode(event.to_dict())),
            )
            connection.execute(
                "INSERT INTO outbox(message_id,world_id,timeline_id,commit_id,kind,payload) "
                "VALUES (?,?,?,?,?,?)",
                (
                    event.event_id,
                    after.version.world_id,
                    after.version.timeline_id,
                    commit_id(after.version),
                    event.type,
                    encode(event.to_dict()),
                ),
            )
        connection.execute(
            "INSERT INTO turn_metrics VALUES (?,?)", (commit_id(after.version), encode(metrics))
        )
        if jobs:
            from .jobs import SQLiteJobRepository

            SQLiteJobRepository(self.db).enqueue_in(connection, jobs)
        self._outbox(connection, after, "TurnCommitted", {"state_hash": after.state_hash})

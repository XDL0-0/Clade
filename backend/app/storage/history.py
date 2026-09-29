"""Read-only reconstruction of immutable commit ancestry, without simulation or AI."""

from __future__ import annotations

import sqlite3
from typing import cast

from app.simulation.v2.context import WorldSnapshot
from app.simulation.v2.reducer import validate_snapshot
from app.simulation.v2.values import digest, natural
from app.simulation.v2.version import WorldVersion

from .codec import decode, replay_delta, restore, version_from
from .database import Database, StorageCorruption
from .objects import ArrayObjectStore


def commit_id(version: WorldVersion) -> str:
    return digest(version.to_dict())


def record_hash(row: sqlite3.Row | dict[str, object]) -> str:
    return digest(
        {
            key: row[key]
            for key in (
                "commit_id",
                "world_id",
                "timeline_id",
                "generation",
                "revision",
                "turn_id",
                "parent_id",
                "base_hash",
                "state_hash",
                "payload",
                "is_checkpoint",
                "command_key",
            )
        }
    )


def head_row(connection: sqlite3.Connection, world_id: str, timeline_id: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT c.* FROM commits c JOIN timelines t ON t.head_id=c.commit_id "
        "WHERE t.world_id=? AND t.timeline_id=?",
        (world_id, timeline_id),
    ).fetchone()
    if row is None:
        raise KeyError((world_id, timeline_id))
    return cast(sqlite3.Row, row)


class HistoryReader:
    def __init__(self, db: Database, objects: ArrayObjectStore) -> None:
        self.db, self.objects = db, objects

    def replay(self, version: WorldVersion) -> WorldSnapshot:
        try:
            return self._replay(version)
        except StorageCorruption:
            raise
        except (KeyError, TypeError, ValueError, OSError) as exc:
            raise StorageCorruption("Malformed committed snapshot or delta") from exc

    def _replay(self, version: WorldVersion) -> WorldSnapshot:
        with self.db.connection() as connection:
            rows: list[sqlite3.Row] = []
            identity: str | None = commit_id(version)
            seen: set[str] = set()
            while identity is not None:
                if identity in seen:
                    raise StorageCorruption("Cyclic commit ancestry")
                seen.add(identity)
                row = connection.execute(
                    "SELECT * FROM commits WHERE commit_id=?", (identity,)
                ).fetchone()
                if row is None:
                    raise StorageCorruption(f"Missing commit {identity}")
                if commit_id(version_from(dict(row))) != identity:
                    raise StorageCorruption("Commit identity mismatch")
                if record_hash(row) != row["record_hash"]:
                    raise StorageCorruption("Commit record checksum mismatch")
                rows.append(row)
                if row["is_checkpoint"]:
                    break
                identity = row["parent_id"]
        if not rows or not rows[-1]["is_checkpoint"]:
            raise StorageCorruption("No reachable checkpoint")
        base = rows.pop()
        snapshot = restore(
            decode(base["payload"]), version_from(dict(base)), base["turn_id"], self.objects
        )
        self._verify(snapshot, base["state_hash"])
        for row in reversed(rows):
            if row["base_hash"] != snapshot.state_hash:
                raise StorageCorruption("Broken commit hash chain")
            snapshot = replay_delta(
                snapshot,
                decode(row["payload"]),
                version_from(dict(row)),
                row["turn_id"],
                self.objects,
            )
            self._verify(snapshot, row["state_hash"])
        version.require(snapshot.version)
        return snapshot

    @staticmethod
    def _verify(snapshot: WorldSnapshot, expected: str) -> None:
        validate_snapshot(snapshot)
        if snapshot.state_hash != expected:
            raise StorageCorruption("Replayed state hash mismatch")

    def at_turn(self, world_id: str, timeline_id: str, turn: int) -> WorldSnapshot:
        """Resolve along the branch ancestry, capped by its fork point."""
        natural(turn, "turn")
        with self.db.connection() as connection:
            row = head_row(connection, world_id, timeline_id)
            seen: set[str] = set()
            while True:
                if row["commit_id"] in seen or record_hash(row) != row["record_hash"]:
                    raise StorageCorruption("Broken historical commit metadata or cycle")
                seen.add(row["commit_id"])
                if row["turn_id"] <= turn:
                    break
                row = connection.execute(
                    "SELECT * FROM commits WHERE commit_id=?", (row["parent_id"],)
                ).fetchone()
                if row is None:
                    raise KeyError(f"Turn {turn} predates recorded history")
            if row["turn_id"] != turn:
                raise KeyError(f"No committed state at turn {turn}")
            boundary = connection.execute(
                "SELECT c.* FROM turn_boundaries b JOIN commits c ON b.commit_id=c.commit_id "
                "WHERE b.world_id=? AND b.timeline_id=? AND b.generation=? AND b.turn_id=?",
                (row["world_id"], row["timeline_id"], row["generation"], turn),
            ).fetchone()
            if boundary is None or boundary["turn_id"] != turn:
                raise StorageCorruption("Missing or invalid turn boundary")
            version = version_from(dict(boundary))
        return self.replay(version)

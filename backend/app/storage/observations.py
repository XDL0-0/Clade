"""Committed metrics/events/profiles share the same revision as the world state."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping

from app.simulation.v2.contracts import StageResult
from app.simulation.v2.values import JsonValue, freeze_mapping
from app.simulation.v2.version import WorldVersion

from .codec import decode, encode
from .database import Database
from .history import commit_id


def save_stage_runs(
    connection: sqlite3.Connection, version: WorldVersion, results: tuple[StageResult, ...]
) -> None:
    for result in results:
        connection.execute(
            "INSERT INTO stage_runs VALUES (?,?,?)",
            (
                commit_id(version),
                result.stage_name,
                encode(
                    {
                        "stage_name": result.stage_name,
                        "stage_version": result.stage_version,
                        "input_hash": result.input_hash,
                        "output_hash": result.output_hash,
                        "random_seed": str(result.random_seed),
                        "duration_ms": result.duration_ms,
                        "metrics": result.metrics,
                        "warnings": result.warnings,
                        "errors": result.errors,
                        "event_ids": [event.event_id for event in result.events],
                    }
                ),
            ),
        )


class ObservationReader:
    def __init__(self, database: Database) -> None:
        self.db = database

    def profile(self, version: WorldVersion) -> tuple[Mapping[str, JsonValue], ...]:
        with self.db.connection() as connection:
            rows = connection.execute(
                "SELECT payload FROM stage_runs WHERE commit_id=? ORDER BY rowid",
                (commit_id(version),),
            ).fetchall()
        return tuple(decode(row["payload"]) for row in rows)

    def events(self, version: WorldVersion) -> tuple[Mapping[str, JsonValue], ...]:
        with self.db.connection() as connection:
            rows = connection.execute(
                "SELECT payload FROM events WHERE commit_id=? ORDER BY ordinal",
                (commit_id(version),),
            ).fetchall()
        return tuple(decode(row["payload"]) for row in rows)

    def metrics(
        self,
        world_id: str,
        timeline_id: str,
        *,
        generation: int,
        after_revision: int = -1,
        limit: int = 100,
    ) -> tuple[Mapping[str, JsonValue], ...]:
        if not 1 <= limit <= 1000 or after_revision < -1 or generation < 0:
            raise ValueError("Invalid metric page")
        with self.db.connection() as connection:
            rows = connection.execute(
                "SELECT c.revision,c.turn_id,m.payload FROM turn_metrics m "
                "JOIN commits c ON c.commit_id=m.commit_id WHERE c.world_id=? AND "
                "c.timeline_id=? AND c.generation=? AND c.revision>? ORDER BY c.revision LIMIT ?",
                (world_id, timeline_id, generation, after_revision, limit),
            ).fetchall()
        return tuple(
            freeze_mapping(
                {
                    "revision": row["revision"],
                    "turn": row["turn_id"],
                    "metrics": decode(row["payload"]),
                }
            )
            for row in rows
        )

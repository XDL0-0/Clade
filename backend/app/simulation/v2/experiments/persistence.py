"""Immutable experiment identity; progress remains in ordinary commit/command history."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass

from app.storage.codec import decode, encode, version_from
from app.storage.database import StorageCorruption
from app.storage.history import commit_id, head_row, record_hash
from app.storage.store import WorldStore

from ..context import WorldSnapshot
from ..engine import TurnCommand
from ..values import JsonValue, digest, freeze_mapping
from ..version import WorldVersion
from .schemas import Branch, ExperimentPlan


class ExperimentConflict(ValueError):
    """A plan or branch no longer has the exact experiment-owned provenance."""


@dataclass(frozen=True)
class Cursor:
    version: WorldVersion
    turn: int
    state_hash: str

    @property
    def snapshot_id(self) -> str:
        return digest([self.version.to_dict(), self.turn, self.state_hash])

    @classmethod
    def snapshot(cls, snapshot: WorldSnapshot) -> Cursor:
        return cls(snapshot.version, snapshot.turn_id, snapshot.state_hash)


def timeline_id(plan: ExperimentPlan, branch: Branch) -> str:
    return "exp-" + digest([plan.source.world_id, plan.id, branch.id])[:48]


@dataclass(frozen=True)
class RegisteredPlan:
    plan: ExperimentPlan
    manifest: Mapping[str, JsonValue]
    manifest_hash: str
    source: Cursor
    seed: int

    def command(self, branch: Branch, before: Cursor, offset: int) -> TurnCommand:
        key = "exp-turn-" + digest([self.manifest_hash, branch.id, offset])
        return TurnCommand(
            before.version,
            key,
            payload={
                **branch.scenario.parameters(offset),
                "experiment": {
                    "manifest_hash": self.manifest_hash,
                    "branch_id": branch.id,
                    "relative_turn": offset,
                },
            },
            rng_namespace=self.plan.rng_namespace,
        )

    def expected_request(
        self, branch: Branch, before: Cursor, offset: int
    ) -> Mapping[str, JsonValue]:
        command = self.command(branch, before, offset)
        return freeze_mapping(
            {
                "active_events": (),
                "jobs": (),
                "expected": before.version.to_dict(),
                "start": before.snapshot_id,
                "command": {**command.payload, "command_id": command.idempotency_key},
                "seed": self.seed,
                "rng": self.plan.rng_namespace,
                "turn": self.source.turn + offset,
                "pressures": (),
            }
        )


def register(store: WorldStore, plan: ExperimentPlan, source: WorldSnapshot) -> RegisteredPlan:
    seed = store.world_seed(source.version.world_id)
    manifest = freeze_mapping(
        {
            "format": "clade.experiment.v1",
            "plan": plan.model_dump(mode="json"),
            "source_snapshot_id": source.snapshot_id,
            "source_state_hash": source.state_hash,
            "model_manifest": source.manifest,
            "seed": seed,
            "branch_timelines": {branch.id: timeline_id(plan, branch) for branch in plan.branches},
        }
    )
    checksum = digest(manifest)
    # An immutable small plan record also survives a failure before the first
    # turn. No progress counters or snapshots are duplicated in this extension.
    with store.db.transaction() as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS simulation_experiments ("
            "world_id TEXT NOT NULL REFERENCES worlds(world_id), experiment_id TEXT NOT NULL, "
            "manifest_hash TEXT NOT NULL, payload TEXT NOT NULL, "
            "PRIMARY KEY(world_id,experiment_id))"
        )
        connection.execute(
            "INSERT OR IGNORE INTO simulation_experiments VALUES (?,?,?,?)",
            (source.version.world_id, plan.id, checksum, encode(manifest)),
        )
        row = connection.execute(
            "SELECT manifest_hash,payload FROM simulation_experiments "
            "WHERE world_id=? AND experiment_id=?",
            (source.version.world_id, plan.id),
        ).fetchone()
        assert row is not None
        if digest(decode(row["payload"])) != row["manifest_hash"]:
            raise StorageCorruption("Experiment manifest checksum mismatch")
        if row["manifest_hash"] != checksum:
            raise ExperimentConflict("Experiment ID already belongs to a different immutable plan")
    return RegisteredPlan(plan, manifest, checksum, Cursor.snapshot(source), seed)


def ensure_fork(store: WorldStore, run: RegisteredPlan, branch: Branch) -> Cursor:
    identity = timeline_id(run.plan, branch)
    with store.db.connection() as connection:
        exists = connection.execute(
            "SELECT 1 FROM timelines WHERE world_id=? AND timeline_id=?",
            (run.source.version.world_id, identity),
        ).fetchone()
    if exists is None:
        try:
            store.fork(run.source.version, identity)
        except sqlite3.IntegrityError as exc:
            if exc.sqlite_errorcode not in (
                sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY,
                sqlite3.SQLITE_CONSTRAINT_UNIQUE,
            ):
                raise
            # Another identical runner may have forked it; validate below.
    genesis = store.history.replay(WorldVersion(run.source.version.world_id, identity))
    if genesis.turn_id != run.source.turn or genesis.state_hash != run.source.state_hash:
        raise ExperimentConflict("Experiment fork does not preserve the requested source")
    return Cursor.snapshot(genesis)


def _check_row(row: sqlite3.Row, run: RegisteredPlan, branch: Branch, before: Cursor) -> Cursor:
    version = version_from(dict(row))
    if record_hash(row) != row["record_hash"]:
        raise StorageCorruption("Experiment commit checksum mismatch")
    if (
        version != before.version.advance()
        or row["parent_id"] != commit_id(before.version)
        or row["base_hash"] != before.state_hash
        or row["turn_id"] != before.turn + 1
    ):
        raise ExperimentConflict("Experiment history is not a contiguous turn prefix")
    if row["input_payload"] is None:
        raise ExperimentConflict("Experiment commit has no owned command")
    request = decode(row["input_payload"])
    if digest(request) != row["input_hash"]:
        raise StorageCorruption("Experiment command checksum mismatch")
    key = run.command(branch, before, version.revision).idempotency_key
    if (
        request != run.expected_request(branch, before, version.revision)
        or row["command_key"] != key
        or row["request_key"] != key
        or row["request_world"] != version.world_id
        or row["request_timeline"] != version.timeline_id
        or row["request_generation"] != 0
    ):
        raise ExperimentConflict("Committed turn input differs from the experiment plan")
    return Cursor(version, int(row["turn_id"]), str(row["state_hash"]))


def progress(
    store: WorldStore, run: RegisteredPlan, branch: Branch, known: Cursor
) -> tuple[Cursor, tuple[Mapping[str, JsonValue], ...]]:
    """Verify the newly committed prefix, with one bounded consistent SQL read."""
    observations: list[Mapping[str, JsonValue]] = []
    with store.db.connection() as connection:
        connection.execute("BEGIN")
        head = head_row(connection, known.version.world_id, known.version.timeline_id)
        fork = connection.execute(
            "SELECT fork_id FROM timelines WHERE world_id=? AND timeline_id=?",
            (known.version.world_id, known.version.timeline_id),
        ).fetchone()
        version = version_from(dict(head))
        if (
            fork is None
            or fork["fork_id"] != commit_id(run.source.version)
            or version.generation != 0
            or not known.version.revision <= version.revision <= run.plan.turns
        ):
            raise ExperimentConflict("Branch was replaced, rewound, or advanced outside the plan")
        # Also validate the branch boundary's actual parent, not only timelines.fork_id.
        genesis = connection.execute(
            "SELECT * FROM commits WHERE commit_id=?",
            (commit_id(WorldVersion(version.world_id, version.timeline_id)),),
        ).fetchone()
        if (
            genesis is None
            or record_hash(genesis) != genesis["record_hash"]
            or genesis["parent_id"] != commit_id(run.source.version)
            or genesis["state_hash"] != run.source.state_hash
            or genesis["base_hash"] != run.source.state_hash
            or genesis["command_key"] is not None
        ):
            raise ExperimentConflict("Branch has a different fork origin")
        rows = connection.execute(
            "SELECT c.*,q.input_hash,q.input_payload,q.command_key AS request_key,"
            "q.world_id AS request_world,q.timeline_id AS request_timeline,"
            "q.generation AS request_generation,m.payload AS metrics_payload "
            "FROM commits c LEFT JOIN commands q ON q.commit_id=c.commit_id "
            "LEFT JOIN turn_metrics m ON m.commit_id=c.commit_id "
            "WHERE c.world_id=? AND c.timeline_id=? AND c.generation=0 "
            "AND c.revision>? AND c.revision<=? ORDER BY c.revision LIMIT ?",
            (
                version.world_id,
                version.timeline_id,
                known.version.revision,
                version.revision,
                run.plan.turns + 1,
            ),
        ).fetchall()
        if len(rows) != version.revision - known.version.revision:
            raise ExperimentConflict("Experiment prefix has missing or duplicate command records")
        for row in rows:
            known = _check_row(row, run, branch, known)
            if row["metrics_payload"] is None:
                raise StorageCorruption("Committed experiment turn is missing metrics")
            stages = decode(row["metrics_payload"])
            metrics = stages.get("reference_metrics", {})
            if not isinstance(metrics, Mapping):
                raise StorageCorruption("Invalid reference metrics payload")
            observations.append(
                freeze_mapping(
                    {
                        "relative_turn": known.version.revision,
                        "version": known.version.to_dict(),
                        "turn": known.turn,
                        "state_hash": known.state_hash,
                        "metrics": metrics,
                    }
                )
            )
        if (
            known.version != version
            or known.turn != head["turn_id"]
            or known.state_hash != head["state_hash"]
            or record_hash(head) != head["record_hash"]
        ):
            raise ExperimentConflict("Experiment head does not match the verified prefix")
    return known, tuple(observations)

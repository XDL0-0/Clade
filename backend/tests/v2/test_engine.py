"""Real SQLite acceptance tests for the opt-in, CPU-only turn coordinator."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import numpy as np
import pytest

from app.ai.jobs.models import JobSpec, JobStatus
from app.simulation.v2.context import TurnContext, WorldSnapshot
from app.simulation.v2.contracts import (
    ArrayPatch,
    SimulationStage,
    StageContract,
    StageResult,
    StateDelta,
    StatePatch,
)
from app.simulation.v2.engine import SimulationEngineV2, TurnCommand
from app.simulation.v2.events import WorldEvent
from app.simulation.v2.pipeline import DeterministicPipeline, StageExecutionError
from app.simulation.v2.seed import SeedManager
from app.simulation.v2.values import FrozenArray, digest, freeze_mapping
from app.simulation.v2.version import VersionConflict, WorldVersion
from app.storage.database import IdempotencyConflict
from app.storage.jobs import SQLiteJobRepository
from app.storage.observations import ObservationReader
from app.storage.store import WorldStore

TABLES = ("commits", "commands", "events", "outbox", "turn_metrics", "stage_runs", "ai_jobs")


class Counter(SimulationStage):
    def __init__(self, version: str = "1") -> None:
        self.contract = StageContract(
            "counter",
            version,
            reads=("state.environment", "arrays.population"),
            writes=("state.environment", "arrays.population"),
        )

    def execute(self, context: TurnContext) -> StageResult:
        old = context.environment_state["count"]
        increment = context.command.get("increment", 1)
        assert isinstance(old, int) and isinstance(increment, int)
        command_id = context.command["command_id"]
        assert isinstance(command_id, str)
        seed = context.seeds.stream(self.contract.name, self.contract.version).seed
        event = WorldEvent.create(
            version=context.world_version.advance(),
            turn=context.turn_id,
            command_id=command_id,
            stage=self.contract.name,
            ordinal=0,
            event_type="Counted",
            target="oak",
            payload={"count": old + increment},
        )
        return StageResult(
            "counter",
            StateDelta(
                state=(
                    StatePatch(("environment", "count"), "replace", old + increment, digest(old)),
                    StatePatch(
                        ("environment", "draw"),
                        "replace",
                        str(seed),
                        digest(context.environment_state["draw"]),
                    ),
                ),
                arrays=(
                    ArrayPatch(
                        "population",
                        FrozenArray.from_numpy(context.population_state.numpy() + increment),
                        context.population_state.content_hash,
                    ),
                ),
            ),
            events=(event,),
            metrics={"count": old + increment, "saved_seed": context.seed},
            warnings=("test diagnostic",),
            evolution_proposals=({"target": "oak"},),
            ai_jobs=({"kind": "species"},),
        )


class Observe(SimulationStage):
    contract = StageContract("observe", "1", ("counter",), ("state.environment.count",))

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def execute(self, context: TurnContext) -> StageResult:
        if self.fail:
            raise RuntimeError("injected stage failure")
        return StageResult("observe", metrics={"observed": context.environment_state["count"]})


def plan(context: TurnContext) -> tuple[JobSpec, ...]:
    assert context.evolution_proposals == ({"target": "oak"},)
    assert context.ai_jobs == ({"kind": "species"},)
    assert all(result.duration_ms == 0 for result in context.stage_results)
    snapshot = replace(
        context.snapshot, version=context.world_version.advance(), turn_id=context.turn_id
    )
    return (
        JobSpec(
            expected_world_version=snapshot.version,
            turn_id=snapshot.turn_id,
            job_type="species",
            target_ids=("oak",),
            source_event_ids=tuple(
                event.event_id for result in context.stage_results for event in result.events
            ),
            snapshot_id=snapshot.snapshot_id,
            payload={"environment": snapshot.domain("environment"), "command": context.command},
        ),
    )


def engine(
    store: WorldStore,
    *,
    model: str = "counter-model-v1",
    stage_version: str = "1",
    fail: bool = False,
    planner: Callable[[TurnContext], tuple[JobSpec, ...]] | None = plan,
) -> SimulationEngineV2:
    return SimulationEngineV2(
        store, DeterministicPipeline([Observe(fail=fail), Counter(stage_version)]), model, planner
    )


def genesis(store: WorldStore, coordinator: SimulationEngineV2, *, seed: int = 19) -> WorldSnapshot:
    return store.create(
        WorldSnapshot(
            WorldVersion("world", "main"),
            0,
            {"environment": {"count": 0, "draw": ""}, "species": {"oak": {}}},
            {"population": FrozenArray.from_numpy(np.array([10], dtype=np.int64))},
            coordinator.manifest,
        ),
        seed=seed,
    )


def counts(store: WorldStore) -> tuple[int, ...]:
    with store.db.connection() as connection:
        return tuple(
            int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in TABLES
        )


def specs(store: WorldStore) -> tuple[JobSpec, ...]:
    with store.db.connection() as connection:
        job_ids = [
            row[0] for row in connection.execute("SELECT job_id FROM ai_jobs ORDER BY rowid")
        ]
    repository = SQLiteJobRepository(store.db)
    result: list[JobSpec] = []
    for job_id in job_ids:
        job = repository.get(job_id)
        assert job is not None and job.status == JobStatus.QUEUED
        result.append(job.spec)
    return tuple(result)


def test_command_recursively_freezes_inputs_and_rejects_invalid_identity() -> None:
    payload = {"nested": {"value": 1}}
    pressure = {"amount": 2}
    command = TurnCommand(
        WorldVersion("world", "main"),
        "step",
        payload,
        external_pressures=(pressure,),
    )
    payload["nested"]["value"] = 9
    pressure["amount"] = 9
    assert command.payload == {"nested": {"value": 1}}
    assert command.external_pressures == ({"amount": 2},)
    with pytest.raises(FrozenInstanceError):
        command.idempotency_key = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        command.payload["new"] = 1  # type: ignore[index]
    for invalid in ("", " "):
        with pytest.raises(ValueError):
            replace(command, idempotency_key=invalid)
        with pytest.raises(ValueError):
            replace(command, rng_namespace=invalid)


def test_seed_command_identity_and_full_observations_commit_together(tmp_path: Path) -> None:
    store = WorldStore(tmp_path)
    coordinator = engine(store)
    initial = genesis(store, coordinator, seed=29)
    command = TurnCommand(initial.version, "first", {"command_id": "spoof", "increment": 2})
    committed = coordinator.run_turn(command)
    assert committed.version == initial.version.advance() and committed.turn_id == 1
    assert committed.domain("environment")["count"] == 2
    np.testing.assert_array_equal(committed.arrays["population"].numpy(), [12])
    assert store.history.replay(initial.version) == initial
    inputs = store.command_input(committed.version)
    assert inputs["seed"] == 29 and inputs["rng"] == "main"
    assert inputs["command"] == {"command_id": "first", "increment": 2}
    observations = ObservationReader(store.db)
    profile = observations.profile(committed.version)
    assert [item["stage_name"] for item in profile] == ["counter", "observe"]
    for item in profile:
        assert item["stage_version"] == "1" and item["input_hash"]
        assert item["output_hash"] == committed.state_hash
        assert isinstance(item["duration_ms"], (float, int)) and item["duration_ms"] >= 0
        assert item["errors"] == ()
    assert profile[0]["random_seed"] == str(SeedManager(29, "main", 1).stream("counter", "1").seed)
    events = observations.events(committed.version)
    assert len(events) == 1 and profile[0]["event_ids"] == (events[0]["event_id"],)
    assert events[0]["version"] == committed.version.to_dict() and events[0]["turn"] == 1
    assert observations.metrics("world", "main", generation=0) == (
        {
            "revision": 1,
            "turn": 1,
            "metrics": {"counter": {"count": 2, "saved_seed": 29}, "observe": {"observed": 2}},
        },
    )
    (job,) = specs(WorldStore(tmp_path))
    assert (
        job.expected_world_version == committed.version and job.snapshot_id == committed.snapshot_id
    )
    assert job.source_event_ids == (events[0]["event_id"],)
    assert job.payload["environment"] == committed.domain("environment")
    assert counts(store) == (2, 1, 1, 4, 1, 2, 1)
    assert [item["kind"] for item in store.messages("world", "main")] == [
        "WorldCreated",
        "Counted",
        "AIJobQueued",
        "TurnCommitted",
    ]


@pytest.mark.parametrize(
    "field,value",
    [
        ("model", "old-unknown"),
        ("stages", {}),
        ("stages", {"counter": "2", "observe": "1"}),
        ("stages", {"counter": "1", "observe": "1", "extra": "1"}),
        ("rng", "other"),
    ],
)
def test_manifest_mismatch_is_rejected_before_stage_execution(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    store = WorldStore(tmp_path)
    coordinator = engine(store)
    initial = genesis(store, coordinator)
    bad_store = WorldStore(tmp_path / "invalid")
    bad = replace(initial, manifest=freeze_mapping({**initial.manifest, field: value}))
    bad_store.create(bad, seed=19)
    before = counts(bad_store)
    with pytest.raises(ValueError, match=f"manifest {field}"):
        engine(bad_store).run_turn(TurnCommand(bad.version, "first"))
    assert counts(bad_store) == before and bad_store.head("world", "main") == bad


@pytest.mark.parametrize("change", ["model", "stage"])
def test_running_model_or_stage_version_cannot_silently_change(tmp_path: Path, change: str) -> None:
    store = WorldStore(tmp_path)
    coordinator = engine(store)
    initial = genesis(store, coordinator)
    changed = engine(
        store,
        model="new" if change == "model" else coordinator.model_id,
        stage_version="2" if change == "stage" else "1",
    )
    with pytest.raises(ValueError, match="explicit migration"):
        changed.run_turn(TurnCommand(initial.version, "first"))
    assert store.head("world", "main") == initial


def test_retries_from_historical_input_apply_deltas_and_jobs_only_once(tmp_path: Path) -> None:
    store = WorldStore(tmp_path)
    coordinator = engine(store)
    initial = genesis(store, coordinator)
    command = TurnCommand(initial.version, "first")
    first = coordinator.run_turn(command)
    latest = coordinator.run_turn(TurnCommand(first.version, "second"))
    before = counts(store)
    assert coordinator.run_turn(command) == first
    assert counts(store) == before == (3, 2, 2, 7, 2, 4, 2)
    assert store.head("world", "main") == latest
    assert latest.domain("environment")["count"] == 2
    with pytest.raises(IdempotencyConflict):
        coordinator.run_turn(replace(command, payload={"increment": 3}))
    with pytest.raises(IdempotencyConflict):
        coordinator.run_turn(replace(command, rng_namespace="changed"))
    assert counts(store) == before


@pytest.mark.parametrize("failure", ["stage", "planner", "transaction", "cas"])
def test_failures_publish_no_partial_state_facts_jobs_or_outbox(
    tmp_path: Path, failure: str
) -> None:
    store = WorldStore(tmp_path)
    coordinator = engine(store)
    initial = genesis(store, coordinator)
    command = TurnCommand(initial.version, "failed")
    expected: type[Exception] = RuntimeError
    if failure == "stage":
        coordinator = engine(store, fail=True)
        expected = StageExecutionError
    elif failure == "planner":

        def fail_plan(context: TurnContext) -> tuple[JobSpec, ...]:
            raise RuntimeError("injected planner failure")

        coordinator = engine(store, planner=fail_plan)
    elif failure == "transaction":
        with store.db.transaction() as connection:
            connection.execute(
                "CREATE TRIGGER fail_commit BEFORE INSERT ON stage_runs "
                "BEGIN SELECT RAISE(ABORT, 'injected write failure'); END"
            )
        expected = sqlite3.IntegrityError
    else:
        coordinator.run_turn(TurnCommand(initial.version, "winner"))
        expected = VersionConflict
    before, messages = counts(store), store.messages("world", "main")
    head = store.head("world", "main")
    with pytest.raises(expected):
        coordinator.run_turn(command)
    assert counts(store) == before and store.messages("world", "main") == messages
    assert store.head("world", "main") == head


def test_shared_rng_namespace_matches_numerics_but_isolates_branch_events_jobs(
    tmp_path: Path,
) -> None:
    store = WorldStore(tmp_path)
    coordinator = engine(store)
    initial = genesis(store, coordinator)
    branch = store.fork(initial.version, "experiment")
    first = coordinator.run_turn(TurnCommand(initial.version, "same", rng_namespace="paired"))
    second = coordinator.run_turn(TurnCommand(branch.version, "same", rng_namespace="paired"))
    assert first.state_hash == second.state_hash and first.arrays == second.arrays
    observations = ObservationReader(store.db)
    left, right = observations.events(first.version), observations.events(second.version)
    assert left[0]["event_id"] != right[0]["event_id"]
    jobs = specs(store)
    assert (
        jobs[0].job_id != jobs[1].job_id and jobs[0].idempotency_scope != jobs[1].idempotency_scope
    )
    assert jobs[0].source_event_ids != jobs[1].source_event_ids
    assert store.command_input(first.version)["rng"] == "paired"
    assert store.command_input(second.version)["rng"] == "paired"
    next_left = coordinator.run_turn(TurnCommand(first.version, "default"))
    next_right = coordinator.run_turn(TurnCommand(second.version, "default"))
    assert next_left.domain("environment")["draw"] != next_right.domain("environment")["draw"]


def test_reopened_save_continues_identically_to_uninterrupted_run(tmp_path: Path) -> None:
    results: list[WorldSnapshot] = []
    saved_specs: list[tuple[JobSpec, ...]] = []
    for reopen in (False, True):
        path = tmp_path / str(reopen)
        store = WorldStore(path, checkpoint_interval=2)
        coordinator = engine(store)
        current = genesis(store, coordinator)
        for turn in range(1, 6):
            if reopen and turn == 3:
                store = WorldStore(path, checkpoint_interval=2)
                coordinator = engine(store)
                current = store.head("world", "main")
            current = coordinator.run_turn(TurnCommand(current.version, f"step-{turn}"))
        results.append(current)
        saved_specs.append(specs(store))
    assert results[0] == results[1]
    assert saved_specs[0] == saved_specs[1]


def test_external_inputs_are_frozen_and_part_of_retry_identity(tmp_path: Path) -> None:
    store = WorldStore(tmp_path)
    coordinator = engine(store, planner=None)
    initial = genesis(store, coordinator)
    event = WorldEvent.create(
        version=initial.version,
        turn=0,
        command_id="genesis",
        stage="input",
        ordinal=0,
        event_type="Pressure",
    )
    command = TurnCommand(
        initial.version, "first", active_events=(event,), external_pressures=({"rain": 2},)
    )
    committed = coordinator.run_turn(command)
    saved = store.command_input(committed.version)
    assert saved["active_events"] == (freeze_mapping(event.to_dict()),)
    assert saved["pressures"] == ({"rain": 2},) and specs(store) == ()
    for changed in (replace(command, active_events=()), replace(command, external_pressures=())):
        with pytest.raises(IdempotencyConflict):
            coordinator.run_turn(changed)
    assert len(ObservationReader(store.db).events(committed.version)) == 1
    assert store.messages("world", "main")[-1]["kind"] == "TurnCommitted"

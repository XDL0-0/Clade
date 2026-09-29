"""Independent acceptance tests for stage isolation and candidate-only reduction."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from itertools import permutations
from typing import Literal

import numpy as np
import pytest

from app.simulation.v2.context import TurnContext, WorldSnapshot
from app.simulation.v2.contracts import (
    ArrayPatch,
    SimulationStage,
    StageContract,
    StageResult,
    StateDelta,
    StatePatch,
)
from app.simulation.v2.events import WorldEvent
from app.simulation.v2.pipeline import DeterministicPipeline, StageExecutionError
from app.simulation.v2.reducer import DeltaConflict, apply_delta
from app.simulation.v2.values import FrozenArray, JsonValue, digest
from app.simulation.v2.version import WorldVersion


class _Stage(SimulationStage):
    def __init__(
        self,
        contract: StageContract,
        run: Callable[[TurnContext], StageResult] | None = None,
    ) -> None:
        self.contract = contract
        self.run = run

    def execute(self, context: TurnContext) -> StageResult:
        return StageResult(self.contract.name) if self.run is None else self.run(context)


def _context() -> TurnContext:
    return TurnContext(
        turn_id=8,
        seed=912,
        snapshot=WorldSnapshot(
            WorldVersion("world", "timeline", 2, 41),
            turn_id=7,
            state={
                "environment": {
                    "climate": {"temperature": 12, "humidity": 0.4},
                    "geology": {"elevation": 8},
                },
                "species": {"private": "hidden"},
                "derived": {},
            },
            arrays={
                "population": FrozenArray.from_numpy(np.array([3, 7], dtype=np.int64)),
                "temperature": FrozenArray.from_numpy(np.array([12.0, 13.0])),
            },
            manifest={"model": "reference-v1"},
        ),
    )


def _event(context: TurnContext, *, stage: str = "events", ordinal: int = 0) -> WorldEvent:
    return WorldEvent.create(
        version=context.world_version.advance(),
        turn=context.turn_id,
        command_id="advance-8",
        stage=stage,
        ordinal=ordinal,
        event_type="TestFact",
    )


def test_same_input_and_seed_produce_identical_candidate_ignoring_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context()

    def random_delta(view: TurnContext) -> StageResult:
        value = view.seeds.stream("random", "2").uint64(0)
        return StageResult(
            "random",
            StateDelta(state=(StatePatch(("derived", "draw"), "create", value),)),
            events=(_event(view, stage="random"),),
            metrics={"draw": value},
        )

    stage = _Stage(StageContract("random", "2", writes=("state.derived",)), random_delta)
    clock = iter([0.0, 0.001, 2.0, 2.125, 4.0, 4.25])
    monkeypatch.setattr("app.simulation.v2.pipeline.perf_counter", lambda: next(clock))
    first = DeterministicPipeline([stage]).execute(context)
    second = DeterministicPipeline([stage]).execute(context)
    assert first.snapshot.state_hash == second.snapshot.state_hash
    assert first.active_events == second.active_events
    assert first.stage_results[0].duration_ms != second.stage_results[0].duration_ms
    assert replace(first.stage_results[0], duration_ms=0) == replace(
        second.stage_results[0], duration_ms=0
    )
    assert first.stage_results[0].random_seed == context.seeds.stream("random", "2").seed
    assert first.stage_results[0].stage_version == "2"
    different = DeterministicPipeline([stage]).execute(replace(context, seed=913))
    assert different.snapshot.state_hash != first.snapshot.state_hash
    assert context.snapshot.domain("derived") == {}
    assert first.world_version == context.world_version
    assert first.snapshot.turn_id == context.snapshot.turn_id
    assert first.start_snapshot_id == context.start_snapshot_id


def test_topology_and_stable_ids_ignore_registration_order() -> None:
    stages = [
        _Stage(StageContract("gamma", "1", dependencies=("alpha",))),
        _Stage(StageContract("beta", "1")),
        _Stage(StageContract("delta", "1", dependencies=("beta",))),
        _Stage(StageContract("alpha", "1")),
    ]
    for registration in permutations(stages):
        result = DeterministicPipeline(registration).execute(_context())
        assert tuple(stage.stage_name for stage in result.stage_results) == (
            "alpha",
            "beta",
            "delta",
            "gamma",
        )


@pytest.mark.parametrize(
    "contracts",
    [
        (StageContract("a", "1"), StageContract("a", "2")),
        (StageContract("a", "1", dependencies=("missing",)),),
        (StageContract("a", "1", dependencies=("a",)),),
        (
            StageContract("a", "1", dependencies=("b",)),
            StageContract("b", "1", dependencies=("a",)),
        ),
        (StageContract("a", "1", side_effects=("database",)),),
        (StageContract("a", "1", deterministic=False),),
    ],
)
def test_invalid_stage_graph_or_impure_contract_is_rejected(
    contracts: tuple[StageContract, ...],
) -> None:
    with pytest.raises(ValueError):
        DeterministicPipeline(_Stage(contract) for contract in contracts)


@pytest.mark.parametrize(
    "first_reads,first_writes,second_reads,second_writes",
    [
        ((), ("state.environment",), ("state.environment.climate",), ()),
        (("state.environment.climate",), (), (), ("state.environment",)),
        ((), ("state.environment",), (), ("state.environment.climate",)),
        ((), ("arrays.population",), ("arrays.population",), ()),
        ((), ("arrays.population",), (), ("arrays.population",)),
    ],
)
def test_unordered_read_write_conflicts_are_rejected(
    first_reads: tuple[str, ...],
    first_writes: tuple[str, ...],
    second_reads: tuple[str, ...],
    second_writes: tuple[str, ...],
) -> None:
    first = _Stage(StageContract("a", "1", reads=first_reads, writes=first_writes))
    second = _Stage(StageContract("b", "1", reads=second_reads, writes=second_writes))
    for registration in ([first, second], [second, first]):
        with pytest.raises(ValueError, match="conflict"):
            DeterministicPipeline(registration)


def test_transitive_dependency_orders_conflicting_access() -> None:
    stages = [
        _Stage(StageContract("reader", "1", ("middle",), ("arrays.population",))),
        _Stage(StageContract("middle", "1", ("writer",))),
        _Stage(StageContract("writer", "1", writes=("arrays.population",))),
    ]
    assert [stage.contract.name for stage in DeterministicPipeline(stages).stages] == [
        "writer",
        "middle",
        "reader",
    ]


@pytest.mark.parametrize(
    "reads,expected",
    [
        ((), {}),
        (("state.environment.climate.temperature",), {"climate": {"temperature": 12}}),
        (
            ("state.environment.climate", "state.environment.climate.temperature"),
            {"climate": {"temperature": 12, "humidity": 0.4}},
        ),
        (
            ("state.environment.climate.temperature", "state.environment.climate"),
            {"climate": {"temperature": 12, "humidity": 0.4}},
        ),
    ],
)
def test_stage_sees_only_declared_nested_reads_and_arrays(
    reads: tuple[str, ...], expected: Mapping[str, JsonValue]
) -> None:
    def inspect(view: TurnContext) -> StageResult:
        assert view.environment_state == expected
        assert "species" not in view.snapshot.state
        assert view.species_state == {}
        assert set(view.snapshot.arrays) == {"population"}
        np.testing.assert_array_equal(view.population_state.numpy(), [3, 7])
        return StageResult("inspect")

    stage = _Stage(StageContract("inspect", "1", reads=(*reads, "arrays.population")), inspect)
    DeterministicPipeline([stage]).execute(_context())


def test_prior_stage_deltas_and_wall_clock_are_not_an_undeclared_read_channel() -> None:
    context = _context()
    initial_event = _event(context, stage="input")
    context = replace(context, active_events=(initial_event,))

    def produce(view: TurnContext) -> StageResult:
        return StageResult(
            "producer",
            StateDelta(state=(StatePatch(("derived", "private"), "create", 123),)),
            events=(_event(view, stage="producer"),),
            metrics={"private": 123},
            warnings=("private diagnostic",),
            evolution_proposals=({"private": 123},),
            ai_jobs=({"private": 123},),
        )

    def inspect(view: TurnContext) -> StageResult:
        assert not view.stage_results, "Prior deltas and duration leak outside declared reads"
        assert view.metrics == {}
        assert view.warnings == view.errors == ()
        assert view.evolution_proposals == view.ai_jobs == ()
        assert view.active_events == (initial_event,)
        assert view.snapshot.state == {}
        return StageResult("inspect")

    stages = [
        _Stage(StageContract("producer", "1", writes=("state.derived",)), produce),
        _Stage(StageContract("inspect", "1", dependencies=("producer",)), inspect),
    ]
    result = DeterministicPipeline(stages).execute(context)
    assert len(result.stage_results) == 2
    assert len(result.active_events) == 2
    assert result.metrics["producer"] == {"private": 123}
    assert result.warnings == ("private diagnostic",)
    assert result.evolution_proposals == result.ai_jobs == ({"private": 123},)


@pytest.mark.parametrize("read", ["state.environment.missing", "arrays.missing"])
def test_missing_declared_input_aborts_before_stage_execution(read: str) -> None:
    called = False

    def execute(view: TurnContext) -> StageResult:
        nonlocal called
        called = True
        return StageResult("missing")

    context = _context()
    initial_hash = context.snapshot.state_hash
    with pytest.raises(StageExecutionError, match="missing"):
        DeterministicPipeline(
            [_Stage(StageContract("missing", "1", reads=(read,)), execute)]
        ).execute(context)
    assert not called
    assert context.snapshot.state_hash == initial_hash


def test_later_stage_reads_updated_candidate_and_validates_against_it() -> None:
    def increment(view: TurnContext) -> StageResult:
        climate = view.environment_state["climate"]
        assert isinstance(climate, Mapping)
        old = climate["temperature"]
        assert isinstance(old, int)
        return StageResult(
            "increment",
            StateDelta(
                state=(
                    StatePatch(
                        ("environment", "climate", "temperature"), "replace", old + 1, digest(old)
                    ),
                )
            ),
        )

    def double(view: TurnContext) -> StageResult:
        climate = view.environment_state["climate"]
        assert isinstance(climate, Mapping)
        old = climate["temperature"]
        assert old == 13
        return StageResult(
            "double",
            StateDelta(
                state=(
                    StatePatch(
                        ("environment", "climate", "temperature"), "replace", 26, digest(old)
                    ),
                )
            ),
        )

    path = ("state.environment.climate.temperature",)
    context = _context()
    pipeline = DeterministicPipeline(
        [
            _Stage(StageContract("double", "1", ("increment",), path, path), double),
            _Stage(StageContract("increment", "1", reads=path, writes=path), increment),
        ]
    )
    result = pipeline.execute(context)
    assert result.snapshot.domain("environment")["climate"] == {"temperature": 26, "humidity": 0.4}
    assert context.snapshot.domain("environment")["climate"] == {"temperature": 12, "humidity": 0.4}
    assert result.stage_results[0].input_hash != result.stage_results[1].input_hash
    assert result.stage_results[-1].output_hash == result.snapshot.state_hash


@pytest.mark.parametrize("operation", ["replace", "delete"])
def test_stale_state_patch_cannot_partially_apply(
    operation: Literal["replace", "delete"],
) -> None:
    snapshot = _context().snapshot
    before = snapshot.state_hash
    delta = StateDelta(
        state=(
            StatePatch(("derived", "new"), "create", 1),
            StatePatch(("environment", "climate", "temperature"), operation, 20, digest(999)),
        )
    )
    with pytest.raises(DeltaConflict, match="Stale"):
        apply_delta(snapshot, delta, writes=("state.derived", "state.environment"))
    assert snapshot.state_hash == before
    assert snapshot.domain("derived") == {}


@pytest.mark.parametrize(
    "patch,writes",
    [
        (StatePatch(("derived", "new"), "create", 1), ("state.species",)),
        (StatePatch(("environment",), "create", {}), ("state.environment",)),
        (StatePatch(("missing", "child"), "create", 1), ("state.missing",)),
        (
            StatePatch(("derived", "missing"), "delete", expected_hash=digest(None)),
            ("state.derived",),
        ),
    ],
)
def test_unauthorized_or_invalid_state_target_is_rejected(
    patch: StatePatch, writes: tuple[str, ...]
) -> None:
    snapshot = _context().snapshot
    before = snapshot.snapshot_id
    with pytest.raises(DeltaConflict):
        apply_delta(snapshot, StateDelta(state=(patch,)), writes=writes)
    assert snapshot.snapshot_id == before


def test_dotted_path_segment_cannot_spoof_nested_write_permission() -> None:
    snapshot = _context().snapshot
    with pytest.raises(ValueError):
        delta = StateDelta(
            state=(StatePatch(("environment", "climate.temperature"), "create", 99),)
        )
        apply_delta(snapshot, delta, writes=("state.environment.climate.temperature",))


def test_dotted_array_name_cannot_introduce_ambiguous_permissions() -> None:
    with pytest.raises(ValueError):
        ArrayPatch("population.private", FrozenArray.from_numpy(np.array([1], dtype=np.int64)))


def test_reducer_applies_valid_create_replace_delete_without_mutating_input() -> None:
    snapshot = _context().snapshot
    replacement = FrozenArray.from_numpy(np.array([2, 8], dtype=np.int64))
    delta = StateDelta(
        state=(
            StatePatch(("derived", "new"), "create", {"value": 1}),
            StatePatch(("environment", "climate", "temperature"), "replace", 14, digest(12)),
            StatePatch(("species", "private"), "delete", expected_hash=digest("hidden")),
        ),
        arrays=(
            ArrayPatch("population", replacement, snapshot.arrays["population"].content_hash),
            ArrayPatch("temperature", None, snapshot.arrays["temperature"].content_hash),
            ArrayPatch("new", FrozenArray.from_numpy(np.array([4.0]))),
        ),
    )
    writes = (
        "state.derived",
        "state.environment",
        "state.species",
        "arrays.population",
        "arrays.temperature",
        "arrays.new",
    )
    candidate = apply_delta(snapshot, delta, writes=writes)
    assert candidate.domain("derived") == {"new": {"value": 1}}
    assert candidate.domain("environment")["climate"] == {"temperature": 14, "humidity": 0.4}
    assert candidate.domain("species") == {}
    assert set(candidate.arrays) == {"population", "new"}
    assert candidate.arrays["population"] == replacement
    assert snapshot.domain("derived") == {}
    assert snapshot.domain("species") == {"private": "hidden"}
    assert set(snapshot.arrays) == {"population", "temperature"}
    with pytest.raises(DeltaConflict):
        apply_delta(candidate, delta, writes=writes)


@pytest.mark.parametrize("case", ["undeclared", "stale", "create-overwrite"])
def test_array_preconditions_and_write_declarations_are_enforced(case: str) -> None:
    snapshot = _context().snapshot
    expected = snapshot.arrays["population"].content_hash
    replacement = FrozenArray.from_numpy(np.array([2, 8], dtype=np.int64))
    patch = ArrayPatch(
        "population",
        replacement,
        None if case == "create-overwrite" else "stale" if case == "stale" else expected,
    )
    with pytest.raises(DeltaConflict):
        apply_delta(
            snapshot,
            StateDelta(arrays=(patch,)),
            writes=("arrays.temperature",) if case == "undeclared" else ("arrays.population",),
        )
    assert snapshot.arrays["population"].content_hash == expected


@pytest.mark.parametrize("case", ["negative", "fractional", "shape", "dtype"])
def test_invalid_population_replacement_is_rejected(case: str) -> None:
    snapshot = _context().snapshot
    values = {
        "negative": np.array([-1, 7], dtype=np.int64),
        "fractional": np.array([1.5, 7.0]),
        "shape": np.array([[3, 7]], dtype=np.int64),
        "dtype": np.array([3, 7], dtype=np.int32),
    }
    patch = ArrayPatch(
        "population",
        FrozenArray.from_numpy(values[case]),
        snapshot.arrays["population"].content_hash,
    )
    with pytest.raises(ValueError):
        apply_delta(snapshot, StateDelta(arrays=(patch,)), writes=("arrays.population",))


@pytest.mark.parametrize(
    "failure", ["exception", "nan", "nan-array", "duplicate", "errors", "wrong-name"]
)
def test_failed_stage_returns_no_candidate_and_preserves_original(failure: str) -> None:
    context = _context()
    before = context.snapshot.snapshot_id

    def first(view: TurnContext) -> StageResult:
        return StageResult(
            "first",
            StateDelta(state=(StatePatch(("derived", "created"), "create", 1),)),
            events=(_event(view, stage="first"),),
        )

    def fail(view: TurnContext) -> StageResult:
        if failure == "exception":
            raise RuntimeError("stage exploded")
        if failure == "nan":
            return StageResult(
                "fail", StateDelta(state=(StatePatch(("derived", "bad"), "create", float("nan")),))
            )
        if failure == "nan-array":
            return StageResult(
                "fail",
                StateDelta(
                    arrays=(ArrayPatch("bad", FrozenArray.from_numpy(np.array([float("nan")]))),)
                ),
            )
        if failure == "duplicate":
            patch = StatePatch(("derived", "bad"), "create", 3)
            return StageResult("fail", StateDelta(state=(patch, patch)))
        return StageResult(
            "other" if failure == "wrong-name" else "fail",
            errors=(("validation failed",) if failure == "errors" else ()),
        )

    pipeline = DeterministicPipeline(
        [
            _Stage(StageContract("first", "1", writes=("state.derived",)), first),
            _Stage(StageContract("fail", "1", ("first",), writes=("state.derived",)), fail),
        ]
    )
    publishable: TurnContext | None = None
    with pytest.raises(StageExecutionError) as error:
        publishable = pipeline.execute(context)
    assert error.value.stage_name == "fail"
    assert publishable is None
    assert context.snapshot.snapshot_id == before
    assert context.stage_results == ()
    assert context.active_events == ()
    assert context.snapshot.domain("derived") == {}


@pytest.mark.parametrize("failure_phase", ["inputs", "execute", "outputs"])
def test_validation_lifecycle_is_ordered_and_aborts_on_failure(failure_phase: str) -> None:
    calls: list[str] = []

    class LifecycleStage(_Stage):
        def validate_inputs(self, context: TurnContext) -> None:
            calls.append("inputs")
            if failure_phase == "inputs":
                raise ValueError("invalid inputs")
            super().validate_inputs(context)

        def execute(self, context: TurnContext) -> StageResult:
            calls.append("execute")
            if failure_phase == "execute":
                raise RuntimeError("execution failure")
            return StageResult("lifecycle")

        def validate_outputs(self, context: TurnContext, result: StageResult) -> None:
            calls.append("outputs")
            if failure_phase == "outputs":
                raise ValueError("invalid outputs")
            super().validate_outputs(context, result)

    with pytest.raises(StageExecutionError):
        DeterministicPipeline([LifecycleStage(StageContract("lifecycle", "1"))]).execute(_context())
    phases = ["inputs", "execute", "outputs"]
    assert calls == phases[: phases.index(failure_phase) + 1]


def test_context_with_existing_errors_is_rejected_before_execution() -> None:
    context = replace(_context(), errors=("invalid world input",))
    called = False

    def execute(view: TurnContext) -> StageResult:
        nonlocal called
        called = True
        return StageResult("must-not-run")

    with pytest.raises((ValueError, StageExecutionError)):
        DeterministicPipeline([_Stage(StageContract("must-not-run", "1"), execute)]).execute(
            context
        )
    assert not called


@pytest.mark.parametrize("component", ["world", "timeline", "generation", "revision", "turn"])
def test_event_identity_must_match_candidate_version_and_turn(component: str) -> None:
    context = _context()
    event = _event(context)
    version = event.version
    versions = {
        "world": replace(version, world_id="other"),
        "timeline": replace(version, timeline_id="other"),
        "generation": replace(version, generation=version.generation + 1),
        "revision": context.world_version,
        "turn": version,
    }
    invalid = replace(event, version=versions[component], turn=7 if component == "turn" else 8)
    stage = _Stage(
        StageContract("events", "1"), lambda view: StageResult("events", events=(invalid,))
    )
    with pytest.raises(StageExecutionError):
        DeterministicPipeline([stage]).execute(context)
    assert context.active_events == ()


@pytest.mark.parametrize("location", ["same-result", "earlier-stage", "active-event"])
def test_duplicate_events_are_rejected_across_all_candidate_sources(location: str) -> None:
    context = _context()
    event = _event(context)
    events = (event, event) if location == "same-result" else (event,)
    stages: list[SimulationStage] = []
    if location == "earlier-stage":
        stages.append(
            _Stage(StageContract("a", "1"), lambda view: StageResult("a", events=(event,)))
        )
    if location == "active-event":
        context = replace(context, active_events=(event,))
    stages.append(
        _Stage(StageContract("events", "1"), lambda view: StageResult("events", events=events))
    )
    with pytest.raises(StageExecutionError, match="duplicate"):
        DeterministicPipeline(stages).execute(context)
    assert context.active_events == ((event,) if location == "active-event" else ())

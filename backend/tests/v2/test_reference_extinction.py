"""Lifecycle metadata changes are reversible until terminal, factual extinction."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import cast

import numpy as np
import pytest
from numpy.typing import NDArray

from app.simulation.v2.context import TurnContext
from app.simulation.v2.contracts import SimulationStage, StageContract, StageResult
from app.simulation.v2.engine import SimulationEngineV2
from app.simulation.v2.pipeline import DeterministicPipeline, StageExecutionError
from app.simulation.v2.reducer import apply_delta
from app.simulation.v2.reference.extinction import ExtinctionStage
from app.simulation.v2.reference.model import explainable_pipeline
from app.simulation.v2.reference.world import MODEL_ID, SpeciesSeed, create_reference_snapshot
from app.simulation.v2.values import FrozenArray, JsonValue, digest
from app.simulation.v2.version import WorldVersion
from app.storage.store import WorldStore


class Published(SimulationStage):
    def __init__(self, name: str = "reference_population") -> None:
        self.contract = StageContract(name, "fixture")

    def execute(self, context: TurnContext) -> StageResult:
        return StageResult(self.contract.name)


def context(*, width: int = 4, height: int = 2) -> TurnContext:
    seed = SpeciesSeed("a", "herbivore", 1.0, 10, habitat="amphibious")
    snapshot = create_reference_snapshot(
        WorldVersion("lifecycle", "main"),
        seed=31,
        manifest={"model": MODEL_ID},
        width=width,
        height=height,
        max_species=2,
        species=[seed],
    )
    return TurnContext(1, snapshot, 31, command={"command_id": "turn-1"})


def metadata(value: TurnContext) -> Mapping[str, JsonValue]:
    return cast(Mapping[str, JsonValue], value.species_state["a"])


def change_metadata(value: TurnContext, **changes: JsonValue) -> TurnContext:
    return value.with_snapshot(
        replace(
            value.snapshot,
            state={
                **value.snapshot.state,
                "species": {**value.species_state, "a": {**metadata(value), **changes}},
            },
        )
    )


def array(value: TurnContext, name: str, values: NDArray[np.generic]) -> TurnContext:
    return value.with_snapshot(
        replace(
            value.snapshot,
            arrays={
                **value.snapshot.arrays,
                name: FrozenArray.from_numpy(values),
            },
        )
    )


def population(value: TurnContext, counts: list[int]) -> TurnContext:
    data = value.snapshot.arrays["population"].numpy().copy()
    data[0] = counts
    return array(value, "population", data)


def run(value: TurnContext) -> tuple[TurnContext, StageResult]:
    stage = ExtinctionStage()
    output = stage.execute(value)
    return value.with_snapshot(
        apply_delta(value.snapshot, output.state_delta, writes=stage.contract.writes)
    ), output


def next_turn(value: TurnContext) -> TurnContext:
    return replace(
        value,
        turn_id=value.turn_id + 1,
        snapshot=replace(
            value.snapshot, turn_id=value.turn_id, version=value.world_version.advance()
        ),
        command={"command_id": f"turn-{value.turn_id + 1}"},
    )


def test_decline_requires_two_consecutive_ten_percent_decreases_and_recovers() -> None:
    first, first_result = run(population(context(), [9] * 8))
    assert metadata(first)["status"] == "Healthy"
    assert metadata(first)["declining_turns"] == 1
    assert not first_result.events
    second, second_result = run(population(next_turn(first), [8] * 8))
    assert metadata(second)["status"] == "Declining"
    assert metadata(second)["declining_turns"] == 2
    assert second_result.events[0].type == "SpeciesDeclining"
    third, third_result = run(population(next_turn(second), [8] * 8))
    assert metadata(third)["status"] == "Healthy"
    assert metadata(third)["declining_turns"] == 0
    assert third_result.events[0].type == "SpeciesRecovered"


def test_exact_ten_percent_boundary_and_interrupting_smaller_decline() -> None:
    value = change_metadata(context(), last_population=100)
    first, _ = run(population(value, [90, 0, 0, 0, 0, 0, 0, 0]))
    assert metadata(first)["declining_turns"] == 1
    second, _ = run(population(next_turn(first), [82, 0, 0, 0, 0, 0, 0, 0]))
    assert metadata(second)["declining_turns"] == 0
    assert metadata(second)["status"] == "Healthy"


@pytest.mark.parametrize(
    ("counts", "status", "event_type"),
    [
        ([0] * 8, "Extinct", "SpeciesExtinct"),
        ([1, 0, 0, 0, 0, 0, 0, 0], "Functionally Extinct", "SpeciesFunctionallyExtinct"),
        ([1] * 8, "Functionally Extinct", "SpeciesFunctionallyExtinct"),
        ([2, 0, 0, 0, 0, 0, 0, 0], "Critical", "SpeciesCritical"),
        ([10, 0, 0, 0, 0, 0, 0, 0], "Critical", "SpeciesCritical"),
        ([11, 0, 0, 0, 0, 0, 0, 0], "Healthy", None),
    ],
)
def test_status_thresholds(counts: list[int], status: str, event_type: str | None) -> None:
    after, output = run(population(context(), counts))
    assert metadata(after)["status"] == status
    assert [event.type for event in output.events] == ([] if event_type is None else [event_type])
    assert output.metrics[f"{status.lower().replace(' ', '_')}_count"] == 1


@pytest.mark.parametrize("old", ["Declining", "Critical", "Functionally Extinct"])
def test_nonterminal_status_can_recover(old: str) -> None:
    after, output = run(change_metadata(context(), status=old, last_population=20))
    assert metadata(after)["status"] == "Healthy"
    assert output.events[0].type == "SpeciesRecovered"


def test_extinction_moves_only_last_live_runs_preserves_lineage_and_is_terminal() -> None:
    value = change_metadata(context(), ancestor="ancestor-id", descendants=("child-id",))
    live, _ = run(population(value, [2, 3, 0, 0, 2, 2, 2, 0]))
    assert metadata(live)["current_habitat_runs"] == ((0, 1), (4, 6))
    assert "last_habitat" not in metadata(live)
    extinct, output = run(population(next_turn(live), [0] * 8))
    history = metadata(extinct)
    assert history["last_habitat"] == ((0, 1), (4, 6))
    assert history["current_habitat_runs"] == ()
    assert history["last_nonzero_population"] == 11
    assert history["last_population"] == 0
    assert history["extinction_turn"] == 2
    assert history["ancestor"] == "ancestor-id"
    assert history["descendants"] == ("child-id",)
    assert output.events[0].type == "SpeciesExtinct"
    assert "last_habitat" not in output.events[0].payload
    assert output.metrics["new_extinctions"] == 1
    assert output.metrics["extinction_rate"] == 1
    repeated, repeat_result = run(extinct)
    assert repeated.snapshot == extinct.snapshot
    assert not repeat_result.events and not repeat_result.state_delta.state
    later, later_result = run(next_turn(extinct))
    assert metadata(later) == history
    assert not later_result.events
    with pytest.raises(ValueError, match="terminal"):
        run(population(next_turn(extinct), [1, 0, 0, 0, 0, 0, 0, 0]))


def test_extinction_aggregates_exclusive_causes_and_exposes_denominator() -> None:
    value = population(change_metadata(context(), last_population=10), [0] * 8)
    deaths = value.snapshot.arrays["mortality"].numpy().copy()
    deaths[1, 0, :2] = [2, 1]
    deaths[2, 0, 4] = 7
    movement = value.snapshot.arrays["migration_in"].numpy().copy()
    movement[0, 0] = 2
    value = array(
        array(array(value, "mortality", deaths), "migration_in", movement),
        "migration_out",
        movement,
    )
    after, output = run(value)
    cause = cast(Mapping[str, JsonValue], metadata(after)["extinction_cause"])
    assert cause["primary"] == "predation"
    assert cause["total_deaths"] == 10
    assert cause["last_population"] == 10
    assert cause["migration_in"] == cause["migration_out"] == 2
    assert cast(Mapping[str, JsonValue], cause["counts"])["starvation"] == 3
    assert cast(Mapping[str, JsonValue], cause["pressure"])["predation"] == 0.7
    assert output.events[0].payload["extinction_cause"] == cause


def test_missing_deaths_are_unknown_and_ties_follow_declared_cause_order() -> None:
    value = population(context(), [0] * 8)
    after, _ = run(value)
    cause = cast(Mapping[str, JsonValue], metadata(after)["extinction_cause"])
    assert cause["primary"] == "unknown"
    assert cause["total_deaths"] == 0
    assert metadata(after)["last_habitat"] == ()
    deaths = value.snapshot.arrays["mortality"].numpy().copy()
    deaths[:2, 0, 0] = 1
    tied, _ = run(array(value, "mortality", deaths))
    assert (
        cast(Mapping[str, JsonValue], metadata(tied)["extinction_cause"])["primary"]
        == "temperature"
    )


def test_current_runs_update_by_field_delta_and_same_turn_is_idempotent() -> None:
    first, _ = run(population(context(), [2, 0, 0, 0, 0, 0, 0, 0]))
    repeated, output = run(first)
    assert repeated.snapshot == first.snapshot
    assert not output.events and not output.state_delta.state
    with pytest.raises(ValueError, match="finalized"):
        run(population(first, [3, 0, 0, 0, 0, 0, 0, 0]))
    second, changed = run(population(next_turn(first), [0, 2, 2, 0, 0, 0, 0, 0]))
    assert metadata(second)["current_habitat_runs"] == ((1, 2),)
    assert not changed.events
    assert any(
        patch.path == ("species", "a", "current_habitat_runs")
        for patch in changed.state_delta.state
    )
    assert all(patch.path[:2] == ("species", "a") for patch in changed.state_delta.state)


def test_pipeline_restriction_event_identity_and_array_immutability() -> None:
    value = population(context(), [0] * 8)
    before = value.snapshot.snapshot_id
    lineage = digest((metadata(value)["ancestor"], metadata(value)["descendants"]))
    pipeline = DeterministicPipeline([ExtinctionStage(after="later"), Published("later")])
    left, right = pipeline.execute(value), pipeline.execute(value)
    output = left.stage_results[-1]
    assert output.events == right.stage_results[-1].events
    assert output.output_hash == right.stage_results[-1].output_hash
    assert left.snapshot.arrays == value.snapshot.arrays
    assert not output.state_delta.arrays
    assert value.snapshot.snapshot_id == before
    assert digest((metadata(left)["ancestor"], metadata(left)["descendants"])) == lineage
    event = output.events[0]
    assert event.target == "a" and event.cause == ("turn-1",)
    assert event.version == value.world_version.advance()
    other = replace(
        value, snapshot=replace(value.snapshot, version=WorldVersion("lifecycle", "fork"))
    )
    assert pipeline.execute(other).stage_results[-1].events[0].event_id != event.event_id


@pytest.mark.parametrize(
    ("key", "bad"),
    [
        ("last_population", -1),
        ("last_population", True),
        ("declining_turns", -1),
        ("declining_turns", 1),
        ("declining_turns", 3),
        ("created_turn", 2),
        ("lifecycle_turn", 2),
        ("extinction_turn", 1),
        ("last_nonzero_population", -2),
        ("slot", True),
        ("slot", 2),
        ("status", "narratively doomed"),
        ("current_habitat_runs", ((0, 1), (2, 3))),
        ("last_habitat", ((8, 8),)),
        ("ancestor", 7),
        ("descendants", ("child", "child")),
        ("extinction_cause", {"counts": {"predation": -1}}),
    ],
)
def test_invalid_history_fails_closed(key: str, bad: JsonValue) -> None:
    value = change_metadata(context(), **{key: bad})
    before = value.snapshot.snapshot_id
    with pytest.raises((ValueError, TypeError)):
        ExtinctionStage().execute(value)
    assert value.snapshot.snapshot_id == before


@pytest.mark.parametrize(
    "name", ["population", "mortality", "migration_in", "migration_out", "biome"]
)
@pytest.mark.parametrize("invalid", ["negative", "dtype", "shape"])
def test_invalid_arrays_rejected(name: str, invalid: str) -> None:
    value = context()
    data = value.snapshot.arrays[name].numpy().copy()
    if invalid == "negative":
        data.flat[0] = -1
    elif invalid == "dtype":
        data = data.astype(np.float64)
    else:
        data = data[..., :-1]
    before = array(value, name, data)
    with pytest.raises((ValueError, TypeError)):
        ExtinctionStage().execute(before)


@pytest.mark.parametrize("name", ["population", "mortality", "migration_in", "migration_out"])
def test_reserved_rows_must_have_no_orphan_lifecycle_entries(name: str) -> None:
    value = context()
    data = value.snapshot.arrays[name].numpy().copy()
    if name == "mortality":
        data[0, 1, 0] = 1
    else:
        data[1, 0] = 1
    with pytest.raises(ValueError):
        ExtinctionStage().execute(array(value, name, data))


def test_duplicate_slots_and_unbalanced_movement_are_rejected() -> None:
    value = context()
    duplicate = value.with_snapshot(
        replace(
            value.snapshot,
            state={
                **value.snapshot.state,
                "species": {**value.species_state, "duplicate": metadata(value)},
            },
        )
    )
    with pytest.raises(ValueError, match="unique"):
        ExtinctionStage().execute(duplicate)
    movement = value.snapshot.arrays["migration_in"].numpy().copy()
    movement[0, 0] = 1
    with pytest.raises(ValueError, match="Migration"):
        ExtinctionStage().execute(array(value, "migration_in", movement))


def test_integer_population_aggregation_does_not_wrap_int64() -> None:
    maximum = int(np.iinfo(np.int64).max)
    after, output = run(population(context(), [maximum] * 8))
    assert metadata(after)["last_population"] == 8 * maximum
    assert metadata(after)["status"] == "Healthy"
    assert not output.events


def test_empty_genesis_and_already_extinct_have_no_events_or_metadata_changes() -> None:
    value = population(change_metadata(context(), status="Extinct", last_population=0), [0] * 8)
    after, output = run(value)
    assert after.snapshot == value.snapshot
    assert not output.events and not output.state_delta.state
    assert output.metrics["extinction_rate"] == 0
    assert output.metrics["extinct_count"] == 1


def test_input_failure_is_atomic_through_the_pipeline() -> None:
    value = change_metadata(context(), status="Extinct")
    pipeline = DeterministicPipeline([Published(), ExtinctionStage()])
    before = value.snapshot.snapshot_id
    with pytest.raises(StageExecutionError, match="terminal"):
        pipeline.execute(value)
    assert value.snapshot.snapshot_id == before


@pytest.mark.parametrize("after", ["", "reference_extinction"])
def test_invalid_dependency_is_rejected(after: str) -> None:
    with pytest.raises(ValueError):
        ExtinctionStage(after=after)


def test_dotted_species_ids_remain_valid_and_names_do_not_change_status() -> None:
    value = population(context(), [0] * 8)
    named = value.with_snapshot(
        replace(
            value.snapshot,
            state={
                **value.snapshot.state,
                "species": {"invincible.never-dies": metadata(value)},
            },
        )
    )
    after, output = run(named)
    retained = cast(Mapping[str, JsonValue], after.species_state["invincible.never-dies"])
    assert retained["status"] == "Extinct"
    assert output.events[0].target == "invincible.never-dies"
    assert len(output.state_delta.state) == 1
    assert output.state_delta.state[0].path == ("species",)
    repeated, no_event = run(after)
    assert repeated.snapshot == after.snapshot and not no_event.events


def test_multiple_species_events_are_stable_unique_and_order_independent() -> None:
    value = population(context(), [0] * 8)
    seed = SpeciesSeed("b", "producer", 1, 10, habitat="amphibious")
    first = value.with_snapshot(
        replace(
            value.snapshot,
            state={
                **value.snapshot.state,
                "species": {"b": seed.metadata(1, 80), "a": metadata(value)},
            },
        )
    )
    second = first.with_snapshot(
        replace(
            first.snapshot,
            state={
                **first.snapshot.state,
                "species": dict(reversed(list(first.species_state.items()))),
            },
        )
    )
    left, right = ExtinctionStage().execute(first), ExtinctionStage().execute(second)
    assert left == right
    assert len(left.events) == 2
    assert len({event.event_id for event in left.events}) == 2
    assert {event.target for event in left.events} == {"a", "b"}
    assert left.metrics["new_extinctions"] == 2


def test_unknown_previous_population_has_explicit_null_pressure_denominator() -> None:
    value = population(change_metadata(context(), last_population=0), [0] * 8)
    after, _ = run(value)
    cause = cast(Mapping[str, JsonValue], metadata(after)["extinction_cause"])
    assert cause["primary"] == "unknown" and cause["last_population"] == 0
    assert all(item is None for item in cast(Mapping[str, JsonValue], cause["pressure"]).values())


def test_large_contiguous_habitat_is_one_run_and_metrics_contain_no_tile_history() -> None:
    after, output = run(context(width=100, height=2))
    assert metadata(after)["current_habitat_runs"] == ((0, 199),)
    assert len(str(dict(output.metrics))) < 500
    assert all(isinstance(value, (int, float)) for value in output.metrics.values())
    next_value, next_output = run(next_turn(after))
    assert metadata(next_value)["current_habitat_runs"] == ((0, 199),)
    assert not any(
        patch.path[-1] == "current_habitat_runs" for patch in next_output.state_delta.state
    )


def test_empty_species_set_has_zero_rates_and_no_events() -> None:
    value = population(context(), [0] * 8)
    value = value.with_snapshot(
        replace(value.snapshot, state={**value.snapshot.state, "species": {}})
    )
    _, output = run(value)
    assert output.metrics["species_count"] == 0
    assert output.metrics["extinction_rate"] == 0
    assert not output.events and not output.state_delta.state


def test_unobserved_turns_break_the_consecutive_decline_streak() -> None:
    first, _ = run(population(context(), [9] * 8))
    skipped = next_turn(next_turn(first))
    after_gap, output = run(population(skipped, [8] * 8))
    assert metadata(after_gap)["status"] == "Healthy"
    assert metadata(after_gap)["declining_turns"] == 0
    assert not output.events
    observed, _ = run(population(next_turn(after_gap), [7] * 8))
    assert metadata(observed)["declining_turns"] == 1
    declining, output = run(population(next_turn(observed), [6] * 8))
    assert metadata(declining)["status"] == "Declining"
    assert [event.type for event in output.events] == ["SpeciesDeclining"]


def test_continued_decline_emits_no_duplicate_transition_and_remains_idempotent() -> None:
    value = context()
    for turn, count in enumerate((9, 8, 7, 6), start=1):
        value, output = run(population(value, [count] * 8))
        assert metadata(value)["declining_turns"] == turn
        assert [event.type for event in output.events] == (
            ["SpeciesDeclining"] if turn == 2 else []
        )
        repeated, retry = run(value)
        assert repeated.snapshot == value.snapshot
        assert not retry.events and not retry.state_delta.state
        value = next_turn(value)


@pytest.mark.parametrize("changes", [{"last_population": 1}, {"current_habitat_runs": ((0, 0),)}])
def test_terminal_metadata_cannot_retain_a_living_population_or_habitat(
    changes: dict[str, JsonValue],
) -> None:
    value = population(change_metadata(context(), status="Extinct", last_population=0), [0] * 8)
    value = change_metadata(value, **changes)
    with pytest.raises(ValueError, match="terminal"):
        ExtinctionStage().execute(value)


def test_isolated_singletons_override_total_abundance_and_can_rejoin_a_breeding_group() -> None:
    value = population(context(width=12, height=1), [1] * 12)
    isolated, output = run(value)
    assert metadata(isolated)["status"] == "Functionally Extinct"
    assert [event.type for event in output.events] == ["SpeciesFunctionallyExtinct"]
    repeated, output = run(next_turn(isolated))
    assert metadata(repeated)["status"] == "Functionally Extinct"
    assert not output.events
    recovered, output = run(population(next_turn(repeated), [2, 0] + [1] * 10))
    assert metadata(recovered)["status"] == "Healthy"
    assert [event.type for event in output.events] == ["SpeciesRecovered"]


def test_stale_scratch_arrays_cannot_supply_an_extinction_cause() -> None:
    value = population(context(), [0] * 8)
    expected = ExtinctionStage().execute(value)
    for name in (
        "deaths",
        "predation_deaths",
        "births",
        "temperature_pressure",
        "food_pressure",
        "predation_pressure",
        "competition",
        "suitability",
    ):
        stale = value.snapshot.arrays[name].numpy().copy()
        stale[0] = 999
        value = array(value, name, stale)
    actual = ExtinctionStage().execute(value)
    assert actual == expected
    cause = cast(Mapping[str, JsonValue], actual.events[0].payload["extinction_cause"])
    assert cause["primary"] == "unknown" and cause["total_deaths"] == 0


def test_dotted_id_domain_patch_preserves_other_species_fossils_and_lineage() -> None:
    value = population(context(), [0] * 8)
    fossil: dict[str, JsonValue] = {
        **SpeciesSeed("fossil", "producer", 1, 0).metadata(1, 0),
        "ancestor": "older.ancestor",
        "descendants": ("live.species",),
        "last_habitat": ((2, 3),),
        "last_nonzero_population": 5,
    }
    value = value.with_snapshot(
        replace(
            value.snapshot,
            state={
                **value.snapshot.state,
                "species": {
                    "live.species": {**metadata(value), "ancestor": "fossil"},
                    "fossil": fossil,
                },
            },
        )
    )
    after, output = run(value)
    assert after.species_state["fossil"] == value.species_state["fossil"]
    live = cast(Mapping[str, JsonValue], after.species_state["live.species"])
    assert live["ancestor"] == "fossil" and live["status"] == "Extinct"
    assert {event.target for event in output.events} == {"live.species"}
    assert len(output.state_delta.state) == 1
    assert output.state_delta.state[0].path == ("species",)


def test_first_turn_extinction_preserves_genesis_distribution_with_lifecycle_manifest(
    tmp_path: Path,
) -> None:
    engine = SimulationEngineV2(WorldStore(tmp_path / "fossils"), explainable_pipeline(), MODEL_ID)
    snapshot = create_reference_snapshot(
        WorldVersion("lifecycle", "genesis"),
        seed=31,
        manifest=engine.manifest,
        width=6,
        height=2,
        max_species=2,
        species=[SpeciesSeed("a", "herbivore", 1, 10)],
    )
    value = TurnContext(1, snapshot, 31, command={"command_id": "turn-1"})
    original = metadata(value)
    runs = cast(tuple[tuple[int, int], ...], original["current_habitat_runs"])
    occupied = tuple(int(tile) for tile in np.flatnonzero(snapshot.arrays["population"].numpy()[0]))
    assert occupied
    assert tuple(tile for start, end in runs for tile in range(start, end + 1)) == occupied
    assert original["lifecycle_turn"] == 0
    assert original["last_nonzero_population"] == 10 * len(occupied)
    after, output = run(population(value, [0] * 12))
    assert metadata(after)["last_habitat"] == runs
    assert metadata(after)["current_habitat_runs"] == ()
    assert metadata(after)["last_nonzero_population"] == original["last_nonzero_population"]
    assert metadata(after)["extinction_turn"] == 1
    assert [event.type for event in output.events] == ["SpeciesExtinct"]

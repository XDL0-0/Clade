"""Conservation and topology acceptance tests for local reference movement."""

from __future__ import annotations

import random
from dataclasses import replace

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from numpy.typing import NDArray

from app.simulation.v2.context import TurnContext
from app.simulation.v2.contracts import SimulationStage, StageContract, StageResult
from app.simulation.v2.pipeline import DeterministicPipeline
from app.simulation.v2.reducer import apply_delta, restrict
from app.simulation.v2.reference.demography_inputs import transported
from app.simulation.v2.reference.movement import ConnectivityStage, DispersalStage, MigrationStage
from app.simulation.v2.reference.world import SpeciesSeed, create_reference_snapshot
from app.simulation.v2.values import FrozenArray
from app.simulation.v2.version import WorldVersion


class FeedingReady(SimulationStage):
    contract = StageContract("reference_feeding", "test")

    def execute(self, context: TurnContext) -> StageResult:
        return StageResult(self.contract.name)


def context(
    width: int = 6, height: int = 1, *, habitat: str = "land", seed: int = 71
) -> TurnContext:
    snapshot = create_reference_snapshot(
        WorldVersion("movement", "timeline", 0, 0),
        seed=seed,
        manifest={"model": "ecology-reference-v1"},
        width=width,
        height=height,
        max_species=2,
        species=(SpeciesSeed("animal", "herbivore", 2.0, 100, habitat=habitat),),
    )
    state = dict(snapshot.state)
    state["species"] = {
        "animal": SpeciesSeed("animal", "herbivore", 2.0, 100, habitat=habitat).metadata(0, 100)
    }
    state["environment"] = {**snapshot.domain("environment"), "ecological_years_per_turn": 1.0}
    result = TurnContext(1, replace(snapshot, state=state), seed)
    size = width * height
    for name, value in {
        "population": np.zeros((2, size), dtype=np.int64),
        "energy_reserve": np.zeros((2, size)),
        "elevation": np.full(size, 10.0),
        "biome": np.full(size, 4, dtype=np.int64),
        "suitability": np.stack((np.ones(size), np.zeros(size))),
        "carrying_capacity": np.stack((np.full(size, 1000.0), np.zeros(size))),
    }.items():
        result = with_array(result, name, value)
    return result


def with_array(context: TurnContext, name: str, value: NDArray[np.generic]) -> TurnContext:
    return replace(
        context,
        snapshot=replace(
            context.snapshot,
            arrays={
                **context.snapshot.arrays,
                name: FrozenArray.from_numpy(value),
            },
        ),
    )


def with_row(context: TurnContext, name: str, values: list[float] | list[int]) -> TurnContext:
    value = np.array(context.snapshot.arrays[name].numpy(), copy=True)
    value[0] = values
    return with_array(context, name, value)


def single(context: TurnContext, stage: SimulationStage) -> TurnContext:
    view = restrict(context, stage.contract.reads)
    proposal = stage.execute(view)
    stage.validate_outputs(view, proposal)
    return replace(
        context,
        snapshot=apply_delta(context.snapshot, proposal.state_delta, writes=stage.contract.writes),
        stage_results=(proposal,),
    )


def array(context: TurnContext, name: str) -> NDArray[np.float64]:
    return np.array(context.snapshot.arrays[name].numpy(), dtype=np.float64)


def living(context: TurnContext) -> NDArray[np.int64]:
    return transported(
        context, np.array(context.snapshot.arrays["population"].numpy(), dtype=np.int64)
    )


def pipeline() -> DeterministicPipeline:
    return DeterministicPipeline(
        [ConnectivityStage(), MigrationStage(), DispersalStage(), FeedingReady()]
    )


def test_dispersal_conserves_population_and_reserve_without_relaying_arrivals() -> None:
    value = with_row(context(), "population", [100, 0, 0, 0, 0, 0])
    value = with_row(value, "energy_reserve", [50.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    before = value.snapshot.state_hash
    output = single(value, DispersalStage())
    np.testing.assert_array_equal(living(output)[0], [80, 10, 0, 0, 0, 10])
    np.testing.assert_allclose(array(output, "energy_reserve")[0], [40, 5, 0, 0, 0, 5])
    np.testing.assert_array_equal(array(output, "population"), array(value, "population"))
    assert output.stage_results[0].metrics["reserve_carbon_moved"] == 10
    assert value.snapshot.state_hash == before
    assert output.stage_results[0].events[0].payload["count"] == 20


def test_dispersal_resets_prior_turn_ledgers() -> None:
    value = with_row(context(), "population", [100, 0, 0, 0, 0, 0])
    value = with_row(value, "migration_in", [0, 50, 0, 0, 0, 0])
    value = with_row(value, "migration_out", [50, 0, 0, 0, 0, 0])
    output = single(value, DispersalStage())
    assert np.sum(array(output, "migration_out")) == 20
    np.testing.assert_array_equal(living(output)[0], [80, 10, 0, 0, 0, 10])


def test_predated_individuals_never_migrate_or_reappear() -> None:
    value = with_row(context(), "population", [100, 100, 0, 0, 0, 0])
    value = with_row(value, "predation_deaths", [100, 90, 0, 0, 0, 0])
    value = with_row(value, "energy_reserve", [0.0, 5.0, 0.0, 0.0, 0.0, 0.0])
    value = with_row(value, "food_pressure", [1.0, 1.0, 1.0, 1.0, 1.0, 1.0])
    output = pipeline().execute(value)
    assert int(living(output).sum()) == 10
    assert array(output, "migration_out")[0, 0] == 0
    assert np.sum(array(output, "energy_reserve")) == pytest.approx(5)
    assert array(output, "population")[0, 0] == 100


def test_empty_population_cannot_create_migrants_or_reserves() -> None:
    value = with_row(context(), "food_pressure", [1.0] * 6)
    output = pipeline().execute(value)
    assert not np.any(living(output))
    assert not np.any(array(output, "energy_reserve"))
    assert all(not item.events for item in output.stage_results)


def test_food_pressure_causes_local_migration_only_when_destination_improves() -> None:
    value = with_row(context(), "population", [100, 0, 0, 0, 0, 0])
    value = with_row(value, "energy_reserve", [50.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    calm = single(value, MigrationStage())
    assert not np.any(array(calm, "migration_out"))
    pressured = with_row(value, "food_pressure", [1.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    moved = single(pressured, MigrationStage())
    assert array(moved, "migration_out")[0, 0] == 50
    assert set(np.flatnonzero(array(moved, "migration_in")[0])) <= {1, 5}
    assert np.sum(array(moved, "energy_reserve")) == 50
    assert len(moved.stage_results[0].events) == 1
    event = moved.stage_results[0].events[0]
    assert event.type == "MigrationOccurred"
    pressures = event.payload["pressures"]
    assert isinstance(pressures, tuple) and "food_pressure" in pressures
    uniform = with_row(pressured, "population", [100] * 6)
    uniform = with_row(uniform, "energy_reserve", [50.0] * 6)
    assert not np.any(array(single(uniform, MigrationStage()), "migration_out"))


@pytest.mark.parametrize("stage", [DispersalStage(), MigrationStage()])
def test_no_suitable_neighbor_means_no_movement(stage: SimulationStage) -> None:
    value = with_row(context(), "population", [100, 0, 0, 0, 0, 0])
    value = with_row(value, "food_pressure", [1.0] * 6)
    value = with_row(value, "suitability", [1.0, 0.19, 1.0, 1.0, 1.0, 0.19])
    output = single(value, stage)
    assert not np.any(array(output, "migration_out"))


def test_migration_uses_start_of_stage_population_not_incoming_relay() -> None:
    value = with_row(context(), "population", [100, 0, 0, 0, 0, 0])
    value = with_row(value, "food_pressure", [1.0] * 6)
    output = single(value, MigrationStage())
    np.testing.assert_array_equal(array(output, "migration_out")[0], [50, 0, 0, 0, 0, 0])
    assert np.sum(living(output)) == 100


@pytest.mark.parametrize(
    "habitat,expected",
    [
        ("land", [0, -1, -1, 0]),
        ("water", [-1, 1, 1, -1]),
        ("amphibious", [0, 0, 0, 0]),
    ],
)
def test_connectivity_uses_habitat_and_x_seam(habitat: str, expected: list[int]) -> None:
    value = with_array(context(4, habitat=habitat), "biome", np.array([4, 0, 1, 4], dtype=np.int64))
    output = single(value, ConnectivityStage())
    np.testing.assert_array_equal(array(output, "connectivity")[0], expected)
    np.testing.assert_array_equal(array(output, "connectivity")[1], [-1] * 4)


def test_land_mountain_and_river_barriers_also_block_connectivity() -> None:
    value = with_array(context(4), "elevation", np.array([0.0, 801.0, 801.0, 0.0]))
    output = single(value, ConnectivityStage())
    np.testing.assert_array_equal(array(output, "connectivity")[0], [0, 1, 1, 0])
    river = with_array(context(4), "river_flux", np.array([0.0, 1001.0, 0.0, 1001.0]))
    np.testing.assert_array_equal(
        array(single(river, ConnectivityStage()), "connectivity")[0], [0, -1, 2, -1]
    )
    river = with_row(river, "population", [100, 0, 0, 0])
    assert not np.any(array(single(river, DispersalStage()), "migration_out"))
    mountain = with_row(value, "population", [100, 0, 0, 0])
    moved = single(mountain, DispersalStage())
    np.testing.assert_array_equal(array(moved, "migration_in")[0], [0, 0, 0, 20])


def test_amphibious_ignores_land_barriers_and_empty_occupancy_does_not_split_components() -> None:
    value = with_array(
        context(4, habitat="amphibious"), "elevation", np.array([0.0, 3000.0, 0.0, 3000.0])
    )
    value = with_array(value, "river_flux", np.full(4, 2000.0))
    value = with_row(value, "population", [1, 0, 1, 0])
    before_species = value.species_state
    output = single(value, ConnectivityStage())
    np.testing.assert_array_equal(array(output, "connectivity")[0], [0] * 4)
    assert output.species_state == before_species


@settings(max_examples=60, deadline=None)
@given(half_width=st.integers(1, 4), height=st.integers(1, 4), seed=st.integers(0, 100000))
def test_small_world_transport_conserves_all_living_counts_and_reserves(
    half_width: int,
    height: int,
    seed: int,
) -> None:
    value = context(half_width * 2, height, seed=seed)
    size = half_width * 2 * height
    rng = np.random.default_rng(seed)
    counts = rng.integers(0, 200, size)
    killed = np.array([rng.integers(0, n + 1) for n in counts], dtype=np.int64)
    reserve = (counts - killed) * rng.uniform(0, 5, size)
    for name, values in {
        "population": counts,
        "predation_deaths": killed,
        "energy_reserve": reserve,
        "suitability": rng.uniform(0, 1, size),
        "food_pressure": rng.uniform(0, 1, size),
    }.items():
        field = np.array(value.snapshot.arrays[name].numpy(), copy=True)
        field[0] = values
        value = with_array(value, name, field)
    before = value.snapshot.state_hash
    output = pipeline().execute(value)
    assert int(living(output).sum()) == int((counts - killed).sum())
    assert np.sum(array(output, "energy_reserve")) == pytest.approx(float(reserve.sum()), abs=1e-9)
    assert np.all(living(output) >= 0)
    assert value.snapshot.state_hash == before
    assert np.array_equal(
        output.snapshot.arrays["population"].numpy(), value.snapshot.arrays["population"].numpy()
    )


@pytest.mark.parametrize("seed", [4, 71])
def test_same_seed_and_inputs_replay_without_global_rng_or_array_mutation(seed: int) -> None:
    value = with_row(context(seed=seed), "population", [13, 7, 29, 1, 0, 2])
    value = with_row(value, "energy_reserve", [7.0, 4.0, 10.0, 0.1, 0.0, 1.0])
    value = with_row(value, "food_pressure", [0.7] * 6)
    before_hash, before_rng = value.snapshot.state_hash, random.getstate()
    first, second = pipeline().execute(value), pipeline().execute(value)
    assert first.snapshot.state_hash == second.snapshot.state_hash
    assert [r.events for r in first.stage_results] == [r.events for r in second.stage_results]
    assert value.snapshot.state_hash == before_hash
    assert random.getstate() == before_rng
    for item in first.stage_results:
        assert not item.ai_jobs
        assert all(isinstance(metric, (int, float, str, bool)) for metric in item.metrics.values())
        assert all(patch.name != "population" for patch in item.state_delta.arrays)


@pytest.mark.parametrize(
    "name,values",
    [
        ("migration_in", [1, 0, 0, 0, 0, 0]),
        ("predation_deaths", [1, 0, 0, 0, 0, 0]),
        ("energy_reserve", [1.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
        ("food_pressure", [2.0] * 6),
    ],
)
def test_invalid_ledgers_pressure_or_unowned_carbon_fail_closed(
    name: str, values: list[int]
) -> None:
    value = with_row(context(), name, values)
    before = value.snapshot.state_hash
    with pytest.raises(ValueError):
        single(value, MigrationStage())
    assert value.snapshot.state_hash == before


def test_unused_slots_reject_population_reserve_scratch_and_labels() -> None:
    for name in ("population", "energy_reserve", "suitability", "migration_in", "connectivity"):
        value = context()
        field = np.array(value.snapshot.arrays[name].numpy(), copy=True)
        field[1, 0] = 1
        value = with_array(value, name, field)
        with pytest.raises(ValueError):
            single(value, ConnectivityStage())


def test_int64_arrival_overflow_is_rejected_without_modifying_inputs() -> None:
    value = with_row(context(2), "population", [np.iinfo(np.int64).max, np.iinfo(np.int64).max])
    value = with_row(value, "suitability", [0.2, 1.0])
    value = with_row(value, "food_pressure", [1.0, 0.0])
    before = value.snapshot.state_hash
    with pytest.raises(ValueError, match="overflows int64"):
        single(value, MigrationStage())
    assert value.snapshot.state_hash == before


@pytest.mark.parametrize(
    "name", ["population", "migration_in", "migration_out", "predation_deaths"]
)
def test_count_matrices_require_exact_int64_dtype_and_shape(name: str) -> None:
    value = with_array(context(), name, np.zeros((2, 6), dtype=np.int32))
    with pytest.raises(ValueError, match="int64"):
        single(value, DispersalStage())
    value = with_array(context(), name, np.zeros((2, 5), dtype=np.int64))
    with pytest.raises(ValueError):
        single(value, DispersalStage())


@pytest.mark.parametrize(
    "pressure",
    [
        "food_pressure",
        "temperature_pressure",
        "water_pressure",
        "predation_pressure",
        "competition",
    ],
)
def test_each_pressure_channel_can_drive_migration(pressure: str) -> None:
    value = with_row(context(), "population", [100, 0, 0, 0, 0, 0])
    value = with_row(
        value, pressure, [2.0 if pressure == "competition" else 1.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    )
    output = single(value, MigrationStage())
    assert array(output, "migration_out")[0, 0] == 50


def test_migration_appends_ledgers_using_live_arrivals_and_moves_matching_reserve() -> None:
    value = with_row(context(), "population", [100, 0, 0, 0, 0, 0])
    value = with_row(value, "migration_out", [20, 0, 0, 0, 0, 0])
    value = with_row(value, "migration_in", [0, 20, 0, 0, 0, 0])
    value = with_row(value, "energy_reserve", [40.0, 10.0, 0.0, 0.0, 0.0, 0.0])
    value = with_row(value, "food_pressure", [0.0, 1.0, 0.0, 0.0, 0.0, 0.0])
    value = with_row(value, "suitability", [1.0, 0.3, 1.0, 1.0, 1.0, 1.0])
    output = single(value, MigrationStage())
    np.testing.assert_array_equal(array(output, "migration_out")[0], [20, 10, 0, 0, 0, 0])
    np.testing.assert_array_equal(array(output, "migration_in")[0], [0, 20, 10, 0, 0, 0])
    np.testing.assert_allclose(array(output, "energy_reserve")[0], [40, 5, 5, 0, 0, 0])
    assert living(output).sum() == 100


def test_fractional_dispersal_uses_reproducible_stochastic_rounding() -> None:
    outcomes: list[int] = []
    for seed in range(100):
        value = with_row(context(2, seed=seed), "population", [1, 0])
        output = single(value, DispersalStage())
        outcomes.append(int(np.sum(array(output, "migration_out"))))
    assert set(outcomes) == {0, 1}
    assert 5 < sum(outcomes) < 40


def test_migration_never_exceeds_half_the_stage_start_available_count() -> None:
    value = with_row(context(2), "population", [3, 0])
    value = with_row(value, "food_pressure", [1.0, 0.0])
    output = single(value, MigrationStage())
    assert array(output, "migration_out")[0, 0] == 1


@pytest.mark.parametrize("biome,habitat", [([0, 4], "land"), ([4, 0], "water")])
def test_land_and_water_migrants_cannot_cross_into_other_habitats(
    biome: list[int],
    habitat: str,
) -> None:
    value = with_array(context(2, habitat=habitat), "biome", np.array(biome, dtype=np.int64))
    value = with_row(value, "population", [100, 0])
    value = with_row(value, "food_pressure", [1.0, 0.0])
    assert not np.any(array(single(value, DispersalStage()), "migration_out"))
    assert not np.any(array(single(value, MigrationStage()), "migration_out"))


def test_duplicate_species_slots_are_rejected() -> None:
    value = context()
    metadata = dict(value.species_state)
    metadata["duplicate"] = metadata["animal"]
    value = replace(
        value,
        snapshot=replace(
            value.snapshot,
            state={
                **value.snapshot.state,
                "species": metadata,
            },
        ),
    )
    with pytest.raises(ValueError, match="unique"):
        single(value, MigrationStage())


@pytest.mark.parametrize(
    "name",
    [
        "energy_reserve",
        "suitability",
        "carrying_capacity",
        "competition",
        "food_pressure",
        "temperature_pressure",
        "water_pressure",
        "predation_pressure",
    ],
)
def test_movement_requires_finite_nonnegative_float64_matrices(name: str) -> None:
    value = with_array(context(), name, np.zeros((2, 6), dtype=np.float32))
    with pytest.raises(ValueError, match="float64"):
        single(value, MigrationStage())
    value = with_array(context(), name, np.full((2, 6), -1.0))
    with pytest.raises(ValueError, match="nonnegative"):
        single(value, MigrationStage())


def test_existing_ledger_int64_overflow_is_rejected_before_narrowing() -> None:
    maximum = int(np.iinfo(np.int64).max)
    value = with_row(context(2), "population", [100, 0])
    value = with_row(value, "migration_in", [maximum, 0])
    value = with_row(value, "migration_out", [maximum, 0])
    value = with_row(value, "food_pressure", [1.0, 0.0])
    before = value.snapshot.state_hash
    with pytest.raises(ValueError, match="overflows int64"):
        single(value, MigrationStage())
    assert value.snapshot.state_hash == before


def test_barrier_endpoints_and_suitability_threshold_are_inclusive() -> None:
    value = with_array(context(2), "elevation", np.array([0.0, 800.0]))
    value = with_array(value, "river_flux", np.full(2, 1000.0))
    value = with_row(value, "suitability", [0.2, 0.2])
    output = single(value, ConnectivityStage())
    np.testing.assert_array_equal(array(output, "connectivity")[0], [0, 0])
    value = with_row(value, "population", [100, 0])
    assert np.sum(array(single(value, DispersalStage()), "migration_out")) == 20


def test_small_reserve_transfer_lost_in_large_destination_stock_fails_closed() -> None:
    value = with_row(context(2), "population", [100, 1])
    value = with_row(value, "energy_reserve", [1.0, 1e18])
    value = with_row(value, "food_pressure", [1.0, 0.0])
    before = value.snapshot.state_hash
    with pytest.raises(ValueError, match="lost at a tile boundary"):
        single(value, MigrationStage())
    assert value.snapshot.state_hash == before

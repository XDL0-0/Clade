"""Independent stock, competition and predation specifications for reference ecology."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import cast

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from numpy.typing import NDArray

from app.simulation.v2.context import TurnContext, WorldSnapshot
from app.simulation.v2.contracts import SimulationStage, StageContract, StageResult
from app.simulation.v2.pipeline import DeterministicPipeline
from app.simulation.v2.reducer import apply_delta
from app.simulation.v2.reference.common import FloatArray
from app.simulation.v2.reference.ecology import (
    CarryingCapacityStage,
    CompetitionStage,
    FeedingStage,
    HabitatSuitabilityStage,
    Species,
)
from app.simulation.v2.reference.feeding import _functional_response
from app.simulation.v2.reference.world import DEFAULT_SPECIES, SpeciesSeed
from app.simulation.v2.values import FrozenArray, JsonValue, digest
from app.simulation.v2.version import WorldVersion

_STAGES = (HabitatSuitabilityStage(), CarryingCapacityStage(), CompetitionStage(), FeedingStage())
_SCRATCH = (
    "suitability",
    "carrying_capacity",
    "competition",
    "temperature_pressure",
    "water_pressure",
    "food_pressure",
    "predation_pressure",
)
_SEEDS = (
    SpeciesSeed("plant", "producer", 1.0, 4, fertility=0.75),
    SpeciesSeed("grazer", "herbivore", 2.0, 4, fertility=0.75),
    SpeciesSeed("rabbit", "herbivore", 1.0, 0, fertility=0.75),
    SpeciesSeed("wolf", "carnivore", 3.0, 0, fertility=0.75),
    SpeciesSeed("lynx", "carnivore", 3.0, 0, fertility=0.75),
    SpeciesSeed("fungus", "decomposer", 0.1, 0, fertility=0.75),
)


class _RegenerationReady(SimulationStage):
    contract = StageContract("reference_regeneration", "test-noop")

    def execute(self, context: TurnContext) -> StageResult:
        return StageResult(self.contract.name)


@pytest.mark.parametrize("predator_id, prey_id", [("hunter", "grazer"), ("fish", "plankton")])
def test_default_predators_can_meet_maintenance_at_high_prey_density(
    predator_id: str,
    prey_id: str,
) -> None:
    def phenotype(identity: str) -> Species:
        seed = next(seed for seed in DEFAULT_SPECIES if seed.species_id == identity)
        return Species(
            identity,
            0,
            seed.role,
            seed.body_mass,
            seed.habitat,
            seed.thermal_optimum,
            seed.thermal_width,
            seed.water_need,
            seed.fertility,
            {key: float(cast(float, value)) for key, value in seed.traits.items()},
        )

    predator, prey = phenotype(predator_id), phenotype(prey_id)
    maximum_ration = 0.7 * prey.mass * _functional_response(predator, prey, 1, 10**12, 1, 1)
    maintenance = predator.mass * (0.25 + 0.05 * sum(predator.traits.values()))
    assert maximum_ration > maintenance
    assert FeedingStage.contract.version == "2"


@pytest.mark.parametrize("name", ["plant_biomass", "energy_reserve", "detritus"])
def test_small_flux_lost_in_large_stock_is_rejected(name: str) -> None:
    context = _context()
    array = _array(context, name).copy()
    if array.ndim == 2:
        array[0] = 1e18
    else:
        array[:] = 1e18
    context = _with_array(context, name, array)
    before = context.snapshot.state_hash
    with pytest.raises(ValueError, match="Feeding ledger balance lost"):
        FeedingStage().execute(context)
    assert context.snapshot.state_hash == before


def _context(*, tiles: int = 2, dt: float = 1.0) -> TurnContext:
    population = np.zeros((7, tiles), dtype=np.int64)
    species: dict[str, JsonValue] = {}
    for row, seed in enumerate(_SEEDS):
        population[row] = seed.population_per_tile
        species[seed.species_id] = seed.metadata(row, seed.population_per_tile * tiles)
    values: dict[str, NDArray[np.generic]] = {
        "population": population,
        "energy_reserve": np.zeros((7, tiles)),
        "temperature": np.full(tiles, 18.0),
        "soil_water": np.full(tiles, 100.0),
        "humidity": np.full(tiles, 0.8),
        "plant_biomass": np.full(tiles, 6.0),
        "nutrients": np.full(tiles, 20.0),
        "detritus": np.zeros(tiles),
        "npp": np.full(tiles, 20.0),
        "biome": np.full(tiles, 4, dtype=np.int64),
        "predation_deaths": np.zeros((7, tiles), dtype=np.int64),
    }
    values.update({name: np.zeros((7, tiles)) for name in _SCRATCH})
    values["suitability"][:6] = 1.0
    return TurnContext(
        turn_id=1,
        seed=771,
        snapshot=WorldSnapshot(
            WorldVersion("ecology", "timeline", 0, 0),
            turn_id=0,
            state={
                "geometry": {"width": tiles, "height": 1},
                "environment": {"ecological_years_per_turn": dt},
                "species": species,
                "food_web": {
                    "edges": (
                        {"predator": "wolf", "prey": "grazer", "preference": 1.0},
                        {"predator": "lynx", "prey": "grazer", "preference": 1.0},
                    )
                },
            },
            arrays={name: FrozenArray.from_numpy(value) for name, value in values.items()},
        ),
    )


def _array(context: TurnContext, name: str) -> FloatArray:
    return np.array(context.snapshot.arrays[name].numpy(), dtype=np.float64)


def _with_array(context: TurnContext, name: str, value: NDArray[np.generic]) -> TurnContext:
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


def _with_state(context: TurnContext, name: str, value: JsonValue) -> TurnContext:
    return replace(
        context,
        snapshot=replace(
            context.snapshot,
            state={
                **context.snapshot.state,
                name: value,
            },
        ),
    )


def _apply(context: TurnContext, stage: SimulationStage) -> tuple[TurnContext, StageResult]:
    result = stage.execute(context)
    snapshot = apply_delta(context.snapshot, result.state_delta, writes=stage.contract.writes)
    return context.with_snapshot(snapshot), result


def _carbon(context: TurnContext, *, subtract_kills: bool = False) -> FloatArray:
    counts = _array(context, "population")
    if subtract_kills:
        counts -= _array(context, "predation_deaths")
    masses = np.zeros(counts.shape[0])
    for raw in context.species_state.values():
        item = cast(Mapping[str, JsonValue], raw)
        masses[cast(int, item["slot"])] = cast(float, item["body_mass"])
    return (
        (counts * masses[:, None]).sum(axis=0)
        + _array(context, "energy_reserve").sum(axis=0)
        + _array(context, "plant_biomass")
        + _array(context, "detritus")
    )


def _hunters(*, prey: int = 10, predators: int = 4, dt: float = 1.0) -> TurnContext:
    context = _context(dt=dt)
    counts = np.zeros((7, 2), dtype=np.int64)
    counts[1] = prey
    counts[3:5] = predators
    context = _with_array(context, "population", counts)
    context = _with_array(context, "plant_biomass", np.zeros(2))
    reserves = np.zeros((7, 2))
    reserves[1] = prey * 0.8
    return _with_array(context, "energy_reserve", reserves)


@st.composite
def _worlds(draw: st.DrawFn) -> TurnContext:
    tiles = draw(st.integers(2, 6))
    dt = draw(st.floats(0, 4, allow_nan=False, allow_infinity=False))
    context = _context(tiles=tiles, dt=dt)
    rng = np.random.default_rng(draw(st.integers(0, 2**32 - 1)))
    counts = rng.integers(0, 60, (7, tiles), dtype=np.int64)
    counts[-1] = 0
    context = _with_array(context, "population", counts)
    reserve = rng.uniform(0, 100, (7, tiles))
    reserve[counts == 0] = 0
    context = _with_array(context, "energy_reserve", reserve)
    for name in ("plant_biomass", "detritus", "nutrients", "soil_water", "npp"):
        context = _with_array(context, name, rng.uniform(0, 200, tiles))
    for name in ("suitability", "competition"):
        factors = rng.uniform(0, 1 if name == "suitability" else 10, (7, tiles))
        factors[-1] = 0
        context = _with_array(context, name, factors)
    return replace(context, seed=draw(st.integers(0, 2**32 - 1)))


@given(_worlds())
@settings(max_examples=70, deadline=None)
def test_feeding_conserves_carbon_per_tile_without_mutating_input(context: TurnContext) -> None:
    before = context.snapshot.snapshot_id
    after, result = _apply(context, FeedingStage())
    np.testing.assert_allclose(_carbon(after, subtract_kills=True), _carbon(context), atol=2e-10)
    np.testing.assert_array_equal(_array(after, "population"), _array(context, "population"))
    kills = np.array(after.snapshot.arrays["predation_deaths"].numpy(), dtype=np.int64)
    assert kills.dtype == np.dtype("int64")
    assert np.all((kills >= 0) & (kills <= _array(context, "population")))
    for name in ("energy_reserve", "plant_biomass", "detritus"):
        assert np.all(_array(after, name) >= 0)
    for name in ("food_pressure", "predation_pressure"):
        assert np.all((_array(after, name) >= 0) & (_array(after, name) <= 1))
    assert context.snapshot.snapshot_id == before
    assert after.snapshot.state == context.snapshot.state
    assert result == FeedingStage().execute(context)
    assert "population" not in {patch.name for patch in result.state_delta.arrays}


def test_leaf_allocation_is_proportional_and_counts_assimilation_loss() -> None:
    context = _context()
    after, _ = _apply(context, FeedingStage())
    np.testing.assert_allclose(_array(after, "energy_reserve")[:2], [[2, 2], [2, 2]])
    np.testing.assert_allclose(_array(after, "detritus"), [2, 2])
    np.testing.assert_array_equal(_array(after, "plant_biomass"), [0, 0])
    np.testing.assert_allclose(_array(after, "food_pressure")[:2], [[0.5, 0.5], [0.75, 0.75]])
    np.testing.assert_array_equal(_array(after, "predation_deaths"), np.zeros((7, 2)))


def test_suitability_and_competition_reduce_intake_without_creating_leaf() -> None:
    context = _with_array(_context(), "plant_biomass", np.full(2, 100.0))
    suitable = _array(context, "suitability")
    suitable[:2] = 0.5
    competing = _array(context, "competition")
    competing[:2] = 1.0
    context = _with_array(_with_array(context, "suitability", suitable), "competition", competing)
    after, _ = _apply(context, FeedingStage())
    np.testing.assert_allclose(_array(after, "energy_reserve")[:2], [[1, 1], [1, 1]])
    np.testing.assert_allclose(_array(after, "plant_biomass"), [97, 97])
    np.testing.assert_allclose(_array(after, "detritus"), [1, 1])


@pytest.mark.parametrize("absence", ["population", "dt", "suitability"])
def test_no_living_consumers_or_no_opportunity_means_no_intake(absence: str) -> None:
    context = _context(dt=0 if absence == "dt" else 1)
    if absence == "population":
        context = _with_array(context, "population", np.zeros((7, 2), dtype=np.int64))
    if absence == "suitability":
        context = _with_array(context, "suitability", np.zeros((7, 2)))
    after, _ = _apply(context, FeedingStage())
    for name in ("plant_biomass", "energy_reserve", "detritus"):
        assert after.snapshot.arrays[name] == context.snapshot.arrays[name]
    np.testing.assert_array_equal(_array(after, "predation_deaths"), np.zeros((7, 2)))


def test_predation_releases_structural_and_proportional_reserve_carbon() -> None:
    context = _hunters(prey=100, predators=20, dt=10)
    after, _ = _apply(context, FeedingStage())
    kills = _array(after, "predation_deaths")[1]
    assert np.all(kills > 0)
    np.testing.assert_allclose(_array(after, "energy_reserve")[1], (100 - kills) * 0.8)
    released = kills * (2.0 + 0.8)
    np.testing.assert_allclose(_array(after, "energy_reserve")[3:5].sum(axis=0), released * 0.7)
    np.testing.assert_allclose(_array(after, "detritus"), released * 0.3)
    np.testing.assert_allclose(_array(after, "predation_pressure")[1], kills / 100)
    np.testing.assert_allclose(_carbon(after, subtract_kills=True), _carbon(context))


def test_shared_prey_cannot_be_killed_more_than_once_and_allocation_varies_by_seed() -> None:
    context = _hunters(prey=1, predators=100, dt=100)
    winners: set[int] = set()
    for seed in range(32):
        after, _ = _apply(replace(context, seed=seed), FeedingStage())
        kills = _array(after, "predation_deaths")
        np.testing.assert_array_equal(kills[1], [1, 1])
        np.testing.assert_array_equal(kills[[0, 2, 3, 4, 5, 6]], np.zeros((6, 2)))
        gain = _array(after, "energy_reserve")[3:5]
        np.testing.assert_allclose(gain.sum(axis=0), [1.96, 1.96])
        winners.add(int(np.argmax(gain[:, 0])))
    assert winners == {0, 1}, "Stable lexical species order must not decide every scarce prey"


def test_holling_response_saturates_as_prey_density_grows() -> None:
    kills = []
    for prey in (100, 1000, 10000):
        after, _ = _apply(_hunters(prey=prey, predators=2), FeedingStage())
        kills.append(float(_array(after, "predation_deaths").sum()))
    assert 0 < kills[0] <= kills[1] <= kills[2]
    assert kills[2] < 3 * kills[0], "A hundredfold prey increase must not drive linear capture"


def test_only_explicit_prey_edges_are_consumed_and_previous_kills_reset() -> None:
    context = _hunters(prey=20, predators=10, dt=10)
    counts = np.array(context.snapshot.arrays["population"].numpy(), dtype=np.int64)
    counts[2] = 20
    context = _with_array(context, "population", counts)
    after, _ = _apply(context, FeedingStage())
    assert np.any(_array(after, "predation_deaths")[1] > 0)
    np.testing.assert_array_equal(_array(after, "predation_deaths")[2], [0, 0])
    edge_free = _with_state(after, "food_web", {"edges": ()})
    reset, _ = _apply(replace(edge_free, turn_id=2), FeedingStage())
    np.testing.assert_array_equal(_array(reset, "predation_deaths"), np.zeros((7, 2)))
    np.testing.assert_array_equal(_array(reset, "predation_pressure"), np.zeros((7, 2)))


@pytest.mark.parametrize(
    "edge",
    [
        {"predator": "ghost", "prey": "grazer", "preference": 1.0},
        {"predator": "wolf", "prey": "ghost", "preference": 1.0},
        {"predator": "wolf", "prey": "plant", "preference": 1.0},
        {"predator": "wolf", "prey": "grazer", "preference": -1.0},
    ],
)
def test_invalid_food_web_edges_are_rejected(edge: dict[str, JsonValue]) -> None:
    context = _with_state(_context(), "food_web", {"edges": (edge,)})
    before = context.snapshot.snapshot_id
    with pytest.raises((ValueError, TypeError)):
        FeedingStage().execute(context)
    assert context.snapshot.snapshot_id == before


def test_pipeline_contracts_replay_and_immutable_species_inputs() -> None:
    context = _context()
    before = context.snapshot.snapshot_id
    species_hash = digest(context.species_state)
    pipeline = DeterministicPipeline([*_STAGES[::-1], _RegenerationReady()])
    first, second = pipeline.execute(context), pipeline.execute(context)
    assert first.snapshot.state_hash == second.snapshot.state_hash
    assert [result.output_hash for result in first.stage_results] == [
        result.output_hash for result in second.stage_results
    ]
    assert [stage.contract.name for stage in pipeline.stages] == [
        "reference_regeneration",
        "reference_suitability",
        "reference_capacity",
        "reference_competition",
        "reference_feeding",
    ]
    previous = context.snapshot
    for stage, result in zip(pipeline.stages, first.stage_results, strict=True):
        assert "arrays.population" not in stage.contract.writes
        assert set(stage.contract.writes) <= set(stage.contract.reads)
        for patch in result.state_delta.arrays:
            assert patch.expected_hash == previous.arrays[patch.name].content_hash
        previous = apply_delta(previous, result.state_delta, writes=stage.contract.writes)
    assert context.snapshot.snapshot_id == before
    assert digest(first.snapshot.domain("species")) == species_hash
    np.testing.assert_array_equal(
        first.snapshot.arrays["population"].numpy(), context.snapshot.arrays["population"].numpy()
    )
    for name in (*_SCRATCH, "predation_deaths", "energy_reserve", "population"):
        array = first.snapshot.arrays[name].numpy()
        assert not array.flags.writeable
        np.testing.assert_array_equal(array[-1], np.zeros(2))


@pytest.mark.parametrize("stage", _STAGES)
@pytest.mark.parametrize("invalid", ["duplicate_slot", "unused_population", "unused_reserve"])
def test_invalid_species_slots_and_orphan_stocks_fail_closed(
    stage: SimulationStage,
    invalid: str,
) -> None:
    context = _context()
    if invalid == "duplicate_slot":
        species = dict(context.species_state)
        plant = dict(cast(Mapping[str, JsonValue], species["plant"]))
        species["plant"] = {**plant, "slot": 1}
        context = _with_state(context, "species", species)
    else:
        name = "population" if invalid == "unused_population" else "energy_reserve"
        values = context.snapshot.arrays[name].numpy().copy()
        values[-1, 0] = 1
        context = _with_array(context, name, values)
    before = context.snapshot.snapshot_id
    with pytest.raises((ValueError, TypeError)):
        stage.execute(context)
    assert context.snapshot.snapshot_id == before


def test_suitability_matches_habitat_and_responds_to_temperature() -> None:
    context = _context()
    context = _with_array(context, "biome", np.array([0, 4], dtype=np.int64))
    baseline, _ = _apply(context, HabitatSuitabilityStage())
    np.testing.assert_array_equal(_array(baseline, "suitability")[:, 0], np.zeros(7))
    warm = _with_array(context, "temperature", np.full(2, 54.0))
    stressed, _ = _apply(warm, HabitatSuitabilityStage())
    assert np.all(_array(stressed, "suitability")[:6, 1] < _array(baseline, "suitability")[:6, 1])
    assert np.all(
        _array(stressed, "temperature_pressure")[:6, 1]
        > _array(baseline, "temperature_pressure")[:6, 1]
    )


def test_competition_increases_with_population_at_fixed_capacity() -> None:
    capacity = np.full((7, 2), 10.0)
    capacity[-1] = 0
    context = _with_array(_context(), "carrying_capacity", capacity)
    low, _ = _apply(context, CompetitionStage())
    counts = np.array(context.snapshot.arrays["population"].numpy(), dtype=np.int64)
    counts[:2] *= 10
    high, _ = _apply(_with_array(context, "population", counts), CompetitionStage())
    assert np.all(_array(high, "competition")[:2] > _array(low, "competition")[:2])
    assert np.all(_array(high, "competition") >= 0)


def test_metrics_remain_small_scalar_summaries() -> None:
    def scalar_tree(value: JsonValue) -> bool:
        if isinstance(value, Mapping):
            return all(scalar_tree(item) for item in value.values())
        return not isinstance(value, (tuple, list))

    context = _context(tiles=100)
    for stage in _STAGES:
        context, result = _apply(context, stage)
        assert scalar_tree(result.metrics)
        assert len(str(dict(result.metrics))) < 3000


def test_capacity_requires_available_resources_and_explicit_predator_edges() -> None:
    context = _with_array(_hunters(), "plant_biomass", np.zeros(2))
    after, _ = _apply(context, CarryingCapacityStage())
    capacity = _array(after, "carrying_capacity")
    np.testing.assert_array_equal(capacity[:3], np.zeros((3, 2)))
    np.testing.assert_array_equal(capacity[5:], np.zeros((2, 2)))
    assert np.all(capacity[3:5] > 0)
    disconnected, _ = _apply(
        _with_state(context, "food_web", {"edges": ()}), CarryingCapacityStage()
    )
    np.testing.assert_array_equal(_array(disconnected, "carrying_capacity"), np.zeros((7, 2)))


def test_tiny_positive_time_step_does_not_overflow_unlimited_food_ratio() -> None:
    context = _context(dt=1e-310)
    after, _ = _apply(context, FeedingStage())
    np.testing.assert_allclose(_carbon(after, subtract_kills=True), _carbon(context))
    np.testing.assert_array_equal(_array(after, "predation_deaths"), np.zeros((7, 2)))
    assert np.isfinite(_array(after, "food_pressure")).all()

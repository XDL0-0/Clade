"""Conservation and boundary specifications for the explicit new resource model."""

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
from app.simulation.v2.pipeline import DeterministicPipeline, StageExecutionError
from app.simulation.v2.reducer import apply_delta
from app.simulation.v2.reference.common import FloatArray
from app.simulation.v2.reference.resources import (
    MODEL_VERSION,
    PrimaryProductivityStage,
    ResourceRegenerationStage,
)
from app.simulation.v2.values import FrozenArray, JsonValue, digest
from app.simulation.v2.version import WorldVersion


class _BiomeReady(SimulationStage):
    contract = StageContract("reference_biome", "test-noop")

    def execute(self, context: TurnContext) -> StageResult:
        return StageResult(self.contract.name)


def test_unrepresentable_stock_flux_fails_closed() -> None:
    context = _with_array(_context(), "plant_biomass", np.full(2, 1e18))
    before = context.snapshot.state_hash
    with pytest.raises(ValueError, match="ledger balance lost"):
        PrimaryProductivityStage().execute(context)
    assert context.snapshot.state_hash == before


def _context(*, width: int = 2, height: int = 1, dt: float = 1 / 12) -> TurnContext:
    tiles = width * height
    values: dict[str, NDArray[np.generic]] = {
        "population": np.full((3, tiles), 10, dtype=np.int64),
        "energy_reserve": np.full((3, tiles), 2.0),
        "temperature": np.full(tiles, 22.0),
        "soil_water": np.full(tiles, 100.0),
        "humidity": np.full(tiles, 0.8),
        "plant_biomass": np.full(tiles, 20.0),
        "nutrients": np.full(tiles, 10.0),
        "detritus": np.full(tiles, 40.0),
        "npp": np.full(tiles, 999.0),
        "biome": np.full(tiles, 4, dtype=np.int64),
    }
    return TurnContext(
        turn_id=1,
        seed=771,
        snapshot=WorldSnapshot(
            WorldVersion("resources", "timeline", 0, 0),
            turn_id=0,
            state={
                "geometry": {"width": width, "height": height},
                "environment": {"ecological_years_per_turn": dt},
                "species": {
                    "leaf": {"slot": 0, "role": "producer", "body_mass": 5.0},
                    "fungus": {"slot": 1, "role": "decomposer", "body_mass": 2.0},
                    "deer": {"slot": 2, "role": "herbivore", "body_mass": 3.0},
                },
                "phenotypes_unrelated": {"immutable": True},
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
    proposal = stage.execute(context)
    snapshot = apply_delta(context.snapshot, proposal.state_delta, writes=stage.contract.writes)
    return context.with_snapshot(snapshot), proposal


def _pool(context: TurnContext) -> FloatArray:
    return (
        _array(context, "plant_biomass")
        + _array(context, "detritus")
        + _array(context, "energy_reserve").sum(axis=0)
    )


def _check_ledger(proposal: StageResult) -> None:
    for name in ("carbon_ledger", "nutrient_ledger", "water_ledger"):
        ledger = cast(Mapping[str, JsonValue], proposal.metrics[name])
        assert cast(float, ledger["max_abs_residual"]) < 5e-10


@st.composite
def _worlds(draw: st.DrawFn) -> TurnContext:
    width = draw(st.sampled_from((2, 4)))
    height = draw(st.integers(1, 4))
    dt = draw(st.floats(0, 30, allow_nan=False, allow_infinity=False))
    context = _context(width=width, height=height, dt=dt)
    rng = np.random.default_rng(draw(st.integers(0, 2**32 - 1)))
    tiles = width * height
    for name in ("plant_biomass", "detritus", "nutrients", "soil_water", "npp"):
        context = _with_array(context, name, rng.uniform(0, 1000, tiles))
    context = _with_array(context, "population", rng.integers(0, 100, (3, tiles), dtype=np.int64))
    context = _with_array(context, "energy_reserve", rng.uniform(0, 500, (3, tiles)))
    context = _with_array(context, "temperature", rng.uniform(-50, 70, tiles))
    context = _with_array(context, "humidity", rng.uniform(0, 1, tiles))
    context = _with_array(context, "biome", rng.integers(0, 7, tiles, dtype=np.int64))
    return replace(context, turn_id=draw(st.integers(1, 1000)))


@given(_worlds())
@settings(max_examples=80, deadline=None)
def test_resource_flows_conserve_carbon_nutrients_and_water_per_tile(context: TurnContext) -> None:
    before = context.snapshot.snapshot_id
    phenotypes = digest(context.species_state)
    primary, production = _apply(context, PrimaryProductivityStage())
    regenerated, regeneration = _apply(primary, ResourceRegenerationStage())
    npp = _array(primary, "npp")
    np.testing.assert_allclose(_pool(primary) - _pool(context), npp, atol=5e-10)
    np.testing.assert_allclose(
        _array(primary, "plant_biomass"), _array(context, "plant_biomass") + npp
    )
    np.testing.assert_allclose(
        _array(context, "nutrients") - _array(primary, "nutrients"), 0.02 * npp, atol=5e-10
    )
    land = _array(context, "biome") >= 2
    np.testing.assert_allclose(
        _array(context, "soil_water") - _array(primary, "soil_water"),
        np.where(land, 0.15 * npp, 0),
        atol=5e-10,
    )
    dt = cast(float, context.environment_state["ecological_years_per_turn"])
    leaves = _array(primary, "plant_biomass")
    litter = np.minimum(leaves, dt * 0.3 * leaves)
    available = _array(primary, "detritus") + litter
    decomposer_mass = _array(primary, "population")[1] * 2.0
    rate = 0.1 + 0.4 * decomposer_mass / (decomposer_mass + 5.0)
    thermal = np.exp(-np.square((_array(primary, "temperature") - 22) / 18))
    processed = np.minimum(available, dt * available * rate * thermal * _array(primary, "humidity"))
    assimilated = np.where(decomposer_mass > 0, 0.6 * processed, 0.0)
    respired = processed - assimilated
    np.testing.assert_allclose(_pool(regenerated) - _pool(primary), -respired, atol=5e-10)
    np.testing.assert_allclose(
        _array(regenerated, "plant_biomass"), _array(primary, "plant_biomass") - litter
    )
    np.testing.assert_allclose(
        _array(regenerated, "detritus"), _array(primary, "detritus") + litter - processed
    )
    np.testing.assert_allclose(
        _array(regenerated, "energy_reserve").sum(axis=0)
        - _array(primary, "energy_reserve").sum(axis=0),
        assimilated,
        atol=5e-10,
    )
    np.testing.assert_allclose(
        _array(regenerated, "nutrients") - _array(primary, "nutrients"), 0.02 * respired, atol=5e-10
    )
    np.testing.assert_array_equal(_array(regenerated, "npp"), npp)
    for result in (production, regeneration):
        _check_ledger(result)
        assert result.metrics["model_version"] == MODEL_VERSION == "ecology-reference-v1"
        assert result.state_delta.state == ()
        assert "population" not in {patch.name for patch in result.state_delta.arrays}
    assert context.snapshot.snapshot_id == before
    assert digest(regenerated.species_state) == phenotypes
    assert regenerated.snapshot.arrays["population"] == context.snapshot.arrays["population"]
    for value in regenerated.snapshot.arrays.values():
        assert not value.numpy().flags.writeable


@given(_worlds())
@settings(max_examples=30, deadline=None)
def test_pipeline_declared_reads_hash_preconditions_and_replay(context: TurnContext) -> None:
    pipeline = DeterministicPipeline(
        [
            ResourceRegenerationStage(),
            _BiomeReady(),
            PrimaryProductivityStage(),
        ]
    )
    a = pipeline.execute(context)
    b = pipeline.execute(context)
    assert a.snapshot.state_hash == b.snapshot.state_hash
    assert [x.output_hash for x in a.stage_results] == [x.output_hash for x in b.stage_results]
    assert a.snapshot.state == context.snapshot.state
    previous = context.snapshot
    for stage, proposal in zip(pipeline.stages, a.stage_results, strict=True):
        assert set(stage.contract.writes) <= set(stage.contract.reads)
        for patch in proposal.state_delta.arrays:
            assert patch.expected_hash == previous.arrays[patch.name].content_hash
        previous = apply_delta(previous, proposal.state_delta, writes=stage.contract.writes)


@pytest.mark.parametrize("limitation", ["producer", "nutrients", "soil_water"])
def test_absent_producer_or_land_supply_prevents_production(limitation: str) -> None:
    context = _context()
    if limitation == "producer":
        counts = context.snapshot.arrays["population"].numpy().copy()
        counts[0] = 0
        context = _with_array(context, "population", counts)
    else:
        context = _with_array(context, limitation, np.zeros(2))
    after, proposal = _apply(context, PrimaryProductivityStage())
    np.testing.assert_array_equal(_array(after, "npp"), np.zeros(2))
    for name in ("plant_biomass", "nutrients", "soil_water"):
        assert after.snapshot.arrays[name] == context.snapshot.arrays[name]
    assert proposal.metrics["carbon_fixed"] == 0.0


def test_aquatic_production_uses_external_water_and_actual_nutrient_cap() -> None:
    context = _context(dt=1000)
    context = _with_array(context, "soil_water", np.zeros(2))
    context = _with_array(context, "biome", np.array([0, 1], dtype=np.int64))
    after, proposal = _apply(context, PrimaryProductivityStage())
    np.testing.assert_allclose(_array(after, "npp"), np.full(2, 500.0))
    np.testing.assert_array_equal(_array(after, "nutrients"), np.zeros(2))
    assert proposal.metrics["water_consumed_mm"] == 0.0
    _check_ledger(proposal)


def test_production_formula_and_water_supply_cap() -> None:
    context = _context(dt=1)
    after, _ = _apply(context, PrimaryProductivityStage())
    expected = 300 * (1 - np.exp(-1)) * 100 / 140 * 10 / 15
    np.testing.assert_allclose(_array(after, "npp"), expected)
    context = _context(dt=1e6)
    context = _with_array(context, "soil_water", np.full(2, 0.15))
    after, _ = _apply(context, PrimaryProductivityStage())
    np.testing.assert_allclose(_array(after, "npp"), np.ones(2))
    np.testing.assert_array_equal(_array(after, "soil_water"), np.zeros(2))


def test_seasonal_light_reverses_between_hemispheres_and_repeats_each_year() -> None:
    context = _context(height=2)
    summer, _ = _apply(replace(context, turn_id=4), PrimaryProductivityStage())
    winter, _ = _apply(replace(context, turn_id=10), PrimaryProductivityStage())
    next_year, _ = _apply(replace(context, turn_id=16), PrimaryProductivityStage())
    np.testing.assert_allclose(_array(summer, "npp"), _array(winter, "npp")[::-1])
    np.testing.assert_array_equal(_array(summer, "npp"), _array(next_year, "npp"))
    assert _array(summer, "npp")[0] > _array(winter, "npp")[0]


def test_decomposer_allocation_is_structural_mass_weighted_and_does_not_create_carbon() -> None:
    context = _context(dt=1)
    species: dict[str, JsonValue] = {
        "small": {"slot": 0, "role": "decomposer", "body_mass": 1.0},
        "large": {"slot": 1, "role": "decomposer", "body_mass": 3.0},
        "deer": {"slot": 2, "role": "herbivore", "body_mass": 10.0},
    }
    context = _with_state(context, "species", species)
    population = np.array([[2, 0], [2, 0], [2, 2]], dtype=np.int64)
    context = _with_array(context, "population", population)
    after, proposal = _apply(context, ResourceRegenerationStage())
    litter = 6.0
    processed = (40 + litter) * (0.1 + 0.4 * 8 / 13) * 0.8
    added = _array(after, "energy_reserve") - _array(context, "energy_reserve")
    np.testing.assert_allclose(added[:, 0], [processed * 0.6 / 4, processed * 0.6 * 3 / 4, 0])
    np.testing.assert_array_equal(added[:, 1], np.zeros(3))
    np.testing.assert_allclose(
        _array(after, "nutrients") - _array(context, "nutrients"),
        [processed * 0.4 * 0.02, (40 + litter) * 0.1 * 0.8 * 0.02],
    )
    assert np.all(_pool(after) <= _pool(context))
    _check_ledger(proposal)


@pytest.mark.parametrize("zero", ["dt", "humidity", "stocks"])
def test_regeneration_noop_edges_and_npp_is_not_reused(zero: str) -> None:
    context = _context(dt=0 if zero == "dt" else 1)
    if zero == "humidity":
        context = _with_array(context, "humidity", np.zeros(2))
    if zero == "stocks":
        for name in ("plant_biomass", "detritus"):
            context = _with_array(context, name, np.zeros(2))
    after, proposal = _apply(context, ResourceRegenerationStage())
    assert proposal.metrics["processed_carbon"] == 0
    assert after.snapshot.arrays["energy_reserve"] == context.snapshot.arrays["energy_reserve"]
    assert after.snapshot.arrays["nutrients"] == context.snapshot.arrays["nutrients"]
    alternative = _with_array(context, "npp", np.full(2, 1e8))
    assert ResourceRegenerationStage().execute(alternative) == proposal
    if zero != "humidity":
        assert after.snapshot == context.snapshot


@pytest.mark.parametrize("stage", [PrimaryProductivityStage(), ResourceRegenerationStage()])
@pytest.mark.parametrize("dt", [-1.0, True, None, "1"])
def test_invalid_time_step_fails_closed(stage: SimulationStage, dt: JsonValue) -> None:
    context = _with_state(_context(), "environment", {"ecological_years_per_turn": dt})
    before = context.snapshot.snapshot_id
    with pytest.raises((ValueError, TypeError)):
        stage.execute(context)
    assert context.snapshot.snapshot_id == before


@pytest.mark.parametrize("stage", [PrimaryProductivityStage(), ResourceRegenerationStage()])
@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("slot", 1),
        ("slot", -1),
        ("slot", True),
        ("role", "omniscient"),
        ("body_mass", 0),
        ("body_mass", -1),
        ("body_mass", True),
    ],
)
def test_invalid_phenotypes_are_rejected(
    stage: SimulationStage,
    field: str,
    invalid: JsonValue,
) -> None:
    context = _context()
    species: dict[str, JsonValue] = dict(context.species_state)
    species["leaf"] = {"slot": 0, "role": "producer", "body_mass": 5.0, field: invalid}
    context = _with_state(context, "species", species)
    with pytest.raises((ValueError, TypeError)):
        stage.execute(context)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("population", np.zeros((3, 2), dtype=np.float64)),
        ("population", np.full((3, 2), -1, dtype=np.int64)),
        ("population", np.zeros((2, 2), dtype=np.int64)),
        ("energy_reserve", np.zeros((2, 2))),
        ("energy_reserve", np.full((3, 2), -1.0)),
        ("temperature", np.zeros(2, dtype=np.float32)),
        ("temperature", np.zeros((1, 2))),
        ("humidity", np.full(2, 1.1)),
        ("humidity", np.full(2, -0.1)),
        ("soil_water", np.full(2, -1.0)),
        ("biome", np.zeros(2)),
        ("biome", np.full(2, 7, dtype=np.int64)),
        ("biome", np.full(2, -1, dtype=np.int64)),
    ],
)
def test_invalid_arrays_fail_without_mutating_input(name: str, value: NDArray[np.generic]) -> None:
    context = _with_array(_context(), name, value)
    before = context.snapshot.snapshot_id
    for stage in (PrimaryProductivityStage(), ResourceRegenerationStage()):
        with pytest.raises((ValueError, TypeError)):
            stage.execute(context)
    assert context.snapshot.snapshot_id == before


def test_empty_world_has_no_production_and_only_abiotic_decomposition() -> None:
    context = _with_state(_context(), "species", {})
    context = _with_array(context, "population", np.zeros((0, 2), dtype=np.int64))
    context = _with_array(context, "energy_reserve", np.zeros((0, 2)))
    after = DeterministicPipeline(
        [
            _BiomeReady(),
            PrimaryProductivityStage(),
            ResourceRegenerationStage(),
        ]
    ).execute(context)
    assert after.stage_results[-2].metrics["carbon_fixed"] == 0
    assert after.stage_results[-1].metrics["assimilated_carbon"] == 0
    assert cast(float, after.stage_results[-1].metrics["carbon_respired"]) > 0


def test_overflow_discards_candidate_and_preserves_input() -> None:
    context = _with_array(_context(dt=10), "detritus", np.full(2, 1e308))
    before = context.snapshot.snapshot_id
    pipeline = DeterministicPipeline(
        [
            _BiomeReady(),
            PrimaryProductivityStage(),
            ResourceRegenerationStage(),
        ]
    )
    with pytest.raises(StageExecutionError, match="overflow"):
        pipeline.execute(context)
    assert context.snapshot.snapshot_id == before


def test_preallocated_unused_slots_are_permitted_only_with_empty_stocks() -> None:
    context = _context()
    population = np.pad(context.snapshot.arrays["population"].numpy(), ((0, 3), (0, 0)))
    reserves = np.pad(context.snapshot.arrays["energy_reserve"].numpy(), ((0, 3), (0, 0)))
    context = _with_array(context, "population", population)
    context = _with_array(context, "energy_reserve", reserves)
    after = DeterministicPipeline(
        [
            _BiomeReady(),
            PrimaryProductivityStage(),
            ResourceRegenerationStage(),
        ]
    ).execute(context)
    np.testing.assert_array_equal(_array(after, "population")[3:], np.zeros((3, 2)))
    np.testing.assert_array_equal(_array(after, "energy_reserve")[3:], np.zeros((3, 2)))
    for name in ("population", "energy_reserve"):
        stock = context.snapshot.arrays[name].numpy().copy()
        stock[4, 1] = 1
        orphan = _with_array(context, name, stock)
        for stage in (PrimaryProductivityStage(), ResourceRegenerationStage()):
            with pytest.raises(ValueError, match="unused species rows"):
                stage.execute(orphan)


def test_resource_metrics_are_bounded_scalar_ledgers() -> None:
    def scalar_tree(value: JsonValue) -> bool:
        if isinstance(value, Mapping):
            return all(scalar_tree(item) for item in value.values())
        return not isinstance(value, tuple)

    context = _context(width=4, height=100)
    for stage in (PrimaryProductivityStage(), ResourceRegenerationStage()):
        metrics = stage.execute(context).metrics
        assert scalar_tree(metrics)
        assert len(str(dict(metrics))) < 2500

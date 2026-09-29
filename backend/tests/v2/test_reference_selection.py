"""Selection indicators and causal counterfactual fitness, independent of AI text."""

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
from app.simulation.v2.reference.ecology import Species
from app.simulation.v2.reference.fitness import (
    EPSILON,
    TraitFitnessGradientStage,
    finite_difference,
    fitness_proxy,
    prepare_fitness,
)
from app.simulation.v2.reference.selection import PRESSURE_AXES, SelectionPressureStage
from app.simulation.v2.reference.world import MORTALITY_CAUSES, TRAITS, SpeciesSeed
from app.simulation.v2.values import FrozenArray, JsonValue
from app.simulation.v2.version import WorldVersion


class _PopulationReady(SimulationStage):
    contract = StageContract("reference_population", "fixture")

    def execute(self, context: TurnContext) -> StageResult:
        return StageResult(self.contract.name)


def _context() -> TurnContext:
    seeds = (
        SpeciesSeed("prey", "herbivore", 3.0, 4, habitat="amphibious"),
        SpeciesSeed("predator", "carnivore", 3.0, 2, habitat="amphibious"),
        SpeciesSeed("plant", "producer", 1.0, 10, habitat="amphibious"),
    )
    arrays: dict[str, NDArray[np.generic]] = {
        "population": np.array([[4, 4], [2, 2], [10, 10], [0, 0]], dtype=np.int64),
        "mortality": np.zeros((8, 4, 2), dtype=np.int64),
        "migration_in": np.zeros((4, 2), dtype=np.int64),
        "migration_out": np.zeros((4, 2), dtype=np.int64),
        "selection_pressure": np.zeros((4, 7)),
        "fitness_gradients": np.zeros((4, 7)),
    }
    for name in (
        "food_pressure",
        "temperature_pressure",
        "water_pressure",
        "predation_pressure",
        "competition",
        "suitability",
    ):
        arrays[name] = np.zeros((4, 2))
    arrays["suitability"][:3] = 1
    return TurnContext(
        1,
        WorldSnapshot(
            WorldVersion("selection", "main"),
            0,
            state={
                "geometry": {"width": 2, "height": 1},
                "environment": {"ecological_years_per_turn": 1 / 12},
                "species": {
                    seed.species_id: seed.metadata(i, 2 * seed.population_per_tile)
                    for i, seed in enumerate(seeds)
                },
                "food_web": {
                    "edges": (
                        {"predator": "predator", "prey": "prey", "preference": 1.0},
                        {"predator": "prey", "prey": "plant", "preference": 1.0},
                    )
                },
            },
            arrays={name: FrozenArray.from_numpy(value) for name, value in arrays.items()},
        ),
        713,
    )


def _array(context: TurnContext, name: str) -> NDArray[np.float64]:
    return np.array(context.snapshot.arrays[name].numpy(), dtype=np.float64)


def _patch(context: TurnContext, **arrays: NDArray[np.generic]) -> TurnContext:
    return context.with_snapshot(
        replace(
            context.snapshot,
            arrays={
                **context.snapshot.arrays,
                **{name: FrozenArray.from_numpy(value) for name, value in arrays.items()},
            },
        )
    )


def _state(context: TurnContext, name: str, value: JsonValue) -> TurnContext:
    return context.with_snapshot(
        replace(context.snapshot, state={**context.snapshot.state, name: value})
    )


def _apply(context: TurnContext, stage: SimulationStage) -> tuple[TurnContext, StageResult]:
    proposal = stage.execute(context)
    return context.with_snapshot(
        apply_delta(context.snapshot, proposal.state_delta, writes=stage.contract.writes)
    ), proposal


def _trait(context: TurnContext, identity: str, trait: str, value: float) -> TurnContext:
    species: dict[str, JsonValue] = dict(context.species_state)
    metadata = dict(cast(Mapping[str, JsonValue], species[identity]))
    traits = dict(cast(Mapping[str, JsonValue], metadata["traits"]))
    traits[trait] = value
    metadata["traits"] = traits
    species[identity] = metadata
    return _state(context, "species", species)


def test_pressure_axes_exposure_and_unmet_migration_are_explicit() -> None:
    context = _context()
    population = np.array(context.population_state.numpy())
    population[0] = [3, 1]
    mortality = np.zeros((8, 4, 2), dtype=np.int64)
    mortality[MORTALITY_CAUSES.index("temperature"), 0] = [1, 0]
    mortality[MORTALITY_CAUSES.index("disease"), 0] = [0, 1]
    fields: dict[str, NDArray[np.generic]] = {"population": population, "mortality": mortality}
    pressure_fields: dict[str, list[float]] = {
        "temperature_pressure": [0.2, 0.8],
        "water_pressure": [0.6, 0],
        "food_pressure": [0.5, 1],
        "predation_pressure": [0, 0.5],
        "competition": [1, 3],
        "suitability": [0.75, 0.5],
    }
    for name, values in pressure_fields.items():
        field = _array(context, name)
        field[0] = values
        fields[name] = field
    incoming = np.zeros((4, 2), dtype=np.int64)
    outgoing = incoming.copy()
    outgoing[0, 0] = incoming[0, 1] = 1
    context = _patch(context, **fields, migration_in=incoming, migration_out=outgoing)
    after, _ = _apply(context, SelectionPressureStage())
    assert PRESSURE_AXES == (
        "temperature",
        "water",
        "food",
        "predation",
        "competition",
        "mobility",
        "disease",
    )
    np.testing.assert_allclose(
        _array(after, "selection_pressure")[0], [0.4, 0.4, 2 / 3, 1 / 6, 7 / 12, 0.5, 1 / 6]
    )


def test_fulfilled_migration_reduces_only_the_mobility_proxy() -> None:
    context = _context()
    food = _array(context, "food_pressure")
    food[0] = 0.5
    context = _patch(context, food_pressure=food)
    no_moves, _ = _apply(context, SelectionPressureStage())
    moves = np.zeros((4, 2), dtype=np.int64)
    moves[0] = 2
    moved, _ = _apply(
        _patch(context, migration_in=moves, migration_out=moves), SelectionPressureStage()
    )
    assert _array(no_moves, "selection_pressure")[0, 5] == 0.5
    assert _array(moved, "selection_pressure")[0, 5] == 0
    np.testing.assert_array_equal(
        _array(moved, "selection_pressure")[:, :5], _array(no_moves, "selection_pressure")[:, :5]
    )


def test_no_predation_means_no_armor_benefit_and_no_fictitious_mobility_benefit() -> None:
    context = _state(_context(), "food_web", {"edges": ()})
    pressure = _array(context, "selection_pressure")
    pressure[:3] = 1
    context = _patch(context, selection_pressure=pressure)
    after, _ = _apply(context, TraitFitnessGradientStage())
    np.testing.assert_allclose(_array(after, "fitness_gradients")[:3], -0.05, atol=2e-13)
    np.testing.assert_array_equal(_array(after, "fitness_gradients")[3], np.zeros(7))


def _response(predator: Species, prey: Species) -> float:
    """Independent hand calculation for two identical tiles with P=2, Q=4."""
    size = np.exp(-0.5 * ((np.log(prey.mass / predator.mass) - np.log(0.2)) / 2) ** 2)
    alpha = (
        0.5
        * size
        * (1 + predator.traits["attack"])
        / (1 + prey.traits["armor"])
        * (1 + predator.traits["speed"])
        / (1 + prey.traits["speed"])
        * (1 + predator.traits["detox"] - prey.traits["toxin"])
        * (1 + predator.traits["cooperation"] * np.log1p(2))
    )
    return float(alpha * 4 / (1 + (0.02 + 0.25 * prey.mass / predator.mass) * alpha * 4))


def test_armor_and_attack_gradients_follow_the_real_holling_parameters() -> None:
    data = prepare_fitness(_context())
    prey, predator, _ = data.selection.species
    pred_low = replace(predator, traits={**predator.traits, "attack": 0.09})
    pred_high = replace(predator, traits={**predator.traits, "attack": 0.11})
    prey_low = replace(prey, traits={**prey.traits, "armor": 0.09})
    prey_high = replace(prey, traits={**prey.traits, "armor": 0.11})
    expected_attack = 0.7 * (_response(pred_high, prey) - _response(pred_low, prey)) / 0.02 - 0.05
    expected_armor = (
        -0.5 * (_response(predator, prey_high) - _response(predator, prey_low)) / 0.02 - 0.05
    )
    np.testing.assert_allclose(
        finite_difference(data, predator)[TRAITS.index("attack")], expected_attack
    )
    np.testing.assert_allclose(finite_difference(data, prey)[TRAITS.index("armor")], expected_armor)
    assert expected_attack > 0 and expected_armor > 0
    assert finite_difference(data, predator)[TRAITS.index("engineering")] == pytest.approx(-0.05)


def test_countertraits_change_selection_and_missing_overlap_removes_it() -> None:
    ordinary = prepare_fitness(_context())
    toxic = prepare_fitness(_trait(_context(), "prey", "toxin", 0.8))
    ordinary_predator, toxic_predator = ordinary.selection.species[1], toxic.selection.species[1]
    assert (
        finite_difference(toxic, toxic_predator)[TRAITS.index("detox")]
        > (finite_difference(ordinary, ordinary_predator)[TRAITS.index("detox")])
    )
    separated = _array(_context(), "suitability")
    separated[1] = 0
    context = _patch(_context(), suitability=separated)
    after, metrics = _apply(context, TraitFitnessGradientStage())
    np.testing.assert_allclose(_array(after, "fitness_gradients")[:3], -0.05, atol=2e-13)
    assert metrics.metrics["active_diet_edges"] == 0


@pytest.mark.parametrize("value", [0.0, 0.1, 0.999, 1.0])
def test_finite_difference_uses_clipped_interval_and_pure_counterfactuals(value: float) -> None:
    context = _trait(_context(), "prey", "armor", value)
    old_hash = context.snapshot.state_hash
    data = prepare_fitness(context)
    prey = data.selection.species[0]
    low, high = max(0, value - EPSILON), min(1, value + EPSILON)
    expected = (
        fitness_proxy(data, replace(prey, traits={**prey.traits, "armor": high}))
        - fitness_proxy(data, replace(prey, traits={**prey.traits, "armor": low}))
    ) / (high - low)
    assert finite_difference(data, prey)[TRAITS.index("armor")] == pytest.approx(expected)
    assert context.snapshot.state_hash == old_hash
    assert prey.traits["armor"] == value


def test_current_zero_population_and_unused_rows_reset_to_zero_even_with_death_exposure() -> None:
    context = _context()
    population = np.array(context.population_state.numpy())
    population[0] = 0
    mortality = np.zeros((8, 4, 2), dtype=np.int64)
    mortality[0, 0] = 50
    pressure, gradient = np.ones((4, 7)), np.ones((4, 7))
    pressure[3] = gradient[3] = 0
    context = _patch(
        context,
        population=population,
        mortality=mortality,
        selection_pressure=pressure,
        fitness_gradients=gradient,
    )
    selected, _ = _apply(context, SelectionPressureStage())
    after, _ = _apply(selected, TraitFitnessGradientStage())
    for name in ("selection_pressure", "fitness_gradients"):
        np.testing.assert_array_equal(_array(after, name)[[0, 3]], np.zeros((2, 7)))


def test_pipeline_replay_declared_hashes_and_order_independence() -> None:
    context = _context()
    species = dict(reversed(list(context.species_state.items())))
    reordered = _state(context, "species", species)
    reordered = _state(
        reordered,
        "food_web",
        {"edges": tuple(reversed(cast(tuple[JsonValue, ...], context.food_web_state["edges"])))},
    )
    pipeline = DeterministicPipeline(
        [TraitFitnessGradientStage(), _PopulationReady(), SelectionPressureStage()]
    )
    original = context.snapshot.snapshot_id
    first, second = pipeline.execute(context), pipeline.execute(context)
    other = pipeline.execute(reordered)
    assert first.snapshot.state_hash == second.snapshot.state_hash
    for name in ("selection_pressure", "fitness_gradients"):
        assert first.snapshot.arrays[name] == other.snapshot.arrays[name]
    previous = context.snapshot
    for stage, proposal in zip(pipeline.stages, first.stage_results, strict=True):
        assert set(stage.contract.writes) <= set(stage.contract.reads)
        for change in proposal.state_delta.arrays:
            assert change.expected_hash == previous.arrays[change.name].content_hash
            assert change.name not in ("population", "energy_reserve")
        assert proposal.state_delta.state == ()
        assert all(not isinstance(value, (tuple, Mapping)) for value in proposal.metrics.values())
        previous = apply_delta(previous, proposal.state_delta, writes=stage.contract.writes)
    assert context.snapshot.snapshot_id == original
    assert first.species_state == context.species_state


@st.composite
def _worlds(draw: st.DrawFn) -> TurnContext:
    context = _context()
    rng = np.random.default_rng(draw(st.integers(0, 2**32 - 1)))
    population = rng.integers(0, 30, (4, 2), dtype=np.int64)
    mortality = rng.integers(0, 10, (8, 4, 2), dtype=np.int64)
    population[3] = mortality[:, 3] = 0
    fields: dict[str, NDArray[np.generic]] = {"population": population, "mortality": mortality}
    for name in (
        "temperature_pressure",
        "water_pressure",
        "food_pressure",
        "predation_pressure",
        "competition",
        "suitability",
    ):
        field = rng.uniform(0, 30 if name == "competition" else 1, (4, 2))
        field[3] = 0
        fields[name] = field
    context = _patch(context, **fields)
    for identity in ("prey", "predator", "plant"):
        for trait in TRAITS:
            context = _trait(context, identity, trait, draw(st.floats(0, 0.4, allow_nan=False)))
    return context


@given(_worlds())
@settings(max_examples=60, deadline=None)
def test_random_selection_and_counterfactuals_are_finite_and_bounded(context: TurnContext) -> None:
    before = context.snapshot.state_hash
    selected, _ = _apply(context, SelectionPressureStage())
    after, _ = _apply(selected, TraitFitnessGradientStage())
    pressure, gradients = _array(after, "selection_pressure"), _array(after, "fitness_gradients")
    assert np.isfinite(pressure).all() and np.isfinite(gradients).all()
    assert np.all((pressure >= 0) & (pressure <= 1))
    extinct = _array(context, "population").sum(axis=1) == 0
    assert np.all(pressure[extinct] == 0) and np.all(gradients[extinct] == 0)
    assert context.snapshot.state_hash == before


@pytest.mark.parametrize(
    "name",
    [
        "population",
        "mortality",
        "migration_in",
        "migration_out",
        "suitability",
        "competition",
        "food_pressure",
        "selection_pressure",
        "fitness_gradients",
    ],
)
def test_orphan_rows_fail_closed(name: str) -> None:
    context = _context()
    value = np.array(context.snapshot.arrays[name].numpy())
    if name == "mortality":
        value[0, 3, 0] = 1
    else:
        value[3, 0] = 1
    arrays = {name: value}
    if name in ("migration_in", "migration_out"):
        arrays.update(migration_in=value, migration_out=value)
    context = _patch(context, **arrays)
    for stage in (SelectionPressureStage(), TraitFitnessGradientStage()):
        with pytest.raises(ValueError, match="unused"):
            stage.execute(context)


@pytest.mark.parametrize("kind", ["unknown", "duplicate", "wrong_role", "negative"])
def test_invalid_food_web_fails_closed(kind: str) -> None:
    edge: dict[str, JsonValue] = {"predator": "predator", "prey": "prey", "preference": 1.0}
    if kind == "unknown":
        edge["prey"] = "missing"
    elif kind == "wrong_role":
        edge["prey"] = "plant"
    elif kind == "negative":
        edge["preference"] = -1
    context = _state(
        _context(), "food_web", {"edges": (edge, edge) if kind == "duplicate" else (edge,)}
    )
    for stage in (SelectionPressureStage(), TraitFitnessGradientStage()):
        with pytest.raises(ValueError):
            stage.execute(context)


def test_annual_fitness_does_not_mix_per_turn_pressure_units_into_score() -> None:
    context = _state(_context(), "food_web", {"edges": ()})
    pressures = _array(context, "selection_pressure")
    pressures[:3] = 1
    stressed = prepare_fitness(_patch(context, selection_pressure=pressures))
    ordinary = prepare_fitness(context)
    for species in ordinary.selection.species:
        assert fitness_proxy(ordinary, species) == pytest.approx(-0.25 - 0.05 * 0.7)
        assert fitness_proxy(stressed, species) == fitness_proxy(ordinary, species)


def test_large_trait_independent_hazard_does_not_erase_maintenance_gradient() -> None:
    context = _state(_context(), "environment", {"ecological_years_per_turn": 0.0})
    population = np.array(context.population_state.numpy())
    population[0] = 1
    population[1] = 10**18
    data = prepare_fitness(_patch(context, population=population))
    prey = data.selection.species[0]
    assert fitness_proxy(data, prey) < -(10**15)
    gradient = finite_difference(data, prey)
    # Attack, cooperation and engineering do not change this prey's incoming edge.
    np.testing.assert_array_equal(
        gradient[[TRAITS.index(name) for name in ("attack", "cooperation", "engineering")]],
        np.full(3, -0.05),
    )


def test_mass_normalization_avoids_overflow_in_finite_carbon_ratios() -> None:
    context = _context()
    species = dict(context.species_state)
    for identity in ("prey", "predator"):
        metadata = dict(cast(Mapping[str, JsonValue], species[identity]))
        metadata["body_mass"] = 1e300
        species[identity] = metadata
    context = _state(context, "species", species)
    population = np.array(context.population_state.numpy())
    population[:2] = 10**18
    data = prepare_fitness(_patch(context, population=population))
    predator = data.selection.species[1]
    assert np.isfinite(fitness_proxy(data, predator))
    assert np.isfinite(finite_difference(data, predator)).all()


@pytest.mark.parametrize("reason", ["preference", "tiles", "habitat"])
def test_inactive_sparse_edges_have_no_trophic_trait_benefit(reason: str) -> None:
    context = _context()
    if reason == "preference":
        context = _state(
            context,
            "food_web",
            {"edges": ({"predator": "predator", "prey": "prey", "preference": 0.0},)},
        )
    elif reason == "tiles":
        population = np.array(context.population_state.numpy())
        population[0], population[1] = [4, 0], [0, 2]
        context = _patch(context, population=population)
    else:
        species = dict(context.species_state)
        for identity, habitat in (("prey", "land"), ("predator", "water")):
            metadata = dict(cast(Mapping[str, JsonValue], species[identity]))
            metadata["habitat"] = habitat
            species[identity] = metadata
        context = _state(context, "species", species)
    after, proposal = _apply(context, TraitFitnessGradientStage())
    assert proposal.metrics["active_diet_edges"] == 0
    np.testing.assert_allclose(_array(after, "fitness_gradients")[:3], -0.05)


def test_prey_pool_cap_is_applied_to_each_counterfactual_before_differencing() -> None:
    context = _state(_context(), "environment", {"ecological_years_per_turn": 100.0})
    data = prepare_fitness(context)
    for species in data.selection.species:
        np.testing.assert_allclose(finite_difference(data, species), -0.05)
    prey, predator, _ = data.selection.species
    assert fitness_proxy(data, prey) == pytest.approx(-1 / 100 - 0.285)
    assert fitness_proxy(data, predator) == pytest.approx(0.7 * 2 / 100 - 0.285)


@pytest.mark.parametrize("trait_value", [-0.1, 1.1, float("nan"), float("inf")])
def test_counterfactual_helpers_reject_invalid_trait_domain(trait_value: float) -> None:
    data = prepare_fitness(_context())
    prey = data.selection.species[0]
    invalid = replace(prey, traits={**prey.traits, "armor": trait_value})
    for helper in (fitness_proxy, finite_difference):
        with pytest.raises(ValueError, match="traits must"):
            helper(data, invalid)


@pytest.mark.parametrize("name", ["selection_pressure", "fitness_gradients"])
@pytest.mark.parametrize("kind", ["dtype", "shape", "nan"])
def test_signal_array_contract_rejects_invalid_storage(name: str, kind: str) -> None:
    value: NDArray[np.generic] = _array(_context(), name)
    if kind == "dtype":
        value = value.astype(np.float32)
    elif kind == "shape":
        value = value[:, :6]
    else:
        value[0, 0] = np.nan
        # Immutable snapshot construction rejects nonfinite buffers even before
        # either stage receives its input.
        with pytest.raises(ValueError, match="NaN"):
            _patch(_context(), **{name: value})
        return
    context = _patch(_context(), **{name: value})
    for stage in (SelectionPressureStage(), TraitFitnessGradientStage()):
        with pytest.raises(ValueError, match=name):
            stage.execute(context)


def test_pipeline_stage_contracts_publish_only_the_owned_float64_signal() -> None:
    stages = (SelectionPressureStage(), TraitFitnessGradientStage())
    for stage, dependency, output in zip(
        stages,
        ("reference_population", "reference_selection"),
        ("selection_pressure", "fitness_gradients"),
        strict=True,
    ):
        assert stage.contract.dependencies == (dependency,)
        assert stage.contract.writes == (f"arrays.{output}",)
        proposal = stage.execute(_context())
        assert proposal.state_delta.state == ()
        assert proposal.events == ()
        assert proposal.ai_jobs == ()
        assert proposal.evolution_proposals == ()
        assert len(proposal.state_delta.arrays) == 1
        value = proposal.state_delta.arrays[0].value
        assert value is not None
        assert value.shape == (4, 7) and value.numpy().dtype == np.dtype("float64")

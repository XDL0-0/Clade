"""Deme evolution is bounded, causal, repeatable and separate from world accounting."""

from __future__ import annotations

import random
from collections.abc import Mapping
from dataclasses import FrozenInstanceError, replace
from typing import cast

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from numpy.typing import NDArray

from app.simulation.v2.context import TurnContext
from app.simulation.v2.contracts import SimulationStage, StageContract, StageResult
from app.simulation.v2.pipeline import DeterministicPipeline
from app.simulation.v2.reducer import apply_delta, restrict
from app.simulation.v2.reference.adaptation import AdaptationStage, adapt_deme
from app.simulation.v2.reference.evolution_contracts import EvolutionBudget, EvolutionTrace
from app.simulation.v2.reference.fitness import TraitFitnessGradientStage
from app.simulation.v2.reference.genetics import GeneFlowStage, GeneticDriftStage, MutationStage
from app.simulation.v2.reference.mortality import MortalityStage
from app.simulation.v2.reference.selection import SelectionPressureStage
from app.simulation.v2.reference.world import (
    MODEL_ID,
    TRAITS,
    SpeciesSeed,
    create_reference_snapshot,
)
from app.simulation.v2.values import FrozenArray, JsonValue, canonical_bytes
from app.simulation.v2.version import WorldVersion

STAGES = (MutationStage(), GeneticDriftStage(), GeneFlowStage(), AdaptationStage())


class FitnessReady(SimulationStage):
    contract = StageContract("reference_fitness", "explicit-fixture")

    def execute(self, context: TurnContext) -> StageResult:
        return StageResult(self.contract.name)


class PopulationReady(SimulationStage):
    contract = StageContract("reference_population", "explicit-fixture")

    def execute(self, context: TurnContext) -> StageResult:
        return StageResult(self.contract.name)


def fixture(size: int = 100, *, seed: int = 37) -> TurnContext:
    initial = create_reference_snapshot(
        WorldVersion("adaptation", "main"),
        seed=seed,
        manifest={"model": MODEL_ID},
        width=4,
        height=1,
        max_species=2,
        species=(
            SpeciesSeed(
                "animal.v1",
                "herbivore",
                2,
                size,
                habitat="amphibious",
                traits={name: 0.2 for name in TRAITS},
            ),
        ),
    )
    population = np.array(initial.arrays["population"].numpy())
    traits = np.zeros((2, 4, 7))
    traits[0] = 0.2
    labels = np.full((2, 4), -1, dtype=np.int64)
    labels[0] = 0
    arrays: dict[str, NDArray[np.generic]] = {
        "deme_traits": traits,
        "trait_proposals": traits.copy(),
        "gene_population": population.copy(),
        "gene_connectivity": labels.copy(),
        "connectivity": labels,
        "isolation_age": np.zeros((2, 4), dtype=np.int64),
        "selection_pressure": np.zeros((2, 7)),
        "fitness_gradients": np.zeros((2, 7)),
        "energy_reserve": np.stack((np.full(4, 10000.0), np.zeros(4))),
    }
    initial = replace(
        initial,
        state={
            **initial.state,
            "environment": {**initial.domain("environment"), "ecological_years_per_turn": 1.0},
        },
    )
    return patch(TurnContext(1, initial, seed), **arrays)


def patch(context: TurnContext, **arrays: NDArray[np.generic]) -> TurnContext:
    return context.with_snapshot(
        replace(
            context.snapshot,
            arrays={
                **context.snapshot.arrays,
                **{name: FrozenArray.from_numpy(value) for name, value in arrays.items()},
            },
        )
    )


def array(context: TurnContext, name: str) -> NDArray[np.generic]:
    return np.array(context.snapshot.arrays[name].numpy())


def single(context: TurnContext, stage: SimulationStage) -> tuple[TurnContext, StageResult]:
    view = restrict(context, stage.contract.reads)
    proposal = stage.execute(view)
    stage.validate_outputs(view, proposal)
    return context.with_snapshot(
        apply_delta(context.snapshot, proposal.state_delta, writes=stage.contract.writes)
    ), proposal


def pipeline() -> DeterministicPipeline:
    return DeterministicPipeline([*reversed(STAGES), FitnessReady()])


def test_budget_projection_and_maintenance_have_no_free_trait_capacity() -> None:
    budget = EvolutionBudget()
    values = budget.project(np.array([-1.0, 4, 2, 2, 2, 2, 2]))
    assert values.min() >= 0 and values.max() <= 1 and values.sum() <= 3
    assert budget.annual_maintenance(values, 2) == pytest.approx(0.3)
    np.testing.assert_array_equal(budget.project(values), values)
    assert EvolutionBudget(0).project(np.ones(7)).sum() == 0
    for invalid in (-1, 3.1, float("nan")):
        with pytest.raises(ValueError):
            EvolutionBudget(invalid)


def test_adaptation_pays_armor_speed_tradeoff_and_next_turn_actual_maintenance() -> None:
    before = fixture()
    gradients = np.zeros((2, 7))
    gradients[0, 0] = 1
    after, proposal = single(patch(before, fitness_gradients=gradients), AdaptationStage())
    updated_metadata = cast(Mapping[str, JsonValue], after.species_state["animal.v1"])
    traits = cast(Mapping[str, float], updated_metadata["traits"])
    assert traits["armor"] == pytest.approx(0.23)
    assert traits["speed"] == pytest.approx(0.1925)
    for name in ("population", "energy_reserve"):
        assert after.snapshot.arrays[name] == before.snapshot.arrays[name]
    metadata = dict(cast(Mapping[str, JsonValue], before.species_state["animal.v1"]))
    observed = dict(cast(Mapping[str, JsonValue], after.species_state["animal.v1"]))
    assert {k: v for k, v in observed.items() if k != "traits"} == {
        k: v for k, v in metadata.items() if k != "traits"
    }
    trace = cast(Mapping[str, JsonValue], proposal.evolution_proposals[0])
    assert cast(Mapping[str, float], trace["tradeoffs"])["armor_speed_payment"] == pytest.approx(
        0.0075
    )
    _, mortality = single(replace(after, turn_id=2), MortalityStage())
    expected = 400 * 2 * (0.25 + 0.05 * sum(traits.values()))
    assert mortality.metrics["carbon_respired"] == pytest.approx(expected)


def test_zero_speed_cannot_gain_unpaid_armor_and_trait_upper_bound_is_paid() -> None:
    for armor, speed in ((0.2, 0), (0.99, 0.001)):
        context = fixture()
        traits = array(context, "deme_traits")
        traits[0, :, 0], traits[0, :, 1] = armor, speed
        gradients = np.zeros((2, 7))
        gradients[0, 0] = 1
        context = patch(
            context, deme_traits=traits, trait_proposals=traits, fitness_gradients=gradients
        )
        after, _ = single(context, AdaptationStage())
        updated = array(after, "deme_traits")[0]
        assert np.all(updated[:, 0] - armor <= speed / 0.25 + 1e-12)
        assert np.all(updated[:, 1] >= 0)


def test_same_seed_global_rng_independence_and_dotted_species_metadata() -> None:
    context = fixture(3)
    original = context.snapshot.snapshot_id
    first = pipeline().execute(context)
    random.seed(191)
    np.random.seed(71)
    second = pipeline().execute(context)
    assert first.snapshot.state_hash == second.snapshot.state_hash
    assert (
        pipeline().execute(replace(context, seed=38)).snapshot.state_hash
        != first.snapshot.state_hash
    )
    assert first.evolution_proposals == second.evolution_proposals
    assert context.snapshot.snapshot_id == original
    np.testing.assert_array_equal(array(first, "gene_population"), array(first, "population"))
    assert set(first.species_state) == {"animal.v1"}
    assert first.stage_results[-1].state_delta.state[0].path == ("species",)
    for stage, proposal in zip(pipeline().stages[1:], first.stage_results[1:], strict=True):
        assert stage.contract.version == proposal.stage_version
        assert proposal.ai_jobs == ()
        assert set(stage.contract.writes) <= set(stage.contract.reads)
        assert all(not isinstance(value, (tuple, Mapping)) for value in proposal.metrics.values())


def test_fixed_seed_drift_variance_scales_inversely_with_population() -> None:
    variances = []
    for size in (4, 400):
        squares = []
        for seed in range(32):
            context = fixture(size, seed=seed)
            after, _ = single(context, GeneticDriftStage())
            squares.extend((array(after, "trait_proposals")[0] - 0.2).ravel() ** 2)
        variances.append(float(np.mean(squares)))
    assert variances[0] / variances[1] == pytest.approx(100, rel=1e-10)


def test_bottleneck_strict_quarter_threshold_doubles_sigma() -> None:
    context = fixture(20)
    outputs = []
    for previous in (80, 81):
        history = array(context, "gene_population")
        history[0] = previous
        _, proposal = single(patch(context, gene_population=history), GeneticDriftStage())
        outputs.append(proposal.metrics)
    assert outputs[0]["bottleneck_tiles"] == 0
    assert outputs[1]["bottleneck_tiles"] == 4
    assert outputs[1]["drift_sigma_max"] == 2 * cast(float, outputs[0]["drift_sigma_max"])


def test_founder_samples_parent_mean_and_absent_tiles_do_not_mutate() -> None:
    context = fixture(1)
    previous = array(context, "gene_population")
    previous[0, 0] = 0
    population = array(context, "population")
    population[0, 1] = 0
    deme = array(context, "deme_traits")
    deme[0, 0] = 0
    after, proposal = single(
        patch(context, gene_population=previous, population=population, deme_traits=deme),
        MutationStage(),
    )
    assert proposal.metrics["founder_tiles"] == 1
    assert np.all(np.abs(array(after, "trait_proposals")[0, 0] - 0.2) < 0.15)
    np.testing.assert_array_equal(array(after, "trait_proposals")[0, 1], deme[0, 1])


def test_mutations_include_beneficial_harmful_and_neutral_proxy_classes() -> None:
    counts = np.zeros(3)
    for seed in range(64):
        context = fixture(seed=seed)
        gradient = np.zeros((2, 7))
        gradient[0, 0] = 1
        _, proposal = single(patch(context, fitness_gradients=gradient), MutationStage())
        counts += [
            cast(float, proposal.metrics[f"mutation_{name}"])
            for name in ("beneficial", "harmful", "neutral")
        ]
    assert np.all(counts > 0)


def test_gene_flow_reduces_within_component_divergence_without_cross_island_mixing() -> None:
    context = fixture()
    proposals = array(context, "trait_proposals")
    proposals[0, :, 0] = [0.1, 0.7, 0.9, 0.9]
    labels = array(context, "connectivity")
    labels[0] = [0, 0, 2, 2]
    after, result = single(
        patch(context, trait_proposals=proposals, connectivity=labels), GeneFlowStage()
    )
    value = np.asarray(array(after, "trait_proposals")[0, :, 0], dtype=np.float64)
    np.testing.assert_allclose(value, [0.175, 0.625, 0.9, 0.9])
    assert value[1] - value[0] == pytest.approx(0.75 * 0.6)
    assert value[:2].mean() == pytest.approx(0.4)
    assert result.metrics["mixing_fraction"] == 0.25
    np.testing.assert_array_equal(array(after, "isolation_age")[0], np.ones(4))


def test_gene_flow_uses_population_weights_and_dt() -> None:
    context = fixture()
    population = array(context, "population")
    population[0] = [100, 300, 0, 0]
    proposals = array(context, "trait_proposals")
    proposals[0, :2, 0] = [0.1, 0.5]
    context = patch(context, population=population, trait_proposals=proposals)
    context = context.with_snapshot(
        replace(
            context.snapshot,
            state={**context.snapshot.state, "environment": {"ecological_years_per_turn": 0.5}},
        )
    )
    after, _ = single(context, GeneFlowStage())
    np.testing.assert_allclose(
        np.asarray(array(after, "trait_proposals")[0, :2, 0], dtype=np.float64),
        [0.1375, 0.4875],
    )
    np.testing.assert_array_equal(array(after, "gene_connectivity")[0], [0, 0, -1, -1])
    assert not array(after, "isolation_age").any()


def test_isolation_age_continues_resets_and_never_tracks_empty_tiles() -> None:
    context = replace(fixture(), turn_id=3)
    labels = array(context, "connectivity")
    labels[0] = [0, 0, 2, 2]
    previous_labels = labels.copy()
    previous_labels[0, 1] = -1
    ages = array(context, "isolation_age")
    ages[0] = [2, 0, 2, 2]
    population = array(context, "population")
    population[0, 2] = 0
    context = patch(
        context,
        connectivity=labels,
        gene_connectivity=previous_labels,
        isolation_age=ages,
        population=population,
    )
    after, _ = single(context, GeneFlowStage())
    np.testing.assert_array_equal(array(after, "isolation_age")[0], [3, 1, 0, 3])
    np.testing.assert_array_equal(array(after, "gene_connectivity")[0], [0, 0, -1, 2])
    joined = labels.copy()
    joined[0] = 0
    after, _ = single(patch(context, connectivity=joined), GeneFlowStage())
    assert not array(after, "isolation_age").any()


def test_extinct_population_has_no_adaptation_creation_or_ai() -> None:
    context = fixture()
    zeros = np.zeros((2, 4), dtype=np.int64)
    context = patch(context, population=zeros, energy_reserve=zeros.astype(np.float64))
    after = pipeline().execute(context)
    assert after.species_state == context.species_state
    assert after.evolution_proposals == ()
    assert all(result.events == () and result.ai_jobs == () for result in after.stage_results)
    np.testing.assert_array_equal(array(after, "deme_traits"), array(context, "deme_traits"))
    np.testing.assert_array_equal(array(after, "gene_population"), zeros)


def test_trace_is_frozen_quantitative_and_significance_gates_events() -> None:
    context = fixture(1)
    previous = array(context, "gene_population")
    previous[0] = [0, 100, 1, 1]
    context = patch(context, gene_population=previous)
    after = pipeline().execute(context)
    trace = cast(Mapping[str, JsonValue], after.evolution_proposals[0])
    assert trace["species"] == "animal.v1" and trace["turn"] == 1
    assert "proxy" in str(trace["fitness_gain_definition"])
    reasons = cast(Mapping[str, float], trace["reasons"])
    assert reasons["founder_tiles"] == 1 and reasons["bottleneck_tiles"] == 1
    assert (
        reasons["drift_max_abs"] > 0 and len(cast(Mapping[str, JsonValue], trace["pressure"])) == 7
    )
    assert after.stage_results[-1].events[0].type == "SpeciesAdapted"
    assert len(canonical_bytes(trace)) < 4096
    unchanged, proposal = single(fixture(), AdaptationStage())
    assert proposal.events == () and proposal.evolution_proposals == ()
    assert unchanged.species_state == fixture().species_state
    value = EvolutionTrace("a", 1, (0.0,) * 7, (0.0,) * 7, 0.0, (), (), 0.0)
    with pytest.raises(FrozenInstanceError):
        value.turn = 2  # type: ignore[misc]


@pytest.mark.parametrize(
    "name",
    [
        "deme_traits",
        "trait_proposals",
        "gene_population",
        "gene_connectivity",
        "isolation_age",
        "selection_pressure",
        "fitness_gradients",
    ],
)
@pytest.mark.parametrize("kind", ["shape", "dtype", "orphan"])
def test_invalid_buffers_fail_closed(name: str, kind: str) -> None:
    context = fixture()
    value = array(context, name)
    if kind == "shape":
        value = value[:1]
    elif kind == "dtype":
        value = value.astype(np.float32)
    else:
        value[1] = 0 if name == "gene_connectivity" else 1
    context = patch(context, **{name: value})
    for stage in STAGES:
        with pytest.raises(ValueError):
            single(context, stage)


@pytest.mark.parametrize("value", [-0.1, 1.1, 0.6])
def test_invalid_deme_coordinate_or_budget_rejects(value: float) -> None:
    context = fixture()
    deme = array(context, "deme_traits")
    deme[0] = value
    with pytest.raises(ValueError, match="budget|bounds"):
        MutationStage().execute(patch(context, deme_traits=deme))


def test_full_real_selection_fitness_evolution_dag_is_finite_and_replayable() -> None:
    context = fixture()
    chain = DeterministicPipeline(
        [PopulationReady(), SelectionPressureStage(), TraitFitnessGradientStage(), *STAGES]
    )
    first, second = chain.execute(context), chain.execute(context)
    assert first.snapshot.state_hash == second.snapshot.state_hash
    assert len(first.stage_results) == 7
    assert set(first.species_state) == set(context.species_state)


def test_directed_step_rejects_projection_that_decreases_linear_proxy() -> None:
    # Raising armor at this saturated budget would reduce valuable speed and attack.
    baseline = np.array([0.5, 1.0, 1.0, 0.5, 0.0, 0.0, 0.0])
    gradient = np.array([1.0, 2.0, 2.0, 0.0, 0.0, 0.0, 0.0])
    final, payment, gain = adapt_deme(baseline, 0.5, gradient, 1, EvolutionBudget())
    np.testing.assert_array_equal(final, baseline)
    assert payment == gain == 0


@pytest.mark.parametrize("dt", [0.0, -1.0, 1.1])
def test_evolution_time_step_is_explicitly_bounded(dt: float) -> None:
    context = fixture()
    context = context.with_snapshot(
        replace(
            context.snapshot,
            state={**context.snapshot.state, "environment": {"ecological_years_per_turn": dt}},
        )
    )
    with pytest.raises(ValueError, match="time step"):
        MutationStage().execute(context)


def test_impossible_isolation_history_and_counter_overflow_fail_closed() -> None:
    context = fixture()
    labels = array(context, "connectivity")
    labels[0] = [0, 0, 2, 2]
    ages = array(context, "isolation_age")
    ages[0] = 2
    with pytest.raises(ValueError, match="current turn"):
        GeneFlowStage().execute(patch(context, isolation_age=ages))
    maximum = np.iinfo(np.int64).max
    ages[0] = maximum
    context = patch(
        replace(context, turn_id=int(maximum)),
        isolation_age=ages,
        connectivity=labels,
        gene_connectivity=labels,
    )
    with pytest.raises(ValueError, match="int64"):
        GeneFlowStage().execute(context)


@given(
    seed=st.integers(0, 2**32 - 1),
    size=st.integers(1, 10000),
    traits=st.lists(st.floats(0, 1, allow_nan=False), min_size=7, max_size=7),
)
@settings(max_examples=40, deadline=None)
def test_random_deme_chain_retains_finite_budgets_and_population(
    seed: int, size: int, traits: list[float]
) -> None:
    context = fixture(size, seed=seed)
    vector = EvolutionBudget().project(np.array(traits))
    deme = array(context, "deme_traits")
    deme[0] = vector
    context = patch(context, deme_traits=deme, trait_proposals=deme)
    after = pipeline().execute(context)
    for name in ("deme_traits", "trait_proposals"):
        value = np.asarray(array(after, name), dtype=np.float64)
        assert np.isfinite(value).all() and np.all((value >= 0) & (value <= 1))
        assert np.all(value.sum(axis=-1) <= 3 + 1e-12)
        assert not value[1].any()
    assert after.snapshot.arrays["population"] == context.snapshot.arrays["population"]
    assert after.snapshot.arrays["energy_reserve"] == context.snapshot.arrays["energy_reserve"]

"""Feedback counterfactuals use actual feeding and mortality response equations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

import numpy as np
import pytest

from app.simulation.v2.context import TurnContext
from app.simulation.v2.reducer import apply_delta, restrict
from app.simulation.v2.reference.ecology import HabitatSuitabilityStage, _species
from app.simulation.v2.reference.feedback import (
    EnvironmentalFitnessStage,
    FeedbackFeedingStage,
    environmental_cost,
)
from app.simulation.v2.reference.feeding import _functional_response
from app.simulation.v2.reference.fitness import finite_difference, prepare_fitness
from app.simulation.v2.reference.model import evolution_pipeline
from app.simulation.v2.reference.physiology import thermal_response, water_response
from app.simulation.v2.reference.world import (
    MODEL_ID,
    TRAITS,
    SpeciesSeed,
    create_reference_snapshot,
)
from app.simulation.v2.values import FrozenArray, JsonValue
from app.simulation.v2.version import WorldVersion


def context(
    *, temperature: float = 18, soil: float = 10, refuge: float = 0, seed: int = 5
) -> TurnContext:
    pipeline = evolution_pipeline()
    snapshot = create_reference_snapshot(
        WorldVersion("feedback", "main"),
        seed=seed,
        width=2,
        height=1,
        max_species=4,
        species=(
            SpeciesSeed("grazer", "herbivore", 2, 20, habitat="amphibious"),
            SpeciesSeed("hunter", "carnivore", 10, 4, habitat="amphibious"),
        ),
        manifest={
            "model": MODEL_ID,
            "stages": {s.contract.name: s.contract.version for s in pipeline.stages},
        },
    )
    arrays = {**snapshot.arrays}
    for name, value in {
        "temperature": temperature,
        "soil_water": soil,
        "plant_biomass": 1000,
        "habitat_complexity": refuge,
    }.items():
        arrays[name] = FrozenArray.from_numpy(np.full(2, value, dtype=np.float64))
    arrays["biome"] = FrozenArray.from_numpy(np.full(2, 4, dtype=np.int64))
    suitable = np.zeros((4, 2), dtype=np.float64)
    suitable[:2] = 1
    arrays["suitability"] = FrozenArray.from_numpy(suitable)
    return TurnContext(1, replace(snapshot, arrays=arrays), seed)


def gradient(ctx: TurnContext) -> np.typing.NDArray[np.float64]:
    stage = EnvironmentalFitnessStage()
    outcome = stage.execute(restrict(ctx, stage.contract.reads))
    snapshot = apply_delta(ctx.snapshot, outcome.state_delta, writes=stage.contract.writes)
    return np.asarray(snapshot.arrays["fitness_gradients"].numpy(), dtype=np.float64)


def test_actual_group_defense_and_predator_cooperation_counter_each_other() -> None:
    prey, predator = _species(context(), 4)
    base = _functional_response(predator, prey, 4, 20, 1, 1, defensive_groups=True)
    grouped = replace(prey, traits={**prey.traits, "cooperation": 0.8})
    protected = _functional_response(predator, grouped, 4, 20, 1, 1, defensive_groups=True)
    cooperative = replace(predator, traits={**predator.traits, "cooperation": 0.8})
    response = _functional_response(cooperative, grouped, 4, 20, 1, 1, defensive_groups=True)
    assert protected < base and response > protected
    data = prepare_fitness(context(), refuge=np.zeros(2), defensive_groups=True)
    assert finite_difference(data, prey)[TRAITS.index("cooperation")] > 0
    assert finite_difference(data, predator)[TRAITS.index("cooperation")] > 0
    # Old recipes explicitly keep their former lack of prey group defense.
    assert _functional_response(predator, grouped, 4, 20, 1, 1) == _functional_response(
        predator, prey, 4, 20, 1, 1
    )


def test_thermal_selection_reflects_shared_mortality_physiology() -> None:
    comfortable, stressful = gradient(context()), gradient(context(temperature=40))
    assert np.all(stressful[:2, TRAITS.index("armor")] > comfortable[:2, TRAITS.index("armor")])
    for name in ("attack", "speed", "cooperation", "toxin", "detox"):
        np.testing.assert_array_equal(
            comfortable[:, TRAITS.index(name)], stressful[:, TRAITS.index(name)]
        )


def test_water_selection_and_energy_cost_are_opposing_real_effects() -> None:
    wet, dry = gradient(context(soil=100000)), gradient(context(soil=10))
    assert np.all(dry[:2, TRAITS.index("engineering")] > wet[:2, TRAITS.index("engineering")])
    ctx = context(soil=10)
    empty = replace(
        ctx.snapshot,
        arrays={**ctx.snapshot.arrays, "energy_reserve": FrozenArray.from_numpy(np.zeros((4, 2)))},
    )
    # No reserve means the construction step cannot spend work; its proxy cost vanishes.
    no_work = gradient(ctx.with_snapshot(empty))
    np.testing.assert_allclose(no_work[:2, -1] - dry[:2, -1], 0.02, atol=1e-12)


def test_physiological_helpers_equal_actual_suitability_stage() -> None:
    ctx = context(temperature=32, soil=7)
    stage = HabitatSuitabilityStage()
    result = stage.execute(ctx)
    after = apply_delta(ctx.snapshot, result.state_delta, writes=stage.contract.writes)
    for item in _species(ctx, 4):
        thermal = thermal_response(item, np.full(2, 32.0))
        water = water_response(item, np.full(2, 7.0), np.ones(2, dtype=np.bool_))
        np.testing.assert_array_equal(
            after.arrays["suitability"].numpy()[item.slot], thermal * water
        )
        score = environmental_cost(
            item,
            temperature=np.full(2, 32.0),
            soil=np.full(2, 7.0),
            biome=np.full(2, 4, dtype=np.int64),
            population=np.full(2, 20, dtype=np.int64),
            reserve=np.zeros(2),
            dt=1 / 12,
        )
        assert score == pytest.approx(float((2 * (1 - thermal) + 0.5 * (1 - water)).mean()))


def test_aquatic_structure_refuge_reduces_actual_seeded_kills_without_creating_energy() -> None:
    totals = [0, 0]
    for seed in range(16):
        for index, refuge in enumerate((0.0, 1.0)):
            ctx = context(refuge=refuge, seed=seed)
            ctx = ctx.with_snapshot(
                replace(
                    ctx.snapshot,
                    arrays={
                        **ctx.snapshot.arrays,
                        "biome": FrozenArray.from_numpy(np.zeros(2, dtype=np.int64)),
                    },
                )
            )
            stage = FeedbackFeedingStage()
            result = stage.execute(restrict(ctx, stage.contract.reads))
            assert not result.errors
            deaths = result.metrics["predation_deaths"]
            assert isinstance(deaths, (int, float))
            totals[index] += int(deaths)
            residual = result.metrics["carbon_max_abs_residual"]
            assert isinstance(residual, (int, float)) and abs(residual) < 1e-9
    assert totals[1] < totals[0]


@pytest.mark.parametrize("bad", [-0.1, 1.1, float("nan"), float("inf")])
def test_invalid_refuge_rejected(bad: float) -> None:
    with pytest.raises(ValueError):
        FeedbackFeedingStage().execute(context(refuge=bad))


def test_gradients_do_not_mutate_world_or_create_nan() -> None:
    ctx = context(temperature=-30, soil=0)
    before = ctx.snapshot.snapshot_id
    values = gradient(ctx)
    assert np.isfinite(values).all() and np.all(values[2:] == 0)
    assert before == ctx.snapshot.snapshot_id
    species: dict[str, JsonValue] = dict(ctx.species_state)
    assert all(isinstance(value, Mapping) for value in species.values())

"""Analytical diversity/biomass observations and compact history state."""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np
import pytest

from app.simulation.v2.context import TurnContext
from app.simulation.v2.reducer import apply_delta
from app.simulation.v2.reference.metrics import MetricsStage
from app.simulation.v2.reference.world import MODEL_ID, SpeciesSeed, create_reference_snapshot
from app.simulation.v2.values import FrozenArray, canonical_bytes
from app.simulation.v2.version import WorldVersion


def fixture() -> TurnContext:
    snapshot = create_reference_snapshot(
        WorldVersion("metrics", "main"),
        seed=21,
        manifest={"model": MODEL_ID},
        width=2,
        height=1,
        max_species=3,
        species=[
            SpeciesSeed("a", "producer", 1.0, 2, habitat="amphibious"),
            SpeciesSeed("b", "herbivore", 1.0, 6, habitat="amphibious"),
        ],
    )
    return TurnContext(1, snapshot, 21)


def test_analytical_diversity_biomass_and_trophic_structure() -> None:
    stage = MetricsStage()
    result = stage.execute(fixture())
    assert result.metrics["species_richness"] == 2
    assert result.metrics["total_population"] == 16
    assert result.metrics["shannon_diversity"] == pytest.approx(
        -0.25 * math.log(0.25) - 0.75 * math.log(0.75)
    )
    assert result.metrics["simpson_diversity"] == pytest.approx(0.375)
    assert result.metrics["total_biomass"] == pytest.approx(417.6)
    assert result.metrics["mean_trophic_level"] == pytest.approx(1.75)
    assert result.metrics["food_web_connectivity"] == 0.5
    assert result.metrics["ecosystem_stability"] == 1.0
    assert result.metrics["genetic_diversity"] is None
    assert len(canonical_bytes(result.metrics)) < 4096


def test_stability_extinction_and_resimulation_do_not_guess_narratives() -> None:
    before = fixture()
    stage = MetricsStage()
    result = stage.execute(before)
    snapshot = apply_delta(before.snapshot, result.state_delta, writes=stage.contract.writes)
    pop = np.array([[0, 0], [6, 6], [0, 0]], dtype=np.int64)
    snapshot = replace(
        snapshot, arrays={**snapshot.arrays, "population": FrozenArray.from_numpy(pop)}
    )
    next_context = TurnContext(2, snapshot, 21)
    second = stage.execute(next_context)
    assert second.metrics["extinctions"] == 1
    assert second.metrics["ecosystem_stability"] == pytest.approx(1 - 4 / 28)
    assert second.metrics["food_web_connectivity"] == 0
    assert second.events == ()
    assert stage.execute(next_context) == second


def test_empty_world_metrics_are_finite_and_explicit() -> None:
    context = fixture()
    snapshot = replace(
        context.snapshot,
        arrays={
            **context.snapshot.arrays,
            "population": FrozenArray.from_numpy(np.zeros((3, 2), dtype=np.int64)),
        },
    )
    result = MetricsStage().execute(context.with_snapshot(snapshot))
    assert result.metrics["species_richness"] == 0
    assert result.metrics["shannon_diversity"] == 0
    assert result.metrics["simpson_diversity"] == 0
    assert result.metrics["mean_trophic_level"] == 0
    assert result.metrics["migration_rate"] == 0
    canonical_bytes(result.metrics)


def test_first_turn_extinctions_use_genesis_baseline() -> None:
    context = fixture()
    pop = np.asarray(context.population_state.numpy(), dtype=np.int64).copy()
    pop[0] = 0
    snapshot = replace(
        context.snapshot,
        arrays={
            **context.snapshot.arrays,
            "population": FrozenArray.from_numpy(pop),
        },
    )
    assert MetricsStage().execute(context.with_snapshot(snapshot)).metrics["extinctions"] == 1


def test_empty_known_previous_world_has_full_stability() -> None:
    context = fixture()
    snapshot = replace(
        context.snapshot,
        state={
            **context.snapshot.state,
            "observability": {"abundance": {}, "turn": 0},
        },
        arrays={
            **context.snapshot.arrays,
            "population": FrozenArray.from_numpy(np.zeros((3, 2), dtype=np.int64)),
        },
    )
    assert (
        MetricsStage().execute(context.with_snapshot(snapshot)).metrics["ecosystem_stability"] == 1
    )


def test_stale_observation_baseline_is_rejected() -> None:
    context = fixture()
    with pytest.raises(ValueError, match="immediately previous"):
        MetricsStage().execute(replace(context, turn_id=10))


@pytest.mark.parametrize("name", ["births", "deaths", "migration_in"])
def test_observation_ledger_cannot_contain_extra_species(name: str) -> None:
    context = fixture()
    snapshot = replace(
        context.snapshot,
        arrays={
            **context.snapshot.arrays,
            name: FrozenArray.from_numpy(np.zeros((4, 2), dtype=np.int64)),
        },
    )
    with pytest.raises(ValueError, match="assigned species rows"):
        MetricsStage().execute(context.with_snapshot(snapshot))

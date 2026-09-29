"""Population accounting must preserve individuals, carbon and nutrient bindings."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from numpy.typing import NDArray

from app.simulation.v2.context import TurnContext
from app.simulation.v2.contracts import SimulationStage, StageContract, StageResult
from app.simulation.v2.pipeline import DeterministicPipeline, StageExecutionError
from app.simulation.v2.reference.demography import PopulationUpdateStage
from app.simulation.v2.reference.mortality import MortalityStage
from app.simulation.v2.reference.reproduction import ReproductionStage
from app.simulation.v2.reference.world import MODEL_ID, SpeciesSeed, create_reference_snapshot
from app.simulation.v2.values import FrozenArray
from app.simulation.v2.version import WorldVersion


class Connected(SimulationStage):
    contract = StageContract("reference_connectivity", "fixture")

    def execute(self, context: TurnContext) -> StageResult:
        return StageResult(self.contract.name)


def context(population: int = 100, reserve: float = 10.0, mass: float = 1.0) -> TurnContext:
    snapshot = create_reference_snapshot(
        WorldVersion("demography", "main"),
        seed=83,
        manifest={"model": MODEL_ID},
        width=2,
        height=1,
        max_species=2,
        species=[SpeciesSeed("a", "producer", mass, population, habitat="amphibious")],
    )
    values = {name: np.array(array.numpy()) for name, array in snapshot.arrays.items()}
    values["energy_reserve"][0] = reserve
    values["suitability"][0] = 1
    values["carrying_capacity"][0] = 10000
    snapshot = replace(
        snapshot, arrays={key: FrozenArray.from_numpy(value) for key, value in values.items()}
    )
    return TurnContext(1, snapshot, 83)


def patch(value: TurnContext, **arrays: NDArray[np.generic]) -> TurnContext:
    return value.with_snapshot(
        replace(
            value.snapshot,
            arrays={
                **value.snapshot.arrays,
                **{key: FrozenArray.from_numpy(array) for key, array in arrays.items()},
            },
        )
    )


def run(value: TurnContext) -> TurnContext:
    return DeterministicPipeline(
        [
            Connected(),
            MortalityStage(),
            ReproductionStage(),
            PopulationUpdateStage(),
        ]
    ).execute(value)


def carbon(value: TurnContext, *, pending_predation: bool = False) -> float:
    result = float(value.snapshot.arrays["energy_reserve"].numpy().sum()) + float(
        value.snapshot.arrays["detritus"].numpy().sum()
    )
    pop = np.asarray(value.population_state.numpy(), dtype=np.int64)
    if pending_predation:
        pop = pop - np.asarray(value.snapshot.arrays["predation_deaths"].numpy(), dtype=np.int64)
    for metadata in value.species_state.values():
        assert isinstance(metadata, Mapping)
        slot, mass = metadata["slot"], metadata["body_mass"]
        assert isinstance(slot, int) and isinstance(mass, (int, float))
        result += float(pop[slot].sum()) * mass
    return result


def test_exclusive_deaths_and_population_ledger() -> None:
    before = context()
    pressures = np.array([[0.4, 0.8], [0, 0]])
    before = patch(before, temperature_pressure=pressures, water_pressure=pressures)
    original = before.snapshot.state_hash
    after = run(before)
    a = after.snapshot.arrays
    pop = np.asarray(before.population_state.numpy(), dtype=np.int64)
    deaths = np.asarray(a["deaths"].numpy(), dtype=np.int64)
    births = np.asarray(a["births"].numpy(), dtype=np.int64)
    np.testing.assert_array_equal(a["population"].numpy(), pop + births - deaths)
    np.testing.assert_array_equal(a["mortality"].numpy().sum(axis=0), deaths)
    assert np.all(deaths <= pop)
    assert before.snapshot.state_hash == original
    respired = after.stage_results[1].metrics["carbon_respired"]
    assert isinstance(respired, float)
    assert carbon(after) == pytest.approx(carbon(before) - respired)
    nutrient_delta = float(a["nutrients"].numpy().sum()) - float(
        before.snapshot.arrays["nutrients"].numpy().sum()
    )
    assert nutrient_delta == pytest.approx(respired * 0.02)


def test_predation_not_double_counted_and_migration_is_accounted() -> None:
    before = patch(
        context(),
        predation_deaths=np.array([[10, 5], [0, 0]], dtype=np.int64),
        migration_in=np.array([[0, 15], [0, 0]], dtype=np.int64),
        migration_out=np.array([[15, 0], [0, 0]], dtype=np.int64),
    )
    after = run(before)
    assert int(after.snapshot.arrays["mortality"].numpy()[2].sum()) == 15
    respired = after.stage_results[1].metrics["carbon_respired"]
    assert isinstance(respired, float)
    assert carbon(after) == pytest.approx(carbon(before, pending_predation=True) - respired)


@pytest.mark.parametrize("reserve", [0.0, 10.0, 100000.0])
def test_extinct_or_zero_population_never_reproduces(reserve: float) -> None:
    after = run(context(0, reserve))
    assert np.all(after.snapshot.arrays["births"].numpy() == 0)
    assert np.all(after.population_state.numpy() == 0)
    assert np.all(after.snapshot.arrays["energy_reserve"].numpy() == 0)


def test_no_reserve_means_no_births_and_starvation_has_an_explicit_cause() -> None:
    after = run(context(100, 0.0))
    assert np.all(after.snapshot.arrays["births"].numpy() == 0)
    assert int(after.snapshot.arrays["mortality"].numpy()[1].sum()) > 0


def test_overcrowding_stops_reproduction_without_creating_deaths() -> None:
    before = patch(context(100, 1000), carrying_capacity=np.array([[1.0, 1.0], [0, 0]]))
    after = run(before)
    assert np.all(after.snapshot.arrays["births"].numpy() == 0)


def test_temperature_pressure_increases_thermal_mortality() -> None:
    cold = patch(context(), temperature_pressure=np.array([[1.0, 1.0], [0, 0]]))
    warm = run(context())
    assert run(cold).snapshot.arrays["mortality"].numpy()[0].sum() > (
        warm.snapshot.arrays["mortality"].numpy()[0].sum()
    )


@settings(max_examples=80, deadline=None)
@given(
    population=st.integers(0, 500),
    reserve=st.floats(0, 2000, allow_nan=False),
    mass=st.floats(0.1, 10, allow_nan=False),
)
def test_mass_and_population_properties(population: int, reserve: float, mass: float) -> None:
    before = context(population, reserve, mass)
    after = run(before)
    assert after.snapshot.state_hash == run(before).snapshot.state_hash
    respired = after.stage_results[1].metrics["carbon_respired"]
    assert isinstance(respired, float)
    assert carbon(after) == pytest.approx(carbon(before) - respired, abs=1e-8)
    for name in ("population", "energy_reserve", "births", "deaths", "mortality"):
        assert np.all(np.asarray(after.snapshot.arrays[name].numpy(), dtype=np.float64) >= 0)


@pytest.mark.parametrize(
    "name, value",
    [
        ("migration_in", np.array([[1, 0], [0, 0]], dtype=np.int64)),
        ("predation_deaths", np.array([[101, 0], [0, 0]], dtype=np.int64)),
        ("temperature_pressure", np.array([[1.1, 0], [0, 0]])),
        ("energy_reserve", np.array([[1, 1], [0, 1]], dtype=np.float64)),
    ],
)
def test_invalid_ledgers_fail_closed(name: str, value: NDArray[np.generic]) -> None:
    before = patch(context(), **{name: value})
    original = before.snapshot.state_hash
    with pytest.raises(StageExecutionError):
        run(before)
    assert before.snapshot.state_hash == original


def test_population_update_rejects_overflow_and_inconsistent_causes() -> None:
    before = context()
    a = np.array([[np.iinfo(np.int64).max, 1], [0, 0]], dtype=np.int64)
    before = patch(before, population=a, births=np.ones_like(a))
    with pytest.raises(ValueError, match="overflows"):
        PopulationUpdateStage().execute(before)
    before = patch(context(), deaths=np.ones((2, 2), dtype=np.int64))
    with pytest.raises(ValueError, match="cause breakdown"):
        PopulationUpdateStage().execute(before)


def test_tiny_capacity_means_zero_births_without_unneeded_division() -> None:
    before = patch(context(100, 1000), carrying_capacity=np.full((2, 2), 1e-310))
    after = run(before)
    assert np.all(after.snapshot.arrays["births"].numpy() == 0)


def test_tiny_mass_with_large_energy_uses_finite_population_budget() -> None:
    before = context(100, 1.0, 1e-310)
    after = run(before)
    assert np.all(np.isfinite(after.population_state.numpy()))


@pytest.mark.parametrize("name", ["energy_reserve", "detritus", "nutrients"])
def test_lost_small_carbon_or_nutrient_flux_is_rejected(name: str) -> None:
    before = context(100, 1e18 if name == "energy_reserve" else 10.0)
    if name != "energy_reserve":
        before = patch(before, **{name: np.full(2, 1e18)})
    with pytest.raises(StageExecutionError, match="ledger balance lost"):
        run(before)


def test_mortality_counts_cannot_wrap_int64() -> None:
    maximum = np.iinfo(np.int64).max
    zero = np.zeros((2, 2), dtype=np.int64)
    pop = zero.copy()
    pop[0] = maximum
    kills, incoming, outgoing = zero.copy(), zero.copy(), zero.copy()
    kills[0, 0] = incoming[0, 0] = outgoing[0, 1] = maximum
    before = patch(
        context(),
        population=pop,
        predation_deaths=kills,
        migration_in=incoming,
        migration_out=outgoing,
    )
    with pytest.raises(ValueError, match="Mortality count exceeds int64"):
        MortalityStage().execute(before)

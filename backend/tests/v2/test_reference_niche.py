"""Finite-stock engineering, canopy microclimate and next-turn hydrology facts."""

from __future__ import annotations

import ctypes
import math
import subprocess
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
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
from app.simulation.v2.reducer import apply_delta, restrict
from app.simulation.v2.reference.common import FloatArray
from app.simulation.v2.reference.environment import ClimateStage
from app.simulation.v2.reference.hydrology import HydrologyStage
from app.simulation.v2.reference.niche import MicroclimateStage, NicheConstructionStage
from app.simulation.v2.reference.world import TRAITS, SpeciesSeed
from app.simulation.v2.values import FrozenArray, JsonValue, digest, thaw
from app.simulation.v2.version import WorldVersion


def _context(*, width: int = 4, height: int = 2, dt: float = 0.5) -> TurnContext:
    tiles = width * height
    water = np.arange(tiles) % 2 == 0
    population = np.zeros((3, tiles), dtype=np.int64)
    population[0], population[1] = 10, 5
    reserve = np.zeros((3, tiles), dtype=np.float64)
    reserve[0], reserve[1] = 3, 0.5
    fields: dict[str, NDArray[np.generic]] = {
        "population": population,
        "energy_reserve": reserve,
        "gene_population": population,
        "deme_traits": np.zeros((3, tiles, 7)),
        "biome": np.where(water, 0, 4).astype(np.int64),
        "elevation": np.where(water, -10.0, 100.0),
        "soil_quality": np.full(tiles, 0.25),
        "habitat_complexity": np.where(water, 0.2, 0.0),
        "temperature": np.full(tiles, 18.0),
        "plant_biomass": np.full(tiles, 100.0),
        "nutrients": np.full(tiles, 20.0),
        "soil_water": np.full(tiles, 100.0),
        "surface_water": np.zeros(tiles),
        "rainfall": np.zeros(tiles),
        "humidity": np.full(tiles, 0.5),
        "river_flux": np.zeros(tiles),
    }
    species: dict[str, JsonValue] = {
        "engineer.one": SpeciesSeed(
            "engineer.one", "producer", 10, 10, habitat="amphibious", traits={"engineering": 1}
        ).metadata(0, tiles * 10),
        "engineer.two": SpeciesSeed(
            "engineer.two", "carnivore", 2, 5, habitat="amphibious", traits={"engineering": 0.4}
        ).metadata(1, tiles * 5),
    }
    return TurnContext(
        turn_id=1,
        seed=19,
        command={"command_id": "engineering-example"},
        snapshot=WorldSnapshot(
            WorldVersion("niche", "main", 0, 0),
            0,
            state={
                "geometry": {"width": width, "height": height},
                "species": species,
                "environment": {
                    "ecological_years_per_turn": dt,
                    "sea_level": 0.0,
                    "baseline_temperature": 15.0,
                    "global_temperature": 15.0,
                    "co2_ppm": 280.0,
                    "warming_offset": 0.0,
                },
            },
            arrays={key: FrozenArray.from_numpy(value) for key, value in fields.items()},
        ),
    )


def _array(context: TurnContext, name: str) -> FloatArray:
    return np.array(context.snapshot.arrays[name].numpy(), dtype=np.float64)


def _with_array(context: TurnContext, name: str, value: NDArray[np.generic]) -> TurnContext:
    return replace(
        context,
        snapshot=replace(
            context.snapshot,
            arrays={**context.snapshot.arrays, name: FrozenArray.from_numpy(value)},
        ),
    )


def _metadata(context: TurnContext, key: str, value: JsonValue) -> TurnContext:
    species = cast(dict[str, JsonValue], thaw(context.species_state))
    first = cast(dict[str, JsonValue], species["engineer.one"])
    first[key] = value
    return replace(
        context,
        snapshot=replace(context.snapshot, state={**context.snapshot.state, "species": species}),
    )


def _apply(context: TurnContext, stage: SimulationStage) -> tuple[TurnContext, StageResult]:
    view = restrict(context, stage.contract.reads)
    proposal = stage.execute(view)
    stage.validate_outputs(view, proposal)
    updated = apply_delta(context.snapshot, proposal.state_delta, writes=stage.contract.writes)
    return replace(context, snapshot=updated), proposal


class _SpeciationReady(SimulationStage):
    contract = StageContract("reference_speciation", "test-only")

    def execute(self, context: TurnContext) -> StageResult:
        return StageResult(self.contract.name)


def test_land_water_work_equations_and_exact_stock_transfer() -> None:
    before = _context(dt=1)
    after, result = _apply(before, NicheConstructionStage())
    actual = _array(before, "energy_reserve") - _array(after, "energy_reserve")
    np.testing.assert_allclose(actual[:2], np.array([[2.0] * 8, [0.08] * 8]))
    work = actual.sum(axis=0)
    np.testing.assert_allclose(_array(after, "nutrients") - 20, 0.02 * work, atol=2e-15)
    land = _array(before, "biome") >= 2
    expected_soil = 1 - (1 - 0.25 * math.exp(-0.005)) * np.exp(-work / 100)
    expected_water = 1 - (1 - 0.2 * math.exp(-0.05)) * np.exp(-work / 100)
    np.testing.assert_allclose(_array(after, "soil_quality")[land], expected_soil[land])
    np.testing.assert_array_equal(_array(after, "soil_quality")[~land], 0.25)
    np.testing.assert_allclose(_array(after, "habitat_complexity")[~land], expected_water[~land])
    np.testing.assert_array_equal(_array(after, "habitat_complexity")[land], 0)
    assert result.metrics["carbon_respired"] == pytest.approx(16.64)
    assert result.metrics["work_by_land"] == result.metrics["work_by_water"]
    assert result.metrics["nutrient_recycled"] == pytest.approx(0.02 * 16.64)
    assert set(p.name for p in result.state_delta.arrays) == {
        "energy_reserve",
        "nutrients",
        "soil_quality",
        "habitat_complexity",
    }


def test_cost_scales_with_dt_and_is_capped_by_owned_reserve() -> None:
    full, _ = _apply(_context(dt=1), NicheConstructionStage())
    half, _ = _apply(_context(dt=0.5), NicheConstructionStage())
    reserve = _array(_context(), "energy_reserve")
    np.testing.assert_allclose(
        reserve - _array(half, "energy_reserve"), (reserve - _array(full, "energy_reserve")) / 2
    )
    initial = _with_array(_context(dt=1), "energy_reserve", reserve * 0.01)
    spent, _ = _apply(initial, NicheConstructionStage())
    np.testing.assert_array_equal(_array(spent, "energy_reserve"), 0)


@pytest.mark.parametrize("zero", ["reserve", "engineering", "population"])
def test_no_free_engineering_only_passive_decay(zero: str) -> None:
    before = _context(dt=1)
    if zero == "engineering":
        species = cast(dict[str, JsonValue], thaw(before.species_state))
        for metadata in species.values():
            cast(dict[str, JsonValue], metadata)["traits"] = dict.fromkeys(TRAITS, 0.0)
        before = replace(
            before,
            snapshot=replace(before.snapshot, state={**before.snapshot.state, "species": species}),
        )
    else:
        before = _with_array(before, "energy_reserve", np.zeros((3, 8)))
        if zero == "population":
            before = _with_array(before, "population", np.zeros((3, 8), dtype=np.int64))
    after, result = _apply(before, NicheConstructionStage())
    assert result.metrics["carbon_respired"] == result.metrics["nutrient_recycled"] == 0
    np.testing.assert_array_equal(_array(after, "nutrients"), _array(before, "nutrients"))
    land = _array(before, "biome") >= 2
    np.testing.assert_allclose(_array(after, "soil_quality")[land], 0.25 * math.exp(-0.005))
    np.testing.assert_allclose(_array(after, "habitat_complexity")[~land], 0.2 * math.exp(-0.05))


def test_event_is_significant_aggregate_and_stably_bounded_to_eight_tiles() -> None:
    before = _context(width=12, height=1, dt=1)
    after, result = _apply(before, NicheConstructionStage())
    assert len(result.events) == 1
    event = result.events[0]
    assert event.type == "NicheConstructed" and event.cause == ("engineering-example",)
    tiles = cast(tuple[Mapping[str, JsonValue], ...], event.payload["top_tiles"])
    assert len(tiles) == 8
    assert [tile["tile"] for tile in tiles][:6] == [1, 3, 5, 7, 9, 11]
    assert event.payload["carbon_respired"] == result.metrics["carbon_respired"]
    assert result.state_delta.state == () and result.ai_jobs == ()
    _, quiet = _apply(_context(dt=0.01), NicheConstructionStage())
    assert quiet.events == ()
    assert after.snapshot.state == before.snapshot.state


def test_replay_immutable_reserved_slots_and_unmodified_genes() -> None:
    context = _context()
    state_hash, species = context.snapshot.state_hash, digest(context.species_state)
    pipeline = DeterministicPipeline([NicheConstructionStage(), _SpeciationReady()])
    first, second = pipeline.execute(context), pipeline.execute(context)
    assert first.snapshot.state_hash == second.snapshot.state_hash
    assert [r.events for r in first.stage_results] == [r.events for r in second.stage_results]
    assert context.snapshot.state_hash == state_hash
    assert digest(first.species_state) == species
    for name in ("population", "gene_population", "deme_traits"):
        assert first.snapshot.arrays[name] == context.snapshot.arrays[name]
    np.testing.assert_array_equal(_array(first, "energy_reserve")[2], 0)
    for array in first.snapshot.arrays.values():
        assert not array.numpy().flags.writeable
    assert NicheConstructionStage.contract.dependencies == ("reference_speciation",)
    assert NicheConstructionStage.contract.version == MicroclimateStage.contract.version == "1"


@given(
    seed=st.integers(0, 2**32 - 1),
    width=st.sampled_from((2, 4)),
    height=st.integers(1, 3),
    dt=st.floats(0.2, 1, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=80, deadline=None)
def test_small_graph_carbon_nutrients_conserved_per_tile(
    seed: int, width: int, height: int, dt: float
) -> None:
    before = _context(width=width, height=height, dt=dt)
    rng = np.random.default_rng(seed)
    tiles = width * height
    pop = rng.integers(0, 100, (3, tiles), dtype=np.int64)
    pop[2] = 0
    reserve = rng.integers(0, 100, (3, tiles)).astype(np.float64)
    reserve[pop == 0] = 0
    before = _with_array(before, "population", pop)
    before = _with_array(before, "energy_reserve", reserve)
    before = _with_array(before, "nutrients", rng.uniform(0, 20, tiles))
    before = _with_array(before, "soil_quality", rng.uniform(0, 1, tiles))
    before = _with_array(before, "habitat_complexity", rng.uniform(0, 1, tiles))
    after, result = _apply(before, NicheConstructionStage())
    spent = (reserve - _array(after, "energy_reserve")).sum(axis=0)
    nutrient_delta = _array(after, "nutrients") - _array(before, "nutrients")
    np.testing.assert_allclose(nutrient_delta, 0.02 * spent, rtol=1e-9, atol=0)
    assert float(spent.sum()) == pytest.approx(result.metrics["carbon_respired"])
    # Body carbon is unchanged, so this also tests total organic C and bound+mineral N.
    mass = np.array([10.0, 2.0, 0.0])[:, None]
    c_before = (mass * pop + reserve).sum(axis=0)
    c_after = (mass * pop + _array(after, "energy_reserve")).sum(axis=0)
    np.testing.assert_allclose(c_after - c_before, -spent, atol=1e-11)
    np.testing.assert_allclose(nutrient_delta + 0.02 * (c_after - c_before), 0, atol=1e-11)
    for name in ("soil_quality", "habitat_complexity"):
        assert np.all((_array(after, name) >= 0) & (_array(after, name) <= 1))


@pytest.mark.parametrize("name", ["energy_reserve", "nutrients"])
def test_large_stock_cannot_swallow_work_or_nutrient_recycling(name: str) -> None:
    before = _context()
    value = _array(before, name)
    value[0] = 1e18
    before = _with_array(before, name, value)
    before_hash = before.snapshot.state_hash
    with pytest.raises(ValueError, match="ledger balance lost"):
        _apply(before, NicheConstructionStage())
    assert before.snapshot.state_hash == before_hash


def test_unregistrable_tiny_work_is_deferred_without_spending_or_habitat_benefit() -> None:
    before = _context(dt=1e-15)
    after, result = _apply(before, NicheConstructionStage())
    unpaid, _ = _apply(
        _with_array(before, "energy_reserve", np.zeros((3, 8))), NicheConstructionStage()
    )
    for name in ("energy_reserve", "nutrients"):
        np.testing.assert_array_equal(_array(after, name), _array(before, name))
    for name in ("soil_quality", "habitat_complexity"):
        np.testing.assert_array_equal(_array(after, name), _array(unpaid, name))
    assert result.metrics["carbon_respired"] == result.metrics["nutrient_recycled"] == 0
    assert 0 < cast(float, result.metrics["carbon_work_deferred"]) < 8 * 2**-40
    assert result.metrics["nutrient_ledger_roundoff_budget"] == 0


def _turn_574_tile() -> TurnContext:
    """Exact stocks/requests from seed 37, tile 0, before niche on turn 574."""
    before = _context(width=2, height=1, dt=1)
    before = _with_array(before, "population", np.array([[1, 1], [53, 53], [0, 0]]))
    before = _with_array(
        before,
        "energy_reserve",
        np.array([[2.6858175664513264] * 2, [8.869615624467675] * 2, [0.0] * 2]),
    )
    before = _with_array(before, "nutrients", np.full(2, 32.06041395596766))
    species = cast(dict[str, JsonValue], thaw(before.species_state))
    for identity, mass, count, request in (
        ("engineer.one", 10, 1, 3.9747164530186923e-05),
        ("engineer.two", 2, 53, 0.00010917575161431587),
    ):
        traits = dict.fromkeys(TRAITS, 0.0)
        traits["engineering"] = request / (0.02 * mass * count)
        cast(dict[str, JsonValue], species[identity])["traits"] = traits
    return replace(
        before,
        snapshot=replace(before.snapshot, state={**before.snapshot.state, "species": species}),
    )


def test_turn_574_recycling_rounding_is_local_bounded_and_replayable() -> None:
    before = _turn_574_tile()
    original = before.snapshot.state_hash
    after, result = _apply(before, NicheConstructionStage())
    repeated, again = _apply(before, NicheConstructionStage())
    work = (_array(before, "energy_reserve") - _array(after, "energy_reserve")).sum(axis=0)
    expected = 0.02 * work
    actual = _array(after, "nutrients") - _array(before, "nutrients")
    error = np.abs(actual - expected)
    assert error[0] == 3.3040235349372175e-15
    assert error[0] > 1e-9 * expected[0]  # The former rule rejects this real state.
    assert np.all(error <= np.spacing(_array(after, "nutrients")))
    assert np.all(error <= result.metrics["ledger_roundoff_cap_per_transfer"])
    assert result.metrics["nutrient_ledger_abs_residual"] == float(error.sum())
    assert result.metrics["carbon_work_deferred"] == 0
    assert original == before.snapshot.state_hash
    assert after.snapshot.state_hash == repeated.snapshot.state_hash
    assert result.metrics == again.metrics


@pytest.mark.parametrize("name", ["energy_reserve", "nutrients"])
def test_meaningful_large_flux_swallowed_by_huge_local_stock_still_fails(name: str) -> None:
    before = _metadata(_context(dt=1), "body_mass", 1e20)
    reserve = _array(before, "energy_reserve")
    reserve[:2] = 1e20
    before = _with_array(before, "energy_reserve", reserve)
    values = _array(before, name)
    values[0] = 1e300
    before = _with_array(before, name, values)
    with pytest.raises(ValueError, match="ledger balance lost"):
        _apply(before, NicheConstructionStage())


def test_large_local_ulp_does_not_expand_absolute_rounding_budget() -> None:
    before = _with_array(_context(dt=1), "nutrients", np.full(8, 1e12))
    with pytest.raises(ValueError, match="nutrient ledger balance lost"):
        _apply(before, NicheConstructionStage())


def test_unregistered_work_above_carbon_deferral_cap_is_rejected() -> None:
    before = _with_array(_context(dt=1e-10), "nutrients", np.full(8, 1e18))
    with pytest.raises(ValueError, match="nutrient ledger balance lost"):
        _apply(before, NicheConstructionStage())


def test_nutrient_multiplication_underflow_rolls_back_owned_reserve() -> None:
    before = _context()
    reserve = _array(before, "energy_reserve")
    reserve[:2] = np.nextafter(0.0, 1.0)
    before = _with_array(before, "energy_reserve", reserve)
    after, result = _apply(before, NicheConstructionStage())
    np.testing.assert_array_equal(_array(after, "energy_reserve"), reserve)
    np.testing.assert_array_equal(_array(after, "nutrients"), _array(before, "nutrients"))
    assert result.metrics["carbon_respired"] == 0
    assert result.metrics["carbon_work_deferred"] == float(reserve.sum())


@pytest.fixture(scope="module")
def _fp_control(tmp_path_factory: pytest.TempPathFactory) -> ctypes.CDLL:
    """Test-only x86 host probe; production niche never imports a native runtime."""
    directory = tmp_path_factory.mktemp("niche-fp-control")
    source, binary = directory / "mxcsr.c", directory / "mxcsr.so"
    source.write_text(
        "#include <xmmintrin.h>\n"
        "unsigned int get_mxcsr(void) { return _mm_getcsr(); }\n"
        "void set_mxcsr(unsigned int value) { _mm_setcsr(value); }\n"
    )
    subprocess.run(["cc", "-shared", "-fPIC", "-O0", str(source), "-o", str(binary)], check=True)
    library = ctypes.CDLL(str(binary))
    library.get_mxcsr.restype = ctypes.c_uint
    library.set_mxcsr.argtypes = [ctypes.c_uint]
    return library


@contextmanager
def _host_mode(control: ctypes.CDLL, flags: int) -> Iterator[None]:
    original = int(control.get_mxcsr())
    control.set_mxcsr((original & ~0x8040) | flags)
    try:
        yield
    finally:
        control.set_mxcsr(original)


def _subnormal_context() -> TurnContext:
    before = _context()
    reserve = _array(before, "energy_reserve")
    reserve[0].view(np.uint64)[:] = [1, 2, 0xFFFFFFFFFFFFF, 1, 2, 3, 4, 5]
    return _with_array(before, "energy_reserve", reserve)


@pytest.mark.parametrize("flush", [False, True])
def test_ftz_preserves_subnormal_bits_without_funded_habitat_effect(
    _fp_control: ctypes.CDLL, flush: bool
) -> None:
    original_mode = int(_fp_control.get_mxcsr())
    with _host_mode(_fp_control, 0):
        before = _subnormal_context()
        reserve = _array(before, "energy_reserve")
        reserve[0] = 0
        control = _with_array(before, "energy_reserve", reserve)
    original_bits = before.snapshot.arrays["energy_reserve"].numpy().view(np.uint64).copy()
    with _host_mode(_fp_control, 0x8000 if flush else 0):
        after, result = _apply(before, NicheConstructionStage())
        baseline, baseline_result = _apply(control, NicheConstructionStage())
        after_bits = after.snapshot.arrays["energy_reserve"].numpy().view(np.uint64)
        np.testing.assert_array_equal(after_bits[0], original_bits[0])
        assert np.all(_array(after, "energy_reserve")[1] < _array(before, "energy_reserve")[1])
        for name in ("nutrients", "soil_quality", "habitat_complexity"):
            assert after.snapshot.arrays[name] == baseline.snapshot.arrays[name]
        assert result.metrics["carbon_respired"] == baseline_result.metrics["carbon_respired"]
        assert result.metrics["subnormal_cohorts_preserved"] == 8
    assert int(_fp_control.get_mxcsr()) == original_mode
    np.testing.assert_array_equal(
        before.snapshot.arrays["energy_reserve"].numpy().view(np.uint64), original_bits
    )


@pytest.mark.parametrize("flags", [0x40, 0x8040])
def test_daz_preserves_subnormal_bits_without_funded_habitat_effect(
    _fp_control: ctypes.CDLL, flags: int
) -> None:
    original_mode = int(_fp_control.get_mxcsr())
    with _host_mode(_fp_control, 0):
        before = _subnormal_context()
        reserve = _array(before, "energy_reserve")
        reserve[0] = 0
        control = _with_array(before, "energy_reserve", reserve)
    original_bits = before.snapshot.arrays["energy_reserve"].numpy().view(np.uint64).copy()
    with _host_mode(_fp_control, flags):
        # Prove that float positivity is insufficient in this environment.
        assert not bool(before.snapshot.arrays["energy_reserve"].numpy()[0, 0] > 0)
        after, result = _apply(before, NicheConstructionStage())
        baseline, baseline_result = _apply(control, NicheConstructionStage())
        after_bits = after.snapshot.arrays["energy_reserve"].numpy().view(np.uint64)
        np.testing.assert_array_equal(after_bits[0], original_bits[0])
        for name in ("nutrients", "soil_quality", "habitat_complexity"):
            assert after.snapshot.arrays[name] == baseline.snapshot.arrays[name]
        assert result.metrics["carbon_respired"] == baseline_result.metrics["carbon_respired"]
        assert result.metrics["subnormal_cohorts_preserved"] == 8
        np.testing.assert_array_equal(
            before.snapshot.arrays["energy_reserve"].numpy().view(np.uint64), original_bits
        )
    assert int(_fp_control.get_mxcsr()) == original_mode


@pytest.mark.parametrize("invalid", ["empty", "unused", "negative"])
def test_daz_cannot_hide_invalid_nonzero_reserve(_fp_control: ctypes.CDLL, invalid: str) -> None:
    with _host_mode(_fp_control, 0):
        before = _context()
        reserve = _array(before, "energy_reserve")
        row = 2 if invalid == "unused" else 0
        reserve[row, 0:1].view(np.uint64)[:] = 0x8000000000000001 if invalid == "negative" else 1
        before = _with_array(before, "energy_reserve", reserve)
        if invalid == "empty":
            pop = before.snapshot.arrays["population"].numpy().copy()
            pop[0, 0] = 0
            before = _with_array(before, "population", pop)
    with _host_mode(_fp_control, 0x8040), pytest.raises(ValueError):
        _apply(before, NicheConstructionStage())


def test_repeated_rounding_has_a_measured_cumulative_inventory_bound() -> None:
    initial = current = _turn_574_tile()
    errors: list[float] = []
    budgets: list[float] = []
    respired: list[float] = []
    for _ in range(300):
        current, result = _apply(current, NicheConstructionStage())
        errors.append(cast(float, result.metrics["nutrient_ledger_abs_residual"]))
        budgets.append(cast(float, result.metrics["nutrient_ledger_roundoff_budget"]))
        respired.append(cast(float, result.metrics["carbon_respired"]))
    recycled = _array(current, "nutrients") - _array(initial, "nutrients")
    spent = _array(initial, "energy_reserve") - _array(current, "energy_reserve")
    assert abs(float(recycled.sum()) - 0.02 * math.fsum(respired)) <= math.fsum(budgets)
    assert abs(float(recycled.sum()) - 0.02 * float(spent.sum())) <= math.fsum(budgets)
    assert math.fsum(errors) <= math.fsum(budgets)
    assert math.fsum(budgets) <= 1e-9 * 0.02 * math.fsum(respired) + 300 * 2 * 2**-40


def test_extinct_zero_cohort_is_valid_and_structures_remain_bounded() -> None:
    before = _context(dt=1)
    population = before.snapshot.arrays["population"].numpy().copy()
    reserve = _array(before, "energy_reserve")
    population[0], reserve[0] = 0, 0
    before = _with_array(before, "population", population)
    before = _with_array(before, "energy_reserve", reserve)
    before = _metadata(before, "status", "Extinct")
    before = _with_array(before, "soil_quality", np.ones(8))
    before = _with_array(before, "habitat_complexity", np.ones(8))
    after, result = _apply(before, NicheConstructionStage())
    np.testing.assert_array_equal(_array(after, "energy_reserve")[0], 0)
    assert result.metrics["carbon_respired"] == pytest.approx(0.64)
    land = _array(after, "biome") >= 2
    assert np.all(_array(after, "soil_quality") <= 1)
    assert np.all(_array(after, "habitat_complexity") <= 1)
    np.testing.assert_array_equal(_array(after, "habitat_complexity")[land], 0)


@pytest.mark.parametrize("dt", [0, -0.1, 1.01, float("inf"), True])
def test_invalid_time_step(dt: float) -> None:
    with pytest.raises(ValueError):
        NicheConstructionStage().execute(_context(dt=dt))


@pytest.mark.parametrize(
    "case",
    [
        "missing_trait",
        "unknown_trait",
        "bool_trait",
        "trait_bound",
        "budget",
        "duplicate_slot",
        "mass",
        "status",
        "extinct",
    ],
)
def test_invalid_species_metadata(case: str) -> None:
    context = _context()
    traits: dict[str, JsonValue] = dict.fromkeys(TRAITS, 0.1)
    if case == "missing_trait":
        traits.pop("speed")
    elif case == "unknown_trait":
        traits["flight"] = 0.1
    elif case == "bool_trait":
        traits["armor"] = True
    elif case == "trait_bound":
        traits["armor"] = 1.1
    changes: dict[str, tuple[str, JsonValue]] = {
        "budget": ("trait_budget", 0.1),
        "duplicate_slot": ("slot", 1),
        "mass": ("body_mass", 0),
        "status": ("status", "unknown"),
        "extinct": ("status", "Extinct"),
    }
    key, value = changes.get(case, ("traits", traits))
    with pytest.raises(ValueError):
        NicheConstructionStage().execute(_metadata(context, key, value))


@pytest.mark.parametrize(
    "case",
    [
        "pop_type",
        "pop_shape",
        "pop_negative",
        "reserve_type",
        "reserve_shape",
        "reserve_nan",
        "reserve_negative",
        "empty",
        "unused",
        "nutrients_negative",
        "soil_bounds",
        "structure_bounds",
        "structure_shape",
        "biome_type",
        "biome_bounds",
    ],
)
def test_invalid_arrays(case: str) -> None:
    context = _context()
    cases: dict[str, tuple[str, NDArray[np.generic]]] = {
        "pop_type": ("population", np.ones((3, 8))),
        "pop_shape": ("population", np.ones((3, 7), dtype=np.int64)),
        "pop_negative": ("population", np.full((3, 8), -1, dtype=np.int64)),
        "reserve_type": ("energy_reserve", np.ones((3, 8), dtype=np.float32)),
        "reserve_shape": ("energy_reserve", np.ones((2, 8))),
        "reserve_nan": ("energy_reserve", np.full((3, 8), np.nan)),
        "reserve_negative": ("energy_reserve", np.full((3, 8), -1.0)),
        "empty": ("population", np.zeros((3, 8), dtype=np.int64)),
        "unused": ("population", np.ones((3, 8), dtype=np.int64)),
        "nutrients_negative": ("nutrients", np.full(8, -1.0)),
        "soil_bounds": ("soil_quality", np.full(8, 1.1)),
        "structure_bounds": ("habitat_complexity", np.full(8, -0.1)),
        "structure_shape": ("habitat_complexity", np.zeros(7)),
        "biome_type": ("biome", np.zeros(8)),
        "biome_bounds": ("biome", np.full(8, 7, dtype=np.int64)),
    }
    name, array = cases[case]
    with pytest.raises(ValueError):
        NicheConstructionStage().execute(_with_array(context, name, array))


def test_canopy_cooling_depends_on_leaf_and_land_not_old_temperature() -> None:
    before = _context(width=4, height=1)
    before = _with_array(before, "elevation", np.array([0.0, 1.0, 1.0, -1.0]))
    before = _with_array(before, "plant_biomass", np.array([100.0, 100.0, 300.0, 300.0]))
    before = _with_array(before, "temperature", np.array([10.0, 10.0, 40.0, 40.0]))
    after, result = _apply(before, MicroclimateStage())
    np.testing.assert_array_equal(_array(after, "temperature"), [10, 9, 38.5, 40])
    warmer = _with_array(before, "temperature", _array(before, "temperature") + 5)
    warmed, _ = _apply(warmer, MicroclimateStage())
    np.testing.assert_array_equal(_array(warmed, "temperature") - _array(after, "temperature"), 5)
    assert result.metrics["canopy_cooling_max_c"] == 1.5
    assert after.environment_state == before.environment_state


def test_climate_rebuild_prevents_previous_cooling_from_accumulating() -> None:
    pipeline = DeterministicPipeline([MicroclimateStage(), ClimateStage()])
    before = _context()
    first = pipeline.execute(before)
    next_input = replace(
        first,
        turn_id=2,
        snapshot=replace(first.snapshot, turn_id=1, version=first.world_version.advance()),
    )
    arbitrary_old = _with_array(next_input, "temperature", np.full(8, -999.0))
    second, second_control = pipeline.execute(next_input), pipeline.execute(arbitrary_old)
    np.testing.assert_array_equal(
        _array(second, "temperature"), _array(second_control, "temperature")
    )


@pytest.mark.parametrize(
    "name,value",
    [
        ("plant_biomass", np.full(8, -1.0)),
        ("plant_biomass", np.full(8, np.inf)),
        ("plant_biomass", np.zeros(7)),
        ("temperature", np.zeros(8, dtype=np.float32)),
        ("elevation", np.full(8, np.nan)),
    ],
)
def test_microclimate_rejects_invalid_fields(name: str, value: NDArray[np.generic]) -> None:
    with pytest.raises(ValueError):
        MicroclimateStage().execute(_with_array(_context(), name, value))


def test_real_next_turn_hydrology_capacity_improves_with_paid_engineering() -> None:
    before = _context(width=2, height=1, dt=1)
    for name, value in {
        "biome": np.full(2, 4, dtype=np.int64),
        "elevation": np.full(2, 100.0),
        "soil_water": np.full(2, 150.0),
        "rainfall": np.full(2, 100.0),
        "temperature": np.full(2, -10.0),
        "habitat_complexity": np.zeros(2),
    }.items():
        before = _with_array(before, name, value)
    funded, _ = _apply(before, NicheConstructionStage())
    empty = _with_array(before, "energy_reserve", np.zeros((3, 2)))
    control, _ = _apply(empty, NicheConstructionStage())

    def next_hydrology(value: TurnContext) -> TurnContext:
        value = replace(
            value,
            turn_id=2,
            snapshot=replace(value.snapshot, turn_id=1, version=value.world_version.advance()),
        )
        return _apply(value, HydrologyStage())[0]

    wet, dry = next_hydrology(funded), next_hydrology(control)
    gain = _array(wet, "soil_water") - _array(dry, "soil_water")
    expected = 100 * (_array(funded, "soil_quality") - _array(control, "soil_quality"))
    np.testing.assert_allclose(gain, expected)
    assert np.all(gain > 1)
    np.testing.assert_allclose(_array(wet, "soil_water") + _array(wet, "surface_water"), 250.0)

"""Recorded fixtures plus removable, isolated adapters to the original CPU oracles."""

from __future__ import annotations

import importlib.util
import math
import random
import sqlite3
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import FrozenInstanceError, asdict, replace
from functools import cache
from pathlib import Path
from types import SimpleNamespace
from typing import Protocol, cast

import numpy as np
import pytest
from numpy.typing import NDArray
from sqlalchemy.engine import Engine

from app.models.config import ResourceSystemConfig
from app.models.environment import MapTile
from app.models.species import Species
from app.services.ecology.resource_manager import ResourceManager, TileResourceState
from app.simulation.v2.stages.resources import (
    MODEL_VERSION,
    LegacyNppParameters,
    LegacyResourceParameters,
    LegacyResourceResult,
    calculate_legacy_npp,
    calculate_legacy_predation_pressure,
    transition_legacy_resources,
)
from app.simulation.v2.values import FrozenArray


class _ResourceOracle(Protocol):
    _tile_states: dict[int, TileResourceState]
    _event_pulses: dict[int, list[tuple[str, float, float, int]]]

    def calculate_npp(self, tile: object) -> float: ...

    def update_resource_dynamics(
        self, tiles: Sequence[object], demand: dict[int, float], turn: int
    ) -> None: ...


class _PredationOracle(Protocol):
    def compute_predation_pressure_matrix(
        self, species: Sequence[object]
    ) -> NDArray[np.float64]: ...


@pytest.fixture(autouse=True)
def _no_database_connections(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("legacy kernel oracle must not connect to a database")

    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(Engine, "connect", forbidden)


def _npp_oracle(params: LegacyNppParameters) -> float:
    config = ResourceSystemConfig(
        temperate_forest_npp_multiplier=params.habitat_multiplier,
        max_npp_per_tile=params.max_npp,
    )
    manager = cast(_ResourceOracle, ResourceManager(config))
    manager._event_pulses[1] = [("fixture", params.event_multiplier, 0.0, 1)]
    return manager.calculate_npp(
        SimpleNamespace(
            id=1,
            temperature=params.temperature,
            humidity=params.humidity,
            y=params.y,
            resources=params.resources,
            habitat_type="temperate",
        )
    )


def _transition_oracle(
    base: float,
    current: float,
    demand: float,
    penalty: float,
    turn: int,
    parameters: LegacyResourceParameters,
) -> LegacyResourceResult:
    config = ResourceSystemConfig(**asdict(parameters))
    manager = cast(_ResourceOracle, ResourceManager(config))
    # Only isolate the upstream NPP computation; execute the original transition.
    manager.calculate_npp = lambda tile: base  # type: ignore[method-assign]
    manager._tile_states[1] = TileResourceState(
        tile_id=1, current_npp=current, overgrazing_penalty=penalty
    )
    manager.update_resource_dynamics([SimpleNamespace(id=1)], {1: demand}, turn)
    state = manager._tile_states[1]
    return LegacyResourceResult(
        state.current_npp,
        state.last_consumption_ratio,
        state.overgrazing_penalty,
        state.t1_capacity_kg,
    )


@cache
def _predation_factory() -> Callable[[], _PredationOracle]:
    # Avoid the unrelated species/__init__.py import tree and GPU initialization.
    path = Path(__file__).resolve().parents[2] / "app/services/species/predation.py"
    spec = importlib.util.spec_from_file_location("_legacy_resource_predation_oracle", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return cast(Callable[[], _PredationOracle], module.PredationService)


def _f(value: object) -> FrozenArray:
    return FrozenArray.from_numpy(np.array(value, dtype=np.float64))


def _web(
    graph: FrozenArray, population: FrozenArray, weight: FrozenArray, trophic: FrozenArray
) -> list[SimpleNamespace]:
    matrix = np.asarray(graph.numpy(), dtype=np.float64)
    return [
        SimpleNamespace(
            lineage_code=str(i),
            status="alive",
            morphology_stats={
                "population": float(population.numpy()[i]),
                "body_weight_g": float(weight.numpy()[i]),
            },
            trophic_level=float(trophic.numpy()[i]),
            prey_species=[str(j) for j in range(matrix.shape[0]) if matrix[i, j] > 0],
            prey_preferences={str(j): float(matrix[i, j]) for j in range(matrix.shape[0])},
        )
        for i in range(matrix.shape[0])
    ]


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, 37105.56890592682),
        ({"y": 0.0}, 7421.113781185361),
        ({"temperature": 56.0}, 0.0),
        ({"humidity": 0.049}, 0.0),
        ({"humidity": 0.05}, 5923.954112698963),
        ({"habitat_multiplier": 1.5}, 55658.35335889022),
        ({"max_npp": 100.0}, 100.0),
    ],
)
def test_recorded_npp_fixtures(overrides: dict[str, float], expected: float) -> None:
    params = replace(LegacyNppParameters(20.0, 0.5, 20.0, 100.0), **overrides)
    assert calculate_legacy_npp(params) == expected == _npp_oracle(params)


@pytest.mark.parametrize("temperature", [-30.00001, -30.0, 20.0, 55.0, 55.00001])
@pytest.mark.parametrize("humidity", [0.0, math.nextafter(0.05, 0), 0.05, 0.5, 1.5])
@pytest.mark.parametrize("y", [0.0, 20.0, 39.0, 80.0])
def test_npp_boundaries_match_original_bits(temperature: float, humidity: float, y: float) -> None:
    params = LegacyNppParameters(temperature, humidity, y, 873.7, 0.83, 1.13)
    assert calculate_legacy_npp(params).hex() == _npp_oracle(params).hex()


def test_legacy_units_and_missing_orm_fields_are_explicit() -> None:
    assert MODEL_VERSION == "legacy-resource-kernels-v1"
    assert "habitat_type" not in MapTile.model_fields
    assert "habitats" not in Species.model_fields
    assert "body_weight_kg" not in Species.model_fields
    # These advertised old switches are unused, including the alternative scaling factor.
    config = ResourceSystemConfig(enable_climate_npp=False, resource_to_npp_factor=999.0)
    manager = cast(_ResourceOracle, ResourceManager(config))
    tile = SimpleNamespace(id=1, temperature=20.0, humidity=0.5, y=20, resources=100.0)
    assert manager.calculate_npp(tile) == 37105.56890592682


@pytest.mark.parametrize(
    ("demand", "expected"),
    [
        (0.0, LegacyResourceResult(58.75, 0.0, 0.0, 2937.5)),
        (35.0, LegacyResourceResult(58.75, 1.0, 0.0, 2937.5)),
        (140.0, LegacyResourceResult(36.25, 4.0, 0.44999999999999996, 1812.5)),
    ],
)
def test_recorded_resource_fixtures(demand: float, expected: LegacyResourceResult) -> None:
    params = LegacyResourceParameters(resource_fluctuation_amplitude=0.0)
    assert transition_legacy_resources(100, 50, demand, 0, 0, params) == expected
    assert _transition_oracle(100, 50, demand, 0, 0, params) == expected


@pytest.mark.parametrize("base,current", [(0.0, 0.0), (0.0, 50.0), (100.0, 0.0), (100.0, 50.0)])
@pytest.mark.parametrize("demand,penalty", [(0.0, 0.0), (5.0, 0.03), (70.0, 0.3), (1e6, 0.9)])
@pytest.mark.parametrize("turn", [0, 7, 18])
def test_resource_zero_branches_penalty_recovery_and_season_match_exactly(
    base: float, current: float, demand: float, penalty: float, turn: int
) -> None:
    params = LegacyResourceParameters()
    assert transition_legacy_resources(base, current, demand, penalty, turn, params) == (
        _transition_oracle(base, current, demand, penalty, turn, params)
    )


def test_old_stock_survives_zero_npp_and_same_turn_updates_advance_again() -> None:
    params = LegacyResourceParameters(resource_fluctuation_amplitude=0)
    assert transition_legacy_resources(0, 50, 0, 0, 0, params).current_npp == 50.0
    assert transition_legacy_resources(100, 0, 0, 0, 0, params).current_npp == 105.0
    first = transition_legacy_resources(100, 50, 0, 0, 0, params)
    second = transition_legacy_resources(100, first.current_npp, 0, 0, 0, params)
    assert second.current_npp == 67.74609375
    zero_capacity = replace(params, resource_capacity_multiplier=0, harvestable_fraction=0)
    assert transition_legacy_resources(100, 50, 100, 0, 0, zero_capacity) == (
        _transition_oracle(100, 50, 100, 0, 0, zero_capacity)
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"harvestable_fraction": 0.2},
        {"overgrazing_threshold": 5.0},
        {"overgrazing_penalty": 0.47},
        {"resource_capacity_multiplier": 0.4},
        {"resource_recovery_rate": 0.0},
        {"resource_fluctuation_amplitude": 1.0},
        {"npp_to_capacity_factor": 0.0},
    ],
)
def test_resource_explicit_coefficients_match_original(overrides: dict[str, float]) -> None:
    params = replace(LegacyResourceParameters(), **overrides)
    assert transition_legacy_resources(123.4, 87.6, 271.0, 0.17, 9, params) == (
        _transition_oracle(123.4, 87.6, 271.0, 0.17, 9, params)
    )


@pytest.mark.parametrize(
    ("wolf_population", "has_prey", "expected"),
    [
        (2.0, True, [0.014987512487364006, 0.02990039838748677, 0.0]),
        (2.0, False, [0.014987512487364006, 0.0, 0.0]),
        (200.0, True, [0.014987512487364006, 0.2999999987633078, 0.46297273137842576]),
    ],
)
def test_recorded_predation_fixtures(
    wolf_population: float, has_prey: bool, expected: list[float]
) -> None:
    inputs = (
        _f([[0, 0, 0], [1, 0, 0], [0, 1 if has_prey else 0, 0]]),
        _f([100, 10, wolf_population]),
        _f([10, 100, 1000]),
        _f([1, 2, 3]),
    )
    actual = calculate_legacy_predation_pressure(*inputs)
    oracle = _predation_factory()().compute_predation_pressure_matrix(_web(*inputs))
    assert actual.data == np.array(expected, dtype=np.float64).tobytes() == oracle.tobytes()


@pytest.mark.parametrize("counts", [[0, 0, 0], [100, 0, 2], [1e-6, 10, 200]])
@pytest.mark.parametrize("weights", [[0, 0, 0], [10, 100, 1000], [0.00001, 100, 0]])
def test_float32_graph_unormalized_rows_zero_mass_and_zero_prey_match_oracle(
    counts: list[float], weights: list[float]
) -> None:
    inputs = (
        _f([[0, 0, 0], [0.123456789, 0, 2.7], [0.3, 0.7123456789, 0]]),
        _f(counts),
        _f(weights),
        _f([1, 2, 3]),
    )
    actual = calculate_legacy_predation_pressure(*inputs)
    oracle = _predation_factory()().compute_predation_pressure_matrix(_web(*inputs))
    assert actual.data == oracle.tobytes()


def test_empty_graph_and_explicit_graph_changes_have_no_stale_count_cache() -> None:
    empty = calculate_legacy_predation_pressure(_f(np.zeros((0, 0))), _f([]), _f([]), _f([]))
    assert empty.shape == (0,) and empty.dtype == "<f8"
    population, weight, trophic = _f([100, 10]), _f([10, 100]), _f([1, 2])
    linked, unlinked = _f([[0, 0], [1, 0]]), _f([[0, 0], [0, 0]])
    before = calculate_legacy_predation_pressure(linked, population, weight, trophic)
    after = calculate_legacy_predation_pressure(unlinked, population, weight, trophic)
    assert before.numpy()[0] > 0 and not np.any(after.numpy())
    legacy = _predation_factory()()
    old_before = legacy.compute_predation_pressure_matrix(_web(linked, population, weight, trophic))
    old_after = legacy.compute_predation_pressure_matrix(
        _web(unlinked, population, weight, trophic)
    )
    assert old_before.tobytes() == old_after.tobytes()  # Known service cache defect.


@pytest.mark.parametrize("invalid", [True, "1", None, math.nan, math.inf, -math.inf, 10**400])
def test_scalar_validation_rejects_nonfinite_or_nonnumeric_inputs(invalid: object) -> None:
    number = cast(float, invalid)
    with pytest.raises((TypeError, ValueError)):
        LegacyNppParameters(number, 0.5, 20, 100)
    with pytest.raises((TypeError, ValueError)):
        LegacyResourceParameters(resource_recovery_rate=number)
    with pytest.raises((TypeError, ValueError)):
        transition_legacy_resources(100, 50, number, 0, 0, LegacyResourceParameters())


@pytest.mark.parametrize(
    "field", ["humidity", "y", "resources", "habitat_multiplier", "event_multiplier", "max_npp"]
)
def test_npp_nonnegative_input_domain(field: str) -> None:
    with pytest.raises(ValueError, match="non-negative"):
        replace(LegacyNppParameters(20, 0.5, 20, 100), **{field: -1.0})


def test_transition_validation_and_overflow_rejection() -> None:
    params = LegacyResourceParameters()
    for index in range(4):
        args = [100.0, 50.0, 10.0, 0.0]
        args[index] = -1.0
        with pytest.raises(ValueError, match="non-negative"):
            transition_legacy_resources(args[0], args[1], args[2], args[3], 0, params)
    for turn in (-1, True, 0.5, 10**400):
        with pytest.raises(ValueError):
            transition_legacy_resources(100, 50, 0, 0, cast(int, turn), params)
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        LegacyResourceParameters(resource_fluctuation_amplitude=1.1)
    with pytest.raises(ValueError, match="finite"):
        transition_legacy_resources(1e308, 1e307, 0, 0, 0, params)


@pytest.mark.parametrize("slot", range(4))
def test_predation_rejects_shape_negative_boolean_and_nonfrozen_inputs(slot: int) -> None:
    inputs = [_f([[1.0]]), _f([1.0]), _f([1.0]), _f([1.0])]
    for invalid in (_f([-1.0]), _f([[]]), FrozenArray.from_numpy(np.array([True])), object()):
        changed = inputs.copy()
        changed[slot] = cast(FrozenArray, invalid)
        with pytest.raises((TypeError, ValueError)):
            calculate_legacy_predation_pressure(*changed)
    shape = (1, 1) if slot == 0 else (1,)
    with pytest.raises(ValueError, match="non-negative"):
        changed = inputs.copy()
        changed[slot] = _f(np.full(shape, -1.0))
        calculate_legacy_predation_pressure(*changed)
    with pytest.raises(TypeError, match="not bool"):
        changed = inputs.copy()
        changed[slot] = FrozenArray.from_numpy(np.ones(shape, dtype=bool))
        calculate_legacy_predation_pressure(*changed)


def test_nonfinite_arrays_and_derived_overflow_cannot_escape() -> None:
    for invalid in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError, match="NaN|infinity"):
            _f([invalid])
    with pytest.raises(ValueError, match="float32"):
        calculate_legacy_predation_pressure(_f([[1e100]]), _f([1]), _f([1]), _f([2]))
    with pytest.raises(ValueError, match="finite"):
        calculate_legacy_predation_pressure(_f([[1]]), _f([1e308]), _f([1e308]), _f([2]))


def test_immutable_outputs_unmodified_inputs_and_unchanged_global_rng() -> None:
    params = LegacyNppParameters(20, 0.5, 20, 100)
    resource = LegacyResourceParameters()
    inputs = (_f([[0, 0], [1, 0]]), _f([100, 10]), _f([10, 100]), _f([1, 2]))
    before = tuple(value.content_hash for value in inputs)
    python_rng, numpy_rng = random.getstate(), repr(np.random.get_state())
    calculate_legacy_npp(params)
    result = transition_legacy_resources(100, 50, 140, 0, 7, resource)
    pressure = calculate_legacy_predation_pressure(*inputs)
    assert random.getstate() == python_rng and repr(np.random.get_state()) == numpy_rng
    assert tuple(value.content_hash for value in inputs) == before
    for frozen in (params, resource, result):
        with pytest.raises(FrozenInstanceError):
            setattr(frozen, next(iter(asdict(frozen))), 0)
    with pytest.raises(ValueError):
        pressure.numpy().setflags(write=True)
    with pytest.raises(ValueError):
        pressure.numpy()[0] = 0


def test_import_is_cpu_only_and_does_not_load_legacy_or_database_modules() -> None:
    script = """
import sys
from app.simulation.v2.stages.resources import MODEL_VERSION
blocked = ('taichi', 'app.tensor', 'app.services', 'app.core.database', 'app.ai', 'sqlalchemy')
loaded = [name for name in sys.modules if any(
    name == prefix or name.startswith(prefix + '.') for prefix in blocked
)]
assert loaded == [], loaded
assert MODEL_VERSION == 'legacy-resource-kernels-v1'
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr

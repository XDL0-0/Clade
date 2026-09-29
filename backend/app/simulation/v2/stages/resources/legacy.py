"""Pure legacy resource arithmetic, not a closed energy or population model.

NPP uses the old game-scaled kg/tile/turn label, without physical area or time
conversion. The transition treats that flux as a stock and never subtracts
actual consumption. Predation returns dimensionless pressure, not a Holling
intake flux. These known model defects are intentionally preserved.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, fields
from typing import TypeAlias

import numpy as np
from numpy.typing import NDArray

from ...values import FrozenArray, natural

MODEL_VERSION = "legacy-resource-kernels-v1"
FloatArray: TypeAlias = NDArray[np.float64]


def _number(value: object, name: str, *, nonnegative: bool = True) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a finite number, not a bool")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    if nonnegative and result < 0:
        raise ValueError(f"{name} must be non-negative")
    return result


@dataclass(frozen=True, slots=True)
class LegacyNppParameters:
    """Explicit old NPP inputs; humidity/resources have no artificial upper cap.

    Temperature is °C, y is the old non-negative row coordinate, resources is
    mineral richness (normally 1–1000), and the two multipliers are unitless.
    The caller resolves habitat/event multipliers; no ORM/default lookup occurs.
    """

    temperature: float
    humidity: float
    y: float
    resources: float
    habitat_multiplier: float = 1.0
    event_multiplier: float = 1.0
    max_npp: float = 100_000.0

    def __post_init__(self) -> None:
        for field in fields(self):
            value = _number(
                getattr(self, field.name), field.name, nonnegative=field.name != "temperature"
            )
            object.__setattr__(self, field.name, value)


def calculate_legacy_npp(parameters: LegacyNppParameters) -> float:
    """Preserve ResourceManager.calculate_npp's order, fixed height 40 and scale 30."""
    if not isinstance(parameters, LegacyNppParameters):
        raise TypeError("parameters must be LegacyNppParameters")
    temp, hum = parameters.temperature, parameters.humidity
    if temp < -30.0 or temp > 55.0:
        return 0.0
    if hum < 0.05:
        return 0.0
    rainfall_mm = hum * 3500.0
    try:
        npp_temp = 3000.0 / (1.0 + math.exp(1.315 - 0.119 * temp))
    except OverflowError:
        npp_temp = 0.0
    npp_water = 3000.0 * (1.0 - math.exp(-0.000664 * rainfall_mm))
    miami_npp = min(npp_temp, npp_water)
    base_npp = miami_npp * 30.0
    half_height = 40 / 2.0
    normalized_lat = abs(parameters.y - half_height) / half_height
    normalized_lat = min(1.0, max(0.0, normalized_lat))
    light_factor = 1.0 - (normalized_lat**2.5) * 0.8
    base_npp *= light_factor
    base_npp *= parameters.habitat_multiplier
    soil_fertility = 0.5 + (parameters.resources / 1000.0)
    base_npp *= soil_fertility
    base_npp *= parameters.event_multiplier
    base_npp = min(base_npp, parameters.max_npp)
    return _number(max(0.0, base_npp), "npp")


@dataclass(frozen=True, slots=True)
class LegacyResourceParameters:
    """Old per-call coefficients, with no implied dt or physical stock unit.

    Rates/multipliers are non-negative. Seasonal amplitude is in [0, 1]. The
    caller decides whether resource dynamics runs and handles event decay.
    """

    harvestable_fraction: float = 0.7
    overgrazing_threshold: float = 1.0
    overgrazing_penalty: float = 0.15
    resource_capacity_multiplier: float = 1.2
    resource_recovery_rate: float = 0.3
    resource_fluctuation_amplitude: float = 0.1
    npp_to_capacity_factor: float = 50.0

    def __post_init__(self) -> None:
        for field in fields(self):
            object.__setattr__(self, field.name, _number(getattr(self, field.name), field.name))
        if self.resource_fluctuation_amplitude > 1.0:
            raise ValueError("resource_fluctuation_amplitude must be in [0, 1]")


@dataclass(frozen=True, slots=True)
class LegacyResourceResult:
    """Legacy current NPP, dimensionless demand ratio/penalty, and T1 kg label."""

    current_npp: float
    consumption_ratio: float
    overgrazing_penalty: float
    t1_capacity_kg: float

    def __post_init__(self) -> None:
        for field in fields(self):
            object.__setattr__(self, field.name, _number(getattr(self, field.name), field.name))


def transition_legacy_resources(
    base_npp: float,
    old_current: float,
    demand: float,
    old_penalty: float,
    turn: int,
    parameters: LegacyResourceParameters,
) -> LegacyResourceResult:
    """Extract ResourceManager.update_resource_dynamics's single-tile arithmetic.

    Demand only drives overgrazing. It is NOT subtracted from a resource stock.
    Repeating a turn advances again, as the old service did; this kernel has no
    scheduler, idempotency guard, event queue, or mutable tile cache.
    """
    base_npp = _number(base_npp, "base_npp")
    old_current = _number(old_current, "old_current")
    demand = _number(demand, "demand")
    old_penalty = _number(old_penalty, "old_penalty")
    natural(turn, "turn")
    _number(turn, "turn")
    if not isinstance(parameters, LegacyResourceParameters):
        raise TypeError("parameters must be LegacyResourceParameters")
    cfg = parameters
    supply = (
        old_current * cfg.harvestable_fraction
        if old_current > 0
        else base_npp * cfg.harvestable_fraction
    )
    ratio = demand / supply if supply > 0 else 0.0
    if ratio > cfg.overgrazing_threshold:
        excess = ratio - cfg.overgrazing_threshold
        penalty = min(0.5, excess * cfg.overgrazing_penalty)
    else:
        penalty = max(0, old_penalty - 0.05)
    capacity = base_npp * cfg.resource_capacity_multiplier
    n = base_npp if old_current == 0 else old_current
    growth = cfg.resource_recovery_rate * n * (1 - n / capacity) if capacity > 0 else 0
    penalty_loss = penalty * n
    current = max(0.1 * base_npp, n + growth - penalty_loss)
    if cfg.resource_fluctuation_amplitude > 0:
        fluctuation = 1.0 + cfg.resource_fluctuation_amplitude * math.sin(turn * 0.5)
        current *= fluctuation
    return LegacyResourceResult(current, ratio, penalty, current * cfg.npp_to_capacity_factor)


def _array(value: FrozenArray, name: str, shape: tuple[int, ...]) -> FloatArray:
    if not isinstance(value, FrozenArray):
        raise TypeError(f"{name} must be a FrozenArray")
    if value.shape != shape:
        raise ValueError(f"{name} must have shape {shape}")
    raw = value.numpy()
    if raw.dtype.kind not in "iuf":
        raise TypeError(f"{name} must have a real numeric dtype, not bool")
    result = np.array(raw, dtype=np.float64, copy=True)
    if not np.isfinite(result).all() or np.any(result < 0):
        raise ValueError(f"{name} must contain finite non-negative values")
    return result


def _finite_array(value: FloatArray, name: str) -> FloatArray:
    if not np.isfinite(value).all():
        raise ValueError(f"{name} exceeds the finite numerical domain")
    return value


def calculate_legacy_predation_pressure(
    graph: FrozenArray,
    population: FrozenArray,
    body_weight_g: FrozenArray,
    trophic_levels: FrozenArray,
) -> FrozenArray:
    """Return immutable float64 (S,) pressure for an explicit, current graph.

    Extracts PredationService.compute_predation_pressure_matrix's arithmetic.
    The caller fixes the same alive-species axis for all four inputs.
    Graph has shape (S,S), predator rows/prey columns, cast to the old float32
    preference matrix; its rows are NOT normalized. Other inputs have shape
    (S,), cast to float64. Biomass is N*grams and demand is 0.1*biomass, with no
    dt. The legacy zero-prey/zero-biomass branches return zero pressure. All
    inputs must be finite and non-negative; no cache or shared-prey allocation
    is introduced. Overflow outside the finite numerical domain is rejected.
    """
    if not isinstance(population, FrozenArray):
        raise TypeError("population must be a FrozenArray")
    if len(population.shape) != 1:
        raise ValueError("population must have shape (S,)")
    size = population.shape[0]
    counts = _array(population, "population", (size,))
    weights = _array(body_weight_g, "body_weight_g", (size,))
    trophic = _array(trophic_levels, "trophic_levels", (size,))
    preferences = _array(graph, "graph", (size, size))
    if np.any(preferences > np.finfo(np.float32).max):
        raise ValueError("graph exceeds the finite float32 domain")
    matrix = preferences.astype(np.float32)
    with np.errstate(over="ignore", invalid="ignore"):
        biomass = _finite_array(counts * weights, "biomass")
        predator_demand = biomass * 0.1
        available_prey = _finite_array(matrix @ biomass, "available_prey")
    with np.errstate(divide="ignore", invalid="ignore"):
        starvation_ratio = np.where(
            available_prey > 0,
            np.maximum(0, (predator_demand - available_prey) / predator_demand),
            0.0,
        )
    # The original second positional 0.0 is copy=False, not the nan value.
    starvation_ratio = np.nan_to_num(starvation_ratio, copy=False)
    starvation_ratio = np.where(trophic < 2.0, 0.0, starvation_ratio)
    starvation_pressure = starvation_ratio**1.5 * 0.5
    with np.errstate(over="ignore", invalid="ignore"):
        predation_demand = _finite_array(matrix.T @ (biomass * 0.1), "predation_demand")
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        pressure_ratio = np.where(biomass > 0, predation_demand / biomass, 0.0)
    pressure_ratio = np.nan_to_num(pressure_ratio, copy=False)
    predation_pressure = 2.0 / (1.0 + np.exp(-pressure_ratio)) - 1.0
    predation_pressure = np.clip(predation_pressure, 0.0, 1.0) * 0.3
    total_pressure = starvation_pressure + predation_pressure
    return FrozenArray.from_numpy(_finite_array(np.clip(total_pressure, 0.0, 1.0), "pressure"))

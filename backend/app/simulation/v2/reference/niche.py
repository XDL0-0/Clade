"""Opt-in canopy cooling and reserve-funded ecological engineering.

Temperature is Celsius; reserve/work/body mass use the reference carbon unit.
Engineering spends .02/year of structural carbon times the species' global
engineering trait. Current biome (0/1 water, 2..6 land), not trophic role or a
species name, selects soil improvement versus aquatic structure. These are
abstract dimensionless habitat indices, not additional carbon stocks or claims
that all aquatic engineers are corals. Every unit spent is respired, returning
.02 nutrient units. No population, genes, global climate or body mass changes.

Microclimate must follow Climate each turn: Climate reconstructs temperature,
so canopy cooling never compounds yesterday's anomaly. Engineering is a later
stage; its soil feedback affects the next turn's real hydrology capacity.

Ledger rounding is bounded locally by an ULP and 2**-40 reference units, in
addition to the existing 1e-9 flux-relative bound. Unregisterable work of at
most 2**-40 carbon per tile is left in reserve, with no funded habitat effect.
Subnormal reserve/request values are classified by bits and never subtracted,
including subtraction of zero: hosts using flush-to-zero would erase the stock.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from ..context import TurnContext
from ..contracts import SimulationStage, StageContract, StageResult
from ..events import WorldEvent
from ..values import JsonValue
from .common import FloatArray, IntArray, event, geometry, number, result
from .ecology import Species, _float, _integer, _species
from .environment import checked_field, finite_outputs
from .extinction import STATUSES
from .world import TRAITS


class MicroclimateStage(SimulationStage):
    contract = StageContract(
        "reference_microclimate",
        "1",
        dependencies=("reference_climate",),
        reads=(
            "state.geometry",
            "state.environment.sea_level",
            "arrays.temperature",
            "arrays.elevation",
            "arrays.plant_biomass",
        ),
        writes=("arrays.temperature",),
    )

    def execute(self, context: TurnContext) -> StageResult:
        super().validate_inputs(context)
        sea = number(context.environment_state["sea_level"], "sea_level")
        temperature = checked_field(context, "temperature")
        elevation = checked_field(context, "elevation")
        leaf = checked_field(context, "plant_biomass", low=0)
        land = elevation > sea
        cooling = np.where(land, 2 * (leaf / (100 + leaf)), 0.0)
        temperature -= cooling
        finite_outputs(temperature)
        return result(
            context,
            self.contract.name,
            arrays={"temperature": temperature},
            metrics={
                "land_tiles": int(np.count_nonzero(land)),
                "canopy_cooling_max_c": float(np.max(cooling)),
                "canopy_cooling_mean_land_c": (
                    float(np.mean(cooling[land])) if np.any(land) else 0.0
                ),
            },
        )


@dataclass(frozen=True)
class _Inputs:
    dt: float
    population: IntArray
    reserve: FloatArray
    nutrients: FloatArray
    soil: FloatArray
    structure: FloatArray
    biome: IntArray
    species: tuple[Species, ...]


def _inputs(context: TurnContext) -> _Inputs:
    width, height = geometry(context)
    shape = context.snapshot.arrays["population"].shape
    if len(shape) != 2 or shape[1] != width * height:
        raise ValueError("population must have species-by-tile shape")
    population = _integer(context, "population", shape)
    reserve = _float(context, "energy_reserve", shape)
    bits = reserve.view(np.uint64)
    nonzero = (bits & np.uint64(0x7FFFFFFFFFFFFFFF)) != 0
    if np.any(nonzero & ((bits >> np.uint64(63)) != 0)):
        raise ValueError("energy_reserve must be nonnegative, including subnormal stocks")
    if np.any(nonzero[population == 0]):
        raise ValueError("Empty cohorts cannot own energy_reserve")
    species = _species(context, shape[0])
    for item in species:
        metadata = context.species_state[item.identity]
        assert isinstance(metadata, Mapping)
        traits = metadata.get("traits")
        if not isinstance(traits, Mapping) or set(traits) != set(TRAITS):
            raise ValueError("Engineering requires the complete named numeric traits")
        budget = number(metadata["trait_budget"], "trait_budget")
        if not 0 <= budget <= 3 or sum(item.traits.values()) > budget + 1e-12:
            raise ValueError("Traits exceed the species budget")
        if metadata.get("status") not in STATUSES:
            raise ValueError("Unknown lifecycle status")
        if metadata["status"] == "Extinct" and np.any(population[item.slot]):
            raise ValueError("Extinct species cannot retain living population")
    unused = sorted(set(range(shape[0])) - {item.slot for item in species})
    if np.any(population[unused]) or np.any(nonzero[unused]):
        raise ValueError("Unused species rows must have zero stocks")
    biome = _integer(context, "biome", (width * height,))
    if np.any(biome > 6):
        raise ValueError("Unknown biome code")
    dt = number(context.environment_state["ecological_years_per_turn"], "ecological time step")
    if not 0 < dt <= 1:
        raise ValueError("Engineering requires ecological time step in (0,1] years")
    return _Inputs(
        dt,
        population,
        reserve,
        checked_field(context, "nutrients", low=0),
        checked_field(context, "soil_quality", low=0, high=1),
        checked_field(context, "habitat_complexity", low=0, high=1),
        biome,
        species,
    )


_ROUNDING_CAP = 2.0**-40


def _subnormal(values: FloatArray) -> np.typing.NDArray[np.bool_]:
    """Do not use float comparisons: DAZ treats nonzero subnormals as zero."""
    magnitude = values.view(np.uint64) & np.uint64(0x7FFFFFFFFFFFFFFF)
    return np.asarray(
        (magnitude != 0) & (magnitude < np.uint64(0x0010000000000000)), dtype=np.bool_
    )


def _rounding_budget(expected: FloatArray, stock: FloatArray) -> FloatArray:
    """At most one local ULP, capped independently of stock size.

    Two rounded subtractions need at most one ULP of the original reserve;
    nutrient addition/delta measurement needs at most one ULP of the new pool.
    For K positive transfers the summed allowance is <= 1e-9*sum(flux) +
    K*2**-40. It cannot grow with a large unrelated stock or species capacity.
    """
    return np.where(
        expected > 0,
        np.maximum(1e-9 * expected, np.minimum(np.spacing(stock), _ROUNDING_CAP)),
        0.0,
    )


def _check_transfer(
    actual: FloatArray, expected: FloatArray, stock: FloatArray, name: str
) -> float:
    residual = np.abs(actual - expected)
    if np.any((expected > _ROUNDING_CAP) & (actual <= 0)) or np.any(
        residual > _rounding_budget(expected, stock)
    ):
        raise ValueError(f"Engineering {name} ledger balance lost at supported precision")
    return float(np.max(residual, initial=0.0))


def _total(values: FloatArray) -> float:
    return math.fsum(values.flat)


class NicheConstructionStage(SimulationStage):
    contract = StageContract(
        "reference_niche",
        "1",
        dependencies=("reference_speciation",),
        reads=(
            "state.geometry",
            "state.species",
            "state.environment.ecological_years_per_turn",
            "arrays.population",
            "arrays.energy_reserve",
            "arrays.nutrients",
            "arrays.soil_quality",
            "arrays.biome",
            "arrays.habitat_complexity",
        ),
        writes=(
            "arrays.energy_reserve",
            "arrays.nutrients",
            "arrays.soil_quality",
            "arrays.habitat_complexity",
        ),
    )

    def execute(self, context: TurnContext) -> StageResult:
        super().validate_inputs(context)
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            try:
                return self._execute(context)
            except (FloatingPointError, OverflowError) as exc:
                raise ValueError("Engineering stocks/fluxes exceed supported precision") from exc

    def _execute(self, context: TurnContext) -> StageResult:
        data = _inputs(context)
        requested = np.zeros_like(data.reserve)
        for item in data.species:
            annual = 0.02 * data.dt * item.traits["engineering"] * item.mass
            requested[item.slot] = np.minimum(
                data.reserve[item.slot], annual * data.population[item.slot]
            )
        protected = _subnormal(data.reserve) | _subnormal(requested)
        deferred = _total(requested[protected])
        requested[protected] = 0
        # Copy retains bits, whereas even x - 0 flushes a subnormal result on
        # FTZ hosts. Work on the representable, nonzero requests only.
        reserve = data.reserve.copy()
        active = requested != 0
        np.subtract(data.reserve, requested, out=reserve, where=active)
        actual = np.zeros_like(reserve)
        np.subtract(data.reserve, reserve, out=actual, where=active)
        carbon_error = _check_transfer(actual, requested, data.reserve, "carbon")
        deferred += _total(requested[actual == 0])
        # Sum the already measured cohort differences, never subtract whole pools.
        work = np.array([math.fsum(column) for column in actual.T], dtype=np.float64)
        recycled = 0.02 * work
        nutrients = data.nutrients.copy()
        np.add(data.nutrients, recycled, out=nutrients, where=recycled != 0)
        nutrient_delta = np.zeros_like(nutrients)
        np.subtract(nutrients, data.nutrients, out=nutrient_delta, where=recycled != 0)
        unregistered = (work > 0) & (nutrient_delta == 0)
        if np.any(unregistered & (work > _ROUNDING_CAP)):
            raise ValueError("Engineering nutrient ledger balance lost at supported precision")
        # Roll back the entire local transaction, including its habitat benefit.
        # This also handles multiplication underflow without destroying reserve.
        deferred += _total(work[unregistered])
        reserve[:, unregistered] = data.reserve[:, unregistered]
        work[unregistered] = recycled[unregistered] = 0
        nutrient_error = _check_transfer(nutrient_delta, recycled, nutrients, "nutrient")
        land = data.biome >= 2
        soil = data.soil.copy()
        decayed = data.soil[land] * math.exp(-0.005 * data.dt)
        soil[land] = decayed + (1 - decayed) * (-np.expm1(-work[land] / 100))
        structure = np.zeros_like(data.structure)
        decayed = data.structure[~land] * math.exp(-0.05 * data.dt)
        structure[~land] = decayed + (1 - decayed) * (-np.expm1(-work[~land] / 100))
        finite_outputs(reserve, nutrients, soil, structure)
        if (
            np.any(reserve < 0)
            or np.any((soil < 0) | (soil > 1))
            or np.any((structure < 0) | (structure > 1))
        ):
            raise ValueError("Engineering output bounds violated")
        soil_delta, structure_delta = soil - data.soil, structure - data.structure
        change = np.maximum(np.abs(soil_delta), np.abs(structure_delta))
        metrics: dict[str, JsonValue] = {
            "ecological_years": data.dt,
            "carbon_respired": _total(work),
            "nutrient_recycled": _total(nutrient_delta),
            "work_by_land": _total(work[land]),
            "work_by_water": _total(work[~land]),
            "soil_quality_delta": _total(soil_delta),
            "habitat_complexity_delta": _total(structure_delta),
            "max_abs_habitat_change": float(np.max(change)),
            "carbon_ledger_max_abs_residual": carbon_error,
            "nutrient_ledger_max_abs_residual": nutrient_error,
            "nutrient_ledger_abs_residual": _total(np.abs(nutrient_delta - recycled)),
            "nutrient_ledger_roundoff_budget": _total(_rounding_budget(recycled, nutrients)),
            "ledger_roundoff_cap_per_transfer": _ROUNDING_CAP,
            "carbon_work_deferred": deferred,
            "subnormal_cohorts_preserved": int(np.count_nonzero(protected)),
        }
        events: tuple[WorldEvent, ...] = ()
        if np.max(change) >= 0.005:
            top = sorted(np.flatnonzero(change), key=lambda tile: (-change[tile], tile))[:8]
            tiles: list[JsonValue] = [
                {
                    "tile": int(tile),
                    "medium": "land" if land[tile] else "water",
                    "work_carbon": float(work[tile]),
                    "soil_quality_delta": float(soil_delta[tile]),
                    "habitat_complexity_delta": float(structure_delta[tile]),
                }
                for tile in top
            ]
            events = (
                event(
                    context,
                    self.contract.name,
                    "NicheConstructed",
                    payload={
                        "reason": "reserve_funded_engineering_and_passive_habitat_decay",
                        **metrics,
                        "top_tiles": tuple(tiles),
                    },
                ),
            )
        return result(
            context,
            self.contract.name,
            arrays={
                "energy_reserve": reserve,
                "nutrients": nutrients,
                "soil_quality": soil,
                "habitat_complexity": structure,
            },
            metrics=metrics,
            events=events,
        )

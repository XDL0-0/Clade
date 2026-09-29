"""Conservative resource flows for the new ``ecology-reference-v1`` model.

Carbon is an abstract mass unit; body_mass is carbon per individual, reserves,
leaves and detritus are carbon stocks per tile. Structural producer carbon gates
photosynthesis but is never consumed here. NPP is this turn's carbon flux, not a
stock. Mineral nutrients bind at 0.02 units/carbon; land photosynthesis consumes
0.15 mm/carbon. Aquatic water is an unmodelled external reservoir. This simple
reference model is not numerically equivalent to any legacy stage.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

import numpy as np

from ..context import TurnContext
from ..contracts import SimulationStage, StageContract, StageResult
from ..values import JsonValue
from .common import FloatArray, IntArray, geometry, number, result

MODEL_VERSION: Final = "ecology-reference-v1"
NUTRIENT_PER_CARBON: Final = 0.02
WATER_PER_CARBON: Final = 0.15
_FLOAT_TILES = (
    "temperature",
    "soil_water",
    "humidity",
    "plant_biomass",
    "nutrients",
    "detritus",
    "npp",
)
_READS = (
    "state.geometry",
    "state.environment.ecological_years_per_turn",
    "state.species",
    "arrays.population",
    "arrays.energy_reserve",
    "arrays.biome",
    *(f"arrays.{name}" for name in _FLOAT_TILES),
)


@dataclass(frozen=True)
class _Inputs:
    width: int
    height: int
    dt: float
    population: IntArray
    reserve: FloatArray
    biome: IntArray
    tile: Mapping[str, FloatArray]
    producer: FloatArray
    decomposer: FloatArray


def _inputs(context: TurnContext) -> _Inputs:
    width, height = geometry(context)
    tiles = width * height
    dt = number(context.environment_state["ecological_years_per_turn"], "ecological time step")
    if dt < 0:
        raise ValueError("ecological time step must be non-negative")
    population_raw = context.snapshot.arrays["population"].numpy()
    if population_raw.dtype != np.dtype("int64") or population_raw.ndim != 2:
        raise ValueError("population must be an int64 species-by-tile matrix")
    rows = population_raw.shape[0]
    population = np.array(population_raw, dtype=np.int64, copy=True)
    if population.shape[1] != tiles or np.any(population < 0):
        raise ValueError("population shape or non-negative stock constraint violated")
    reserve_raw = context.snapshot.arrays["energy_reserve"].numpy()
    if reserve_raw.dtype != np.dtype("float64") or reserve_raw.shape != population.shape:
        raise ValueError("energy_reserve must be a float64 population-shaped matrix")
    reserve = np.array(reserve_raw, dtype=np.float64, copy=True)
    if not np.isfinite(reserve).all() or np.any(reserve < 0):
        raise ValueError("energy_reserve must contain finite non-negative stocks")
    tile: dict[str, FloatArray] = {}
    for name in _FLOAT_TILES:
        raw = context.snapshot.arrays[name].numpy()
        if raw.dtype != np.dtype("float64") or raw.shape != (tiles,):
            raise ValueError(f"{name} must be a float64 tile vector")
        value = np.array(raw, dtype=np.float64, copy=True)
        if not np.isfinite(value).all() or (name != "temperature" and np.any(value < 0)):
            raise ValueError(f"{name} must contain finite, non-negative stocks/factors")
        tile[name] = value
    if np.any(tile["humidity"] > 1):
        raise ValueError("humidity must lie in [0, 1]")
    biome_raw = context.snapshot.arrays["biome"].numpy()
    if biome_raw.dtype != np.dtype("int64") or biome_raw.shape != (tiles,):
        raise ValueError("biome must be an int64 tile vector")
    biome = np.array(biome_raw, dtype=np.int64, copy=True)
    if np.any((biome < 0) | (biome > 6)):
        raise ValueError("biome code must lie in [0, 6]")
    producer = np.zeros(tiles, dtype=np.float64)
    decomposer = np.zeros(population.shape, dtype=np.float64)
    slots: set[int] = set()
    for phenotype in context.species_state.values():
        if not isinstance(phenotype, Mapping):
            raise ValueError("species phenotype must be a mapping")
        slot, role = phenotype["slot"], phenotype["role"]
        if type(slot) is not int or slot < 0 or slot >= rows or slot in slots:
            raise ValueError("species slots must be unique valid population rows")
        if role not in ("producer", "herbivore", "carnivore", "decomposer"):
            raise ValueError("unknown species role")
        mass = number(phenotype["body_mass"], "body_mass")
        if mass <= 0:
            raise ValueError("body_mass must be positive")
        slots.add(slot)
        if role == "producer":
            producer += population[slot] * mass
        elif role == "decomposer":
            decomposer[slot] = population[slot] * mass
    unused = sorted(set(range(rows)) - slots)
    if np.any(population[unused] != 0) or np.any(reserve[unused] != 0):
        raise ValueError("unused species rows must have zero population and energy_reserve")
    if not np.isfinite(producer).all() or not np.isfinite(decomposer).all():
        raise ValueError("structural biomass must be finite")
    return _Inputs(width, height, dt, population, reserve, biome, tile, producer, decomposer)


def _thermal(temperature: FloatArray) -> FloatArray:
    return np.asarray(np.exp(-np.square((temperature - 22.0) / 18.0)), dtype=np.float64)


def _light(data: _Inputs, turn: int) -> FloatArray:
    """Row-centre latitude and a fixed 12-turn year, northern summer at turn 4.

    Cosine solar incidence is zero when the sun is below the model's horizon;
    this is a physical non-negative irradiance bound, not an input repair.
    """
    latitude = np.repeat(
        np.pi / 2 - np.pi * (np.arange(data.height) + 0.5) / data.height, data.width
    )
    declination = np.deg2rad(23.44) * np.sin(2 * np.pi * ((turn - 1) % 12) / 12)
    light: FloatArray = np.maximum(0.0, np.cos(latitude - declination))
    return light


def _max_residual(array: FloatArray) -> float:
    return float(np.max(np.abs(array), initial=0.0))


def _ledger(
    data: _Inputs,
    leaf: FloatArray,
    detritus: FloatArray,
    reserve: FloatArray,
    nutrients: FloatArray,
    soil_water: FloatArray,
    fixed: FloatArray,
    respired: FloatArray,
    bound: FloatArray,
    returned: FloatArray,
    consumed: FloatArray,
) -> dict[str, JsonValue]:
    before = data.tile["plant_biomass"] + data.tile["detritus"] + data.reserve.sum(axis=0)
    after = leaf + detritus + reserve.sum(axis=0)
    nitrogen_before = data.tile["nutrients"] + NUTRIENT_PER_CARBON * before
    nitrogen_after = nutrients + NUTRIENT_PER_CARBON * after
    # Form differences before adding large pools. Otherwise cancellation can
    # report a perfect balance while small NPP disappears below a stock's ULP.
    carbon_change = (
        (leaf - data.tile["plant_biomass"])
        + (detritus - data.tile["detritus"])
        + (reserve - data.reserve).sum(axis=0)
    )
    carbon_residual = carbon_change - fixed + respired
    nutrient_residual = nutrients - data.tile["nutrients"] + 0.02 * carbon_change
    water_residual = soil_water - data.tile["soil_water"] + consumed
    scale = (
        np.abs(leaf - data.tile["plant_biomass"])
        + np.abs(detritus - data.tile["detritus"])
        + np.abs(reserve - data.reserve).sum(axis=0)
        + fixed
        + respired
    )
    if (
        np.any(np.abs(carbon_residual) > 1e-9 + 1e-12 * scale)
        or np.any(np.abs(nutrient_residual) > 1e-9 + 1e-12 * 0.02 * scale)
        or np.any(np.abs(water_residual) > 1e-9 + 1e-12 * consumed)
    ):
        raise ValueError("Resource ledger balance lost at the supported numerical precision")
    return {
        "model_version": MODEL_VERSION,
        "ecological_years": data.dt,
        "carbon_fixed": float(fixed.sum()),
        "carbon_respired": float(respired.sum()),
        "nutrients_bound": float(bound.sum()),
        "nutrients_returned": float(returned.sum()),
        "water_consumed_mm": float(consumed.sum()),
        "carbon_ledger": {
            "pool": "leaf + detritus + all energy_reserve; excludes unchanged structure",
            "before": float(before.sum()),
            "after": float(after.sum()),
            "fixed": float(fixed.sum()),
            "respired": float(respired.sum()),
            "max_abs_residual": _max_residual(carbon_residual),
        },
        "nutrient_ledger": {
            "stoichiometry_nutrient_per_carbon": NUTRIENT_PER_CARBON,
            "mineral_before": float(data.tile["nutrients"].sum()),
            "mineral_after": float(nutrients.sum()),
            "bound": float(bound.sum()),
            "returned": float(returned.sum()),
            "total_before": float(nitrogen_before.sum()),
            "total_after": float(nitrogen_after.sum()),
            "max_abs_residual": _max_residual(nutrient_residual),
        },
        "water_ledger": {
            "scope": "soil water; aquatic supply is external and unconstrained",
            "soil_before": float(data.tile["soil_water"].sum()),
            "soil_after": float(soil_water.sum()),
            "consumed": float(consumed.sum()),
            "max_abs_residual": _max_residual(water_residual),
        },
    }


class PrimaryProductivityStage(SimulationStage):
    contract = StageContract(
        "reference_productivity",
        MODEL_VERSION,
        dependencies=("reference_biome",),
        reads=_READS,
        writes=("arrays.plant_biomass", "arrays.nutrients", "arrays.soil_water", "arrays.npp"),
    )

    def execute(self, context: TurnContext) -> StageResult:
        super().validate_inputs(context)
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            data = _inputs(context)
            tile = data.tile
            land = data.biome >= 2
            canopy = -np.expm1(-data.producer / 50.0)
            water = np.where(land, tile["soil_water"] / (tile["soil_water"] + 40.0), 1.0)
            nutrient = tile["nutrients"] / (tile["nutrients"] + 5.0)
            potential = (
                data.dt
                * 300.0
                * canopy
                * _light(data, context.turn_id)
                * _thermal(tile["temperature"])
                * water
                * nutrient
            )
            npp = np.minimum(potential, tile["nutrients"] / NUTRIENT_PER_CARBON)
            npp[land] = np.minimum(npp[land], tile["soil_water"][land] / WATER_PER_CARBON)
            # Minima also enforce supply caps against a multiply/divide rounding ULP.
            bound = np.minimum(tile["nutrients"], NUTRIENT_PER_CARBON * npp)
            consumed = np.where(land, np.minimum(tile["soil_water"], WATER_PER_CARBON * npp), 0.0)
            leaf = tile["plant_biomass"] + npp
            nutrients = tile["nutrients"] - bound
            soil = tile["soil_water"] - consumed
            zero = np.zeros_like(npp)
            metrics = _ledger(
                data,
                leaf,
                tile["detritus"],
                data.reserve,
                nutrients,
                soil,
                npp,
                zero,
                bound,
                zero,
                consumed,
            )
            metrics["potential_npp"] = float(potential.sum())
            return result(
                context,
                self.contract.name,
                arrays={
                    "plant_biomass": leaf,
                    "nutrients": nutrients,
                    "soil_water": soil,
                    "npp": npp,
                },
                metrics=metrics,
            )


class ResourceRegenerationStage(SimulationStage):
    contract = StageContract(
        "reference_regeneration",
        MODEL_VERSION,
        dependencies=("reference_productivity",),
        reads=_READS,
        writes=(
            "arrays.plant_biomass",
            "arrays.detritus",
            "arrays.energy_reserve",
            "arrays.nutrients",
        ),
    )

    def execute(self, context: TurnContext) -> StageResult:
        super().validate_inputs(context)
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            data = _inputs(context)
            tile = data.tile
            litter = np.minimum(tile["plant_biomass"], data.dt * 0.3 * tile["plant_biomass"])
            leaf = tile["plant_biomass"] - litter
            available = tile["detritus"] + litter
            biomass = data.decomposer.sum(axis=0)
            rate = 0.1 + 0.4 * biomass / (biomass + 5.0)
            processed = np.minimum(
                available,
                data.dt * available * rate * _thermal(tile["temperature"]) * tile["humidity"],
            )
            assimilated = np.where(biomass > 0, processed * 0.6, 0.0)
            shares = np.divide(
                data.decomposer,
                biomass,
                out=np.zeros_like(data.decomposer),
                where=biomass > 0,
            )
            reserve = data.reserve + shares * assimilated
            respired = processed - assimilated
            returned = respired * NUTRIENT_PER_CARBON
            detritus = available - processed
            nutrients = tile["nutrients"] + returned
            zero = np.zeros_like(processed)
            metrics = _ledger(
                data,
                leaf,
                detritus,
                reserve,
                nutrients,
                tile["soil_water"],
                zero,
                respired,
                zero,
                returned,
                zero,
            )
            metrics.update(
                {
                    "litter_carbon": float(litter.sum()),
                    "processed_carbon": float(processed.sum()),
                    "assimilated_carbon": float(assimilated.sum()),
                }
            )
            return result(
                context,
                self.contract.name,
                arrays={
                    "plant_biomass": leaf,
                    "detritus": detritus,
                    "energy_reserve": reserve,
                    "nutrients": nutrients,
                },
                metrics=metrics,
            )

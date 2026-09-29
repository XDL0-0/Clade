"""Habitat, finite-resource capacity, competition and feeding for the new model.

These explicit reference equations are not a legacy numerical-parity adapter.
Scratch arrays have reserved species rows and are replaced each turn. Population
and phenotypes remain read-only; demography applies predation_deaths exactly once.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from ..context import TurnContext
from ..contracts import SimulationStage, StageContract, StageResult
from .common import FloatArray, IntArray, geometry, number, result
from .world import FEEDING_VERSION

MODEL_VERSION = "ecology-reference-v1"
TRAITS = ("armor", "speed", "attack", "cooperation", "toxin", "detox", "engineering")
SCRATCH = (
    "suitability",
    "carrying_capacity",
    "competition",
    "temperature_pressure",
    "water_pressure",
    "food_pressure",
    "predation_pressure",
)
TILES = ("temperature", "soil_water", "humidity", "plant_biomass", "detritus")
_READS = (
    "state.geometry",
    "state.environment.ecological_years_per_turn",
    "state.species",
    "state.food_web",
    "arrays.population",
    "arrays.energy_reserve",
    "arrays.biome",
    "arrays.predation_deaths",
    *(f"arrays.{name}" for name in (*SCRATCH, *TILES)),
)


@dataclass(frozen=True)
class Species:
    identity: str
    slot: int
    role: str
    mass: float
    habitat: str
    optimum: float
    width: float
    water_need: float
    fertility: float
    traits: Mapping[str, float]


@dataclass(frozen=True)
class EcologyInputs:
    dt: float
    population: IntArray
    reserve: FloatArray
    biome: IntArray
    arrays: Mapping[str, FloatArray]
    species: tuple[Species, ...]
    edges: tuple[tuple[int, int, float], ...]


def _float(context: TurnContext, name: str, shape: tuple[int, ...]) -> FloatArray:
    source = context.snapshot.arrays[name].numpy()
    if source.dtype != np.dtype("float64") or source.shape != shape:
        raise ValueError(f"{name} must be float64 with shape {shape}")
    array = np.array(source, dtype=np.float64, copy=True)
    if not np.isfinite(array).all() or (name != "temperature" and np.any(array < 0)):
        raise ValueError(f"{name} must be finite and non-negative")
    if name in (
        *SCRATCH[:1],
        "humidity",
        "temperature_pressure",
        "water_pressure",
        "food_pressure",
        "predation_pressure",
    ) and np.any(array > 1):
        raise ValueError(f"{name} must be in [0,1]")
    return array


def _integer(context: TurnContext, name: str, shape: tuple[int, ...]) -> IntArray:
    source = context.snapshot.arrays[name].numpy()
    if source.dtype != np.dtype("int64") or source.shape != shape:
        raise ValueError(f"{name} must be int64 with shape {shape}")
    array = np.array(source, dtype=np.int64, copy=True)
    if np.any(array < 0):
        raise ValueError(f"{name} must be non-negative")
    return array


def _species(context: TurnContext, rows: int) -> tuple[Species, ...]:
    species: list[Species] = []
    used: set[int] = set()
    for identity, metadata in context.species_state.items():
        if not isinstance(metadata, Mapping):
            raise ValueError("species metadata must be a mapping")
        slot, role, habitat = metadata["slot"], metadata["role"], metadata["habitat"]
        if type(slot) is not int or not 0 <= slot < rows or slot in used:
            raise ValueError("species slots must be unique valid rows")
        if role not in ("producer", "herbivore", "carnivore", "decomposer"):
            raise ValueError("unknown species role")
        if habitat not in ("land", "water", "amphibious"):
            raise ValueError("unknown species habitat")
        assert isinstance(role, str) and isinstance(habitat, str)
        mass = number(metadata["body_mass"], "body_mass")
        optimum = number(metadata["thermal_optimum"], "thermal_optimum")
        width = number(metadata["thermal_width"], "thermal_width")
        water_need = number(metadata["water_need"], "water_need")
        fertility = number(metadata["fertility"], "fertility")
        if mass <= 0 or width <= 0 or not 0 <= water_need <= 10 or not 0 <= fertility <= 10:
            raise ValueError("invalid species physiological parameters")
        traits = metadata.get("traits", {})
        if not isinstance(traits, Mapping) or set(traits) - set(TRAITS):
            raise ValueError("unknown or invalid species traits")
        parsed = {key: number(traits.get(key, 0.0), key) for key in TRAITS}
        if (
            any(not 0 <= value <= 1 for value in parsed.values())
            or sum(parsed.values()) > 3 + 1e-12
        ):
            raise ValueError("traits must be bounded and share a budget of 3")
        used.add(slot)
        species.append(
            Species(
                identity, slot, role, mass, habitat, optimum, width, water_need, fertility, parsed
            )
        )
    return tuple(sorted(species, key=lambda item: item.slot))


def _inputs(context: TurnContext) -> EcologyInputs:
    width, height = geometry(context)
    tiles = width * height
    shape = context.snapshot.arrays["population"].shape
    if len(shape) != 2 or shape[1] != tiles:
        raise ValueError("population must have species-by-tile shape")
    population = _integer(context, "population", shape)
    reserve = _float(context, "energy_reserve", shape)
    biome = _integer(context, "biome", (tiles,))
    deaths = _integer(context, "predation_deaths", shape)
    if np.any(biome > 6):
        raise ValueError("unknown biome")
    arrays = {name: _float(context, name, shape) for name in SCRATCH}
    arrays.update({name: _float(context, name, (tiles,)) for name in TILES})
    dt = number(context.environment_state["ecological_years_per_turn"], "ecological time step")
    if dt < 0:
        raise ValueError("ecological time step must be non-negative")
    species = _species(context, shape[0])
    unused = sorted(set(range(shape[0])) - {item.slot for item in species})
    if any(
        np.any(array[unused] != 0)
        for array in (population, reserve, deaths, *(arrays[name] for name in SCRATCH))
    ):
        raise ValueError("unused species rows must contain zero stocks and scratch values")
    by_id = {item.identity: item for item in species}
    raw_edges = context.food_web_state.get("edges", ())
    if not isinstance(raw_edges, tuple):
        raise ValueError("food_web edges must be a sequence")
    edges: list[tuple[int, int, float]] = []
    seen: set[tuple[int, int]] = set()
    for edge in raw_edges:
        if not isinstance(edge, Mapping):
            raise ValueError("food edge must be a mapping")
        predator_id, prey_id = edge.get("predator"), edge.get("prey")
        if not isinstance(predator_id, str) or not isinstance(prey_id, str):
            raise ValueError("food edge requires species IDs")
        if predator_id not in by_id or prey_id not in by_id:
            raise ValueError("food edge references unknown species")
        predator, prey = by_id[predator_id], by_id[prey_id]
        if (predator.role, prey.role) not in (
            ("herbivore", "producer"),
            ("carnivore", "herbivore"),
        ):
            raise ValueError("food edge must be herbivore->producer or carnivore->herbivore")
        pair = (predator.slot, prey.slot)
        preference = number(edge.get("preference", 1.0), "food preference")
        if pair in seen or preference < 0:
            raise ValueError("duplicate food edge or negative preference")
        seen.add(pair)
        edges.append((*pair, preference))
    return EcologyInputs(dt, population, reserve, biome, arrays, species, tuple(sorted(edges)))


def habitat_overlap(first: Species, second: Species) -> float:
    if first.habitat == second.habitat:
        return 1.0
    return 0.75 if "amphibious" in (first.habitat, second.habitat) else 0.0


def niche_overlap(first: Species, second: Species) -> float:
    """Symmetric trait/thermal/habitat overlap with explicit shared-resource roles."""
    role = (
        1.0
        if first.role == second.role
        else (0.5 if {first.role, second.role} == {"producer", "herbivore"} else 0.0)
    )
    thermal = np.exp(-((first.optimum - second.optimum) ** 2) / (first.width**2 + second.width**2))
    distance = sum(abs(first.traits[key] - second.traits[key]) for key in TRAITS) / len(TRAITS)
    return float(role * habitat_overlap(first, second) * thermal * (1 - 0.25 * distance))


class HabitatSuitabilityStage(SimulationStage):
    contract = StageContract(
        "reference_suitability",
        MODEL_VERSION,
        dependencies=("reference_regeneration",),
        reads=_READS,
        writes=("arrays.suitability", "arrays.temperature_pressure", "arrays.water_pressure"),
    )

    def execute(self, context: TurnContext) -> StageResult:
        super().validate_inputs(context)
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            data = _inputs(context)
            suitability = np.zeros_like(data.reserve)
            temperature_pressure = np.zeros_like(data.reserve)
            water_pressure = np.zeros_like(data.reserve)
            land = data.biome >= 2
            for item in data.species:
                thermal = np.exp(
                    -np.square(
                        (data.arrays["temperature"] - item.optimum)
                        / (item.width * (1 + 0.25 * item.traits["armor"]))
                    )
                )
                need = 40 * item.water_need * (1 - 0.5 * item.traits["engineering"])
                soil = data.arrays["soil_water"]
                hydration = np.divide(
                    soil, soil + need, out=np.ones_like(soil), where=soil + need > 0
                )
                hydration = np.where(land, hydration, 1.0)
                habitat = (
                    np.ones_like(land)
                    if item.habitat == "amphibious"
                    else (land if item.habitat == "land" else ~land)
                )
                suitability[item.slot] = habitat * thermal * hydration
                temperature_pressure[item.slot] = 1 - thermal
                water_pressure[item.slot] = 1 - hydration
            return result(
                context,
                self.contract.name,
                arrays={
                    "suitability": suitability,
                    "temperature_pressure": temperature_pressure,
                    "water_pressure": water_pressure,
                },
                metrics={"suitability_total": float(suitability.sum())},
            )


class CarryingCapacityStage(SimulationStage):
    contract = StageContract(
        "reference_capacity",
        MODEL_VERSION,
        dependencies=("reference_suitability",),
        reads=_READS,
        writes=("arrays.carrying_capacity",),
    )

    def execute(self, context: TurnContext) -> StageResult:
        super().validate_inputs(context)
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            data = _inputs(context)
            capacity = np.zeros_like(data.reserve)
            by_slot = {item.slot: item for item in data.species}
            for item in data.species:
                if item.role in ("producer", "herbivore"):
                    edible = data.arrays["plant_biomass"] * (1 if item.role == "producer" else 0.5)
                elif item.role == "decomposer":
                    edible = 0.6 * data.arrays["detritus"]
                else:
                    edible = np.zeros(data.population.shape[1], dtype=np.float64)
                    for predator, prey, preference in data.edges:
                        if predator == item.slot:
                            target = by_slot[prey]
                            available = np.where(
                                data.population[prey] > 0,
                                data.population[prey] * target.mass + data.reserve[prey],
                                0.0,
                            )
                            edible += available * (
                                0.7 * min(1.0, preference) * habitat_overlap(item, target)
                            )
                capacity[item.slot] = (
                    edible
                    / (item.mass * (0.25 + item.fertility))
                    * (
                        data.arrays["suitability"][item.slot]
                        * (1 - data.arrays["water_pressure"][item.slot])
                    )
                )
            return result(
                context,
                self.contract.name,
                arrays={"carrying_capacity": capacity},
                metrics={"capacity_total": float(capacity.sum())},
            )


class CompetitionStage(SimulationStage):
    contract = StageContract(
        "reference_competition",
        MODEL_VERSION,
        dependencies=("reference_capacity",),
        reads=_READS,
        writes=("arrays.competition",),
    )

    def execute(self, context: TurnContext) -> StageResult:
        super().validate_inputs(context)
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            data = _inputs(context)
            intra = np.zeros_like(data.reserve)
            inter = np.zeros_like(data.reserve)
            for first in data.species:
                denominator = 1 + data.arrays["carrying_capacity"][first.slot]
                intra[first.slot] = data.population[first.slot] / denominator
                for second in data.species:
                    if first.slot != second.slot:
                        inter[first.slot] += (
                            niche_overlap(first, second)
                            * data.population[second.slot]
                            / denominator
                        )
            return result(
                context,
                self.contract.name,
                arrays={"competition": intra + inter},
                metrics={
                    "intraspecific_pressure_total": float(intra.sum()),
                    "interspecific_pressure_total": float(inter.sum()),
                },
            )


class FeedingStage(SimulationStage):
    contract = StageContract(
        "reference_feeding",
        FEEDING_VERSION,
        dependencies=("reference_competition",),
        reads=_READS,
        writes=(
            "arrays.plant_biomass",
            "arrays.detritus",
            "arrays.energy_reserve",
            "arrays.food_pressure",
            "arrays.predation_pressure",
            "arrays.predation_deaths",
        ),
    )

    def execute(self, context: TurnContext) -> StageResult:
        from .feeding import run_feeding

        super().validate_inputs(context)
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            return run_feeding(context, _inputs(context), self.contract.name)

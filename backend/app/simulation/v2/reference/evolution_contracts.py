"""Bounded deme phenotype proposals and serializable, factual evolution traces.

Deme means approximate genetic composition; they are not individual genotypes.
Projection clips each coordinate then radially contracts to the shared budget.
The existing mortality equation charges .05 * sum(traits) * body_mass per year
on the next turn; these pure proposals never spend carbon a second time.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from ..context import TurnContext
from ..values import JsonValue, natural
from .common import FloatArray, IntArray, geometry, number
from .ecology import MODEL_VERSION, Species, _species
from .selection import PRESSURE_AXES
from .world import TRAITS

READS = (
    "state.geometry",
    "state.species",
    "state.environment.ecological_years_per_turn",
    *(
        f"arrays.{name}"
        for name in (
            "population",
            "deme_traits",
            "trait_proposals",
            "gene_population",
            "connectivity",
            "gene_connectivity",
            "isolation_age",
            "selection_pressure",
            "fitness_gradients",
        )
    ),
)


@dataclass(frozen=True, slots=True)
class EvolutionBudget:
    limit: float = 3.0

    def __post_init__(self) -> None:
        if not 0 <= number(self.limit, "trait budget") <= 3:
            raise ValueError("Trait budget must lie in [0,3]")

    def validate(self, values: FloatArray) -> None:
        if values.shape[-1:] != (len(TRAITS),) or not np.isfinite(values).all():
            raise ValueError("Traits need seven finite coordinates")
        if np.any((values < 0) | (values > 1)) or np.any(values.sum(axis=-1) > self.limit + 1e-12):
            raise ValueError("Traits exceed coordinate bounds or shared budget")

    def project(self, values: FloatArray) -> FloatArray:
        if values.shape != (len(TRAITS),) or not np.isfinite(values).all():
            raise ValueError("Projection needs one finite seven-trait vector")
        bounded = np.clip(values, 0.0, 1.0)
        total = float(bounded.sum())
        if total > self.limit:
            bounded *= self.limit / total
            excess = float(bounded.sum()) - self.limit
            if excess > 0:
                bounded[int(np.argmax(bounded))] -= excess
        self.validate(bounded)
        return bounded

    def annual_maintenance(self, values: FloatArray, mass: float) -> float:
        self.validate(values)
        if values.ndim != 1 or not 0 < number(mass, "body mass"):
            raise ValueError("Maintenance needs one phenotype and positive mass")
        return 0.05 * float(values.sum()) * mass


@dataclass(frozen=True)
class EvolutionInputs:
    population: IntArray
    previous_population: IntArray
    deme: FloatArray
    proposals: FloatArray
    connectivity: IntArray
    previous_connectivity: IntArray
    isolation_age: IntArray
    pressure: FloatArray
    gradients: FloatArray
    species: tuple[Species, ...]
    budgets: Mapping[int, EvolutionBudget]
    dt: float


def _integer(context: TurnContext, name: str, shape: tuple[int, ...], low: int = 0) -> IntArray:
    raw = context.snapshot.arrays[name].numpy()
    if raw.dtype != np.dtype("int64") or raw.shape != shape:
        raise ValueError(f"{name} must match int64 shape {shape}")
    value = np.array(raw, dtype=np.int64, copy=True)
    if np.any(value < low):
        raise ValueError(f"{name} contains invalid negative values")
    return value


def _float(context: TurnContext, name: str, shape: tuple[int, ...]) -> FloatArray:
    raw = context.snapshot.arrays[name].numpy()
    if raw.dtype != np.dtype("float64") or raw.shape != shape:
        raise ValueError(f"{name} must match float64 shape {shape}")
    value = np.array(raw, dtype=np.float64, copy=True)
    if not np.isfinite(value).all():
        raise ValueError(f"{name} must be finite")
    return value


def evolution_inputs(context: TurnContext) -> EvolutionInputs:
    width, height = geometry(context)
    shape = context.snapshot.arrays["population"].shape
    if len(shape) != 2 or shape[1] != width * height:
        raise ValueError("Evolution population must have species-by-tile axes")
    population = _integer(context, "population", shape)
    previous = _integer(context, "gene_population", shape)
    deme = _float(context, "deme_traits", (*shape, len(TRAITS)))
    proposals = _float(context, "trait_proposals", deme.shape)
    connectivity = _integer(context, "connectivity", shape, -1)
    old_connectivity = _integer(context, "gene_connectivity", shape, -1)
    age = _integer(context, "isolation_age", shape)
    if np.any(connectivity >= shape[1]) or np.any(old_connectivity >= shape[1]):
        raise ValueError("Connectivity labels must be tile IDs or -1")
    if np.any(age > context.turn_id):
        raise ValueError("Isolation age cannot exceed the current turn")
    if np.any((old_connectivity < 0) & (age > 0)):
        raise ValueError("Blocked gene connectivity cannot retain isolation age")
    pressure = _float(context, "selection_pressure", (shape[0], len(PRESSURE_AXES)))
    gradients = _float(context, "fitness_gradients", (shape[0], len(TRAITS)))
    if np.any((pressure < 0) | (pressure > 1)):
        raise ValueError("Selection pressure must lie in [0,1]")
    species = _species(context, shape[0])
    budgets: dict[int, EvolutionBudget] = {}
    for item in species:
        metadata = context.species_state[item.identity]
        assert isinstance(metadata, Mapping)
        traits = metadata["traits"]
        if not isinstance(traits, Mapping) or set(traits) != set(TRAITS):
            raise ValueError("Evolution metadata requires the complete named trait axes")
        budget = EvolutionBudget(number(metadata["trait_budget"], "trait budget"))
        budget.validate(np.array([item.traits[name] for name in TRAITS]))
        budget.validate(deme[item.slot])
        budget.validate(proposals[item.slot])
        if metadata["status"] == "Extinct" and np.any(population[item.slot]):
            raise ValueError("Extinct species cannot adapt living individuals")
        budgets[item.slot] = budget
    unused = sorted(set(range(shape[0])) - set(budgets))
    if any(
        np.any(value[unused])
        for value in (
            population,
            previous,
            deme,
            proposals,
            age,
            pressure,
            gradients,
        )
    ) or any(np.any(value[unused] != -1) for value in (connectivity, old_connectivity)):
        raise ValueError("Unused evolution rows must be zero, with labels -1")
    dt = number(context.environment_state["ecological_years_per_turn"], "ecological time step")
    if not 0 < dt <= 1:
        raise ValueError("Evolution requires ecological time step in (0,1] years")
    return EvolutionInputs(
        population,
        previous,
        deme,
        proposals,
        connectivity,
        old_connectivity,
        age,
        pressure,
        gradients,
        species,
        budgets,
        dt,
    )


def weighted_traits(values: FloatArray, population: IntArray) -> FloatArray:
    total = int(population.sum(dtype=object))
    if total <= 0:
        raise ValueError("A weighted phenotype requires living individuals")
    weights = population.astype(np.float64) / total
    return np.asarray((values * weights[:, None]).sum(axis=0), dtype=np.float64)


def founder_mask(
    data: EvolutionInputs, slot: int
) -> np.ndarray[tuple[int, ...], np.dtype[np.bool_]]:
    return np.asarray(
        (data.population[slot] > 0) & (data.previous_population[slot] == 0), dtype=np.bool_
    )


def bottleneck_mask(
    data: EvolutionInputs, slot: int
) -> np.ndarray[tuple[int, ...], np.dtype[np.bool_]]:
    # Object arithmetic preserves the strict quarter-population threshold at int64 limits.
    return np.asarray(
        (data.population[slot] > 0)
        & (data.population[slot].astype(object) * 4 < data.previous_population[slot]),
        dtype=np.bool_,
    )


@dataclass(frozen=True, slots=True)
class EvolutionTrace:
    species: str
    turn: int
    trait_changes: tuple[float, ...]
    pressure: tuple[float, ...]
    fitness_gain: float
    tradeoffs: tuple[tuple[str, float], ...]
    reasons: tuple[tuple[str, float], ...]
    max_deme_change: float

    def __post_init__(self) -> None:
        natural(self.turn, "trace turn")
        for name in ("trait_changes", "pressure"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        for name in ("tradeoffs", "reasons"):
            object.__setattr__(
                self, name, tuple((key, value) for key, value in getattr(self, name))
            )
        if not self.species or len(self.trait_changes) != len(TRAITS) or len(self.pressure) != 7:
            raise ValueError("Evolution trace has invalid species or axes")
        values = (
            *self.trait_changes,
            *self.pressure,
            self.fitness_gain,
            self.max_deme_change,
            *(v for _, v in self.tradeoffs),
            *(v for _, v in self.reasons),
        )
        if any(not math.isfinite(value) for value in values):
            raise ValueError("Evolution trace must contain finite quantitative evidence")
        if any(not 0 <= value <= 1 for value in self.pressure) or self.max_deme_change < 0:
            raise ValueError("Evolution trace pressure or change is invalid")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "species": self.species,
            "turn": self.turn,
            "trait_changes": dict(zip(TRAITS, self.trait_changes, strict=True)),
            "pressure": dict(zip(PRESSURE_AXES, self.pressure, strict=True)),
            "fitness_gain": self.fitness_gain,
            "fitness_gain_definition": (
                "linear annual fitness proxy gradient dot trait change; not measured survival"
            ),
            "tradeoffs": dict(self.tradeoffs),
            "reasons": dict(self.reasons),
            "max_deme_change": self.max_deme_change,
            "model_version": MODEL_VERSION,
        }

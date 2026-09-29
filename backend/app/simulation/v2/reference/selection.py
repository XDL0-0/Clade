"""Observed selection pressures; no demographic or phenotype ownership.

Pressure axes are temperature, water, food, predation, competition, mobility and
disease. Exposure uses the post-demography living population plus this turn's
exclusive deaths, including births and redistribution rather than reconstructing
the original population. These dimensionless [0,1] descriptive indicators are
not annual hazards or probabilities of future survival. Mobility is residual
stress after recorded departures, not an inversion of the movement equations.
Unassigned slots must have zero population and scratch; extinct rows output zero.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from ..context import TurnContext
from ..contracts import SimulationStage, StageContract, StageResult
from .common import FloatArray, IntArray, geometry, number, result
from .ecology import MODEL_VERSION, Species, _integer, _species
from .world import MORTALITY_CAUSES, TRAITS

PRESSURE_AXES = ("temperature", "water", "food", "predation", "competition", "mobility", "disease")
_FIELDS = (
    "temperature_pressure",
    "water_pressure",
    "food_pressure",
    "predation_pressure",
    "competition",
    "suitability",
)
READS = (
    "state.geometry",
    "state.species",
    "state.food_web",
    "state.environment.ecological_years_per_turn",
    "arrays.population",
    "arrays.mortality",
    "arrays.migration_in",
    "arrays.migration_out",
    "arrays.selection_pressure",
    "arrays.fitness_gradients",
    *(f"arrays.{name}" for name in _FIELDS),
)


@dataclass(frozen=True)
class SelectionInputs:
    dt: float
    population: IntArray
    mortality: IntArray
    incoming: IntArray
    outgoing: IntArray
    arrays: Mapping[str, FloatArray]
    species: tuple[Species, ...]
    edges: tuple[tuple[int, int, float], ...]


def _float(
    context: TurnContext,
    name: str,
    shape: tuple[int, ...],
    *,
    bounded: bool = False,
    signed: bool = False,
) -> FloatArray:
    value = context.snapshot.arrays[name].numpy()
    if value.dtype != np.dtype("float64") or value.shape != shape:
        raise ValueError(f"{name} must be float64 with shape {shape}")
    array = np.array(value, dtype=np.float64, copy=True)
    if not np.isfinite(array).all() or (not signed and np.any(array < 0)):
        raise ValueError(f"{name} must contain finite valid values")
    if bounded and np.any(array > 1):
        raise ValueError(f"{name} must lie in [0,1]")
    return array


def selection_inputs(context: TurnContext) -> SelectionInputs:
    width, height = geometry(context)
    shape = context.snapshot.arrays["population"].shape
    if len(shape) != 2 or shape[1] != width * height:
        raise ValueError("population must be species-by-tile")
    population = _integer(context, "population", shape)
    mortality = _integer(context, "mortality", (len(MORTALITY_CAUSES), *shape))
    incoming = _integer(context, "migration_in", shape)
    outgoing = _integer(context, "migration_out", shape)
    if not np.array_equal(incoming.sum(axis=1, dtype=object), outgoing.sum(axis=1, dtype=object)):
        raise ValueError("migration must conserve each species")
    arrays = {name: _float(context, name, shape, bounded=name != "competition") for name in _FIELDS}
    arrays["selection_pressure"] = _float(
        context,
        "selection_pressure",
        (shape[0], len(PRESSURE_AXES)),
        bounded=True,
    )
    arrays["fitness_gradients"] = _float(
        context,
        "fitness_gradients",
        (shape[0], len(TRAITS)),
        signed=True,
    )
    species = _species(context, shape[0])
    unused = sorted(set(range(shape[0])) - {item.slot for item in species})
    if np.any(mortality[:, unused] != 0) or any(
        np.any(array[unused] != 0) for array in (population, incoming, outgoing, *arrays.values())
    ):
        raise ValueError("unused species rows must have zero population and scratch")
    dt = number(context.environment_state["ecological_years_per_turn"], "ecological time step")
    if dt < 0:
        raise ValueError("ecological time step must be non-negative")
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
            raise ValueError("unsupported food edge roles")
        pair = (predator.slot, prey.slot)
        preference = number(edge.get("preference", 1), "food preference")
        if pair in seen or preference < 0:
            raise ValueError("duplicate edge or negative food preference")
        seen.add(pair)
        edges.append((*pair, preference))
    return SelectionInputs(
        dt, population, mortality, incoming, outgoing, arrays, species, tuple(sorted(edges))
    )


class SelectionPressureStage(SimulationStage):
    contract = StageContract(
        "reference_selection",
        MODEL_VERSION,
        dependencies=("reference_population",),
        reads=READS,
        writes=("arrays.selection_pressure",),
    )

    def execute(self, context: TurnContext) -> StageResult:
        super().validate_inputs(context)
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            data = selection_inputs(context)
            exposure = data.population.astype(np.float64) + data.mortality.sum(
                axis=0, dtype=np.float64
            )
            totals = exposure.sum(axis=1)
            active = data.population.sum(axis=1, dtype=object) > 0
            pressure = np.zeros((data.population.shape[0], len(PRESSURE_AXES)), dtype=np.float64)
            for axis, name in enumerate(_FIELDS[:4]):
                pressure[:, axis] = np.divide(
                    (data.arrays[name] * exposure).sum(axis=1),
                    totals,
                    out=np.zeros_like(totals),
                    where=totals > 0,
                )
            competition = data.arrays["competition"] / (1 + data.arrays["competition"])
            pressure[:, 4] = np.divide(
                (competition * exposure).sum(axis=1),
                totals,
                out=np.zeros_like(totals),
                where=totals > 0,
            )
            # One-departure-per-exposed-individual stress proxy, without claiming
            # these are actual desired moves. Ledgers can include both dispersal
            # and migration; departures only relieve the stress at their source.
            stress = np.maximum(1 - data.arrays["suitability"], data.arrays["food_pressure"])
            desired = exposure * stress
            unmet = desired - np.minimum(desired, data.outgoing)
            pressure[:, 5] = np.divide(
                unmet.sum(axis=1), totals, out=np.zeros_like(totals), where=totals > 0
            )
            pressure[:, 6] = np.divide(
                data.mortality[MORTALITY_CAUSES.index("disease")].sum(axis=1, dtype=np.float64),
                totals,
                out=np.zeros_like(totals),
                where=totals > 0,
            )
            pressure[~active] = 0.0
            if not np.isfinite(pressure).all() or np.any((pressure < 0) | (pressure > 1)):
                raise ValueError("selection pressure escaped its bounded exposure definition")
            return result(
                context,
                self.contract.name,
                arrays={"selection_pressure": pressure},
                metrics={
                    "model_version": MODEL_VERSION,
                    "pressure_units": "dimensionless [0,1]; disease is a per-turn death fraction",
                    "exposure": "post-demography population plus exclusive deaths this turn",
                    "mobility_proxy": "residual stress after departures; not migration demand",
                    **{
                        f"{axis}_pressure_total": float(pressure[:, i].sum())
                        for i, axis in enumerate(PRESSURE_AXES)
                    },
                },
            )

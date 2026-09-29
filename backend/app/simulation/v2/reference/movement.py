"""Conservative local transport and habitat connectivity for ecology-reference-v1.

Individuals stay in population until demography applies the integer movement
ledger. Their reserve carbon moves immediately, in proportion to live emigrants.
All routes are planned from a stage-start snapshot, never from intermediate arrivals.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from ..context import TurnContext
from ..contracts import SimulationStage, StageContract, StageResult
from ..values import JsonValue
from .common import FloatArray, IntArray, event, result
from .demography_inputs import Cohort, float_matrix, inputs, integer_matrix, transported
from .environment import checked_codes, checked_field, layout
from .topology import connected_components, neighbor_graph

MODEL_VERSION = "ecology-reference-v1"
_PRESSURES = ("food_pressure", "temperature_pressure", "water_pressure", "predation_pressure")
_MATRICES = (*_PRESSURES, "competition", "suitability", "carrying_capacity")
_READS = (
    "state.geometry",
    "state.environment.ecological_years_per_turn",
    "state.species",
    "arrays.population",
    "arrays.energy_reserve",
    "arrays.predation_deaths",
    "arrays.migration_in",
    "arrays.migration_out",
    "arrays.biome",
    "arrays.elevation",
    "arrays.river_flux",
    *(f"arrays.{name}" for name in _MATRICES),
)
_WRITES = ("arrays.migration_in", "arrays.migration_out", "arrays.energy_reserve")


@dataclass(frozen=True)
class MovementInputs:
    population: IntArray
    available: IntArray
    reserve: FloatArray
    incoming: IntArray
    outgoing: IntArray
    species: tuple[Cohort, ...]
    habitats: Mapping[int, str]
    fields: Mapping[str, FloatArray]
    biome: IntArray
    elevation: FloatArray
    river: FloatArray
    graph: tuple[tuple[int, ...], ...]
    dt: float


def _inputs(context: TurnContext, *, reset: bool = False) -> MovementInputs:
    width, height = layout(context)
    population, reserve, species, dt = inputs(context)
    killed = integer_matrix(context, "predation_deaths")
    incoming = integer_matrix(context, "migration_in")
    outgoing = integer_matrix(context, "migration_out")
    if any(value.shape != population.shape for value in (killed, incoming, outgoing)):
        raise ValueError("Movement ledger axes mismatch")
    if np.any(killed > population):
        raise ValueError("Predation exceeds starting population")
    if not np.array_equal(incoming.sum(axis=1, dtype=object), outgoing.sum(axis=1, dtype=object)):
        raise ValueError("Movement ledgers must conserve each species")
    available = population - killed if reset else transported(context, population)
    if np.any((available == 0) & (reserve > 0)):
        raise ValueError("Reserve carbon cannot belong to absent or predated individuals")
    fields = {name: float_matrix(context, name, population.shape) for name in _MATRICES}
    if any(np.any(fields[name] > 1) for name in (*_PRESSURES, "suitability")):
        raise ValueError("Suitability and migration pressures must lie in [0,1]")
    habitats: dict[int, str] = {}
    for cohort in species:
        metadata = context.species_state[cohort.identity]
        assert isinstance(metadata, Mapping)
        habitat = metadata["habitat"]
        if habitat not in ("land", "water", "amphibious"):
            raise ValueError("Unknown migration habitat")
        if metadata["role"] not in ("producer", "herbivore", "carnivore", "decomposer"):
            raise ValueError("Unknown migration trophic role")
        assert isinstance(habitat, str)
        habitats[cohort.slot] = habitat
    unused = sorted(set(range(population.shape[0])) - set(habitats))
    if any(np.any(value[unused]) for value in (killed, incoming, outgoing, *fields.values())):
        raise ValueError("Unassigned species rows cannot contain movement or ecological data")
    return MovementInputs(
        population,
        available,
        reserve,
        incoming,
        outgoing,
        species,
        habitats,
        fields,
        checked_codes(context, "biome", 6),
        checked_field(context, "elevation"),
        checked_field(context, "river_flux", low=0),
        neighbor_graph(width, height),
        dt,
    )


def _habitable(data: MovementInputs, habitat: str) -> list[bool]:
    return [
        habitat == "amphibious"
        or (code >= 2 and bool(data.river[tile] <= 1000) if habitat == "land" else code <= 1)
        for tile, code in enumerate(data.biome.tolist())
    ]


def _passages(data: MovementInputs, habitat: str) -> tuple[tuple[int, ...], ...]:
    allowed = _habitable(data, habitat)
    return tuple(
        tuple(
            target
            for target in neighbors
            if allowed[source]
            and allowed[target]
            and (
                habitat != "land"
                or (
                    abs(float(data.elevation[source]) - float(data.elevation[target])) <= 800
                    and max(data.river[source], data.river[target]) <= 1000
                )
            )
        )
        for source, neighbors in enumerate(data.graph)
    )


def _narrow(value: object) -> IntArray:
    array = np.asarray(value, dtype=object)
    if np.any(array < 0) or np.any(array > np.iinfo(np.int64).max):
        raise ValueError("Movement ledger or resulting population overflows int64")
    return np.asarray(array, dtype=np.int64)


def _transport(
    context: TurnContext,
    data: MovementInputs,
    name: str,
    *,
    disperse: bool,
) -> StageResult:
    addition_in = np.zeros(data.population.shape, dtype=object)
    addition_out = np.zeros(data.population.shape, dtype=object)
    reserve_out = np.zeros_like(data.reserve)
    reserve_in = np.zeros_like(data.reserve)
    events = []
    moved = 0
    for cohort in data.species:
        slot = cohort.slot
        graph = _passages(data, data.habitats[slot])
        suitability = data.fields["suitability"][slot]
        density = data.available[slot] / (1 + data.fields["carrying_capacity"][slot])
        overcrowding = np.clip(np.maximum(density, data.fields["competition"][slot]) - 1, 0, 1)
        pressure = np.maximum.reduce(
            [*(data.fields[key][slot] for key in _PRESSURES), overcrowding]
        )
        species_moved = 0
        reasons: set[str] = set()
        for source, adjacent in enumerate(graph):
            available = int(data.available[slot, source])
            if available == 0:
                continue
            targets = [target for target in adjacent if suitability[target] >= 0.2]
            stream = context.seeds.stream(
                name,
                MODEL_VERSION,
                entity=cohort.identity,
                purpose=f"tile:{source}:destination-rank",
            )
            if disperse:
                expected = available * data.dt * 0.2
                rounding = context.seeds.stream(
                    name, MODEL_VERSION, entity=cohort.identity, purpose="dispersal-rounding"
                )
                count = min(
                    available, math.floor(expected) + int(rounding.uniform(source) < expected % 1)
                )
                targets.sort(key=lambda tile: (stream.uint64(tile), tile))
            else:
                targets = [
                    target
                    for target in targets
                    if (
                        suitability[target] > suitability[source] + 1e-12
                        or density[target] < density[source] - 1e-12
                    )
                ]
                count = min(
                    available // 2,
                    math.floor(available * min(0.5, data.dt * 3 * float(pressure[source]))),
                )
                if targets:
                    ranked = [
                        (
                            float(
                                max(0.0, suitability[target] - suitability[source])
                                + max(
                                    0.0, (density[source] - density[target]) / (1 + density[source])
                                )
                            ),
                            stream.uint64(target),
                            target,
                        )
                        for target in targets
                    ]
                    targets = [max(ranked)[2]]
            if not targets or count == 0:
                continue
            share, remainder = divmod(count, len(targets))
            transfers = [
                (target, share + int(index < remainder)) for index, target in enumerate(targets)
            ]
            transfers = [(target, amount) for target, amount in transfers if amount]
            carbon = float(data.reserve[slot, source]) * (count / available)
            carbon_left = carbon
            for index, (target, amount) in enumerate(transfers):
                portion = carbon_left if index == len(transfers) - 1 else carbon * (amount / count)
                addition_in[slot, target] += amount
                reserve_in[slot, target] += portion
                carbon_left -= portion
            addition_out[slot, source] = count
            reserve_out[slot, source] = carbon
            species_moved += count
            if not disperse:
                reasons.update(key for key in _PRESSURES if data.fields[key][slot, source] > 0)
                if overcrowding[source] > 0:
                    reasons.add("overcrowding")
        if species_moved:
            moved += species_moved
            events.append(
                event(
                    context,
                    name,
                    "MigrationOccurred",
                    ordinal=slot,
                    actor=cohort.identity,
                    payload={
                        "count": species_moved,
                        "reason": "undirected_dispersal" if disperse else "pressure_gradient",
                        "pressures": tuple(sorted(reasons)),
                    },
                )
            )
    incoming = _narrow(addition_in + (0 if disperse else data.incoming.astype(object)))
    outgoing = _narrow(addition_out + (0 if disperse else data.outgoing.astype(object)))
    final_available = _narrow(data.available.astype(object) + addition_in - addition_out)
    if not np.array_equal(
        data.available.sum(axis=1, dtype=object), final_available.sum(axis=1, dtype=object)
    ):
        raise ValueError("Movement created or destroyed individuals")
    reserve = data.reserve - reserve_out + reserve_in
    if not np.isfinite(reserve).all() or np.any(reserve < 0):
        raise ValueError("Moved reserves must be finite and nonnegative")
    # Scale error to the actual transport, not the stock: a large destination
    # reserve must not silently absorb small incoming carbon below its ULP.
    cell_residual = (reserve - data.reserve) - reserve_in + reserve_out
    cell_tolerance = 1e-10 + 1e-12 * (reserve_in + reserve_out)
    if np.any(np.abs(cell_residual) > cell_tolerance):
        raise ValueError("Movement reserve carbon was lost at a tile boundary")
    residual = 0.0
    for before, after in zip(data.reserve, reserve, strict=True):
        initial, final = math.fsum(before), math.fsum(after)
        if not math.isclose(initial, final, rel_tol=1e-12, abs_tol=1e-10):
            raise ValueError("Movement did not conserve reserve carbon")
        residual += final - initial
    return result(
        context,
        name,
        arrays={
            "migration_in": incoming,
            "migration_out": outgoing,
            "energy_reserve": reserve,
        },
        events=tuple(events),
        metrics={
            "individuals_moved": moved,
            "species_moved": len(events),
            "reserve_carbon_moved": math.fsum(reserve_out.flat),
            "reserve_balance_residual": residual,
        },
    )


class DispersalStage(SimulationStage):
    contract = StageContract(
        "reference_dispersal",
        MODEL_VERSION,
        dependencies=("reference_feeding",),
        reads=_READS,
        writes=_WRITES,
    )

    def execute(self, context: TurnContext) -> StageResult:
        super().validate_inputs(context)
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            return _transport(
                context, _inputs(context, reset=True), self.contract.name, disperse=True
            )


class MigrationStage(SimulationStage):
    contract = StageContract(
        "reference_migration",
        MODEL_VERSION,
        dependencies=("reference_dispersal",),
        reads=_READS,
        writes=_WRITES,
    )

    def execute(self, context: TurnContext) -> StageResult:
        super().validate_inputs(context)
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            return _transport(context, _inputs(context), self.contract.name, disperse=False)


class ConnectivityStage(SimulationStage):
    contract = StageContract(
        "reference_connectivity",
        MODEL_VERSION,
        dependencies=("reference_migration",),
        reads=(*_READS, "arrays.connectivity"),
        writes=("arrays.connectivity",),
    )

    def execute(self, context: TurnContext) -> StageResult:
        super().validate_inputs(context)
        data = _inputs(context)
        original = context.snapshot.arrays["connectivity"].numpy()
        if original.dtype != np.dtype("int64") or original.shape != data.population.shape:
            raise ValueError("connectivity must have the int64 population shape")
        labels = np.array(original, dtype=np.int64, copy=True)
        if np.any(labels < -1) or np.any(labels >= data.population.shape[1]):
            raise ValueError("Invalid connectivity label")
        unused = sorted(set(range(data.population.shape[0])) - set(data.habitats))
        if np.any(labels[unused] != -1):
            raise ValueError("Unassigned species rows cannot have connectivity labels")
        labels.fill(-1)
        count = 0
        for cohort in data.species:
            habitat = data.habitats[cohort.slot]
            active = [
                allowed and bool(suit >= 0.2)
                for allowed, suit in zip(
                    _habitable(data, habitat), data.fields["suitability"][cohort.slot], strict=True
                )
            ]
            for component in connected_components(active, _passages(data, habitat)):
                labels[cohort.slot, list(component)] = min(component)
                count += 1
        metrics: dict[str, JsonValue] = {
            "connected_components": count,
            "passable_species_tiles": int(np.sum(labels >= 0)),
        }
        return result(context, self.contract.name, arrays={"connectivity": labels}, metrics=metrics)

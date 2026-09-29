"""Numerical lifecycle facts after population publication, without deleting species.

The reference breeding threshold is two individuals on at least one tile, not a
claim about real minimum viable populations. Habitat runs use inclusive tile
intervals. On extinction the last live runs move to history; no per-turn habitat
history or narrative inference is kept. Mortality pressures are counts divided
by the previous observed population, not probabilities or inferred causation.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

import numpy as np

from ..context import TurnContext
from ..contracts import SimulationStage, StageContract, StageResult, StatePatch
from ..events import WorldEvent
from ..values import JsonValue
from .common import IntArray, event, geometry, result, state_patch
from .world import MORTALITY_CAUSES

STATUSES = ("Healthy", "Declining", "Critical", "Functionally Extinct", "Extinct")
_EVENTS = dict(
    zip(
        STATUSES,
        (
            "SpeciesRecovered",
            "SpeciesDeclining",
            "SpeciesCritical",
            "SpeciesFunctionallyExtinct",
            "SpeciesExtinct",
        ),
        strict=True,
    )
)
_READS = (
    "state.geometry",
    "state.species",
    "arrays.population",
    "arrays.mortality",
    "arrays.migration_in",
    "arrays.migration_out",
    "arrays.biome",
)
Runs = tuple[tuple[int, int], ...]


def _count(value: JsonValue, name: str, maximum: int | None = None) -> int:
    if type(value) is not int or value < 0 or (maximum is not None and value > maximum):
        raise ValueError(f"{name} must be a nonnegative integer within its historical bounds")
    return value


def _runs(value: JsonValue, tiles: int) -> Runs:
    if not isinstance(value, tuple):
        raise ValueError("Habitat runs must be inclusive interval tuples")
    output: list[tuple[int, int]] = []
    previous = -2
    for pair in value:
        if not isinstance(pair, tuple) or len(pair) != 2:
            raise ValueError("Habitat runs must contain two-index intervals")
        start = _count(pair[0], "habitat start", tiles - 1)
        end = _count(pair[1], "habitat end", tiles - 1)
        if end < start or start <= previous + 1:
            raise ValueError("Habitat runs must be sorted, disjoint and compressed")
        output.append((start, end))
        previous = end
    return tuple(output)


def _occupied(row: IntArray) -> Runs:
    output: list[tuple[int, int]] = []
    for raw in np.flatnonzero(row):
        tile = int(raw)
        if output and output[-1][1] + 1 == tile:
            output[-1] = (output[-1][0], tile)
        else:
            output.append((tile, tile))
    return tuple(output)


def _integer(context: TurnContext, name: str, shape: tuple[int, ...]) -> IntArray:
    value = context.snapshot.arrays[name].numpy()
    if value.dtype != np.dtype("int64") or value.shape != shape or np.any(np.less(value, 0)):
        raise ValueError(f"{name} must be nonnegative int64 with shape {shape}")
    return np.asarray(value, dtype=np.int64)


@dataclass(frozen=True)
class _Species:
    identity: str
    slot: int
    metadata: Mapping[str, JsonValue]
    status: str
    last: int
    declining: int
    habitat: Runs


def _species(context: TurnContext, rows: int, tiles: int) -> tuple[_Species, ...]:
    output: list[_Species] = []
    slots: set[int] = set()
    maximum = tiles * int(np.iinfo(np.int64).max)
    for identity, metadata in sorted(context.species_state.items()):
        if not identity or not isinstance(metadata, Mapping):
            raise ValueError("Species need a nonempty ID and metadata mapping")
        slot = _count(metadata["slot"], "slot", rows - 1)
        if slot in slots:
            raise ValueError("Species slots must be unique")
        slots.add(slot)
        status = metadata["status"]
        if not isinstance(status, str) or status not in STATUSES:
            raise ValueError("Unknown lifecycle status")
        last = _count(metadata["last_population"], "last_population", maximum)
        observed_turn = _count(
            metadata.get("lifecycle_turn", context.turn_id - 1), "lifecycle_turn", context.turn_id
        )
        declining = _count(metadata["declining_turns"], "declining_turns", observed_turn)
        created = _count(metadata.get("created_turn", 0), "created_turn", context.turn_id)
        for key in ("extinction_turn", "lifecycle_turn"):
            if key in metadata:
                turn = _count(metadata[key], key, context.turn_id)
                if turn < created or (key == "extinction_turn" and status != "Extinct"):
                    raise ValueError("Lifecycle history contradicts creation or current status")
        if "last_nonzero_population" in metadata:
            _count(metadata["last_nonzero_population"], "last_nonzero_population", maximum)
        ancestor = metadata["ancestor"]
        descendants = metadata["descendants"]
        if ancestor is not None and (not isinstance(ancestor, str) or not ancestor):
            raise ValueError("ancestor must be an ID or null")
        if (
            not isinstance(descendants, tuple)
            or any(not isinstance(item, str) or not item for item in descendants)
            or len(set(descendants)) != len(descendants)
        ):
            raise ValueError("descendants must be unique species IDs")
        cause = metadata["extinction_cause"]
        if cause is not None:
            if not isinstance(cause, Mapping):
                raise ValueError("extinction_cause must be a structured record or null")
            for key in (
                "last_population",
                "last_nonzero_population",
                "total_deaths",
                "population_after",
                "migration_in",
                "migration_out",
            ):
                if key in cause:
                    _count(cause[key], f"extinction_cause.{key}")
            counts = cause.get("counts", {})
            if not isinstance(counts, Mapping):
                raise ValueError("extinction cause counts must be a mapping")
            for key, count in counts.items():
                if key not in MORTALITY_CAUSES:
                    raise ValueError("Unknown historical mortality cause")
                _count(count, f"extinction_cause.counts.{key}")
        habitat = _runs(metadata.get("current_habitat_runs", ()), tiles)
        _runs(metadata.get("last_habitat", ()), tiles)
        if status == "Extinct" and (last or habitat):
            raise ValueError("Extinct is terminal and cannot retain living population or habitat")
        output.append(_Species(identity, slot, metadata, status, last, declining, habitat))
    return tuple(output)


def _cause(
    item: _Species, mortality: IntArray, incoming: IntArray, outgoing: IntArray, last_live: int
) -> dict[str, JsonValue]:
    counts = {
        name: int(mortality[index, item.slot].sum(dtype=object))
        for index, name in enumerate(MORTALITY_CAUSES)
    }
    total = sum(counts.values())
    primary = max(counts, key=lambda name: counts[name]) if total else "unknown"
    return {
        "primary": primary,
        "counts": cast(dict[str, JsonValue], counts),
        "pressure": {
            name: count / item.last if item.last else None for name, count in counts.items()
        },
        "pressure_basis": "deaths this turn / previous observed population; not probability",
        "total_deaths": total,
        "last_population": item.last,
        "last_nonzero_population": last_live,
        "population_after": 0,
        "migration_in": int(incoming[item.slot].sum(dtype=object)),
        "migration_out": int(outgoing[item.slot].sum(dtype=object)),
    }


class ExtinctionStage(SimulationStage):
    contract = StageContract(
        "reference_extinction",
        "1",
        dependencies=("reference_population",),
        reads=_READS,
        writes=("state.species",),
    )

    def __init__(self, after: str = "reference_population") -> None:
        if after == self.contract.name:
            raise ValueError("Extinction cannot depend on itself")
        self.contract = StageContract(
            "reference_extinction",
            "1",
            dependencies=(after,),
            reads=_READS,
            writes=("state.species",),
        )

    def execute(self, context: TurnContext) -> StageResult:
        super().validate_inputs(context)
        width, height = geometry(context)
        tiles = width * height
        shape = context.snapshot.arrays["population"].shape
        if len(shape) != 2 or shape[1] != tiles:
            raise ValueError("population must have species-by-tile axes")
        population = _integer(context, "population", shape)
        mortality = _integer(context, "mortality", (len(MORTALITY_CAUSES), *shape))
        incoming = _integer(context, "migration_in", shape)
        outgoing = _integer(context, "migration_out", shape)
        biome = _integer(context, "biome", (tiles,))
        if np.any(biome > 6):
            raise ValueError("Unknown biome code")
        if not np.array_equal(
            incoming.sum(axis=1, dtype=object), outgoing.sum(axis=1, dtype=object)
        ):
            raise ValueError("Migration must conserve each species population")
        species = _species(context, shape[0], tiles)
        unused = sorted(set(range(shape[0])) - {item.slot for item in species})
        if any(np.any(value[unused]) for value in (population, incoming, outgoing)) or np.any(
            mortality[:, unused]
        ):
            raise ValueError("Unused species rows cannot contain population or lifecycle ledgers")
        patches: list[StatePatch] = []
        changes: dict[str, dict[str, JsonValue]] = {}
        events: list[WorldEvent] = []
        counts = dict.fromkeys(STATUSES, 0)
        newly_extinct = 0
        for item in species:
            row = population[item.slot]
            total = int(row.sum(dtype=object))
            habitat = _occupied(row)
            if item.status == "Extinct":
                if total:
                    raise ValueError(
                        "Extinct is terminal; living population requires a new species"
                    )
                counts["Extinct"] += 1
                continue
            if item.metadata.get("lifecycle_turn") == context.turn_id:
                if item.last != total or item.habitat != habitat:
                    raise ValueError(
                        "Population changed after lifecycle was finalized for this turn"
                    )
                counts[item.status] += 1
                continue
            # A gap between observations cannot establish a one-turn decline.
            # Older metadata without a turn stamp retains its existing baseline.
            previous_turn = item.metadata.get("lifecycle_turn", context.turn_id - 1)
            falling = (
                previous_turn == context.turn_id - 1
                and item.last > total
                and 10 * (item.last - total) >= item.last
            )
            declining = item.declining + 1 if falling else 0
            status = (
                "Extinct"
                if total == 0
                else "Functionally Extinct"
                if int(row.max(initial=0)) < 2
                else "Critical"
                if total <= 10
                else "Declining"
                if declining >= 2
                else "Healthy"
            )
            updates: dict[str, JsonValue] = {
                "status": status,
                "last_population": total,
                "declining_turns": declining,
                "lifecycle_turn": context.turn_id,
                "current_habitat_runs": habitat,
            }
            if total:
                updates["last_nonzero_population"] = total
            else:
                last_live = item.last or _count(
                    item.metadata.get("last_nonzero_population", 0), "last_nonzero_population"
                )
                updates.update(
                    extinction_turn=context.turn_id,
                    last_nonzero_population=last_live,
                    last_habitat=item.habitat,
                    extinction_cause=_cause(item, mortality, incoming, outgoing, last_live),
                )
                newly_extinct += 1
            for key, value in updates.items():
                if key not in item.metadata or item.metadata[key] != value:
                    changes.setdefault(item.identity, {})[key] = value
            if status != item.status:
                events.append(
                    event(
                        context,
                        self.contract.name,
                        _EVENTS[status],
                        ordinal=item.slot,
                        target=item.identity,
                        payload={
                            "previous_status": item.status,
                            "status": status,
                            "population": total,
                            "previous_population": item.last,
                            "declining_turns": declining,
                            "breeding_group_threshold": 2,
                            "largest_local_population": int(row.max(initial=0)),
                            "extinction_cause": updates.get("extinction_cause"),
                        },
                    )
                )
            counts[status] += 1
        if any("." in identity for identity in changes):
            # StatePatch path segments cannot contain dots. Preserve otherwise
            # valid species IDs using one domain patch only for this case.
            updated = dict(context.species_state)
            for item in species:
                if item.identity in changes:
                    updated[item.identity] = {**item.metadata, **changes[item.identity]}
            patches.append(state_patch(context, ("species",), updated))
        else:
            for identity, values in changes.items():
                for key, value in values.items():
                    patches.append(state_patch(context, ("species", identity, key), value))
        eligible = sum(item.status != "Extinct" for item in species)
        return result(
            context,
            self.contract.name,
            state=tuple(patches),
            events=tuple(events),
            metrics={
                "species_count": len(species),
                "status_changes": len(events),
                "new_extinctions": newly_extinct,
                "extinction_rate": newly_extinct / eligible if eligible else 0.0,
                **{
                    f"{status.lower().replace(' ', '_')}_count": count
                    for status, count in counts.items()
                },
            },
        )

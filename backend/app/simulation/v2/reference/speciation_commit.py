"""Atomic application of the recomputed reference speciation evidence.

Only real stocks and persistent deme state move. Ecological scratch remains
unchanged: a new row has zeros (connectivity uses -1) until next turn's ecology.
One compact turn marker prevents repeated splits/defer events within a turn.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import cast

import numpy as np
from numpy.typing import NDArray

from ..context import TurnContext
from ..contracts import StageResult, StatePatch
from ..events import WorldEvent
from ..values import JsonValue, digest
from .common import IntArray, event, number, result, state_patch
from .speciation import (
    GENES,
    lifecycle_marker,
    pending,
    proposals,
    speciation_inputs,
    tile_runs,
    trait_mean,
    validate_scratch,
)
from .world import TRAITS

_PHENOTYPE = (
    "role",
    "body_mass",
    "habitat",
    "thermal_optimum",
    "thermal_width",
    "water_need",
    "fertility",
    "lifespan",
    "trait_budget",
    "trophic_level",
)


def _edges(context: TurnContext) -> list[dict[str, JsonValue]]:
    value = context.food_web_state.get("edges", ())
    if not isinstance(value, tuple):
        raise ValueError("Food web edges must be a tuple")
    output: list[dict[str, JsonValue]] = []
    seen: set[tuple[str, str]] = set()
    for edge in value:
        if not isinstance(edge, Mapping):
            raise ValueError("Food web edges must be mappings")
        predator, prey = edge.get("predator"), edge.get("prey")
        if not isinstance(predator, str) or not isinstance(prey, str):
            raise ValueError("Food web edges require species IDs")
        if predator not in context.species_state or prey not in context.species_state:
            raise ValueError("Food web edge references an unknown species")
        first = cast(Mapping[str, JsonValue], context.species_state[predator])
        second = cast(Mapping[str, JsonValue], context.species_state[prey])
        if (first["role"], second["role"]) not in (
            ("herbivore", "producer"),
            ("carnivore", "herbivore"),
        ):
            raise ValueError("Food web edge has unsupported trophic roles")
        if (predator, prey) in seen or number(edge.get("preference", 1.0), "preference") < 0:
            raise ValueError("Duplicate food edge or negative preference")
        seen.add((predator, prey))
        output.append(dict(edge))
    return sorted(output, key=lambda edge: (str(edge["predator"]), str(edge["prey"])))


def commit_speciation(context: TurnContext, stage: str) -> StageResult:
    if "pending_speciation" not in context.snapshot.domain("evolution"):
        raise ValueError("Speciation commit requires the proposal stage's pending record")
    requested = pending(context)
    if lifecycle_marker(context) == context.turn_id:
        if requested:
            raise ValueError("Speciation already committed for this turn")
        return result(context, stage, metrics={"species_created": 0, "deferred": 0})
    data = speciation_inputs(context)
    if digest(requested) != digest(proposals(context, data)):
        raise ValueError("Stale, duplicate or tampered speciation proposal evidence")
    validate_scratch(context, data)
    edges = _edges(context)
    species: dict[str, JsonValue] = dict(context.species_state)
    available = sorted(set(range(data.population.shape[0])) - {item.slot for item in data.species})
    arrays: dict[str, NDArray[np.generic]] = {
        "population": data.population.copy(),
        "energy_reserve": data.reserve.copy(),
        **{name: data.integers[name].copy() for name in GENES if name in data.integers},
        **{name: data.floats[name].copy() for name in GENES if name in data.floats},
    }
    events: list[WorldEvent] = []
    created = deferred = 0
    reserve_moved = 0.0
    for ordinal, raw in enumerate(requested):
        assert isinstance(raw, Mapping)  # Exact equality with the generated schema above.
        parent, child = cast(str, raw["parent"]), cast(str, raw["child_id"])
        parent_slot, component = cast(int, raw["parent_slot"]), cast(int, raw["component"])
        if child in species:
            raise ValueError("A speciation child ID cannot reuse a living or fossil identity")
        payload = {
            key: raw[key]
            for key in (
                "proposal_id",
                "parent",
                "child_id",
                "score",
                "cause_terms",
                "isolation_turns",
                "component",
                "population",
                "remaining_population",
                "genetic_rms_scale",
            )
        }
        if not available:
            deferred += 1
            events.append(
                event(
                    context,
                    stage,
                    "SpeciationDeferred",
                    ordinal=2 * ordinal,
                    actor=parent,
                    target=parent,
                    payload={**payload, "reason": "species_capacity"},
                )
            )
            continue
        slot = available.pop(0)
        metadata = cast(Mapping[str, JsonValue], species[parent])
        mask = data.integers["connectivity"][parent_slot] == component
        reserve_moved += math.fsum(float(value) for value in data.reserve[parent_slot, mask])
        for name in ("population", "energy_reserve", "deme_traits", "trait_proposals"):
            arrays[name][slot, mask] = arrays[name][parent_slot, mask]
            arrays[name][parent_slot, mask] = 0
            source = context.snapshot.arrays[name].numpy()[parent_slot, mask]
            if (
                not np.array_equal(arrays[name][slot, mask], source)
                or np.any(arrays[name][parent_slot, mask] != 0)
                or np.any(arrays[name][slot, ~mask] != 0)
            ):
                raise ValueError("Speciation must transfer every local stock and deme exactly")
        arrays["gene_population"][slot] = arrays["population"][slot]
        arrays["gene_population"][parent_slot, mask] = 0
        arrays["gene_connectivity"][slot, mask] = arrays["gene_connectivity"][parent_slot, mask]
        arrays["gene_connectivity"][parent_slot, mask] = -1
        arrays["isolation_age"][parent_slot, mask] = 0
        arrays["isolation_age"][slot] = 0
        child_row = arrays["population"][slot]
        status = "Functionally Extinct" if int(child_row.max(initial=0)) < 2 else "Healthy"
        species[child] = {
            **{key: metadata[key] for key in _PHENOTYPE},
            "slot": slot,
            "traits": raw["traits"],
            "status": status,
            "ancestor": parent,
            "descendants": (),
            "created_turn": context.turn_id,
            "last_population": raw["population"],
            "last_nonzero_population": raw["population"],
            "current_habitat_runs": tile_runs(child_row > 0),
            "lifecycle_turn": context.turn_id,
            "declining_turns": 0,
            "extinction_cause": None,
        }
        descendants = cast(tuple[JsonValue, ...], metadata["descendants"])
        if child in descendants:
            raise ValueError("A child cannot already appear in the parent lineage")
        prior_population = metadata.get("last_population")
        if type(prior_population) is not int or prior_population < 0:
            raise ValueError("Parent lifecycle baseline must be a nonnegative integer")
        # Identity transfer is not demographic decline. Keep genuine mortality
        # in the remaining lineage's baseline for the later lifecycle stage.
        baseline = max(0, prior_population - cast(int, raw["population"]))
        remaining = cast(IntArray, arrays["population"])[parent_slot]
        mean = trait_mean(data.floats["deme_traits"][parent_slot], remaining, remaining > 0)
        species[parent] = {
            **metadata,
            "descendants": (*descendants, child),
            "last_population": baseline,
            "traits": {name: float(mean[index]) for index, name in enumerate(TRAITS)},
        }
        child_metadata = cast(Mapping[str, JsonValue], species[child])
        if child_metadata["body_mass"] != metadata["body_mass"]:
            raise ValueError("A lineage split must preserve individual structural mass")
        payload.update(parent_baseline_before=prior_population, parent_baseline_after=baseline)
        copied: list[dict[str, JsonValue]] = []
        for edge in edges:
            if edge["predator"] == parent:
                copied.append({**edge, "predator": child})
            elif edge["prey"] == parent:
                copied.append({**edge, "prey": child})
        edges.extend(copied)
        created += 1
        for offset, kind in enumerate(("SpeciesCreated", "SpeciationOccurred")):
            events.append(
                event(
                    context,
                    stage,
                    kind,
                    ordinal=2 * ordinal + offset,
                    actor=parent,
                    target=child,
                    payload={**payload, "child_slot": slot},
                )
            )
    population = cast(IntArray, arrays["population"])
    old_total = int(data.population.sum(dtype=object))
    new_total = int(population.sum(dtype=object))
    if not np.array_equal(
        data.population.sum(axis=0, dtype=object), population.sum(axis=0, dtype=object)
    ):
        raise ValueError("Speciation must conserve integer population on every tile")
    before = math.fsum(float(value) for value in data.reserve.flat) + math.fsum(
        int(data.population[item.slot].sum(dtype=object)) * item.mass for item in data.species
    )
    after = math.fsum(float(value) for value in arrays["energy_reserve"].flat) + math.fsum(
        int(population[cast(int, metadata["slot"])].sum(dtype=object))
        * number(metadata["body_mass"], "body mass")
        for metadata in (cast(Mapping[str, JsonValue], value) for value in species.values())
    )
    if (
        not math.isfinite(before)
        or not math.isfinite(after)
        or not math.isclose(before, after, rel_tol=1e-12, abs_tol=1e-9)
    ):
        raise ValueError("Speciation must conserve structural and reserve carbon")
    patches: list[StatePatch] = []
    if created:
        patches.append(state_patch(context, ("species",), species))
        ordered_edges: JsonValue = tuple(
            sorted(edges, key=lambda edge: (str(edge["predator"]), str(edge["prey"])))
        )
        if ordered_edges != context.food_web_state.get("edges", ()):
            patches.append(state_patch(context, ("food_web", "edges"), ordered_edges))
    if requested or "pending_speciation" not in context.snapshot.domain("evolution"):
        patches.append(state_patch(context, ("evolution", "pending_speciation"), ()))
    patches.append(state_patch(context, ("evolution", "speciation_turn"), context.turn_id))
    return result(
        context,
        stage,
        state=tuple(patches),
        arrays=arrays if created else {},
        events=tuple(events),
        metrics={
            "species_created": created,
            "deferred": deferred,
            "population_before": old_total,
            "population_after": new_total,
            "carbon_before": before,
            "carbon_after": after,
            "carbon_balance_residual": after - before,
            "reserve_carbon_moved": reserve_moved,
        },
    )

"""Connected-deme approximation kernels, with explicitly separate RNG streams.

Focus homogenizes only currently occupied members of one passable component.
Its drift uses the component population, suppressing within-component diversity;
separate geographic components are never pooled. Critical uses original kernels.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np

from ..context import TurnContext
from ..contracts import StageResult
from ..values import JsonValue
from .adaptation import adapt_deme
from .common import FloatArray, IntArray, event, result, state_patch
from .ecology import Species
from .evolution_contracts import EvolutionInputs, EvolutionTrace, weighted_traits
from .resolution_policy import Decision
from .world import TRAITS


def components(data: EvolutionInputs, row: int) -> tuple[tuple[int, IntArray], ...]:
    occupied = data.population[row] > 0
    if np.any(occupied & (data.connectivity[row] < 0)):
        raise ValueError("Blocked occupied habitats require Critical resolution")
    return tuple(
        (int(label), np.flatnonzero(occupied & (data.connectivity[row] == label)))
        for label in np.unique(data.connectivity[row, occupied])
    )


def component_proposals(
    context: TurnContext, data: EvolutionInputs, item: Species, dt: float
) -> tuple[FloatArray, int]:
    proposed = data.deme[item.slot].copy()
    groups = components(data, item.slot)
    budget = data.budgets[item.slot]
    for label, members in groups:
        counts = data.population[item.slot, members]
        mean = weighted_traits(data.deme[item.slot, members], counts)
        mutation, drift = np.zeros(len(TRAITS)), np.zeros(len(TRAITS))
        size = int(counts.sum(dtype=object))
        for axis, name in enumerate(TRAITS):
            stream = context.seeds.stream(
                "reference_resolution_mutation",
                "1",
                entity=item.identity,
                purpose=f"component:{label}:{name}",
            )
            if stream.uniform(0) < 0.1 * dt:
                mutation[axis] = 0.01 * math.sqrt(dt) * np.clip(stream.normal(1), -3.0, 3.0)
            stream = context.seeds.stream(
                "reference_resolution_drift",
                "1",
                entity=item.identity,
                purpose=f"component:{label}:{name}",
            )
            drift[axis] = 0.02 * math.sqrt(dt / size) * np.clip(stream.normal(0), -3.0, 3.0)
        proposed[members] = budget.project(budget.project(mean + mutation) + drift)
    return proposed, len(groups)


def adapt_scheduled(
    context: TurnContext, data: EvolutionInputs, decisions: Mapping[str, Decision]
) -> StageResult:
    updated = data.deme.copy()
    species = dict(context.species_state)
    events, traces = [], []
    maximum = 0.0
    for item in data.species:
        row, decision = item.slot, decisions[item.identity]
        occupied = data.population[row] > 0
        if not np.any(occupied):
            continue
        old = np.array([item.traits[name] for name in TRAITS])
        budget, gradient = data.budgets[row], data.gradients[row]
        speed = np.zeros(data.population.shape[1])
        gains = np.zeros_like(speed)
        if decision.due and decision.tier == "Critical":
            for tile in np.flatnonzero(occupied):
                baseline = (
                    old[0] if data.previous_population[row, tile] == 0 else data.deme[row, tile, 0]
                )
                updated[row, tile], speed[tile], gains[tile] = adapt_deme(
                    data.proposals[row, tile], float(baseline), gradient, decision.dt, budget
                )
        elif decision.due:
            for _, members in components(data, row):
                counts = data.population[row, members]
                proposal = weighted_traits(data.proposals[row, members], counts)
                baseline = weighted_traits(data.deme[row, members], counts)
                values, paid, gain = adapt_deme(
                    proposal, float(baseline[0]), gradient, decision.dt, budget
                )
                updated[row, members], speed[members], gains[members] = values, paid, gain
        # Current population weights remain authoritative even on a skipped turn.
        mean = budget.project(weighted_traits(updated[row], data.population[row]))
        metadata = context.species_state[item.identity]
        assert isinstance(metadata, Mapping)
        species[item.identity] = {
            **metadata,
            "traits": dict(zip(TRAITS, map(float, mean), strict=True)),
        }
        change = mean - old
        local_max = float(np.max(np.abs(updated[row, occupied] - data.deme[row, occupied])))
        maximum = max(maximum, local_max)
        if max(local_max, float(np.max(np.abs(change)))) < 0.01:
            continue
        weights = data.population[row].astype(np.float64) / int(
            data.population[row].sum(dtype=object)
        )
        trace = EvolutionTrace(
            item.identity,
            context.turn_id,
            tuple(map(float, change)),
            tuple(map(float, data.pressure[row])),
            math.fsum(float(g) * float(d) for g, d in zip(gradient, change, strict=True)),
            (
                ("speed_change", float(change[1])),
                ("armor_speed_payment", float((speed * weights).sum())),
                (
                    "annual_maintenance_change_per_individual",
                    budget.annual_maintenance(mean, item.mass)
                    - budget.annual_maintenance(old, item.mass),
                ),
            ),
            (
                ("resolution_effective_years", decision.dt),
                (
                    "resolution_component_approximation",
                    float(decision.tier != "Critical" and decision.due),
                ),
                ("demographic_mean_only", float(not decision.due)),
                ("directed_linear_proxy_gain", float((gains * weights).sum())),
            ),
            local_max,
        )
        payload: dict[str, JsonValue] = {
            **trace.to_json(),
            "resolution_tier": decision.tier,
            "resolution_approximation": "current-component homogeneous phenotype"
            if decision.tier != "Critical" and decision.due
            else "per-tile"
            if decision.due
            else "demographic mean only",
        }
        traces.append(payload)
        events.append(
            event(
                context,
                "reference_adaptation",
                "SpeciesAdapted",
                ordinal=row,
                actor=item.identity,
                payload=payload,
            )
        )
    from dataclasses import replace

    return replace(
        result(
            context,
            "reference_adaptation",
            arrays={"deme_traits": updated, "gene_population": data.population},
            state=(state_patch(context, ("species",), species),),
            events=tuple(events),
            metrics={"significant_adaptations": len(traces), "max_deme_change": maximum},
        ),
        evolution_proposals=tuple(traces),
    )

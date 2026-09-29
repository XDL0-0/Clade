"""Budgeted small proxy-gradient steps and immutable quantitative adaptation facts.

A species-wide finite-difference gradient is reused in each local deme; this is
not a local ecological optimizer. Armor gains consume .25 units of speed per
unit armor before shared-budget projection. Existing mortality pays resulting
maintenance next turn. Metadata publishes the living population-weighted mean;
local deme divergence remains available to speciation.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import replace

import numpy as np

from ..context import TurnContext
from ..contracts import SimulationStage, StageContract, StageResult
from ..values import JsonValue
from .common import FloatArray, event, result, state_patch
from .ecology import MODEL_VERSION
from .evolution_contracts import (
    READS,
    EvolutionBudget,
    EvolutionTrace,
    evolution_inputs,
    weighted_traits,
)
from .genetics import drift_proposal, mutation_proposal
from .world import TRAITS


def _tradeoff(values: FloatArray, armor_baseline: float) -> tuple[FloatArray, float]:
    candidate = np.clip(values, 0, 1)
    armor_gain = max(0.0, float(candidate[0]) - armor_baseline)
    paid = min(0.25 * armor_gain, float(candidate[1]))
    # Zero-speed phenotypes cannot receive unpaid armor.
    candidate[0] -= armor_gain - paid / 0.25
    candidate[1] -= paid
    return candidate, paid


def adapt_deme(
    proposal: FloatArray,
    armor_baseline: float,
    gradient: FloatArray,
    dt: float,
    budget: EvolutionBudget,
) -> tuple[FloatArray, float, float]:
    """Pay inherited tradeoffs, then accept only a nondecreasing linear-proxy step.

    Mutation/drift can still lower the whole-turn proxy. Projection is radial,
    so a gradient step can be rejected at a budget boundary; no optimum is claimed.
    """
    baseline, inherited_payment = _tradeoff(proposal, armor_baseline)
    baseline = budget.project(baseline)
    direction = gradient / max(1.0, float(np.max(np.abs(gradient))))
    candidate, payment = _tradeoff(baseline + 0.03 * dt * direction, float(baseline[0]))
    candidate = budget.project(candidate)
    gain = math.fsum(
        float(g) * float(d) for g, d in zip(gradient, candidate - baseline, strict=True)
    )
    if gain < 0:
        return baseline, inherited_payment, 0.0
    return candidate, inherited_payment + payment, gain


class AdaptationStage(SimulationStage):
    contract = StageContract(
        "reference_adaptation",
        MODEL_VERSION,
        ("reference_gene_flow",),
        reads=READS,
        writes=("arrays.deme_traits", "arrays.gene_population", "state.species"),
    )

    def execute(self, context: TurnContext) -> StageResult:
        super().validate_inputs(context)
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            data = evolution_inputs(context)
            updated = data.deme.copy()
            species = dict(context.species_state)
            traces: list[JsonValue] = []
            events = []
            maximum = maintenance_change = 0.0
            for item in data.species:
                row = item.slot
                occupied = data.population[row] > 0
                if not np.any(occupied):
                    continue
                budget = data.budgets[row]
                old = np.array([item.traits[name] for name in TRAITS])
                gradient = data.gradients[row]
                direction = gradient / max(1.0, float(np.max(np.abs(gradient))))
                speed_costs = np.zeros(data.population.shape[1])
                directed_gains = np.zeros(data.population.shape[1])
                for tile in np.flatnonzero(occupied):
                    armor_baseline = (
                        old[0]
                        if data.previous_population[row, tile] == 0
                        else data.deme[row, tile, 0]
                    )
                    updated[row, tile], speed_costs[tile], directed_gains[tile] = adapt_deme(
                        data.proposals[row, tile], float(armor_baseline), gradient, data.dt, budget
                    )
                mean = budget.project(weighted_traits(updated[row], data.population[row]))
                change = mean - old
                metadata = context.species_state[item.identity]
                assert isinstance(metadata, Mapping)
                species[item.identity] = {
                    **metadata,
                    "traits": dict(zip(TRAITS, map(float, mean), strict=True)),
                }
                local_max = float(np.max(np.abs(updated[row, occupied] - data.deme[row, occupied])))
                maximum = max(maximum, local_max)
                cost = budget.annual_maintenance(mean, item.mass) - budget.annual_maintenance(
                    old, item.mass
                )
                maintenance_change += cost
                if max(local_max, float(np.max(np.abs(change)))) < 0.01:
                    continue
                # Reuse the exact deterministic proposal helpers only for emitted
                # traces; restricted stage contexts intentionally omit old results.
                mutation, mutation_stats = mutation_proposal(context, data, item)
                drift, drift_stats = drift_proposal(context, data, item, mutation)
                reasons = {
                    **mutation_stats,
                    **drift_stats,
                    "gene_flow_max_abs": float(
                        np.max(np.abs(data.proposals[row, occupied] - drift[occupied]))
                    ),
                    "gradient_step_max_abs": 0.03 * data.dt * float(np.max(np.abs(direction))),
                }
                weights = data.population[row].astype(np.float64) / int(
                    data.population[row].sum(dtype=object)
                )
                reasons["directed_linear_proxy_gain"] = float((directed_gains * weights).sum())
                trace = EvolutionTrace(
                    item.identity,
                    context.turn_id,
                    tuple(map(float, change)),
                    tuple(map(float, data.pressure[row])),
                    math.fsum(float(g) * float(d) for g, d in zip(gradient, change, strict=True)),
                    (
                        ("speed_change", float(change[1])),
                        ("armor_speed_payment", float((speed_costs * weights).sum())),
                        ("annual_maintenance_change_per_individual", cost),
                    ),
                    tuple(sorted(reasons.items())),
                    local_max,
                )
                payload = trace.to_json()
                traces.append(payload)
                events.append(
                    event(
                        context,
                        self.contract.name,
                        "SpeciesAdapted",
                        ordinal=row,
                        actor=item.identity,
                        payload=payload,
                    )
                )
            proposal = result(
                context,
                self.contract.name,
                arrays={"deme_traits": updated, "gene_population": data.population},
                state=(state_patch(context, ("species",), species),),
                events=tuple(events),
                metrics={
                    "significant_adaptations": len(traces),
                    "max_deme_change": maximum,
                    "annual_maintenance_change_per_individual_sum": maintenance_change,
                },
            )
            return replace(proposal, stage_version=MODEL_VERSION, evolution_proposals=tuple(traces))

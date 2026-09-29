"""Seeded deme mutation, Gaussian drift and connectivity-limited gene flow.

Drift std=.02*sqrt(dt/N), doubled after a strict >75% local population loss.
Newly occupied tiles sample the parent species mean, not tracked migrant genomes.
Mutation classes describe the local linear proxy after projection, not measured
fitness. Gene flow mixes 25%*dt towards each occupied component's weighted mean.
"""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

from ..context import TurnContext
from ..contracts import SimulationStage, StageContract, StageResult
from .common import FloatArray, result
from .ecology import MODEL_VERSION, Species
from .evolution_contracts import (
    READS,
    EvolutionInputs,
    bottleneck_mask,
    evolution_inputs,
    founder_mask,
    weighted_traits,
)
from .world import TRAITS


def mutation_proposal(
    context: TurnContext,
    data: EvolutionInputs,
    item: Species,
) -> tuple[FloatArray, dict[str, float]]:
    slot = item.slot
    proposal = data.deme[slot].copy()
    founders = founder_mask(data, slot)
    counts = {
        "mutation_beneficial": 0.0,
        "mutation_harmful": 0.0,
        "mutation_neutral": 0.0,
        "founder_tiles": float(founders.sum()),
        "mutation_max_abs": 0.0,
        "founder_max_abs": 0.0,
    }
    mean = np.array([item.traits[name] for name in TRAITS])
    budget = data.budgets[slot]
    for tile in np.flatnonzero(data.population[slot] > 0):
        size = int(data.population[slot, tile])
        if founders[tile]:
            offset = np.array(
                [
                    0.03
                    * math.sqrt(data.dt / size)
                    * np.clip(
                        context.seeds.stream(
                            "reference_mutation",
                            MODEL_VERSION,
                            entity=item.identity,
                            purpose=f"founder:{name}",
                        ).normal(int(tile)),
                        -3.0,
                        3.0,
                    )
                    for name in TRAITS
                ]
            )
            proposal[tile] = budget.project(mean + offset)
            counts["founder_max_abs"] = max(
                counts["founder_max_abs"], float(np.max(np.abs(proposal[tile] - mean)))
            )
        before = proposal[tile].copy()
        delta = np.zeros(len(TRAITS), dtype=np.float64)
        attempts = 0
        for axis, name in enumerate(TRAITS):
            stream = context.seeds.stream(
                "reference_mutation",
                MODEL_VERSION,
                entity=item.identity,
                purpose=f"mutation:{name}",
            )
            if stream.uniform(int(tile) * 2) < 0.1 * data.dt:
                delta[axis] = (
                    0.01 * math.sqrt(data.dt) * np.clip(stream.normal(int(tile) * 2 + 1), -3.0, 3.0)
                )
                attempts += 1
        proposal[tile] = budget.project(before + delta)
        change = proposal[tile] - before
        counts["mutation_max_abs"] = max(counts["mutation_max_abs"], float(np.max(np.abs(change))))
        if attempts:
            proxy = math.fsum(
                float(a) * float(b) for a, b in zip(data.gradients[slot], change, strict=True)
            )
            kind = "beneficial" if proxy > 1e-12 else "harmful" if proxy < -1e-12 else "neutral"
            counts[f"mutation_{kind}"] += 1
    return proposal, counts


def drift_proposal(
    context: TurnContext,
    data: EvolutionInputs,
    item: Species,
    proposal: FloatArray,
) -> tuple[FloatArray, dict[str, float]]:
    updated = proposal.copy()
    bottlenecks = bottleneck_mask(data, item.slot)
    maximum = sigma_max = 0.0
    for tile in np.flatnonzero(data.population[item.slot] > 0):
        sigma = 0.02 * math.sqrt(data.dt / int(data.population[item.slot, tile]))
        sigma *= 2 if bottlenecks[tile] else 1
        delta = np.array(
            [
                sigma
                * np.clip(
                    context.seeds.stream(
                        "reference_drift",
                        MODEL_VERSION,
                        entity=item.identity,
                        purpose=f"drift:{name}",
                    ).normal(int(tile)),
                    -3.0,
                    3.0,
                )
                for name in TRAITS
            ]
        )
        updated[tile] = data.budgets[item.slot].project(proposal[tile] + delta)
        maximum = max(maximum, float(np.max(np.abs(updated[tile] - proposal[tile]))))
        sigma_max = max(sigma_max, sigma)
    return updated, {
        "bottleneck_tiles": float(bottlenecks.sum()),
        "drift_max_abs": maximum,
        "drift_sigma_max": sigma_max,
    }


class MutationStage(SimulationStage):
    contract = StageContract(
        "reference_mutation",
        MODEL_VERSION,
        ("reference_fitness",),
        reads=READS,
        writes=("arrays.trait_proposals",),
    )

    def execute(self, context: TurnContext) -> StageResult:
        super().validate_inputs(context)
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            data = evolution_inputs(context)
            proposals = data.deme.copy()
            totals: dict[str, float] = {}
            for item in data.species:
                proposals[item.slot], counts = mutation_proposal(context, data, item)
                for name, value in counts.items():
                    totals[name] = (
                        max(totals.get(name, 0), value)
                        if name.endswith("max_abs")
                        else (totals.get(name, 0) + value)
                    )
            return replace(
                result(
                    context,
                    self.contract.name,
                    arrays={"trait_proposals": proposals},
                    metrics=totals,
                ),
                stage_version=MODEL_VERSION,
            )


class GeneticDriftStage(SimulationStage):
    contract = StageContract(
        "reference_drift",
        MODEL_VERSION,
        ("reference_mutation",),
        reads=READS,
        writes=("arrays.trait_proposals",),
    )

    def execute(self, context: TurnContext) -> StageResult:
        super().validate_inputs(context)
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            data = evolution_inputs(context)
            proposals = data.proposals.copy()
            maximum = bottlenecks = sigma_max = 0.0
            for item in data.species:
                proposals[item.slot], stats = drift_proposal(
                    context, data, item, proposals[item.slot]
                )
                maximum = max(maximum, stats["drift_max_abs"])
                sigma_max = max(sigma_max, stats["drift_sigma_max"])
                bottlenecks += stats["bottleneck_tiles"]
            return replace(
                result(
                    context,
                    self.contract.name,
                    arrays={"trait_proposals": proposals},
                    metrics={
                        "drift_max_abs": maximum,
                        "bottleneck_tiles": bottlenecks,
                        "drift_sigma_max": sigma_max,
                    },
                ),
                stage_version=MODEL_VERSION,
            )


class GeneFlowStage(SimulationStage):
    contract = StageContract(
        "reference_gene_flow",
        MODEL_VERSION,
        ("reference_drift",),
        reads=READS,
        writes=("arrays.trait_proposals", "arrays.gene_connectivity", "arrays.isolation_age"),
    )

    def execute(self, context: TurnContext) -> StageResult:
        super().validate_inputs(context)
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            data = evolution_inputs(context)
            proposals = data.proposals.copy()
            labels = np.full(data.population.shape, -1, dtype=np.int64)
            ages = np.zeros(data.population.shape, dtype=np.int64)
            components = isolated = 0
            for item in data.species:
                row = item.slot
                occupied = (data.population[row] > 0) & (data.connectivity[row] >= 0)
                labels[row, occupied] = data.connectivity[row, occupied]
                groups = np.unique(labels[row, occupied])
                components += len(groups)
                for label in groups:
                    members = occupied & (labels[row] == label)
                    mean = weighted_traits(
                        data.proposals[row, members], data.population[row, members]
                    )
                    for tile in np.flatnonzero(members):
                        proposals[row, tile] = data.budgets[row].project(
                            (1 - 0.25 * data.dt) * data.proposals[row, tile] + 0.25 * data.dt * mean
                        )
                    if len(groups) > 1:
                        isolated += 1
                        for tile in np.flatnonzero(members):
                            age = (
                                int(data.isolation_age[row, tile])
                                if (
                                    data.previous_connectivity[row, tile] == label
                                    and data.previous_population[row, tile] > 0
                                )
                                else 0
                            )
                            if age == np.iinfo(np.int64).max:
                                raise ValueError("Isolation age exceeds int64")
                            if age + 1 > context.turn_id:
                                raise ValueError("Isolation age cannot exceed the current turn")
                            ages[row, tile] = age + 1
            return replace(
                result(
                    context,
                    self.contract.name,
                    arrays={
                        "trait_proposals": proposals,
                        "gene_connectivity": labels,
                        "isolation_age": ages,
                    },
                    metrics={
                        "occupied_components": components,
                        "isolated_components": isolated,
                        "mixing_fraction": 0.25 * data.dt,
                        "max_isolation_age": int(ages.max(initial=0)),
                    },
                ),
                stage_version=MODEL_VERSION,
            )

"""Opt-in policy plus fused adaptive-resolution evolution stage.

Replace Mutation/Drift/GeneFlow/Adaptation with these two classes after the
unchanged fitness stage. Keep all ecology, population, speciation and extinction
stages. All-Critical mode delegates the original four numerical stages exactly.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from ..context import TurnContext
from ..contracts import SimulationStage, StageContract, StageResult
from ..reducer import apply_delta, restrict
from ..values import JsonValue, digest
from .adaptation import AdaptationStage
from .common import result, state_patch
from .evolution_contracts import READS, evolution_inputs
from .genetics import (
    GeneFlowStage,
    GeneticDriftStage,
    MutationStage,
    drift_proposal,
    mutation_proposal,
)
from .resolution_kernels import adapt_scheduled, component_proposals
from .resolution_policy import BACKGROUND_INTERVAL, read_schedule
from .resolution_policy import ResolutionPolicyStage as ResolutionPolicyStage

_ARRAYS = (
    "trait_proposals",
    "deme_traits",
    "gene_population",
    "gene_connectivity",
    "isolation_age",
)


def _apply(context: TurnContext, stage: SimulationStage) -> tuple[TurnContext, StageResult]:
    view = restrict(context, stage.contract.reads)
    proposal = stage.execute(view)
    stage.validate_outputs(view, proposal)
    return context.with_snapshot(
        apply_delta(context.snapshot, proposal.state_delta, writes=stage.contract.writes)
    ), proposal


class ResolutionEvolutionStage(SimulationStage):
    contract = StageContract(
        "reference_adaptation",
        "resolution-1",
        ("reference_resolution",),
        reads=(*READS, "state.evolution"),
        writes=(*(f"arrays.{name}" for name in _ARRAYS), "state.species", "state.evolution"),
    )

    def execute(self, context: TurnContext) -> StageResult:
        self.validate_inputs(context)
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            return self._execute(context)

    def _execute(self, context: TurnContext) -> StageResult:
        schedule, decisions = read_schedule(context)
        data = evolution_inputs(context)
        fine = aggregate = skipped = 0
        for item in data.species:
            occupied = int(np.count_nonzero(data.population[item.slot]))
            decision = decisions[item.identity]
            if decision.tier == "Critical":
                fine += occupied
            elif not decision.due:
                skipped += occupied
        exact = all(d.tier == "Critical" and d.due and d.dt == data.dt for d in decisions.values())
        current = context
        if exact:
            for stage in (MutationStage(), GeneticDriftStage(), GeneFlowStage(), AdaptationStage()):
                current, adapted = _apply(current, stage)
        else:
            proposals = data.deme.copy()
            for item in data.species:
                decision = decisions[item.identity]
                if not decision.due:
                    continue
                local = replace(data, dt=decision.dt)
                if decision.tier == "Critical":
                    mutated, _ = mutation_proposal(context, local, item)
                    proposals[item.slot], _ = drift_proposal(context, local, item, mutated)
                else:
                    proposals[item.slot], groups = component_proposals(
                        context, local, item, decision.dt
                    )
                    aggregate += groups
            proposed = result(context, self.contract.name, arrays={"trait_proposals": proposals})
            current = context.with_snapshot(
                apply_delta(context.snapshot, proposed.state_delta, writes=self.contract.writes)
            )
            # Real labels and isolation ages always advance at the actual turn dt.
            current, _ = _apply(current, GeneFlowStage())
            adapted = adapt_scheduled(current, evolution_inputs(current), decisions)
            current = current.with_snapshot(
                apply_delta(current.snapshot, adapted.state_delta, writes=self.contract.writes)
            )
        records: dict[str, JsonValue] = {}
        recently_adapted = {
            event.actor for event in adapted.events if event.type == "SpeciesAdapted"
        }
        for identity, decision in decisions.items():
            records[identity] = replace(
                decision,
                last_update=context.turn_id if decision.due else decision.last_update,
                elapsed=0.0 if decision.due else decision.elapsed,
                recent_until=(
                    context.turn_id + BACKGROUND_INTERVAL
                    if identity in recently_adapted
                    else decision.recent_until
                ),
            ).to_json()
        evolution = {
            **context.snapshot.domain("evolution"),
            "resolution": {
                **schedule,
                "complete": True,
                "records": records,
                "plan_hash": digest(records),
            },
        }
        # Original fine traces rerun two kernels for significant adaptations.
        trace_work = (
            sum(
                2 * int(np.count_nonzero(data.population[item.slot]))
                for item in data.species
                if any(event.actor == item.identity for event in adapted.events)
            )
            if exact
            else 0
        )
        metrics: dict[str, JsonValue] = {
            **adapted.metrics,
            "fine_demes": fine,
            "aggregate_components": aggregate,
            "skipped_demes": skipped,
            "fine_kernel_evaluations": 3 * fine + trace_work,
            "aggregate_kernel_evaluations": 3 * aggregate,
            "gene_flow_tile_updates": int(
                np.count_nonzero((data.population > 0) & (data.connectivity >= 0))
            ),
            "effective_years_max": max((d.dt for d in decisions.values()), default=0.0),
            "discarded_elapsed_years": sum(d.discarded_years for d in decisions.values()),
            "all_critical_exact_path": exact,
            "scope": (
                "mutation/drift/adaptation only; fitness and ecological accounting "
                "remain full resolution"
            ),
        }
        proposal = result(
            context,
            self.contract.name,
            arrays={name: current.snapshot.arrays[name].numpy() for name in _ARRAYS},
            state=(
                state_patch(context, ("species",), current.species_state),
                state_patch(context, ("evolution",), evolution),
            ),
            events=adapted.events,
            metrics=metrics,
        )
        return replace(
            proposal,
            stage_version=self.contract.version,
            evolution_proposals=adapted.evolution_proposals,
        )

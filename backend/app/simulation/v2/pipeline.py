"""Stable DAG execution with isolated stage views and fail-closed candidate reduction."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from time import perf_counter

from .context import TurnContext
from .contracts import SimulationStage
from .numerics import isolated_numerics
from .reducer import apply_delta, covers, restrict, validate_snapshot


class StageExecutionError(RuntimeError):
    def __init__(self, stage_name: str, message: str) -> None:
        self.stage_name = stage_name
        super().__init__(f"Stage {stage_name}: {message}")


class DeterministicPipeline:
    """The output is a candidate only; CommitService later owns durable publication."""

    def __init__(self, stages: Iterable[SimulationStage]) -> None:
        pending = list(stages)
        names = {stage.contract.name for stage in pending}
        if len(names) != len(pending):
            raise ValueError("Duplicate stage IDs")
        for stage in pending:
            contract = stage.contract
            if not contract.deterministic or contract.side_effects:
                raise ValueError(f"Simulation stage {contract.name} must be pure and deterministic")
            if set(contract.dependencies) - names:
                raise ValueError(f"Missing dependencies for {contract.name}")
        ordered: list[SimulationStage] = []
        visited: set[str] = set()
        ancestors: dict[str, set[str]] = {}
        while pending:
            ready = sorted(
                (s for s in pending if set(s.contract.dependencies) <= visited),
                key=lambda s: s.contract.name,
            )
            if not ready:
                raise ValueError("Stage dependency cycle")
            for stage in ready:
                parents = set(stage.contract.dependencies)
                ancestors[stage.contract.name] = parents.union(
                    *(ancestors[name] for name in parents)
                )
                for earlier in ordered:
                    if earlier.contract.name in ancestors[stage.contract.name]:
                        continue
                    for left in earlier.contract.writes:
                        for right in (*stage.contract.reads, *stage.contract.writes):
                            if covers(left, right) or covers(right, left):
                                raise ValueError("Unordered stage read/write conflict")
                    for left in stage.contract.writes:
                        for right in earlier.contract.reads:
                            if covers(left, right) or covers(right, left):
                                raise ValueError("Unordered stage read/write conflict")
                ordered.append(stage)
                visited.add(stage.contract.name)
                pending.remove(stage)
        self.stages = tuple(ordered)

    @isolated_numerics
    def execute(self, context: TurnContext) -> TurnContext:
        if context.errors:
            raise ValueError(f"Cannot execute an invalid context: {context.errors}")
        validate_snapshot(context.snapshot)
        candidate = context
        event_ids = {event.event_id for event in context.active_events}
        for stage in self.stages:
            contract = stage.contract
            started = perf_counter()
            try:
                stage_context = replace(
                    restrict(candidate, contract.reads), active_events=context.active_events
                )
                stage.validate_inputs(stage_context)
                result = stage.execute(stage_context)
                stage.validate_outputs(stage_context, result)
                snapshot = apply_delta(
                    candidate.snapshot, result.state_delta, writes=contract.writes
                )
                for event in result.events:
                    context.world_version.advance().require(event.version)
                    if event.turn != context.turn_id or event.event_id in event_ids:
                        raise ValueError("Wrong turn or duplicate event identity")
                    event_ids.add(event.event_id)
                result = replace(
                    result,
                    stage_version=contract.version,
                    random_seed=context.seeds.stream(contract.name, contract.version).seed,
                    input_hash=stage_context.input_hash,
                    output_hash=snapshot.state_hash,
                    duration_ms=(perf_counter() - started) * 1000,
                )
                candidate = replace(
                    candidate,
                    snapshot=snapshot,
                    stage_results=(*candidate.stage_results, result),
                    evolution_proposals=(
                        *candidate.evolution_proposals,
                        *result.evolution_proposals,
                    ),
                    ai_jobs=(*candidate.ai_jobs, *result.ai_jobs),
                    metrics={**candidate.metrics, contract.name: result.metrics},
                    warnings=(*candidate.warnings, *result.warnings),
                )
            except Exception as exc:
                raise StageExecutionError(contract.name, str(exc)) from exc
        return candidate

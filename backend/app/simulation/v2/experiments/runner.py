"""Bounded synchronous fork experiments with durable prefix verification and retry."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor

from app.storage.codec import version_from
from app.storage.database import IdempotencyConflict
from app.storage.history import head_row
from app.storage.store import WorldStore

from ..engine import SimulationEngineV2
from ..pipeline import DeterministicPipeline
from ..values import JsonValue, freeze_mapping
from ..version import VersionConflict, WorldVersion
from .persistence import (
    Cursor,
    ExperimentConflict,
    RegisteredPlan,
    ensure_fork,
    progress,
    register,
    timeline_id,
)
from .results import BranchResult, ExperimentResult, comparisons, summarize
from .schemas import Branch, ExperimentPlan

PipelineFactory = Callable[[], DeterministicPipeline]


class ExperimentRunner:
    def __init__(self, store: WorldStore, *, pipeline_factory: PipelineFactory) -> None:
        """Factory is mandatory and must return fresh pipelines for the saved recipe.

        No providers, planners, background daemon or global mutable runner exists.
        The supplied store is shared; each branch gets its own engine/pipeline.
        """
        self.store = store
        self.pipeline_factory = pipeline_factory

    def run(self, plan: ExperimentPlan, *, workers: int = 1) -> ExperimentResult:
        if type(workers) is not int or not 1 <= workers <= 4:
            raise ValueError("Workers must be an integer in [1,4]")
        # Revalidate even model_construct/model_copy objects at the mutation boundary.
        plan = ExperimentPlan.parse_json(plan.model_dump_json())
        source = self.store.history.replay(plan.source.value())
        if source.turn_id > 2**63 - 1 - plan.turns:
            raise ValueError("Experiment turn horizon exceeds the durable int64 range")
        model = source.manifest.get("model")
        if not isinstance(model, str) or not model:
            raise ValueError("Source has no versioned model identity")
        engines = {
            branch.id: SimulationEngineV2(self.store, self.pipeline_factory(), model)
            for branch in plan.branches
        }
        instances = [id(stage) for engine in engines.values() for stage in engine.pipeline.stages]
        if len(instances) != len(set(instances)):
            raise ValueError("Pipeline factory must create independent stage instances")
        for engine in engines.values():
            if any(source.manifest.get(key) != value for key, value in engine.manifest.items()):
                raise ValueError("Factory does not match source recipe; no implicit model upgrade")
        run = register(self.store, plan, source)

        def branch_task(branch: Branch) -> BranchResult:
            return self._branch(run, branch, engines[branch.id])

        if workers == 1:
            branches = tuple(branch_task(branch) for branch in plan.branches)
        else:
            with ThreadPoolExecutor(max_workers=min(workers, len(plan.branches))) as pool:
                branches = tuple(pool.map(branch_task, plan.branches))
        return ExperimentResult(
            run.manifest_hash, run.manifest, branches, comparisons(branches, plan.control)
        )

    def _observed_head(self, run: RegisteredPlan, branch: Branch) -> WorldVersion | None:
        with self.store.db.connection() as connection:
            try:
                return version_from(
                    dict(
                        head_row(
                            connection, run.source.version.world_id, timeline_id(run.plan, branch)
                        )
                    )
                )
            except KeyError:
                return None

    def _branch(
        self, run: RegisteredPlan, branch: Branch, engine: SimulationEngineV2
    ) -> BranchResult:
        cursor: Cursor | None = None
        observations: tuple[Mapping[str, JsonValue], ...] = ()
        try:
            cursor = ensure_fork(self.store, run, branch)
            # Each iteration either observes at least one new owned turn or
            # commits one. CAS races with another identical runner are harmless;
            # an external command makes the next prefix verification fail.
            for _ in range(run.plan.turns + 1):
                cursor, newly_committed = progress(self.store, run, branch, cursor)
                observations += newly_committed
                if cursor.version.revision == run.plan.turns:
                    snapshot = self.store.history.replay(cursor.version)
                    if self._observed_head(run, branch) != cursor.version:
                        raise VersionConflict("Branch changed while completing the experiment")
                    return BranchResult(
                        branch.id,
                        timeline_id(run.plan, branch),
                        "completed",
                        run.plan.turns,
                        run.plan.turns,
                        cursor.version,
                        cursor.version,
                        cursor.turn,
                        cursor.state_hash,
                        observations,
                        summarize(snapshot, observations),
                    )
                command = run.command(branch, cursor, cursor.version.revision + 1)
                try:
                    engine.run_turn(command)
                except VersionConflict:
                    # Distinguish an identical concurrent success from a foreign
                    # update by verifying actual committed command inputs above.
                    if self._observed_head(run, branch) == cursor.version:
                        raise
            raise ExperimentConflict("Experiment exceeded its bounded progress attempts")
        except Exception as exc:
            failure = exc
            # A commit can succeed before its acknowledgement is interrupted.
            # Report the durable verified prefix, including that successful turn.
            if cursor is not None:
                try:
                    cursor, durable = progress(self.store, run, branch, cursor)
                    observations += durable
                except Exception as verification_error:
                    failure = verification_error
            conflict = isinstance(
                failure, (ExperimentConflict, VersionConflict, IdempotencyConflict)
            )
            return BranchResult(
                branch.id,
                timeline_id(run.plan, branch),
                "conflict" if conflict else "failed",
                cursor.version.revision if cursor else 0,
                run.plan.turns,
                cursor.version if cursor else None,
                self._observed_head(run, branch),
                cursor.turn if cursor else None,
                cursor.state_hash if cursor else None,
                observations,
                freeze_mapping({}),
                freeze_mapping({"type": type(failure).__name__, "message": str(failure)}),
            )

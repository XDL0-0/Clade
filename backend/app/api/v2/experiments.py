"""Explicit experiment execution and read-only durable progress projections."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.simulation.v2.experiments import ExperimentPlan, ExperimentRunner
from app.simulation.v2.experiments.persistence import (
    Cursor,
    ExperimentConflict,
    RegisteredPlan,
    progress,
    timeline_id,
)
from app.simulation.v2.values import JsonValue, digest, thaw
from app.simulation.v2.version import WorldVersion
from app.storage.codec import decode
from app.storage.database import StorageCorruption

from .schemas import ID_PATTERN, Page
from .service import NotFound, SimulationService

RouteID = Annotated[str, Path(pattern=ID_PATTERN, min_length=1, max_length=64)]


def _registered(service: SimulationService, world: str, identity: str) -> RegisteredPlan:
    service.require_world(world)
    with service.store.db.connection() as connection:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='simulation_experiments'"
        ).fetchone()
        row = (
            connection.execute(
                "SELECT * FROM simulation_experiments WHERE world_id=? AND experiment_id=?",
                (world, identity),
            ).fetchone()
            if table
            else None
        )
    if row is None:
        raise NotFound("Experiment not found")
    manifest = decode(row["payload"])
    if digest(manifest) != row["manifest_hash"] or manifest.get("format") != "clade.experiment.v1":
        raise StorageCorruption("Experiment manifest checksum or format mismatch")
    try:
        from app.simulation.v2.values import canonical_bytes

        plan = ExperimentPlan.parse_json(canonical_bytes(manifest["plan"]))
        if plan.source.world_id != world or plan.id != identity:
            raise ValueError("Experiment identity differs from its stored plan")
        source = service.store.history.replay(plan.source.value())
        seed = service.store.world_seed(world)
        if (
            manifest.get("source_snapshot_id") != source.snapshot_id
            or manifest.get("source_state_hash") != source.state_hash
            or manifest.get("model_manifest") != source.manifest
            or manifest.get("seed") != seed
            or manifest.get("branch_timelines")
            != {branch.id: timeline_id(plan, branch) for branch in plan.branches}
        ):
            raise ValueError("Experiment source disagrees with its frozen manifest")
    except (KeyError, TypeError, ValueError) as error:
        raise StorageCorruption("Invalid stored experiment manifest") from error
    return RegisteredPlan(plan, manifest, row["manifest_hash"], Cursor.snapshot(source), seed)


def experiment_view(
    service: SimulationService, world: str, identity: str
) -> Mapping[str, JsonValue]:
    run = _registered(service, world, identity)
    branches: list[JsonValue] = []
    for branch in run.plan.branches:
        timeline = timeline_id(run.plan, branch)
        record: dict[str, JsonValue] = {
            "branch_id": branch.id,
            "name": branch.name,
            "timeline_id": timeline,
            "target_turns": run.plan.turns,
        }
        try:
            service.head_version(world, timeline)
        except NotFound:
            record.update(status="pending", completed_turns=0, observations=())
        else:
            try:
                cursor, observations = progress(
                    service.store,
                    run,
                    branch,
                    Cursor(WorldVersion(world, timeline), run.source.turn, run.source.state_hash),
                )
                record.update(
                    status="completed" if cursor.version.revision == run.plan.turns else "partial",
                    completed_turns=cursor.version.revision,
                    version=cursor.version.to_dict(),
                    turn=cursor.turn,
                    state_hash=cursor.state_hash,
                    observations=observations,
                )
            except ExperimentConflict as error:
                record.update(status="conflict", error=str(error))
        branches.append(record)
    return {
        "manifest_hash": run.manifest_hash,
        "manifest": run.manifest,
        "branches": tuple(branches),
    }


def list_experiments(service: SimulationService, world: str, page: Page) -> Mapping[str, JsonValue]:
    service.require_world(world)
    with service.store.db.connection() as connection:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='simulation_experiments'"
        ).fetchone()
        rows = (
            connection.execute(
                "SELECT experiment_id FROM simulation_experiments "
                "WHERE world_id=? ORDER BY experiment_id LIMIT ? OFFSET ?",
                (world, page.limit + 1, page.offset),
            ).fetchall()
            if table
            else []
        )
    items: list[JsonValue] = []
    for row in rows[: page.limit]:
        run = _registered(service, world, row["experiment_id"])
        items.append(
            {
                "id": run.plan.id,
                "name": run.plan.name,
                "source": run.plan.source.model_dump(),
                "manifest_hash": run.manifest_hash,
                "turns": run.plan.turns,
                "branch_count": len(run.plan.branches),
            }
        )
    return {
        "items": tuple(items),
        "next_offset": page.offset + page.limit if len(rows) > page.limit else None,
    }


def create_experiment_router(service: SimulationService, *, prefix: str) -> APIRouter:
    router = APIRouter(prefix=prefix)
    execution_gate = asyncio.Lock()

    @router.post("/experiments/run")
    async def run(request: Request, workers: Annotated[int, Query(ge=1, le=4)] = 2) -> JSONResponse:
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 4_000_000:
                raise HTTPException(status_code=413, detail="Experiment exceeds 4 MB")
        plan = ExperimentPlan.parse_json(bytes(raw))
        service.require_version(plan.source.value())
        factory = service.pipeline_factory_for(plan.source.world_id)
        runner = ExperimentRunner(service.store, pipeline_factory=factory)
        # No detached task. A disconnect can leave a committed prefix; submitting
        # the same frozen plan validates that prefix and resumes safely.
        # At most one batch (up to four workers) per application. These locks
        # coordinate execution only; SQLite command identity remains authority.
        async with execution_gate:
            result = await run_in_threadpool(runner.run, plan, workers=workers)
        return JSONResponse(thaw(result.to_dict()))

    @router.get("/worlds/{world_id}/experiments")
    def listing(world_id: RouteID, page: Annotated[Page, Query()]) -> JSONResponse:
        return JSONResponse(thaw(list_experiments(service, world_id, page)))

    @router.get("/worlds/{world_id}/experiments/{experiment_id}")
    def detail(world_id: RouteID, experiment_id: RouteID) -> JSONResponse:
        return JSONResponse(thaw(experiment_view(service, world_id, experiment_id)))

    return router

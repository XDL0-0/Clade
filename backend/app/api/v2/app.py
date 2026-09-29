"""Standalone CPU FastAPI factory; importing it never starts the legacy runtime."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.simulation.v2.experiments import ExperimentConflict
from app.simulation.v2.pipeline import StageExecutionError
from app.simulation.v2.reference.model import explainable_pipeline
from app.simulation.v2.version import VersionConflict
from app.storage.database import IdempotencyConflict, StorageCorruption

from .experiments import create_experiment_router
from .routes import create_router
from .service import NarrativePlanner, NotFound, PipelineFactory, SimulationService


def create_app(
    root: Path,
    *,
    pipeline_factory: PipelineFactory = explainable_pipeline,
    narrative_planner: NarrativePlanner | None = None,
    route_prefix: str = "/api/v2",
    compatible_factories: tuple[PipelineFactory, ...] = (),
) -> FastAPI:
    service = SimulationService(
        root, pipeline_factory, narrative_planner, compatible_factories=compatible_factories
    )
    application = FastAPI(title="Clade CPU simulation v2", version="2")
    application.state.simulation_service = service
    application.include_router(create_router(service, prefix=route_prefix))
    application.include_router(create_experiment_router(service, prefix=route_prefix))

    async def not_found(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=404)

    async def conflict(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    async def invalid(request: Request, exc: Exception) -> JSONResponse:
        if isinstance(exc, StageExecutionError) and not isinstance(
            exc.__cause__, (ValueError, FloatingPointError)
        ):
            raise exc
        if isinstance(exc.__cause__, StorageCorruption):
            raise exc
        return JSONResponse({"detail": str(exc)}, status_code=422)

    async def corrupt(request: Request, exc: Exception) -> JSONResponse:
        logging.getLogger(__name__).error(
            "Committed world storage failed validation",
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        return JSONResponse(
            {"detail": "Committed world storage failed validation"}, status_code=500
        )

    application.add_exception_handler(NotFound, not_found)
    for conflict_type in (VersionConflict, IdempotencyConflict, ExperimentConflict):
        application.add_exception_handler(conflict_type, conflict)
    for invalid_type in (ValueError, StageExecutionError):
        application.add_exception_handler(invalid_type, invalid)
    application.add_exception_handler(StorageCorruption, corrupt)
    return application

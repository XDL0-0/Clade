"""Explicit Lab recipe and a small mount boundary for the existing application."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from app.ai.jobs.planner import ReferenceNarrativePlanner
from app.simulation.v2.reference.model import (
    ecological_pipeline,
    evolution_pipeline,
    explainable_pipeline,
    feedback_pipeline,
)

from .app import create_app


def create_lab(root: Path, *, mounted: bool = False) -> FastAPI:
    return create_app(
        root,
        pipeline_factory=feedback_pipeline,
        narrative_planner=ReferenceNarrativePlanner(),
        compatible_factories=(evolution_pipeline, explainable_pipeline, ecological_pipeline),
        route_prefix="" if mounted else "/api/v2",
    )


def mount_lab(application: FastAPI, root: Path) -> None:
    """Called at startup, not import; repeated lifespan entries reuse this app store."""
    if getattr(application.state, "reference_lab", None) is None:
        lab = create_lab(root, mounted=True)
        application.mount("/api/v2", lab, name="simulation-v2")
        application.state.reference_lab = lab

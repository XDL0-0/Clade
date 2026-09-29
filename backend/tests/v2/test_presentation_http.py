"""Presentation routes preserve selected history and distinguish local templates."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient

from app.ai.jobs.models import JobSpec
from app.api.v2 import SimulationService
from app.api.v2.runtime import create_lab
from app.simulation.v2.context import TurnContext, WorldSnapshot
from app.simulation.v2.contracts import StageResult
from app.simulation.v2.events import WorldEvent
from scripts.run_narrative_jobs import run_jobs

BASE = "/api/v2/worlds/readers/main"


def annotate(service: SimulationService, before: WorldSnapshot) -> WorldSnapshot:
    future = replace(before, version=before.version.advance(), turn_id=before.turn_id + 1)
    event = WorldEvent.create(
        version=future.version,
        turn=future.turn_id,
        command_id=f"observe-{future.turn_id}",
        stage="observation_fixture",
        ordinal=0,
        event_type="SpeciesObserved",
        target="grazer",
    )
    job = JobSpec(
        future.version,
        future.turn_id,
        "species",
        ("grazer",),
        (event.event_id,),
        snapshot_id=future.snapshot_id,
    )
    context = TurnContext(
        future.turn_id,
        before,
        31,
        stage_results=(StageResult("observation_fixture", events=(event,)),),
    )
    committed = service.store.commit(context, command_key=f"observe-{future.turn_id}", jobs=(job,))
    finished = asyncio.run(run_jobs(service.store.root, limit=1))
    assert len(finished) == 1
    return committed


def test_http_annotation_origin_history_rewind_and_diagnostic_scope(tmp_path: Path) -> None:
    app = create_lab(tmp_path)
    service = cast(SimulationService, app.state.simulation_service)
    with TestClient(app) as client:
        initial = client.post(
            "/api/v2/worlds",
            json={"world_id": "readers", "seed": 31, "width": 4, "height": 2, "max_species": 8},
        )
        assert initial.status_code == 201
        zero = service.store.head("readers", "main")
        one = annotate(service, zero)
        two = annotate(service, one)
        page = client.get(BASE + "/narratives?turn=1&species_id=grazer&limit=1").json()
        assert page["version"] == one.version.to_dict()
        assert len(page["items"]) == 1
        annotation = page["items"][0]["annotations"][0]
        assert annotation["source"] == "offline_template"
        assert annotation["fallback_used"] is False
        assert annotation["result"]["species_id"] == "grazer"
        assert client.get(BASE + "/narratives?turn=0").json()["items"] == []
        assert client.get(BASE + "/narratives?species_id=hunter").json()["items"] == []
        restored = client.post(
            BASE + "/rewind",
            json={
                "expected_version": two.version.to_dict(),
                "source_version": one.version.to_dict(),
                "idempotency_key": "restore-one",
            },
        )
        assert restored.status_code == 200
        groups = client.get(BASE + "/narratives").json()["items"]
        assert [group["turn"] for group in groups] == [1]
        assert groups[0]["annotations"] == [annotation]
        diagnostics = client.get(BASE + "/diagnostics?turn=0").json()
        assert diagnostics["turn"] == 0 and diagnostics["species_count"] == 7
        assert diagnostics["scope"]["rss_bytes"] == "current_process"
        assert diagnostics["scope"]["save_bytes"] == "entire_store_directory"
        assert diagnostics["gpu_memory_bytes"] is diagnostics["ai_token_usage"] is None
        assert diagnostics["save_bytes"] > 0
        assert service.store.head("readers", "main").snapshot_id == restored.json()["snapshot_id"]
        assert service.store.history.replay(zero.version).state_hash == zero.state_hash


@pytest.mark.parametrize(
    "suffix, status",
    [
        ("/narratives?limit=0", 422),
        ("/narratives?offset=-1", 422),
        ("/narratives?species_id=absent", 404),
        ("/narratives?turn=999", 404),
        ("/diagnostics?turn=999", 404),
        ("/diagnostics?turn=true", 422),
        ("/diagnostics?unexpected=yes", 422),
    ],
)
def test_presentation_routes_reject_invalid_or_missing_selection(
    tmp_path: Path, suffix: str, status: int
) -> None:
    with TestClient(create_lab(tmp_path)) as client:
        client.post(
            "/api/v2/worlds",
            json={"world_id": "readers", "width": 4, "height": 2, "max_species": 8},
        )
        assert client.get(BASE + suffix).status_code == status
        assert client.get("/api/v2/worlds/missing/main/diagnostics").status_code == 404

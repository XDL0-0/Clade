"""Production Lab recipe and mount keep the legacy surface separate."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v2.runtime import create_lab, mount_lab


def test_lab_recipe_runs_evolution_with_full_profile(tmp_path: Path) -> None:
    with TestClient(create_lab(tmp_path)) as client:
        initial = client.post(
            "/api/v2/worlds",
            json={"world_id": "lab", "seed": 4, "width": 4, "height": 2, "max_species": 8},
        )
        assert initial.status_code == 201
        advanced = client.post(
            "/api/v2/worlds/lab/main/turns",
            json={"expected_version": initial.json()["version"], "idempotency_key": "one"},
        )
        assert advanced.status_code == 200, advanced.text
        profile = client.get("/api/v2/worlds/lab/main/profile").json()["stages"]
        assert len(profile) == 28
        assert "reference_adaptation" in {stage["stage_name"] for stage in profile}
        assert "reference_niche" in {stage["stage_name"] for stage in profile}
        assert "habitat_complexity" in advanced.json()["map"]


def test_mount_has_single_prefix_and_independent_exception_handlers(tmp_path: Path) -> None:
    parent = FastAPI()
    mount_lab(parent, tmp_path)
    mount_lab(parent, tmp_path)
    assert len([route for route in parent.routes if getattr(route, "path", "") == "/api/v2"]) == 1
    with TestClient(parent) as client:
        assert client.get("/api/v2/worlds").json()["items"] == []
        assert client.get("/api/v2/worlds/absent/main/snapshot").status_code == 404
        assert (
            client.post("/api/v2/worlds", json={"world_id": "bad", "seed": True}).status_code == 422
        )

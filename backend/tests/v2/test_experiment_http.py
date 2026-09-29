"""Real HTTP experiments use recorded manifests and never mutate source worlds."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v2 import SimulationService
from app.api.v2.runtime import create_lab


def setup(client: TestClient) -> dict[str, Any]:
    initial = client.post(
        "/api/v2/worlds",
        json={"world_id": "scenarios", "seed": 37, "width": 4, "height": 2, "max_species": 8},
    )
    assert initial.status_code == 201
    return {
        "version": 1,
        "id": "paired",
        "name": "Paired warming",
        "source": initial.json()["version"],
        "turns": 2,
        "rng_namespace": "paired",
        "control": "control",
        "branches": [
            {
                "id": "control",
                "name": "Control",
                "scenario": {"version": 1, "id": "baseline", "name": "Unchanged", "forcing": []},
            },
            {
                "id": "warm",
                "name": "Warmer",
                "scenario": {
                    "version": 1,
                    "id": "warming",
                    "name": "Warm forcing",
                    "forcing": [{"turn": 1, "warming_offset": 4.0}],
                },
            },
        ],
    }


def test_parallel_http_run_retries_listing_and_readonly_progress(tmp_path: Path) -> None:
    app = create_lab(tmp_path)
    with TestClient(app) as client:
        plan = setup(client)
        listing = "/api/v2/worlds/scenarios/experiments"
        assert client.get(listing).json()["items"] == []
        result = client.post("/api/v2/experiments/run?workers=2", json=plan)
        assert result.status_code == 200, result.text
        data = result.json()
        assert data["completed"] is True
        assert [branch["completed_turns"] for branch in data["branches"]] == [2, 2]
        assert data["comparisons"]["warm"]["available"] is True
        same = client.post("/api/v2/experiments/run?workers=1", json=plan)
        assert same.status_code == 200 and same.json() == data
        items = client.get(listing).json()["items"]
        assert len(items) == 1 and items[0]["id"] == "paired"
        view = client.get(listing + "/paired").json()
        assert view["manifest_hash"] == data["manifest_hash"]
        assert [branch["status"] for branch in view["branches"]] == ["completed", "completed"]
        assert [len(branch["observations"]) for branch in view["branches"]] == [2, 2]
        source = client.get("/api/v2/worlds/scenarios/main/snapshot").json()
        assert source["turn"] == 0 and source["version"] == plan["source"]
        world_timelines = client.get("/api/v2/worlds/scenarios/timelines").json()["items"]
        assert len(world_timelines) == 3
        assert client.get(listing + "/absent").status_code == 404
        assert client.get("/api/v2/worlds/absent/experiments").status_code == 404
        plan["branches"][1]["scenario"]["forcing"][0]["warming_offset"] = 5.0
        conflict = client.post("/api/v2/experiments/run", json=plan)
        assert conflict.status_code == 409
        assert client.get(listing + "/paired").json() == view


@pytest.mark.parametrize("bad", ["duplicate", "unknown", "nan", "bool", "empty", "source"])
def test_experiment_http_strict_inputs_do_not_create_branches(tmp_path: Path, bad: str) -> None:
    with TestClient(create_lab(tmp_path)) as client:
        plan = setup(client)
        status = 422
        if bad == "unknown":
            plan["decide_population"] = 1000000
        elif bad == "nan":
            plan["branches"][1]["scenario"]["forcing"][0]["warming_offset"] = float("nan")
        elif bad == "bool":
            plan["version"] = True
        elif bad == "empty":
            plan = {}
        elif bad == "source":
            plan["source"]["revision"] = 99
            status = 404
        encoded = json.dumps(plan)
        if bad == "duplicate":
            encoded = encoded[:-1] + ', "id": "another"}'
        result = client.post(
            "/api/v2/experiments/run", content=encoded, headers={"content-type": "application/json"}
        )
        assert result.status_code == status, result.text
        assert len(client.get("/api/v2/worlds/scenarios/timelines").json()["items"]) == 1


def test_experiment_manifest_corruption_and_foreign_rewind_are_not_hidden(tmp_path: Path) -> None:
    with TestClient(create_lab(tmp_path)) as client:
        plan = setup(client)
        data = client.post("/api/v2/experiments/run", json=plan).json()
        branch = data["branches"][0]
        rewind = client.post(
            f"/api/v2/worlds/scenarios/{branch['timeline_id']}/rewind",
            json={
                "expected_version": branch["version"],
                "source_version": plan["source"],
                "idempotency_key": "foreign-rewind",
            },
        )
        assert rewind.status_code == 200
        path = "/api/v2/worlds/scenarios/experiments/paired"
        assert client.get(path).json()["branches"][0]["status"] == "conflict"
        retried = client.post("/api/v2/experiments/run", json=plan).json()
        assert retried["completed"] is False
        assert retried["branches"][0]["status"] == "conflict"
        service = cast(SimulationService, cast(FastAPI, client.app).state.simulation_service)
        with service.store.db.transaction() as connection:
            connection.execute("UPDATE simulation_experiments SET manifest_hash='broken'")
        assert client.get(path).status_code == 500
        assert client.get("/api/v2/worlds/scenarios/experiments").status_code == 500

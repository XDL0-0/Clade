"""Changing the default new-world recipe never silently changes existing worlds."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.api.v2 import create_app
from app.api.v2.runtime import create_lab
from app.simulation.v2.reference.model import (
    ecological_pipeline,
    evolution_pipeline,
    explainable_pipeline,
)


def test_lab_selects_saved_explicit_recipe(tmp_path: Path) -> None:
    for name, recipe in (
        ("ecology", ecological_pipeline),
        ("explanation", explainable_pipeline),
        ("evolution", evolution_pipeline),
    ):
        with TestClient(create_app(tmp_path, pipeline_factory=recipe)) as client:
            created = client.post(
                "/api/v2/worlds", json={"world_id": name, "width": 4, "height": 2, "max_species": 8}
            ).json()
        with TestClient(create_lab(tmp_path)) as client:
            prefix = f"/api/v2/worlds/{name}/main"
            result = client.post(
                prefix + "/turns",
                json={"expected_version": created["version"], "idempotency_key": "one"},
            )
            assert result.status_code == 200, result.text
            stages = client.get(prefix + "/profile").json()["stages"]
            assert len(stages) == len(recipe().stages)
            assert not any(stage["stage_name"] == "reference_niche" for stage in stages)
            assert client.get(prefix + "/snapshot?turn=0").json() == created

"""Captured pre-feedback 26-stage state is a regression oracle for shared helpers."""

from dataclasses import replace
from pathlib import Path

from app.simulation.v2.context import TurnContext
from app.simulation.v2.engine import SimulationEngineV2
from app.simulation.v2.reference.model import evolution_pipeline
from app.simulation.v2.reference.world import MODEL_ID, create_reference_snapshot
from app.simulation.v2.version import WorldVersion
from app.storage.store import WorldStore


def test_shared_physiology_and_feeding_extensions_preserve_original_recipe(tmp_path: Path) -> None:
    pipeline = evolution_pipeline()
    engine = SimulationEngineV2(WorldStore(tmp_path), pipeline, MODEL_ID)
    snapshot = create_reference_snapshot(
        WorldVersion("reference-benchmark", "control"),
        seed=37,
        manifest=engine.manifest,
        width=8,
        height=4,
        max_species=16,
    )
    for turn in range(1, 101):
        output = pipeline.execute(
            TurnContext(turn, snapshot, 37, command={"command_id": f"turn-{turn}"})
        )
        snapshot = replace(output.snapshot, version=snapshot.version.advance(), turn_id=turn)
    # evidence/evolution-1000-seed37.json, recorded before feedback kernels existed.
    assert snapshot.state_hash == "73e81e617d141600c2cb1c99ca98e9740ee5924f236b7ee75d15216bd75b4d5f"

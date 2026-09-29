"""Real environment-to-lifecycle integration without AI or trait mutation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import numpy as np

from app.simulation.v2.context import TurnContext
from app.simulation.v2.engine import SimulationEngineV2, TurnCommand
from app.simulation.v2.reference.model import explainable_pipeline
from app.simulation.v2.reference.world import MODEL_ID, create_reference_snapshot
from app.simulation.v2.values import JsonValue
from app.simulation.v2.version import WorldVersion
from app.storage.observations import ObservationReader
from app.storage.store import WorldStore


def test_pressure_gradient_and_extinction_are_durable_facts(tmp_path: Path) -> None:
    store = WorldStore(tmp_path / "world")
    pipeline = explainable_pipeline()
    engine = SimulationEngineV2(store, pipeline, MODEL_ID)
    initial = store.create(
        create_reference_snapshot(
            WorldVersion("traces", "control"),
            seed=37,
            manifest=engine.manifest,
            width=4,
            height=3,
            max_species=8,
        ),
        seed=37,
    )
    current = engine.run_turn(
        TurnCommand(initial.version, "disaster", payload={"disaster_severity": 1.0})
    )
    assert len(ObservationReader(store.db).profile(current.version)) == 20
    assert current.arrays["selection_pressure"].shape == (8, 7)
    assert current.arrays["fitness_gradients"].shape == (8, 7)
    assert np.isfinite(current.arrays["fitness_gradients"].numpy()).all()
    assert store.history.replay(current.version).snapshot_id == current.snapshot_id
    assert initial.snapshot_id == store.history.replay(initial.version).snapshot_id
    for identity, phenotype in current.domain("species").items():
        old = initial.domain("species")[identity]
        assert isinstance(phenotype, Mapping) and isinstance(old, Mapping)
        assert phenotype["traits"] == old["traits"]  # signals must not change phenotype
        slot = phenotype["slot"]
        assert isinstance(slot, int)
        if phenotype["status"] == "Extinct":
            assert np.all(current.arrays["population"].numpy()[slot] == 0)
            cause = phenotype["extinction_cause"]
            assert isinstance(cause, Mapping) and cause["total_deaths"]
            assert phenotype["last_habitat"] == old["current_habitat_runs"]
    history = [current]
    for turn in range(2, 9):
        current = engine.run_turn(
            TurnCommand(current.version, f"turn-{turn}", payload={"disaster_severity": 1.0})
        )
        history.append(current)
    extinct_ids: set[str] = set()
    for snapshot in history:
        for event in ObservationReader(store.db).events(snapshot.version):
            if event["type"] == "SpeciesExtinct":
                target = event["target"]
                assert isinstance(target, str) and target not in extinct_ids
                extinct_ids.add(target)
    assert extinct_ids


def test_twenty_turns_repeat_with_same_seed_and_input() -> None:
    pipeline = explainable_pipeline()
    manifest: dict[str, JsonValue] = {
        "model": MODEL_ID,
        "stages": {stage.contract.name: stage.contract.version for stage in pipeline.stages},
    }
    initial = create_reference_snapshot(
        WorldVersion("signals", "main"),
        seed=10,
        manifest=manifest,
        width=4,
        height=3,
        max_species=8,
    )
    left = right = initial
    for turn in range(1, 21):
        a = pipeline.execute(TurnContext(turn, left, 10, command={"command_id": str(turn)}))
        b = pipeline.execute(TurnContext(turn, right, 10, command={"command_id": str(turn)}))
        assert a.snapshot.state_hash == b.snapshot.state_hash
        assert [event for result in a.stage_results for event in result.events] == [
            event for result in b.stage_results for event in result.events
        ]
        left = replace(a.snapshot, version=left.version.advance(), turn_id=turn)
        right = replace(b.snapshot, version=right.version.advance(), turn_id=turn)

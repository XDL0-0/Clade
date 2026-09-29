"""All seventeen ecological stages share a durable, replayable mass balance."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import numpy as np
import pytest

from app.simulation.v2.engine import SimulationEngineV2, TurnCommand
from app.simulation.v2.reference.model import ecological_pipeline
from app.simulation.v2.reference.world import MODEL_ID, create_reference_snapshot
from app.simulation.v2.version import WorldVersion
from app.storage.observations import ObservationReader
from app.storage.store import WorldStore
from scripts.benchmark_reference_ecology import organic_carbon


def test_ecological_durable_replay_paired_branch_and_conservation(tmp_path: Path) -> None:
    store = WorldStore(tmp_path / "ecosystem", checkpoint_interval=3)
    pipeline = ecological_pipeline()
    engine = SimulationEngineV2(store, pipeline, MODEL_ID)
    current = store.create(
        create_reference_snapshot(
            WorldVersion("integration", "control"),
            seed=37,
            manifest=engine.manifest,
            width=4,
            height=3,
            max_species=8,
        ),
        seed=37,
    )
    history = [current]
    old_carbon = organic_carbon(current)
    total_n = float(current.arrays["nutrients"].numpy().sum()) + 0.02 * old_carbon
    for turn in range(1, 11):
        old = current
        current = engine.run_turn(TurnCommand(current.version, f"turn-{turn}"))
        history.append(current)
        profile = ObservationReader(store.db).profile(current.version)
        values: dict[str, float] = {}
        for stage in profile:
            metrics = stage["metrics"]
            assert isinstance(metrics, Mapping)
            for key in ("carbon_fixed", "carbon_respired"):
                value = metrics.get(key, 0)
                assert isinstance(value, (int, float))
                values[key] = values.get(key, 0) + value
        carbon = organic_carbon(current)
        assert carbon - old_carbon == pytest.approx(
            values["carbon_fixed"] - values["carbon_respired"], abs=1e-8
        )
        assert float(current.arrays["nutrients"].numpy().sum()) + 0.02 * carbon == pytest.approx(
            total_n, abs=1e-8
        )
        old_carbon = carbon
        pop = np.asarray(old.arrays["population"].numpy(), dtype=np.int64)
        a = {
            name: np.asarray(current.arrays[name].numpy(), dtype=np.int64)
            for name in (
                "population",
                "births",
                "deaths",
                "migration_in",
                "migration_out",
            )
        }
        np.testing.assert_array_equal(
            a["population"],
            pop + a["births"] - a["deaths"] + a["migration_in"] - a["migration_out"],
        )
        assert len(profile) == 17
        assert (
            engine.run_turn(TurnCommand(old.version, f"turn-{turn}")).snapshot_id
            == current.snapshot_id
        )
    reopened = WorldStore(tmp_path / "ecosystem")
    for snapshot in history:
        assert reopened.history.replay(snapshot.version).snapshot_id == snapshot.snapshot_id
    fork = store.fork(history[5].version, "paired")
    paired = engine.run_turn(TurnCommand(fork.version, "same-input", rng_namespace="control"))
    assert paired.state_hash == history[6].state_hash
    warmed = engine.run_turn(
        TurnCommand(
            paired.version, "warmer", rng_namespace="control", payload={"warming_offset": 4.0}
        )
    )
    assert warmed.state_hash != history[7].state_hash
    assert store.head("integration", "control").snapshot_id == history[-1].snapshot_id

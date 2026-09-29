"""Genesis is isolated, bounded and compatible with the durable pure engine."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from app.simulation.v2.context import TurnContext
from app.simulation.v2.engine import SimulationEngineV2, TurnCommand
from app.simulation.v2.pipeline import DeterministicPipeline
from app.simulation.v2.reference.environment import BiomeStage, ClimateStage, GeologyStage
from app.simulation.v2.reference.hydrology import HydrologyStage
from app.simulation.v2.reference.resources import (
    PrimaryProductivityStage,
    ResourceRegenerationStage,
)
from app.simulation.v2.reference.world import MODEL_ID, SpeciesSeed, create_reference_snapshot
from app.simulation.v2.values import JsonValue
from app.simulation.v2.version import WorldVersion
from app.storage.store import WorldStore


def pipeline() -> DeterministicPipeline:
    return DeterministicPipeline(
        [
            ClimateStage(),
            GeologyStage(),
            HydrologyStage(),
            BiomeStage(),
            PrimaryProductivityStage(),
            ResourceRegenerationStage(),
        ]
    )


def manifest() -> dict[str, JsonValue]:
    return {
        "model": MODEL_ID,
        "rng": "clade-blake2b-counter-v1",
        "stages": {stage.contract.name: stage.contract.version for stage in pipeline().stages},
    }


def test_genesis_is_stable_sorted_and_independent_of_global_rng() -> None:
    first = SpeciesSeed("a", "producer", 1.0, 10)
    second = SpeciesSeed("b", "herbivore", 2.0, 2)
    version = WorldVersion("world", "main")
    left = create_reference_snapshot(version, seed=10, manifest=manifest(), species=[first, second])
    np.random.seed(666)
    np.random.random(500)
    right = create_reference_snapshot(
        version, seed=10, manifest=manifest(), species=[second, first]
    )
    assert left.snapshot_id == right.snapshot_id
    other = create_reference_snapshot(
        version, seed=11, manifest=manifest(), species=[first, second]
    )
    assert left.state_hash != other.state_hash
    assert np.all(left.arrays["population"].numpy()[2:] == 0)
    assert np.all(left.arrays["energy_reserve"].numpy()[2:] == 0)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"width": 3},
        {"width": True},
        {"height": 0},
        {"max_species": 2},
        {"max_species": 257},
        {"width": 256, "height": 256, "max_species": 256},
        {"manifest": {"model": "legacy"}},
        {"seed": -1},
    ],
)
def test_invalid_genesis_rejected(kwargs: dict[str, object]) -> None:
    args = {"version": WorldVersion("world", "main"), "seed": 10, "manifest": manifest(), **kwargs}
    with pytest.raises((ValueError, TypeError)):
        create_reference_snapshot(**args)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"body_mass": 0},
        {"fertility": -1},
        {"traits": {"arbitrary": 0.2}},
        {"traits": {"armor": float("nan")}},
        {"habitat": "sky"},
        {"population_per_tile": -1},
        {"population_per_tile": True},
        {"traits": {"armor": 1, "speed": 1, "attack": 1, "cooperation": 1}},
    ],
)
def test_invalid_species_rejected(kwargs: dict[str, object]) -> None:
    args = {
        "species_id": "a",
        "role": "producer",
        "body_mass": 1.0,
        "population_per_tile": 2,
        **kwargs,
    }
    with pytest.raises((ValueError, TypeError)):
        SpeciesSeed(**args)  # type: ignore[arg-type]


def test_duplicate_ids_and_non_genesis_version_rejected() -> None:
    item = SpeciesSeed("a", "producer", 1.0, 10)
    version = WorldVersion("world", "main")
    with pytest.raises(ValueError, match="unique"):
        create_reference_snapshot(version, seed=1, manifest=manifest(), species=[item, item])
    with pytest.raises(ValueError, match="Genesis"):
        create_reference_snapshot(version.advance(), seed=1, manifest=manifest())


def test_unknown_topology_fails_closed() -> None:
    snapshot = create_reference_snapshot(
        WorldVersion("world", "main"), seed=8, manifest=manifest(), width=4, height=3
    )
    changed = replace(
        snapshot,
        state={
            **snapshot.state,
            "geometry": {
                "width": 4,
                "height": 3,
                "topology": "square-torus-v999",
            },
        },
    )
    from app.simulation.v2.pipeline import StageExecutionError

    with pytest.raises(StageExecutionError, match="Unknown reference topology"):
        pipeline().execute(TurnContext(1, changed, 8))


def test_environment_resource_integration_is_deterministic_and_closed() -> None:
    snapshot = create_reference_snapshot(
        WorldVersion("world", "main"), seed=8, manifest=manifest(), width=4, height=3
    )
    original = snapshot.state_hash
    context = TurnContext(1, snapshot, 8)
    a, b = pipeline().execute(context), pipeline().execute(context)
    assert a.snapshot.state_hash == b.snapshot.state_hash
    assert snapshot.state_hash == original
    assert a.snapshot.arrays["population"] == snapshot.arrays["population"]
    for stage in a.stage_results[-2:]:
        for name in ("carbon_ledger", "nutrient_ledger", "water_ledger"):
            ledger = stage.metrics[name]
            assert isinstance(ledger, dict) or hasattr(ledger, "keys")
    for array in a.snapshot.arrays.values():
        assert np.isfinite(array.numpy()).all()


def test_durable_environment_replay_and_branch_share_inputs(tmp_path: Path) -> None:
    store = WorldStore(tmp_path / "world", checkpoint_interval=2)
    engine = SimulationEngineV2(store, pipeline(), MODEL_ID)
    initial = create_reference_snapshot(
        WorldVersion("world", "main"), seed=27, manifest=engine.manifest, width=4, height=3
    )
    store.create(initial, seed=27)
    history = [initial]
    for turn in range(1, 5):
        history.append(engine.run_turn(TurnCommand(history[-1].version, f"turn-{turn}")))
    reopened = WorldStore(tmp_path / "world")
    for snapshot in history:
        assert reopened.history.replay(snapshot.version).snapshot_id == snapshot.snapshot_id
    fork = store.fork(history[2].version, timeline_id="warming")
    same = engine.run_turn(TurnCommand(fork.version, "same", rng_namespace="main"))
    assert same.state_hash == history[3].state_hash
    warmer = engine.run_turn(TurnCommand(same.version, "warm", payload={"warming_offset": 4.0}))
    warm_temperature = warmer.domain("environment")["global_temperature"]
    control_temperature = history[4].domain("environment")["global_temperature"]
    assert isinstance(warm_temperature, float) and isinstance(control_temperature, float)
    assert warm_temperature > control_temperature
    assert store.head("world", "main").snapshot_id == history[4].snapshot_id
    assert replace(initial, version=WorldVersion("other", "main")).state_hash == initial.state_hash

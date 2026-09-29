"""Saved commands and fixed turn boundaries are separate from mutable branch heads."""

from dataclasses import replace
from pathlib import Path

import pytest

from app.simulation.v2.context import TurnContext, WorldSnapshot
from app.simulation.v2.version import WorldVersion
from app.storage.database import StorageCorruption
from app.storage.store import WorldStore


def test_commands_preserve_inputs_for_resimulation_and_check_their_hash(tmp_path: Path) -> None:
    store = WorldStore(tmp_path)
    initial = store.create(WorldSnapshot(WorldVersion("w", "t"), 0), seed=42)
    request = TurnContext(
        1,
        initial,
        42,
        command={"action": "advance"},
        external_pressures=({"warming": 4},),
        rng_namespace="common-control-seed",
    )
    first = store.commit(request, command_key="turn-1")
    inputs = store.command_input(first.version)
    assert inputs["command"] == {"action": "advance"}
    assert inputs["pressures"] == ({"warming": 4},)
    assert inputs["seed"] == store.world_seed("w") == 42
    assert inputs["rng"] == "common-control-seed"
    with store.db.transaction() as connection:
        connection.execute("UPDATE commands SET input_payload='{}'")
    with pytest.raises(StorageCorruption, match="checksum"):
        store.command_input(first.version)


def test_wrong_seed_cannot_silently_change_saved_world(tmp_path: Path) -> None:
    store = WorldStore(tmp_path)
    initial = store.create(WorldSnapshot(WorldVersion("w", "t"), 0), seed=42)
    with pytest.raises(ValueError, match="seed"):
        store.commit(TurnContext(1, initial, 43), command_key="wrong-seed")
    assert store.head("w", "t") == initial


def test_turn_lookup_keeps_original_boundary_after_same_turn_replacement(tmp_path: Path) -> None:
    store = WorldStore(tmp_path)
    initial = store.create(WorldSnapshot(WorldVersion("w", "t"), 0, {"counter": 0}), seed=42)
    context = TurnContext(1, initial, 42).with_snapshot(replace(initial, state={"counter": 1}))
    first = store.commit(context, command_key="turn-1")
    replacement = store.replace_head(first.version, first.version, command_key="reload")
    assert replacement.version != first.version
    assert store.head("w", "t").version == replacement.version
    assert store.history.at_turn("w", "t", 1).version == first.version
    child = store.fork(replacement.version, "experiment")
    assert store.history.at_turn("w", "experiment", 1).version == first.version
    second = store.commit(TurnContext(2, child, 42), command_key="turn-2")
    assert store.history.at_turn("w", "experiment", 2).version == second.version
    assert store.history.at_turn("w", "experiment", 1).version == first.version
    assert store.head("w", "t").version == replacement.version


def test_observations_use_the_same_committed_revision(tmp_path: Path) -> None:
    from app.simulation.v2.contracts import StageResult
    from app.simulation.v2.events import WorldEvent
    from app.storage.observations import ObservationReader

    store = WorldStore(tmp_path)
    initial = store.create(WorldSnapshot(WorldVersion("w", "t"), 0), seed=42)
    event = WorldEvent.create(
        version=initial.version.advance(),
        turn=1,
        command_id="turn-1",
        stage="observe",
        ordinal=0,
        event_type="ClimateShift",
    )
    result = StageResult(
        "observe",
        events=(event,),
        stage_version="2",
        duration_ms=3.25,
        input_hash="input",
        output_hash=initial.state_hash,
        random_seed=2**180,
    )
    context = TurnContext(1, initial, 42, stage_results=(result,), metrics={"npp": 7.0})
    first = store.commit(context, command_key="turn-1")
    reader = ObservationReader(store.db)
    profile = reader.profile(first.version)
    assert len(profile) == 1
    assert profile[0]["stage_version"] == "2"
    assert profile[0]["duration_ms"] == 3.25
    assert profile[0]["random_seed"] == str(2**180)
    assert reader.events(first.version)[0]["event_id"] == event.event_id
    assert reader.metrics("w", "t", generation=0) == (
        {"revision": 1, "turn": 1, "metrics": {"npp": 7.0}},
    )
    assert reader.profile(initial.version) == ()
    assert reader.metrics("w", "unrelated", generation=0) == ()

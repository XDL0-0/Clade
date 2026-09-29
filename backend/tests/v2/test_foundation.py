"""Independent acceptance checks for the pure, deterministic v2 foundation."""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
from collections.abc import Mapping, MutableMapping, MutableSequence
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import Literal, cast

import numpy as np
import pytest

from app.simulation.v2.context import TurnContext, WorldSnapshot
from app.simulation.v2.contracts import (
    ArrayPatch,
    StageContract,
    StageResult,
    StateDelta,
    StatePatch,
)
from app.simulation.v2.events import WorldEvent
from app.simulation.v2.seed import SeedManager, SeedStream
from app.simulation.v2.values import FrozenArray, JsonValue, canonical_bytes, digest, freeze
from app.simulation.v2.version import VersionConflict, WorldVersion

BACKEND = Path(__file__).resolve().parents[2]


def _snapshot() -> WorldSnapshot:
    return WorldSnapshot(
        WorldVersion("world", "timeline", generation=2, revision=41),
        turn_id=7,
        state={"environment": {"temperature": 12.5}},
        arrays={"population": FrozenArray.from_numpy(np.array([3, 7], dtype=np.int64))},
        manifest={"rng": "clade-blake2b-counter-v1"},
    )


def _child(source: str, *, hash_seed: str = "0") -> str:
    environment = dict(os.environ, PYTHONPATH=str(BACKEND), PYTHONHASHSEED=hash_seed)
    result = subprocess.run(
        [sys.executable, "-c", source],
        cwd=BACKEND,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    return result.stdout.strip()


def test_version_advances_revision_across_generation_replacement_without_aba() -> None:
    before = WorldVersion("world", "timeline", generation=2, revision=41)
    ordinary = before.advance()
    restored = ordinary.advance(replace_generation=True)
    assert ordinary == WorldVersion("world", "timeline", generation=2, revision=42)
    assert restored == WorldVersion("world", "timeline", generation=3, revision=43)
    assert before == WorldVersion("world", "timeline", generation=2, revision=41)
    restored.require(WorldVersion("world", "timeline", generation=3, revision=43))
    with pytest.raises(VersionConflict):
        restored.require(before)
    field_name = "revision"
    with pytest.raises(FrozenInstanceError):
        setattr(before, field_name, 43)


@pytest.mark.parametrize(
    "other",
    [
        WorldVersion("other", "timeline", 2, 41),
        WorldVersion("world", "other", 2, 41),
        WorldVersion("world", "timeline", 3, 41),
        WorldVersion("world", "timeline", 2, 42),
    ],
)
def test_expected_version_checks_every_identity_component(other: WorldVersion) -> None:
    with pytest.raises(VersionConflict):
        WorldVersion("world", "timeline", 2, 41).require(other)


@pytest.mark.parametrize("invalid", [-1, True, 1.5, "1"])
def test_version_rejects_invalid_numeric_identity(invalid: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        WorldVersion("world", "timeline", generation=cast(int, invalid))
    with pytest.raises((TypeError, ValueError)):
        WorldVersion("world", "timeline", revision=cast(int, invalid))


@pytest.mark.parametrize("invalid", ["", 7, False, ["world"]])
def test_version_rejects_invalid_string_identity(invalid: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        WorldVersion(cast(str, invalid), "timeline")
    with pytest.raises((TypeError, ValueError)):
        WorldVersion("world", cast(str, invalid))


def test_snapshot_deep_freezes_inputs_and_numeric_storage() -> None:
    nested: dict[str, object] = {"items": [{"temperature": 12.5}]}
    source_state: dict[str, object] = {"environment": nested}
    source_manifest: dict[str, object] = {"stages": [{"name": "climate"}]}
    source_array = np.array([[1, 2], [3, 4]], dtype=np.int64)
    arrays = {"population": FrozenArray.from_numpy(source_array)}
    snapshot = WorldSnapshot(
        WorldVersion("world", "timeline"),
        0,
        cast(Mapping[str, JsonValue], source_state),
        arrays,
        cast(Mapping[str, JsonValue], source_manifest),
    )
    initial_hash = snapshot.state_hash
    source_array[0, 0] = 999
    nested["items"] = []
    source_state.clear()
    source_manifest.clear()
    arrays.clear()
    assert snapshot.state_hash == initial_hash
    assert snapshot.state["environment"] == {"items": ({"temperature": 12.5},)}
    assert snapshot.manifest == {"stages": ({"name": "climate"},)}
    with pytest.raises(TypeError):
        cast(MutableMapping[str, JsonValue], snapshot.state)["new"] = 1
    environment = snapshot.domain("environment")
    with pytest.raises(TypeError):
        cast(MutableMapping[str, JsonValue], environment)["items"] = ()
    with pytest.raises(TypeError):
        cast(MutableSequence[JsonValue], environment["items"])[0] = 1
    frozen = snapshot.arrays["population"]
    view = frozen.numpy()
    np.testing.assert_array_equal(view, [[1, 2], [3, 4]])
    assert isinstance(frozen.data, bytes)
    assert not view.flags.writeable
    with pytest.raises(ValueError):
        view[0, 0] = 99
    with pytest.raises(ValueError):
        view.setflags(write=True)
    assert isinstance(view.base, np.ndarray)
    with pytest.raises(ValueError):
        view.base.setflags(write=True)
    with pytest.raises(TypeError):
        cast(MutableMapping[str, FrozenArray], snapshot.arrays)["new"] = frozen


def test_snapshot_hash_separates_numeric_state_and_identity() -> None:
    snapshot = _snapshot()
    other_identity = replace(snapshot, version=snapshot.version.advance(), turn_id=8)
    reordered = replace(
        snapshot,
        state={"environment": {"temperature": 12.5}},
        arrays=dict(reversed(list(snapshot.arrays.items()))),
    )
    assert other_identity.state_hash == snapshot.state_hash == reordered.state_hash
    assert other_identity.snapshot_id != snapshot.snapshot_id
    assert replace(snapshot, manifest={"rng": "next"}).state_hash != snapshot.state_hash
    assert replace(snapshot, state={"environment": {"temperature": 13.0}}).state_hash != (
        snapshot.state_hash
    )


def test_canonical_json_is_order_independent_and_normalizes_signed_zero() -> None:
    first = {"z": [1, {"b": True, "a": "生态"}], "a": -0.0}
    second = {"a": 0.0, "z": (1, {"a": "生态", "b": True})}
    assert canonical_bytes(first) == b'{"a":0.0,"z":[1,{"a":"\xe7\x94\x9f\xe6\x80\x81","b":true}]}'
    assert canonical_bytes(first) == canonical_bytes(second)
    assert digest(first) == digest(second)
    assert len({digest(None), digest(False), digest(0), digest(0.0), digest("0")}) == 5


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), object(), {1: "x"}])
def test_canonical_values_reject_non_json_or_nonfinite_input(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        freeze({"nested": [value]})
    with pytest.raises((TypeError, ValueError)):
        canonical_bytes(value)


def test_numeric_hash_includes_dtype_shape_and_normalizes_endianness_and_zero() -> None:
    native = FrozenArray.from_numpy(np.array([[1, 2], [3, 4]], dtype="<i8"))
    foreign = FrozenArray.from_numpy(np.array([[1, 2], [3, 4]], dtype=">i8"))
    flat = FrozenArray.from_numpy(np.array([1, 2, 3, 4], dtype="<i8"))
    narrow = FrozenArray.from_numpy(np.array([[1, 2], [3, 4]], dtype="<i4"))
    assert native == foreign
    assert native.content_hash == foreign.content_hash
    assert len({native.content_hash, flat.content_hash, narrow.content_hash}) == 3
    integer_zeros = FrozenArray.from_numpy(np.zeros(4, dtype="<i8"))
    float_zeros = FrozenArray.from_numpy(np.zeros(4, dtype="<f8"))
    assert integer_zeros.data == float_zeros.data
    assert integer_zeros.content_hash != float_zeros.content_hash
    positive = FrozenArray.from_numpy(np.array([0.0], dtype="<f8"))
    negative = FrozenArray.from_numpy(np.array([-0.0], dtype=">f8"))
    assert positive.content_hash == negative.content_hash
    noncontiguous = np.arange(12, dtype=np.int64).reshape(3, 4)[:, ::2]
    assert FrozenArray.from_numpy(noncontiguous) == FrozenArray.from_numpy(noncontiguous.copy())


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_numeric_buffers_reject_nonfinite_values(value: float) -> None:
    with pytest.raises(ValueError):
        FrozenArray.from_numpy(np.array([value], dtype=np.float64))


@pytest.mark.parametrize("dtype", ["O", "complex128", "U4", "datetime64[D]"])
def test_numeric_buffers_reject_unsupported_dtypes(dtype: str) -> None:
    with pytest.raises((TypeError, ValueError)):
        FrozenArray.from_numpy(np.zeros(2, dtype=dtype))


@pytest.mark.parametrize("shape", [(-1,), (True,), (2,), (1, 2)])
def test_numeric_buffers_reject_invalid_shape_or_size(shape: tuple[int, ...]) -> None:
    with pytest.raises(ValueError):
        FrozenArray("<i8", shape, bytes(8))


def test_rng_known_integer_and_uniform_vectors() -> None:
    stream = SeedManager(20260929, "timeline-alpha", 17).stream(
        "mortality", "2", entity="species-7", purpose="hazard"
    )
    assert stream.key.hex() == "a0a8ec1a6f0bce61c012be4ca3c071b4fd65d6a53f73a3dc0caacaf9140dc61f"
    assert [stream.uint64(i) for i in range(4)] == [
        10245840594235682402,
        7039028571959699412,
        17037456611358027419,
        12124398010296220884,
    ]
    assert [stream.uniform(i) for i in range(4)] == [
        0.5554281315605248,
        0.3815865034953121,
        0.9236023735830946,
        0.6572649331421044,
    ]
    assert [stream.randint(i, -5, 17) for i in range(4)] == [5, 15, -2, -1]


def test_rng_is_stateless_and_does_not_change_global_random_generators() -> None:
    python_before = random.getstate()
    numpy_before = np.random.get_state()
    manager = SeedManager(77, "timeline", 9)
    stream = manager.stream("stage", "1", entity="species", purpose="birth")
    expected = {counter: stream.uint64(counter) for counter in range(20)}
    for counter in reversed(range(20)):
        manager.stream("other-stage", "1").normal(counter)
        stream.randint(counter, 0, 10)
        assert stream.uint64(counter) == expected[counter]
    assert random.getstate() == python_before
    numpy_after = np.random.get_state()
    assert numpy_after[0] == numpy_before[0]
    np.testing.assert_array_equal(numpy_after[1], numpy_before[1])
    assert numpy_after[2:] == numpy_before[2:]


def test_rng_streams_separate_all_declared_namespaces() -> None:
    manager = SeedManager(77, "timeline", 9)
    keys = [
        manager.stream("stage", "1", entity="species", purpose="birth").key,
        manager.stream("other-stage", "1", entity="species", purpose="birth").key,
        manager.stream("stage", "2", entity="species", purpose="birth").key,
        manager.stream("stage", "1", entity="new-species", purpose="birth").key,
        manager.stream("stage", "1", entity="species", purpose="death").key,
        replace(manager, rng_namespace="branch")
        .stream("stage", "1", entity="species", purpose="birth")
        .key,
        replace(manager, world_seed=78).stream("stage", "1", entity="species", purpose="birth").key,
        replace(manager, turn_id=10).stream("stage", "1", entity="species", purpose="birth").key,
    ]
    assert len(set(keys)) == len(keys)
    assert manager.stream("ab", "c").key != manager.stream("a", "bc").key


def test_rng_reproduces_in_subprocesses_with_different_hash_and_task_order() -> None:
    source = """
import json
import os
from app.simulation.v2.seed import SeedManager
manager = SeedManager(77, 'timeline', 9)
tasks = {'climate', 'births', 'movement', 'death'}
if os.environ['PYTHONHASHSEED'] == '1':
    tasks = sorted(tasks, reverse=True)
results = {
    task: [manager.stream(task, '1', entity='species').uint64(i) for i in range(3)]
    for task in tasks
}
print(json.dumps(results, sort_keys=True))
"""
    first = _child(source, hash_seed="1")
    second = _child(source, hash_seed="2147483647")
    assert json.loads(first) == json.loads(second)


@pytest.mark.parametrize("invalid", [-1, True, 1.5])
def test_rng_rejects_invalid_counters(invalid: object) -> None:
    stream = SeedManager(1, "timeline", 0).stream("stage", "1")
    with pytest.raises((TypeError, ValueError)):
        stream.uint64(cast(int, invalid))
    with pytest.raises((TypeError, ValueError)):
        stream.uint64(0, attempt=cast(int, invalid))


@pytest.mark.parametrize("low,high", [(0, 0), (2, 1), (0, 2**64 + 1), (0.5, 2), (False, 2)])
def test_rng_rejects_invalid_integer_ranges(low: object, high: object) -> None:
    stream = SeedManager(1, "timeline", 0).stream("stage", "1")
    with pytest.raises((TypeError, ValueError)):
        stream.randint(0, cast(int, low), cast(int, high))


def test_events_have_stable_source_identity_and_frozen_payload() -> None:
    version = WorldVersion("world", "timeline", 2, 41)
    nested: dict[str, object] = {"population": [2, 3]}
    event = WorldEvent.create(
        version=version,
        turn=7,
        command_id="command",
        stage="population",
        ordinal=0,
        event_type="SpeciesCreated",
        payload=cast(Mapping[str, JsonValue], nested),
    )
    repeated = WorldEvent.create(
        version=version,
        turn=7,
        command_id="command",
        stage="population",
        ordinal=0,
        event_type="SpeciesCreated",
        payload={"population": (2, 3)},
    )
    assert event.event_id == repeated.event_id
    nested["population"] = []
    assert event.payload == {"population": (2, 3)}
    with pytest.raises(TypeError):
        cast(MutableMapping[str, JsonValue], event.payload)["population"] = ()
    ids = {event.event_id}
    for command, stage, ordinal, identity in [
        ("command2", "population", 0, version),
        ("command", "climate", 0, version),
        ("command", "population", 1, version),
        ("command", "population", 0, replace(version, world_id="other")),
        ("command", "population", 0, replace(version, timeline_id="branch")),
        ("command", "population", 0, replace(version, generation=3)),
        ("command", "population", 0, replace(version, revision=42)),
    ]:
        ids.add(
            WorldEvent.create(
                version=identity,
                turn=7,
                command_id=command,
                stage=stage,
                ordinal=ordinal,
                event_type="SpeciesCreated",
            ).event_id
        )
    assert len(ids) == 8
    assert event.to_dict()["version"] == version.to_dict()


@pytest.mark.parametrize("invalid", [-1, True])
def test_event_rejects_invalid_position(invalid: int) -> None:
    with pytest.raises(ValueError):
        WorldEvent.create(
            version=WorldVersion("world", "timeline"),
            turn=0,
            command_id="command",
            stage="stage",
            ordinal=invalid,
            event_type="TurnCommitted",
        )


def test_context_freezes_inputs_and_preserves_start_identity() -> None:
    snapshot = _snapshot()
    command: dict[str, object] = {"kind": "Advance", "payload": [1]}
    pressures: list[object] = [{"heat": [2]}]
    context = TurnContext(
        turn_id=8,
        snapshot=snapshot,
        seed=123,
        command=cast(Mapping[str, JsonValue], command),
        external_pressures=cast(tuple[JsonValue, ...], pressures),
    )
    command.clear()
    pressures.clear()
    assert context.command == {"kind": "Advance", "payload": (1,)}
    assert context.external_pressures == ({"heat": (2,)},)
    assert context.start_snapshot_id == snapshot.snapshot_id
    assert context.world_version == snapshot.version
    assert context.world_id == "world"
    assert context.timeline_id == context.rng_namespace == "timeline"
    assert context.seeds == SeedManager(123, "timeline", 8)
    assert context.environment_state == {"temperature": 12.5}
    updated = context.with_snapshot(replace(snapshot, state={"environment": {"temperature": 20}}))
    assert updated.start_snapshot_id == snapshot.snapshot_id
    assert context.environment_state == {"temperature": 12.5}
    assert updated.environment_state == {"temperature": 20}
    with pytest.raises(VersionConflict):
        context.with_snapshot(replace(snapshot, version=snapshot.version.advance()))
    with pytest.raises(ValueError):
        TurnContext(snapshot.turn_id, snapshot, seed=123)


def test_context_rejects_live_exception_objects() -> None:
    with pytest.raises((TypeError, ValueError)):
        TurnContext(8, _snapshot(), seed=123, errors=cast(tuple[str, ...], (ValueError("bad"),)))


@pytest.mark.parametrize("field", ["dependencies", "reads", "writes", "side_effects"])
def test_stage_contract_rejects_duplicate_declarations(field: str) -> None:
    duplicates = ("state.environment", "state.environment")
    with pytest.raises(ValueError):
        StageContract(
            "climate",
            "1",
            dependencies=duplicates if field == "dependencies" else (),
            reads=duplicates if field == "reads" else (),
            writes=duplicates if field == "writes" else (),
            side_effects=duplicates if field == "side_effects" else (),
        )


@pytest.mark.parametrize("path", ["environment", "state.", "arrays.", "state..temperature"])
def test_stage_contract_rejects_invalid_io_paths(path: str) -> None:
    with pytest.raises(ValueError):
        StageContract("stage", "1", reads=(path,))
    with pytest.raises(ValueError):
        StageContract("stage", "1", writes=(path,))


def test_state_patch_freezes_payload_and_rejects_duplicate_or_overlapping_paths() -> None:
    source: dict[str, object] = {"items": [1, 2]}
    patch = StatePatch(("environment", "new"), "create", cast(JsonValue, source))
    source.clear()
    assert patch.value == {"items": (1, 2)}
    with pytest.raises(ValueError):
        StateDelta(state=(patch, patch))
    with pytest.raises(ValueError):
        StateDelta(state=(patch, StatePatch(("environment",), "create", {})))
    with pytest.raises(ValueError):
        StateDelta(state=(StatePatch(("environment",), "create", {}), patch))
    assert len(StateDelta(state=(patch, StatePatch(("species", "new"), "create", {}))).state) == 2


def test_state_patch_requires_valid_path_operation_and_precondition() -> None:
    with pytest.raises(ValueError):
        StatePatch((), "create")
    with pytest.raises(ValueError):
        StatePatch(("environment", ""), "create")
    with pytest.raises(ValueError):
        StatePatch(("environment",), "replace")
    with pytest.raises(ValueError):
        StatePatch(("environment",), "delete")
    with pytest.raises(ValueError):
        StatePatch(("environment",), "create", expected_hash="already-present")
    with pytest.raises(ValueError):
        StatePatch(("environment",), cast(Literal["create", "replace", "delete"], "unknown"))


def test_array_patch_rejects_duplicate_targets_or_invalid_values() -> None:
    buffer = FrozenArray.from_numpy(np.array([1, 2], dtype=np.int64))
    patch = ArrayPatch("population", buffer)
    with pytest.raises(ValueError):
        StateDelta(arrays=(patch, patch))
    with pytest.raises(ValueError):
        ArrayPatch("", buffer)
    with pytest.raises(ValueError):
        ArrayPatch("population", None)
    with pytest.raises((TypeError, ValueError)):
        ArrayPatch("population", cast(FrozenArray, np.array([1])))


@pytest.mark.parametrize("duration", [-1.0, float("nan"), float("inf")])
def test_stage_result_rejects_invalid_duration(duration: float) -> None:
    with pytest.raises(ValueError):
        StageResult("stage", duration_ms=duration)


def test_stage_result_deep_freezes_metrics_and_proposals() -> None:
    metrics: dict[str, object] = {"counts": [1, 2]}
    proposals: list[object] = [{"traits": [0.1]}]
    result = StageResult(
        "stage",
        metrics=cast(Mapping[str, JsonValue], metrics),
        evolution_proposals=cast(tuple[JsonValue, ...], proposals),
    )
    metrics.clear()
    proposals.clear()
    assert result.metrics == {"counts": (1, 2)}
    assert result.evolution_proposals == ({"traits": (0.1,)},)


def test_pure_package_import_does_not_load_database_gpu_or_ai_router() -> None:
    loaded = json.loads(
        _child("""
import json
import sys
import app.simulation.v2
forbidden = ('taichi', 'app.core.database', 'app.ai.model_router')
print(json.dumps(sorted(
    name for name in sys.modules
    if any(name == prefix or name.startswith(prefix + '.') for prefix in forbidden)
)))
""")
    )
    assert loaded == []


def test_numeric_buffer_and_seed_key_copy_mutable_byte_inputs() -> None:
    source = bytearray(32)
    array = FrozenArray("<i8", (4,), cast(bytes, source))
    stream = SeedStream(cast(bytes, source))
    first_sample = stream.uint64(0)
    source[0] = 1
    assert array.data == bytes(32)
    assert stream.key == bytes(32)
    assert stream.uint64(0) == first_sample


def test_structured_numeric_dtype_is_rejected() -> None:
    with pytest.raises(ValueError):
        FrozenArray.from_numpy(np.zeros(2, dtype=[("count", "<i8")]))


def test_contract_and_delta_copy_mutable_sequence_inputs() -> None:
    dependencies = ["environment"]
    contract = StageContract("population", "1", dependencies=cast(tuple[str, ...], dependencies))
    patches = [StatePatch(("environment", "temperature"), "create", 12.0)]
    delta = StateDelta(state=cast(tuple[StatePatch, ...], patches))
    dependencies.clear()
    patches.clear()
    assert contract.dependencies == ("environment",)
    assert len(delta.state) == 1
    assert isinstance(delta.state, tuple)


def test_delta_rejects_untyped_patch_entries() -> None:
    with pytest.raises((TypeError, ValueError)):
        StateDelta(state=cast(tuple[StatePatch, ...], ({"path": "environment"},)))
    with pytest.raises((TypeError, ValueError)):
        StateDelta(arrays=cast(tuple[ArrayPatch, ...], ({"name": "population"},)))


@pytest.mark.parametrize("identity", ["", " ", 123])
def test_stage_contract_requires_string_name_and_version(identity: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        StageContract(cast(str, identity), "1")
    with pytest.raises((TypeError, ValueError)):
        StageContract("stage", cast(str, identity))

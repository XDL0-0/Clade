"""Independent acceptance tests for durable, branch-scoped world history."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from threading import Barrier
from typing import cast

import numpy as np
import pytest

from app.simulation.v2.context import TurnContext, WorldSnapshot
from app.simulation.v2.contracts import StageResult
from app.simulation.v2.events import WorldEvent
from app.simulation.v2.values import FrozenArray, JsonValue, freeze_mapping
from app.simulation.v2.version import VersionConflict, WorldVersion
from app.storage.database import IdempotencyConflict, StorageCorruption
from app.storage.history import commit_id
from app.storage.store import WorldStore

TABLES = ("commits", "commands", "events", "outbox", "turn_metrics")


def array(*values: int) -> FrozenArray:
    return FrozenArray.from_numpy(np.array(values, dtype=np.int64))


def genesis(store: WorldStore) -> WorldSnapshot:
    return store.create(
        WorldSnapshot(
            WorldVersion("world", "main"),
            0,
            freeze_mapping({"species": {"oak": {"age": 0}, "fern": {"age": 2}}}),
            {"population": array(4, 8), "unchanged": array(7)},
            freeze_mapping({"model": "test-v1"}),
        ),
        seed=17,
    )


def candidate(
    before: WorldSnapshot,
    *,
    state: Mapping[str, JsonValue] | None = None,
    arrays: Mapping[str, FrozenArray] | None = None,
    command: str = "step",
) -> TurnContext:
    next_turn = before.turn_id + 1
    event = WorldEvent.create(
        version=before.version.advance(),
        turn=next_turn,
        command_id=command,
        stage="acceptance",
        ordinal=0,
        event_type="PopulationChanged",
        payload=freeze_mapping({"turn": next_turn}),
    )
    proposed = replace(
        before,
        state=before.state if state is None else state,
        arrays={**before.arrays, "population": array(next_turn, next_turn + 10)}
        if arrays is None
        else arrays,
    )
    return TurnContext(
        next_turn,
        proposed,
        17,
        command=freeze_mapping({"kind": command}),
        start_snapshot_id=before.snapshot_id,
        stage_results=(StageResult("acceptance", events=(event,)),),
        metrics=freeze_mapping({"population": 2 * next_turn + 10}),
    )


def counts(store: WorldStore) -> tuple[int, ...]:
    with store.db.connection() as connection:
        return tuple(
            int(connection.execute(f"SELECT count(*) FROM {t}").fetchone()[0]) for t in TABLES
        )


def assert_snapshot(actual: WorldSnapshot, expected: WorldSnapshot) -> None:
    assert actual.version == expected.version
    assert actual.turn_id == expected.turn_id
    assert actual.snapshot_id == expected.snapshot_id
    assert actual.state == expected.state
    assert actual.manifest == expected.manifest
    assert actual.arrays == expected.arrays
    for name in actual.arrays:
        np.testing.assert_array_equal(actual.arrays[name].numpy(), expected.arrays[name].numpy())


def object_bytes(store: WorldStore) -> dict[Path, bytes]:
    return {
        path.relative_to(store.root): path.read_bytes()
        for path in (store.root / "objects").rglob("*.npz")
    }


def test_every_turn_reconstructs_checkpoints_entity_tombstones_and_arrays(tmp_path: Path) -> None:
    store = WorldStore(tmp_path, checkpoint_interval=2)
    saved = [genesis(store)]
    original_objects = object_bytes(store)
    for turn in range(1, 7):
        species: dict[str, object] = {"oak": {"age": turn}}
        if turn < 3:
            species["fern"] = {"age": 2}
        if turn >= 3:
            species["moss"] = {"age": turn - 3, "traits": ["wet", "small"]}
        arrays = {"population": array(turn, 10 + turn), "unchanged": array(7)}
        if turn == 2:
            arrays["temporary"] = array(30, 40, 50)
        request = candidate(saved[-1], state=freeze_mapping({"species": species}), arrays=arrays)
        committed = store.commit(request, command_key=f"step-{turn}")
        expected = replace(request.snapshot, version=saved[-1].version.advance(), turn_id=turn)
        assert_snapshot(committed, expected)
        saved.append(expected)
    reopened = WorldStore(tmp_path, checkpoint_interval=2)
    for expected in saved:
        assert_snapshot(reopened.history.replay(expected.version), expected)
        assert_snapshot(reopened.history.at_turn("world", "main", expected.turn_id), expected)
    assert_snapshot(reopened.head("world", "main"), saved[-1])
    assert {path: (tmp_path / path).read_bytes() for path in original_objects} == original_objects
    assert saved[0].arrays["population"] == array(4, 8)
    with store.db.connection() as connection:
        rows = connection.execute("SELECT turn_id,is_checkpoint FROM commits ORDER BY revision")
        assert [tuple(row) for row in rows] == [(t, int(t % 2 == 0)) for t in range(7)]


def test_successful_retry_survives_later_head_without_duplicate_effects(tmp_path: Path) -> None:
    store = WorldStore(tmp_path)
    initial = genesis(store)
    request = candidate(initial)
    first = store.commit(request, command_key="first")
    latest = store.commit(candidate(first), command_key="second")
    before_counts = counts(store)
    assert_snapshot(store.commit(request, command_key="first"), first)
    assert counts(store) == before_counts == (3, 2, 2, 5, 2)
    assert_snapshot(store.head("world", "main"), latest)
    with pytest.raises(IdempotencyConflict):
        store.commit(
            replace(request, command=freeze_mapping({"kind": "different"})), command_key="first"
        )


def test_idempotency_covers_active_event_inputs(tmp_path: Path) -> None:
    store = WorldStore(tmp_path)
    initial = genesis(store)
    request = candidate(initial)
    store.commit(request, command_key="same")
    changed_input = replace(request, active_events=request.stage_results[0].events)
    assert changed_input.input_hash != request.input_hash
    with pytest.raises(IdempotencyConflict):
        store.commit(changed_input, command_key="same")


@pytest.mark.parametrize("duplicate", [False, True])
def test_concurrent_candidates_use_cas_and_deduplicate_commands(
    tmp_path: Path, duplicate: bool
) -> None:
    store = WorldStore(tmp_path)
    request = candidate(genesis(store))
    barrier = Barrier(2)

    def submit(index: int) -> WorldSnapshot | VersionConflict:
        barrier.wait(timeout=10)
        try:
            return store.commit(request, command_key="same" if duplicate else f"worker-{index}")
        except VersionConflict as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, (0, 1)))
    successes = [r for r in results if isinstance(r, WorldSnapshot)]
    assert len(successes) == (2 if duplicate else 1)
    if duplicate:
        assert_snapshot(successes[0], successes[1])
    else:
        assert sum(isinstance(r, VersionConflict) for r in results) == 1
    assert counts(store) == (2, 1, 1, 3, 1)
    assert_snapshot(store.head("world", "main"), successes[0])


@pytest.mark.parametrize("table", ["events", "turn_metrics", "outbox"])
def test_transaction_failure_rolls_back_all_observable_effects(tmp_path: Path, table: str) -> None:
    store = WorldStore(tmp_path)
    initial = genesis(store)
    request = candidate(initial)
    original_counts, original_messages = counts(store), store.messages("world", "main")
    condition = " WHEN NEW.kind='TurnCommitted'" if table == "outbox" else ""
    with store.db.transaction() as connection:
        connection.execute(
            f"CREATE TRIGGER fail_commit BEFORE INSERT ON {table}{condition} "
            "BEGIN SELECT RAISE(ABORT, 'injected write failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="injected write failure"):
        store.commit(request, command_key="retry-after-failure")
    assert_snapshot(store.head("world", "main"), initial)
    assert counts(store) == original_counts
    assert store.messages("world", "main") == original_messages
    with store.db.transaction() as connection:
        connection.execute("DROP TRIGGER fail_commit")
    assert store.commit(request, command_key="retry-after-failure").turn_id == 1
    assert counts(store) == (2, 1, 1, 3, 1)


def test_forks_share_objects_and_remain_fixed_when_parent_advances(tmp_path: Path) -> None:
    store = WorldStore(tmp_path)
    initial = genesis(store)
    parent = store.commit(candidate(initial), command_key="parent-1")
    saved_objects = object_bytes(store)
    child = store.fork(parent.version, "child")
    sibling = store.fork(parent.version, "sibling")
    assert object_bytes(store) == saved_objects
    assert child.state_hash == sibling.state_hash == parent.state_hash
    parent_next = store.commit(candidate(parent), command_key="parent-2")
    child_next = store.commit(
        candidate(child, arrays={"population": array(99)}), command_key="child-2"
    )
    assert_snapshot(store.head("world", "sibling"), sibling)
    assert_snapshot(store.history.replay(child.version), child)
    assert_snapshot(store.head("world", "main"), parent_next)
    assert_snapshot(store.head("world", "child"), child_next)
    assert_snapshot(store.history.at_turn("world", "child", 0), initial)
    assert child_next.arrays["population"] != parent_next.arrays["population"]
    assert {path: (tmp_path / path).read_bytes() for path in saved_objects} == saved_objects


def test_rewind_increments_generation_and_rejects_same_turn_stale_candidate(tmp_path: Path) -> None:
    store = WorldStore(tmp_path)
    initial = genesis(store)
    old_request = candidate(initial)
    first = store.commit(old_request, command_key="old")
    restored = store.replace_head(first.version, initial.version, command_key="rewind")
    assert restored.turn_id == initial.turn_id
    assert restored.state_hash == initial.state_hash
    assert restored.version.generation == first.version.generation + 1
    assert restored.version.revision > first.version.revision
    with pytest.raises(VersionConflict):
        store.commit(old_request, command_key="previously-unseen")
    fresh = store.commit(candidate(restored), command_key="old")
    assert fresh.turn_id == first.turn_id
    assert fresh.version != first.version
    assert_snapshot(store.commit(old_request, command_key="old"), first)
    assert_snapshot(
        store.replace_head(first.version, initial.version, command_key="rewind"), restored
    )
    assert_snapshot(store.head("world", "main"), fresh)


def test_history_reads_do_not_move_head_or_write_records(tmp_path: Path) -> None:
    store = WorldStore(tmp_path, checkpoint_interval=2)
    initial = genesis(store)
    first = store.commit(candidate(initial), command_key="one")
    latest = store.commit(candidate(first), command_key="two")
    saved_counts, saved_objects = counts(store), object_bytes(store)
    for version in (initial, first, latest):
        assert_snapshot(store.history.replay(version.version), version)
        assert_snapshot(store.history.at_turn("world", "main", version.turn_id), version)
    with pytest.raises(KeyError):
        store.history.at_turn("world", "main", 3)
    assert_snapshot(store.head("world", "main"), latest)
    assert counts(store) == saved_counts
    assert object_bytes(store) == saved_objects


@pytest.mark.parametrize(
    "corruption",
    [
        "invalid-json",
        "missing-checkpoint-field",
        "missing-delta-field",
        "nonmapping-patch",
        "entity-base",
        "base-hash",
        "result-hash",
    ],
)
def test_corrupt_records_are_explicitly_rejected(tmp_path: Path, corruption: str) -> None:
    store = WorldStore(tmp_path)
    initial = genesis(store)
    latest = store.commit(
        candidate(initial, state=freeze_mapping({"species": {"oak": {"age": 1}}})),
        command_key="one",
    )
    target = initial.version if corruption == "missing-checkpoint-field" else latest.version
    with store.db.transaction() as connection:
        raw = connection.execute(
            "SELECT payload FROM commits WHERE commit_id=?", (commit_id(target),)
        ).fetchone()[0]
        payload = cast(dict[str, object], json.loads(raw))
        column = "payload"
        if corruption in {"base-hash", "result-hash"}:
            column = "base_hash" if corruption == "base-hash" else "state_hash"
            value = "0" * 64
        elif corruption == "invalid-json":
            value = "{not json"
        else:
            if corruption in {"missing-checkpoint-field", "missing-delta-field"}:
                del payload["state"]
            elif corruption == "nonmapping-patch":
                payload["state"] = [123]
            else:
                patches = cast(list[dict[str, object]], payload["state"])
                patches[0]["expected_hash"] = "0" * 64
            value = json.dumps(payload)
        connection.execute(
            f"UPDATE commits SET {column}=? WHERE commit_id=?", (value, commit_id(target))
        )
    with pytest.raises(StorageCorruption):
        store.history.replay(latest.version)


@pytest.mark.parametrize("checkpoint_interval", [1, 50])
def test_corrupt_turn_metadata_is_rejected(tmp_path: Path, checkpoint_interval: int) -> None:
    store = WorldStore(tmp_path, checkpoint_interval=checkpoint_interval)
    latest = store.commit(candidate(genesis(store)), command_key="one")
    with store.db.transaction() as connection:
        connection.execute(
            "UPDATE commits SET turn_id=91 WHERE commit_id=?", (commit_id(latest.version),)
        )
    with pytest.raises(StorageCorruption):
        store.history.replay(latest.version)


@pytest.mark.parametrize("missing", [True, False])
def test_missing_or_damaged_chunk_cannot_return_a_partial_world(
    tmp_path: Path, missing: bool
) -> None:
    store = WorldStore(tmp_path)
    initial = genesis(store)
    chunk = next((tmp_path / "objects").rglob("*.npz"))
    if missing:
        chunk.unlink()
    else:
        chunk.write_bytes(b"not an npz file")
    with pytest.raises(StorageCorruption):
        store.history.replay(initial.version)


def test_sse_clients_have_independent_cursors_and_offline_messages_survive(tmp_path: Path) -> None:
    store = WorldStore(tmp_path)
    initial = genesis(store)
    first = store.commit(candidate(initial), command_key="one")
    first_page = store.messages("world", "main", limit=2)
    cursor = first_page[-1]["cursor"]
    assert isinstance(cursor, int)
    latest = store.commit(candidate(first), command_key="two")
    reopened = WorldStore(tmp_path)
    all_messages = reopened.messages("world", "main")
    slow_client = reopened.messages("world", "main", after=0, limit=2)
    fast_client = reopened.messages("world", "main", after=cursor)
    assert slow_client == first_page
    assert slow_client + fast_client == all_messages
    assert len(all_messages) == 5
    assert len({m["message_id"] for m in all_messages}) == 5
    last_cursor = all_messages[-1]["cursor"]
    assert isinstance(last_cursor, int)
    assert reopened.messages("world", "main", after=last_cursor) == ()
    assert reopened.messages("world", "another-timeline") == ()
    assert_snapshot(reopened.head("world", "main"), latest)


def test_sqlite_backup_includes_commits_still_in_wal(tmp_path: Path) -> None:
    store = WorldStore(tmp_path / "live")
    initial = genesis(store)
    with store.db.connection() as keepalive:
        keepalive.execute("PRAGMA wal_autocheckpoint=0")
        latest = store.commit(candidate(initial), command_key="wal-only")
        wal = Path(str(store.db.path) + "-wal")
        assert wal.exists() and wal.stat().st_size > 0
        with sqlite3.connect(f"file:{store.db.path}?immutable=1", uri=True) as main_only:
            assert main_only.execute("SELECT count(*) FROM commits").fetchone()[0] == 1
        target = tmp_path / "backup.sqlite"
        store.db.backup(target)
        with sqlite3.connect(target) as backup:
            assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert backup.execute("SELECT head_id FROM timelines").fetchone()[0] == commit_id(
                latest.version
            )
            assert backup.execute("SELECT count(*) FROM events").fetchone()[0] == 1
            assert backup.execute("SELECT count(*) FROM outbox").fetchone()[0] == 3


def test_corrupt_at_turn_ancestry_is_rejected_without_unbounded_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = WorldStore(tmp_path)
    latest = store.commit(candidate(genesis(store)), command_key="one")
    with store.db.transaction() as connection:
        connection.execute(
            "UPDATE commits SET parent_id=commit_id WHERE commit_id=?", (commit_id(latest.version),)
        )
    original_connection = store.db.connection
    operations = 0

    def stop_runaway_query() -> int:
        nonlocal operations
        operations += 1
        return int(operations > 1000)

    @contextmanager
    def bounded_connection() -> Iterator[sqlite3.Connection]:
        with original_connection() as connection:
            connection.set_progress_handler(stop_runaway_query, 100)
            yield connection

    monkeypatch.setattr(store.db, "connection", bounded_connection)
    with pytest.raises(StorageCorruption):
        store.history.at_turn("world", "main", 0)


@pytest.mark.parametrize(
    "field", ["format", "schema_version", "minimum_reader_version", "array_codec"]
)
def test_unknown_save_format_is_rejected(tmp_path: Path, field: str) -> None:
    store = WorldStore(tmp_path)
    genesis(store)
    metadata = tmp_path / "metadata.json"
    fields = json.loads(metadata.read_text())
    fields[field] = "unknown" if isinstance(fields[field], str) else 999
    metadata.write_text(json.dumps(fields))
    with pytest.raises(StorageCorruption):
        WorldStore(tmp_path)

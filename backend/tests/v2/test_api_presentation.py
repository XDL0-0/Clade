"""Presentation walks selected semantic ancestry and never writes numerical state."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import cast

import numpy as np
import pytest

from app.ai.jobs.models import JobSpec, JobStatus
from app.ai.jobs.schemas import fallback_result
from app.api.v2 import presentation
from app.api.v2.presentation import narrative_history, runtime_diagnostics
from app.simulation.v2.context import TurnContext, WorldSnapshot
from app.simulation.v2.contracts import StageResult
from app.simulation.v2.events import WorldEvent
from app.simulation.v2.values import FrozenArray, JsonValue, digest
from app.simulation.v2.version import WorldVersion
from app.storage.codec import decode, encode
from app.storage.database import StorageCorruption
from app.storage.history import commit_id, record_hash
from app.storage.jobs import SQLiteJobRepository
from app.storage.store import WorldStore


def initial(store: WorldStore, world: str = "world") -> WorldSnapshot:
    return store.create(
        WorldSnapshot(
            WorldVersion(world, "main"),
            0,
            {
                "species": {
                    "a.id": {"slot": 0, "status": "Healthy"},
                    "fossil": {"slot": 1, "status": "Extinct"},
                    "b": {"slot": 2, "status": "Healthy"},
                }
            },
            {
                "population": FrozenArray.from_numpy(
                    np.array([[3, 4], [0, 0], [5, 0], [0, 0]], dtype=np.int64)
                )
            },
        ),
        seed=31,
    )


def advance(
    store: WorldStore,
    before: WorldSnapshot,
    targets: tuple[str, ...] = ("a.id",),
    *,
    apply: bool = True,
) -> tuple[WorldSnapshot, tuple[JobSpec, ...]]:
    turn = before.turn_id + 1
    events = tuple(
        WorldEvent.create(
            version=before.version.advance(),
            turn=turn,
            command_id=f"turn-{turn}",
            stage="facts",
            ordinal=index,
            event_type="SpeciesObserved",
            target=target,
        )
        for index, target in enumerate(targets)
    )
    context = TurnContext(turn, before, 31, stage_results=(StageResult("facts", events=events),))
    future = replace(before, version=before.version.advance(), turn_id=turn)
    jobs = tuple(
        JobSpec(
            future.version,
            turn,
            "species",
            (target,),
            (event.event_id,),
            snapshot_id=future.snapshot_id,
        )
        for target, event in zip(targets, events, strict=True)
    )
    after = store.commit(context, command_key=f"turn-{turn}", jobs=jobs)
    if apply:
        repo = SQLiteJobRepository(store.db)
        for _ in jobs:
            claimed = repo.claim("test", now=100.0, lease_seconds=20.0)
            assert claimed is not None
            done = repo.finish(
                claimed.job_id, claimed.lease_token, result=fallback_result(claimed.spec), now=101.0
            )
            assert done is not None and done.status == JobStatus.APPLIED
    return after, jobs


def page_annotations(page: Mapping[str, JsonValue]) -> tuple[Mapping[str, JsonValue], ...]:
    groups = cast(tuple[Mapping[str, JsonValue], ...], page["items"])
    return tuple(
        item
        for group in groups
        for item in cast(tuple[Mapping[str, JsonValue], ...], group["annotations"])
    )


def change_record(store: WorldStore, version: WorldVersion, field: str, value: JsonValue) -> None:
    assert field in ("parent_id", "base_hash", "record_hash")
    with store.db.connection() as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            f"UPDATE commits SET {field}=? WHERE commit_id=?", (value, commit_id(version))
        )
        if field != "record_hash":
            row = connection.execute(
                "SELECT * FROM commits WHERE commit_id=?", (commit_id(version),)
            ).fetchone()
            connection.execute(
                "UPDATE commits SET record_hash=? WHERE commit_id=?",
                (record_hash(row), commit_id(version)),
            )


def test_selected_ancestors_inherit_without_cousin_future_or_other_world(tmp_path: Path) -> None:
    store = WorldStore(tmp_path)
    base = initial(store)
    first, first_jobs = advance(store, base)
    fork = store.fork(first.version, "fork")
    cousin = store.fork(first.version, "cousin")
    cousin_next, cousin_jobs = advance(store, cousin)
    future, future_jobs = advance(store, first)
    other, other_jobs = advance(store, initial(store, "other"))
    fork_next, fork_jobs = advance(store, fork, ("b",))
    before = fork_next.snapshot_id
    page = narrative_history(store, fork_next)
    ids = {item["job_id"] for item in page_annotations(page)}
    assert ids == {first_jobs[0].job_id, fork_jobs[0].job_id}
    assert not ids & {cousin_jobs[0].job_id, future_jobs[0].job_id, other_jobs[0].job_id}
    assert {item["job_id"] for item in page_annotations(narrative_history(store, first))} == {
        first_jobs[0].job_id
    }
    assert {
        item["job_id"]
        for item in page_annotations(narrative_history(store, fork_next, species_id="a.id"))
    } == {first_jobs[0].job_id}
    assert narrative_history(store, fork_next, species_id="missing")["items"] == ()
    assert page["next_offset"] is None and page["truncated"] is False
    assert page["scanned"] == 4
    assert store.head("world", "fork").snapshot_id == before
    assert store.head("world", "cousin") == cousin_next
    assert store.head("world", "main") == future and store.head("other", "main") == other


def test_real_rewind_follows_verified_source_not_abandoned_audit_parent(tmp_path: Path) -> None:
    store = WorldStore(tmp_path)
    base = initial(store)
    first, first_jobs = advance(store, base)
    second, abandoned_jobs = advance(store, first)
    rewound = store.replace_head(second.version, first.version, command_key="rewind")
    assert rewound.turn_id == first.turn_id and rewound.version.generation == 1
    assert {item["job_id"] for item in page_annotations(narrative_history(store, rewound))} == {
        first_jobs[0].job_id
    }
    branched, new_jobs = advance(store, rewound)
    assert branched.turn_id == second.turn_id
    ids = {item["job_id"] for item in page_annotations(narrative_history(store, branched))}
    assert ids == {first_jobs[0].job_id, new_jobs[0].job_id}
    assert abandoned_jobs[0].job_id not in ids
    same = store.replace_head(branched.version, branched.version, command_key="same-turn")
    assert {item["job_id"] for item in page_annotations(narrative_history(store, same))} == ids


def test_full_commit_pagination_keeps_all_annotations_and_skips_empty_commits(
    tmp_path: Path,
) -> None:
    store = WorldStore(tmp_path)
    base = initial(store)
    first, older = advance(store, base, ("a.id", "b", "a.id"))
    second, _ = advance(store, first, ())
    third, newer = advance(store, second, ("a.id", "b"))
    page = narrative_history(store, third, limit=1)
    assert len(page_annotations(page)) == 2 and page["next_offset"] == 1
    assert page["scanned"] == 1 and page["truncated"] is True
    scan = narrative_history(store, third, limit=1, offset=1, max_scan=1)
    assert not page_annotations(scan) and scan["next_offset"] == 2 and scan["scanned"] == 1
    following = narrative_history(store, third, limit=1, offset=2)
    assert len(page_annotations(following)) == 3 and following["next_offset"] == 3
    final = narrative_history(store, third, limit=1, offset=3)
    assert final["items"] == () and final["next_offset"] is None
    combined = (*page_annotations(page), *page_annotations(following))
    assert {item["job_id"] for item in combined} == {job.job_id for job in (*older, *newer)}
    assert narrative_history(store, third, offset=100)["scanned"] == 0


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("limit", 0),
        ("limit", 101),
        ("limit", True),
        ("offset", -1),
        ("offset", 1_000_001),
        ("max_scan", 0),
        ("max_scan", 1001),
        ("species_id", ""),
    ],
)
def test_query_parameters_are_bounded(tmp_path: Path, key: str, value: JsonValue) -> None:
    store = WorldStore(tmp_path)
    snapshot = initial(store)
    with pytest.raises(ValueError):
        if key == "species_id":
            narrative_history(store, snapshot, species_id=cast(str, value))
        elif key == "limit":
            narrative_history(store, snapshot, limit=cast(int, value))
        elif key == "offset":
            narrative_history(store, snapshot, offset=cast(int, value))
        else:
            narrative_history(store, snapshot, max_scan=cast(int, value))


@pytest.mark.parametrize("corruption", ["checksum", "missing", "cycle", "foreign", "base"])
def test_corrupt_ancestry_fails_closed(tmp_path: Path, corruption: str) -> None:
    store = WorldStore(tmp_path)
    head, _ = advance(store, initial(store))
    if corruption == "checksum":
        change_record(store, head.version, "record_hash", "corrupt")
    elif corruption == "base":
        change_record(store, head.version, "base_hash", "corrupt")
    else:
        target = (
            commit_id(initial(store, "foreign").version)
            if corruption == "foreign"
            else ("missing" if corruption == "missing" else commit_id(head.version))
        )
        change_record(store, head.version, "parent_id", target)
    with pytest.raises(StorageCorruption):
        narrative_history(store, head)


@pytest.mark.parametrize("corruption", ["command-hash", "source", "expected", "association"])
def test_rewind_command_must_authenticate_its_full_source(tmp_path: Path, corruption: str) -> None:
    store = WorldStore(tmp_path)
    first, _ = advance(store, initial(store))
    second, _ = advance(store, first)
    head = store.replace_head(second.version, first.version, command_key="rewind")
    with store.db.connection() as connection:
        row = connection.execute(
            "SELECT * FROM commands WHERE commit_id=?", (commit_id(head.version),)
        ).fetchone()
        payload = dict(decode(row["input_payload"]))
        if corruption in ("source", "expected"):
            payload[corruption] = {"world_id": "world", "timeline_id": "main", "revision": 1}
        key = "wrong" if corruption == "association" else row["command_key"]
        checksum = "wrong" if corruption == "command-hash" else digest(payload)
        connection.execute(
            "UPDATE commands SET command_key=?,input_payload=?,input_hash=? WHERE commit_id=?",
            (key, encode(payload), checksum, commit_id(head.version)),
        )
    with pytest.raises(StorageCorruption):
        narrative_history(store, head)


@pytest.mark.parametrize("corruption", ["job-hash", "result", "commit", "job-result"])
def test_annotations_require_applied_job_and_valid_frozen_result(
    tmp_path: Path, corruption: str
) -> None:
    store = WorldStore(tmp_path)
    base = initial(store)
    head, jobs = advance(store, base)
    with store.db.connection() as connection:
        if corruption == "job-hash":
            connection.execute(
                "UPDATE ai_jobs SET input_hash='wrong' WHERE job_id=?", (jobs[0].job_id,)
            )
        elif corruption == "commit":
            connection.execute(
                "UPDATE annotations SET commit_id=? WHERE job_id=?",
                (commit_id(base.version), jobs[0].job_id),
            )
        elif corruption == "result":
            result = {**fallback_result(jobs[0]), "population": 999}
            connection.execute(
                "UPDATE annotations SET payload=? WHERE job_id=?", (encode(result), jobs[0].job_id)
            )
        else:
            row = connection.execute(
                "SELECT payload FROM ai_jobs WHERE job_id=?", (jobs[0].job_id,)
            ).fetchone()
            payload = dict(decode(row["payload"]))
            payload["result"] = {**fallback_result(jobs[0]), "common_name": "Different result"}
            connection.execute(
                "UPDATE ai_jobs SET payload=? WHERE job_id=?", (encode(payload), jobs[0].job_id)
            )
    with pytest.raises(StorageCorruption):
        narrative_history(store, head)


def test_non_applied_annotations_are_never_presented(tmp_path: Path) -> None:
    store = WorldStore(tmp_path)
    head, jobs = advance(store, initial(store))
    with store.db.connection() as connection:
        row = connection.execute(
            "SELECT payload FROM ai_jobs WHERE job_id=?", (jobs[0].job_id,)
        ).fetchone()
        payload = {**decode(row["payload"]), "status": "CANCELLED"}
        connection.execute(
            "UPDATE ai_jobs SET status='CANCELLED',payload=? WHERE job_id=?",
            (encode(payload), jobs[0].job_id),
        )
    assert narrative_history(store, head)["items"] == ()


def test_diagnostics_scope_counts_unknown_usage_and_read_only_hashes(tmp_path: Path) -> None:
    store = WorldStore(tmp_path)
    first, _ = advance(store, initial(store))
    head, jobs = advance(store, first, ("a.id", "b"), apply=False)
    cousin = store.fork(first.version, "cousin")
    advance(store, cousin, apply=False)
    advance(store, initial(store, "other"), apply=False)
    original = head.snapshot_id
    with store.db.connection() as connection:
        before = tuple(
            connection.execute("SELECT payload FROM annotations ORDER BY job_id").fetchall()
        )
    details = runtime_diagnostics(store, head)
    assert details["species_count"] == 3 and details["active_species_count"] == 2
    assert details["population_habitat_records"] == 3 and details["species_capacity"] == 4
    counts = cast(Mapping[str, JsonValue], details["ai_jobs"])
    assert counts["QUEUED"] == len(jobs) and counts["APPLIED"] == 0
    earlier = cast(Mapping[str, JsonValue], runtime_diagnostics(store, first)["ai_jobs"])
    assert earlier["APPLIED"] == 1 and earlier["QUEUED"] == 0
    assert details["gpu_memory_bytes"] is details["ai_token_usage"] is None
    assert details["rss_bytes"] is None or cast(int, details["rss_bytes"]) > 0
    assert cast(int, details["save_bytes"]) > 0 and details["save_bytes_approximate"] is True
    assert cast(Mapping[str, JsonValue], details["scope"])["save_bytes"] == "entire_store_directory"
    assert "CPU" in cast(str, details["gpu_note"])
    assert store.head("world", "main").snapshot_id == original
    with store.db.connection() as connection:
        assert (
            tuple(connection.execute("SELECT payload FROM annotations ORDER BY job_id").fetchall())
            == before
        )


def test_diagnostics_reject_a_forged_snapshot(tmp_path: Path) -> None:
    store = WorldStore(tmp_path)
    snapshot = initial(store)
    forged = replace(snapshot, state={**snapshot.state, "forged": True})
    with pytest.raises(StorageCorruption):
        runtime_diagnostics(store, forged)
    with pytest.raises(StorageCorruption):
        narrative_history(store, forged)


def test_rss_absence_is_null_and_store_scan_ignores_symlinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable(self: Path) -> str:
        raise FileNotFoundError

    monkeypatch.setattr(Path, "read_text", unavailable)
    assert presentation._rss_bytes() is None
    root = tmp_path / "save"
    root.mkdir()
    (root / "first").write_bytes(b"123")
    (root / "nested").mkdir()
    (root / "nested" / "second").write_bytes(b"12345")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "large").write_bytes(b"x" * 1000)
    (root / "linked-file").symlink_to(outside / "large")
    (root / "linked-directory").symlink_to(outside, target_is_directory=True)
    assert presentation._save_bytes(root) == (8, 0)


def test_presentation_import_has_no_taichi_or_legacy_main() -> None:
    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import app.api.v2.presentation; "
                "assert not any(name == 'taichi' or name.startswith('taichi.') "
                "for name in sys.modules); "
                "assert 'app.main' not in sys.modules"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

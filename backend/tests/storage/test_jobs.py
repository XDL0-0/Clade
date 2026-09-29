"""Independent acceptance of durable AI ownership, scope and numerical isolation."""

from __future__ import annotations

from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Barrier

import numpy as np
import pytest

from app.ai.jobs.models import AIJob, JobSpec, JobStatus
from app.ai.jobs.schemas import fallback_result
from app.simulation.v2.context import TurnContext, WorldSnapshot
from app.simulation.v2.contracts import StageResult
from app.simulation.v2.events import WorldEvent
from app.simulation.v2.values import FrozenArray, JsonValue, freeze_mapping
from app.simulation.v2.version import WorldVersion
from app.storage.database import IdempotencyConflict
from app.storage.jobs import SQLiteJobRepository
from app.storage.store import WorldStore

TABLES = ("commits", "commands", "events", "outbox", "ai_jobs", "annotations", "narrative_heads")


@dataclass
class Clock:
    now: float = 100.0

    def __call__(self) -> float:
        return self.now


def initial(store: WorldStore) -> WorldSnapshot:
    return store.create(
        WorldSnapshot(
            WorldVersion("world", "main"),
            0,
            freeze_mapping({"species": {"oak": {"population": 40, "traits": {"size": 2}}}}),
            {"population": FrozenArray.from_numpy(np.array([40], dtype=np.int64))},
        ),
        seed=23,
    )


def candidate(before: WorldSnapshot) -> TurnContext:
    turn = before.turn_id + 1
    event = WorldEvent.create(
        version=before.version.advance(),
        turn=turn,
        command_id=f"turn-{turn}",
        stage="acceptance",
        ordinal=0,
        event_type="SpeciesObserved",
        target="oak",
    )
    return TurnContext(
        turn,
        before,
        23,
        stage_results=(StageResult("acceptance", events=(event,)),),
    )


def spec_for(request: TurnContext, *, key: str = "narrative") -> JobSpec:
    committed = replace(
        request.snapshot,
        version=request.world_version.advance(),
        turn_id=request.turn_id,
    )
    return JobSpec(
        committed.version,
        committed.turn_id,
        "species",
        ("oak",),
        tuple(event.event_id for stage in request.stage_results for event in stage.events),
        snapshot_id=committed.snapshot_id,
        idempotency_key=key,
    )


def setup(root: Path) -> tuple[WorldStore, SQLiteJobRepository, Clock, WorldSnapshot, JobSpec]:
    store, clock = WorldStore(root), Clock()
    request = candidate(initial(store))
    spec = spec_for(request)
    head = store.commit(request, command_key="turn-1", jobs=(spec,))
    return store, SQLiteJobRepository(store.db, clock=clock), clock, head, spec


def claim(repo: SQLiteJobRepository, clock: Clock, worker: str = "worker") -> AIJob:
    job = repo.claim(worker, now=clock.now, lease_seconds=10)
    assert job is not None
    return job


def counts(store: WorldStore) -> tuple[int, ...]:
    with store.db.connection() as connection:
        return tuple(
            int(connection.execute(f"SELECT count(*) FROM {name}").fetchone()[0]) for name in TABLES
        )


def kinds(store: WorldStore, timeline: str = "main") -> list[str]:
    return [str(message["kind"]) for message in store.messages("world", timeline)]


def finish(repo: SQLiteJobRepository, job: AIJob, clock: Clock) -> AIJob | None:
    return repo.finish(
        job.job_id,
        job.lease_token,
        result=fallback_result(job.spec),
        now=clock.now,
    )


def test_conflicting_jobs_roll_back_world_event_and_outbox(tmp_path: Path) -> None:
    store = WorldStore(tmp_path)
    before = initial(store)
    request = candidate(before)
    first = spec_for(request)
    conflict = replace(first, payload=freeze_mapping({"facts": "different input"}))
    baseline = counts(store)
    with pytest.raises(IdempotencyConflict):
        store.commit(request, command_key="turn-1", jobs=(first, conflict))
    assert counts(store) == baseline
    assert store.head("world", "main") == before
    repo = SQLiteJobRepository(store.db)
    assert repo.get(first.job_id) is None
    assert repo.claim("worker", now=100, lease_seconds=10) is None


def test_same_command_cannot_hide_changed_job_input(tmp_path: Path) -> None:
    store = WorldStore(tmp_path)
    request = candidate(initial(store))
    first = spec_for(request)
    store.commit(request, command_key="same-command", jobs=(first,))
    baseline = counts(store)
    changed = replace(first, payload=freeze_mapping({"facts": "changed"}))
    with pytest.raises(IdempotencyConflict):
        store.commit(request, command_key="same-command", jobs=(changed,))
    assert counts(store) == baseline
    saved = SQLiteJobRepository(store.db).get(first.job_id)
    assert saved is not None and saved.spec == first


@pytest.mark.parametrize("source", ["unknown", "previous-commit"])
def test_job_sources_must_belong_to_atomic_commit(tmp_path: Path, source: str) -> None:
    store, repo, _, head, first = setup(tmp_path)
    request = candidate(head)
    invalid = replace(
        spec_for(request, key="second"),
        source_event_ids=("missing",) if source == "unknown" else first.source_event_ids,
    )
    baseline = counts(store)
    with pytest.raises(ValueError, match="source event"):
        store.commit(request, command_key="turn-2", jobs=(invalid,))
    assert counts(store) == baseline
    assert store.head("world", "main") == head
    assert repo.get(invalid.job_id) is None


def test_duplicate_enqueue_and_concurrent_finish_apply_once(tmp_path: Path) -> None:
    store = WorldStore(tmp_path)
    request = candidate(initial(store))
    spec = spec_for(request)
    head = store.commit(request, command_key="turn-1", jobs=(spec, spec))
    repo, clock, barrier = SQLiteJobRepository(store.db), Clock(), Barrier(2)
    job = claim(repo, clock)

    def complete(_: int) -> AIJob | None:
        barrier.wait(timeout=10)
        return finish(SQLiteJobRepository(store.db), job, clock)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(complete, (0, 1)))
    assert all(result is not None and result.status == JobStatus.APPLIED for result in results)
    assert results[0] == results[1] == repo.get(job.job_id)
    assert len(repo.annotations(head.version)) == 1
    assert kinds(store).count("AIJobQueued") == kinds(store).count("NarrativeReady") == 1
    assert store.head("world", "main") == head


def test_applied_redelivery_after_head_moves_returns_original_commit(tmp_path: Path) -> None:
    store, repo, clock, head, _ = setup(tmp_path)
    job = claim(repo, clock)
    applied = finish(repo, job, clock)
    latest = store.commit(candidate(head), command_key="turn-2")
    baseline = counts(store)
    clock.now += 100
    assert finish(repo, job, clock) == applied
    assert repo.get(job.job_id) == applied
    assert counts(store) == baseline
    assert len(repo.annotations(head.version)) == 1
    assert repo.annotations(latest.version) == ()
    assert store.head("world", "main") == latest


@pytest.mark.parametrize("replacement", [False, True])
def test_head_change_and_same_turn_rewind_reject_late_result(
    tmp_path: Path,
    replacement: bool,
) -> None:
    store, repo, clock, head, _ = setup(tmp_path)
    job = claim(repo, clock)
    if replacement:
        latest = store.replace_head(head.version, head.version, command_key="rewind")
        assert latest.turn_id == head.turn_id
        assert latest.version.generation == head.version.generation + 1
    else:
        latest = store.commit(candidate(head), command_key="turn-2")
    stale = finish(repo, job, clock)
    assert stale is not None and stale.status == JobStatus.STALE
    assert repo.annotations(head.version) == repo.annotations(latest.version) == ()
    assert kinds(store).count("AIJobStale") == 1
    assert "NarrativeReady" not in kinds(store)
    assert store.head("world", "main") == latest


def test_claim_rejects_queued_jobs_from_a_stale_head(tmp_path: Path) -> None:
    store, repo, clock, head, spec = setup(tmp_path)
    latest = store.commit(candidate(head), command_key="turn-2")
    assert repo.claim("worker", now=clock.now, lease_seconds=10) is None
    stale = repo.get(spec.job_id)
    assert stale is not None and stale.status == JobStatus.STALE
    assert repo.annotations(head.version) == ()
    assert store.head("world", "main") == latest


def test_parent_job_remains_parent_scoped_after_fork(tmp_path: Path) -> None:
    store, repo, clock, parent, _ = setup(tmp_path)
    job = claim(repo, clock)
    child = store.fork(parent.version, "child")
    child_next = store.commit(candidate(child), command_key="child-turn-2")
    child_messages = kinds(store, "child")
    applied = finish(repo, job, clock)
    assert applied is not None and applied.status == JobStatus.APPLIED
    assert len(repo.annotations(parent.version)) == 1
    assert repo.annotations(child.version) == repo.annotations(child_next.version) == ()
    assert kinds(store, "child") == child_messages
    assert store.head("world", "main") == parent
    assert store.head("world", "child") == child_next
    assert counts(store)[4] == 1


def test_expired_lease_reclaim_fences_old_worker(tmp_path: Path) -> None:
    store, repo, clock, head, _ = setup(tmp_path)
    old = claim(repo, clock, "old")
    clock.now += 10
    assert finish(repo, old, clock) is None
    renewed = claim(repo, clock, "new")
    assert renewed.job_id == old.job_id
    assert renewed.attempts == old.attempts + 1
    assert renewed.lease_token > old.lease_token
    assert finish(repo, old, clock) is None
    assert repo.fail(old.job_id, old.lease_token, error="late", now=clock.now) is None
    assert repo.cancel(old.job_id, old.lease_token, reason="late", now=clock.now) is None
    applied = finish(repo, renewed, clock)
    assert applied is not None and applied.status == JobStatus.APPLIED
    assert len(repo.annotations(head.version)) == 1
    assert kinds(store).count("NarrativeReady") == 1


def test_claim_race_grants_only_one_lease(tmp_path: Path) -> None:
    store, repo, clock, _, _ = setup(tmp_path)
    barrier = Barrier(2)

    def acquire(index: int) -> AIJob | None:
        barrier.wait(timeout=10)
        return SQLiteJobRepository(store.db).claim(str(index), now=clock.now, lease_seconds=10)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(acquire, (0, 1)))
    assert sum(job is not None for job in results) == 1
    acquired = next(job for job in results if job is not None)
    assert acquired.attempts == acquired.lease_token == 1
    assert repo.get(acquired.job_id) == acquired


def test_cancel_and_finish_race_has_one_terminal_effect(tmp_path: Path) -> None:
    store, repo, clock, head, _ = setup(tmp_path)
    job, barrier = claim(repo, clock), Barrier(2)

    def complete(cancel: bool) -> AIJob | None:
        barrier.wait(timeout=10)
        other = SQLiteJobRepository(store.db)
        if cancel:
            return other.cancel(job.job_id, job.lease_token, now=clock.now, reason="stop")
        return finish(other, job, clock)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(complete, (False, True)))
    terminal = repo.get(job.job_id)
    assert terminal is not None and terminal.status in {JobStatus.APPLIED, JobStatus.CANCELLED}
    assert sum(result is not None for result in results) == 1
    applied = terminal.status == JobStatus.APPLIED
    assert len(repo.annotations(head.version)) == int(applied)
    assert kinds(store).count("NarrativeReady") == int(applied)
    assert kinds(store).count("AIJobCancelled") == int(not applied)
    assert store.head("world", "main") == head


def test_commit_and_finish_race_checks_head_inside_transaction(tmp_path: Path) -> None:
    store, repo, clock, head, _ = setup(tmp_path)
    job, barrier = claim(repo, clock), Barrier(2)

    def apply_or_advance(advance: bool) -> AIJob | WorldSnapshot | None:
        barrier.wait(timeout=10)
        if advance:
            return store.commit(candidate(head), command_key="turn-2")
        return finish(SQLiteJobRepository(store.db), job, clock)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(apply_or_advance, (False, True)))
    terminal, latest = repo.get(job.job_id), results[1]
    assert isinstance(latest, WorldSnapshot)
    assert terminal is not None and terminal.status in {JobStatus.APPLIED, JobStatus.STALE}
    assert len(repo.annotations(head.version)) == int(terminal.status == JobStatus.APPLIED)
    assert repo.annotations(latest.version) == ()
    assert kinds(store).count("NarrativeReady") + kinds(store).count("AIJobStale") == 1
    assert store.head("world", "main") == latest


def test_scope_cancel_includes_queued_jobs_without_cancelling_child(tmp_path: Path) -> None:
    store, repo, clock, parent, parent_spec = setup(tmp_path)
    child = store.fork(parent.version, "child")
    request = candidate(child)
    child_spec = spec_for(request)
    child_next = store.commit(request, command_key="child-turn-2", jobs=(child_spec,))
    assert repo.cancel_scope(parent.version, now=clock.now, reason="switch") == 1
    parent_job, child_job = repo.get(parent_spec.job_id), repo.get(child_spec.job_id)
    assert parent_job is not None and parent_job.status == JobStatus.CANCELLED
    assert child_job is not None and child_job.status == JobStatus.QUEUED
    applied = finish(repo, claim(repo, clock), clock)
    assert applied is not None and applied.spec == child_spec
    assert repo.annotations(parent.version) == ()
    assert len(repo.annotations(child_next.version)) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"population": 999},
        {"traits": {"size": 999}},
        {"status": "extinct"},
        {"species_id": "another-world-species"},
        {"source_event_ids": ["unknown"]},
        {"organ_descriptions": [{"organ_id": "unknown", "description": "invented"}]},
    ],
)
def test_invalid_schema_and_unknown_targets_cannot_write(
    tmp_path: Path,
    change: Mapping[str, JsonValue],
) -> None:
    store, repo, clock, head, _ = setup(tmp_path)
    job = claim(repo, clock)
    baseline = counts(store)
    with pytest.raises(ValueError):
        repo.finish(
            job.job_id,
            job.lease_token,
            result={**fallback_result(job.spec), **change},
            now=clock.now,
        )
    assert counts(store) == baseline
    assert repo.get(job.job_id) == job
    assert repo.annotations(head.version) == ()
    assert store.head("world", "main") == head


def test_restart_recovers_and_exhausted_leases_use_deterministic_fallback(tmp_path: Path) -> None:
    store, repo, clock, head, spec = setup(tmp_path)
    for attempt in range(1, 4):
        reopened = WorldStore(tmp_path)
        repo = SQLiteJobRepository(reopened.db, clock=clock)
        job = claim(repo, clock, f"crashing-{attempt}")
        assert job.attempts == job.lease_token == attempt
        clock.now += 10
    assert repo.claim("last", now=clock.now, lease_seconds=10) is None
    failed = repo.get(spec.job_id)
    assert failed is not None and failed.status == JobStatus.FAILED
    assert failed.fallback_used and failed.result == fallback_result(spec)
    assert failed.attempts == failed.max_attempts == 3
    assert repo.annotations(head.version) == ()
    assert kinds(store).count("AIJobFailed") == 1
    assert store.head("world", "main") == head


def test_retryable_failures_keep_identity_until_budget_exhausts(tmp_path: Path) -> None:
    store, repo, clock, head, spec = setup(tmp_path)
    for attempt in range(1, 4):
        job = claim(repo, clock)
        assert job.job_id == spec.job_id and job.attempts == job.lease_token == attempt
        failed = repo.fail(
            job.job_id,
            job.lease_token,
            now=clock.now,
            error="provider timeout",
            retryable=True,
            fallback_result=fallback_result(spec),
        )
        assert failed is not None
        assert failed.status == (JobStatus.QUEUED if attempt < 3 else JobStatus.FAILED)
        assert failed.fallback_used == (attempt == 3)
    assert repo.claim("extra", now=clock.now, lease_seconds=10) is None
    assert kinds(store).count("AIJobFailed") == 1
    assert repo.annotations(head.version) == ()
    assert store.head("world", "main") == head


@pytest.mark.parametrize(
    "terminal",
    [JobStatus.APPLIED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.STALE],
)
def test_terminal_jobs_never_resurrect(tmp_path: Path, terminal: JobStatus) -> None:
    store, repo, clock, head, _ = setup(tmp_path)
    job = claim(repo, clock)
    if terminal == JobStatus.APPLIED:
        finish(repo, job, clock)
    elif terminal == JobStatus.FAILED:
        repo.fail(job.job_id, job.lease_token, now=clock.now, error="bad result")
    elif terminal == JobStatus.CANCELLED:
        assert repo.cancel_scope(head.version, now=clock.now, reason="switch") == 1
    else:
        store.commit(candidate(head), command_key="turn-2")
        finish(repo, job, clock)
    saved, baseline = repo.get(job.job_id), counts(store)
    assert saved is not None and saved.status == terminal
    clock.now += 100
    assert repo.claim("retry", now=clock.now, lease_seconds=10) is None
    assert finish(repo, job, clock) == (saved if terminal == JobStatus.APPLIED else None)
    assert (
        repo.fail(
            job.job_id,
            job.lease_token,
            now=clock.now,
            error="retry",
            retryable=True,
        )
        is None
    )
    assert repo.cancel(job.job_id, job.lease_token, now=clock.now, reason="again") is None
    assert repo.cancel_scope(head.version, now=clock.now, reason="again") == 0
    assert repo.get(job.job_id) == saved
    assert counts(store) == baseline


@pytest.mark.parametrize("description", [None, "fast narrative", "different narrative"])
def test_ai_text_and_completion_timing_cannot_change_numerical_hash(
    tmp_path: Path,
    description: str | None,
) -> None:
    control = WorldStore(tmp_path / "disabled")
    expected = control.commit(candidate(initial(control)), command_key="turn-1")
    store, repo, clock, head, _ = setup(tmp_path / "enabled")
    job = claim(repo, clock)
    if description is not None:
        clock.now += 5 if description == "different narrative" else 0
        result = {**fallback_result(job.spec), "description": description}
        repo.finish(job.job_id, job.lease_token, result=result, now=clock.now)
    current = store.head("world", "main")
    assert current.version == head.version == expected.version
    assert current.state_hash == head.state_hash == expected.state_hash
    assert current.arrays == expected.arrays
    next_actual = store.commit(candidate(current), command_key="turn-2")
    next_expected = control.commit(candidate(expected), command_key="turn-2")
    assert next_actual.state_hash == next_expected.state_hash

"""No network or timing sleeps: fake clocks and explicit events control coordination."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine, Mapping, MutableMapping
from dataclasses import dataclass, replace
from typing import cast

import pytest

from app.ai.jobs import (
    AIJob,
    JobSpec,
    JobStatus,
    NarrativeWorker,
    RetryableProviderError,
    WorkerConfig,
    fallback_result,
)
from app.simulation.v2.values import JsonValue, canonical_bytes
from app.simulation.v2.version import WorldVersion


@dataclass
class FakeClock:
    now: float = 100.0

    def __call__(self) -> float:
        return self.now


def new_job(*, max_attempts: int = 3) -> AIJob:
    return AIJob(
        JobSpec(
            WorldVersion("world", "timeline", 2, 7),
            turn_id=4,
            job_type="species",
            target_ids=("species-a",),
            source_event_ids=("event-a",),
            payload={"trace": {"record": "frozen"}},
        ),
        created_at=90.0,
        max_attempts=max_attempts,
    )


class FakeRepository:
    def __init__(self, job: AIJob) -> None:
        self.job = job
        self.finishes = 0
        self.failures = 0

    def claim(self, worker_id: str, *, now: float, lease_seconds: float) -> AIJob | None:
        expired = (
            self.job.status == JobStatus.RUNNING
            and self.job.lease_until is not None
            and self.job.lease_until <= now
        )
        if self.job.status != JobStatus.QUEUED and not expired:
            return None
        if self.job.attempts >= self.job.max_attempts:
            return None
        self.job = replace(
            self.job,
            status=JobStatus.RUNNING,
            attempts=self.job.attempts + 1,
            lease_token=self.job.lease_token + 1,
            lease_owner=worker_id,
            lease_until=now + lease_seconds,
            started_at=now,
        )
        return self.job

    def _current(self, job_id: str, lease_token: int, now: float) -> bool:
        return (
            self.job.job_id == job_id
            and self.job.status == JobStatus.RUNNING
            and self.job.lease_token == lease_token
            and self.job.lease_until is not None
            and self.job.lease_until > now
        )

    def finish(
        self,
        job_id: str,
        lease_token: int,
        *,
        result: Mapping[str, JsonValue],
        now: float,
        validation_errors: tuple[str, ...] = (),
    ) -> AIJob | None:
        if not self._current(job_id, lease_token, now):
            return None
        self.finishes += 1
        self.job = replace(
            self.job,
            status=JobStatus.READY,
            result=result,
            completed_at=now,
            validation_errors=validation_errors,
        )
        return self.job

    def fail(
        self,
        job_id: str,
        lease_token: int,
        *,
        error: str,
        now: float,
        retryable: bool = False,
        fallback_result: Mapping[str, JsonValue] | None = None,
        validation_errors: tuple[str, ...] = (),
    ) -> AIJob | None:
        if not self._current(job_id, lease_token, now):
            return None
        self.failures += 1
        self.job = replace(
            self.job,
            status=JobStatus.QUEUED if retryable else JobStatus.FAILED,
            error=error,
            result=fallback_result,
            fallback_used=fallback_result is not None,
            completed_at=None if retryable else now,
            validation_errors=validation_errors,
        )
        return self.job

    def cancel(self, job_id: str, lease_token: int, *, now: float, reason: str) -> AIJob | None:
        if not self._current(job_id, lease_token, now):
            return None
        self.job = replace(self.job, status=JobStatus.CANCELLED, error=reason, completed_at=now)
        return self.job


class FakeProvider:
    def __init__(self, responses: list[str | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[Mapping[str, object], Mapping[str, JsonValue], str | None]] = []

    async def generate(
        self,
        *,
        schema: Mapping[str, object],
        payload: Mapping[str, JsonValue],
        repair_error: str | None = None,
    ) -> str:
        self.calls.append((schema, payload, repair_error))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class GatedProvider:
    def __init__(self, result: str) -> None:
        self.result = result
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False

    async def generate(
        self,
        *,
        schema: Mapping[str, object],
        payload: Mapping[str, JsonValue],
        repair_error: str | None = None,
    ) -> str:
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return self.result


def response(job: AIJob) -> str:
    return canonical_bytes(fallback_result(job.spec)).decode()


async def test_success_delivers_schema_and_deeply_frozen_facts_only() -> None:
    repository = FakeRepository(new_job())
    provider = FakeProvider([response(repository.job)])
    worker = NarrativeWorker(repository, provider, clock=FakeClock())
    result = await worker.run_once("worker-a")
    assert result is not None and result.status == JobStatus.READY
    assert result.attempts == 1 and repository.finishes == 1
    assert not result.fallback_used
    schema, payload, repair = provider.calls[0]
    assert schema["additionalProperties"] is False and repair is None
    assert payload["expected_world_version"] == repository.job.spec.expected_world_version.to_dict()
    facts = cast(Mapping[str, JsonValue], payload["payload"])
    with pytest.raises(TypeError):
        cast(MutableMapping[str, JsonValue], facts["trace"])["record"] = "changed"
    assert await worker.run_once("worker-b") is None
    assert len(provider.calls) == 1


async def test_one_repair_keeps_job_identity_and_reports_bounded_schema_error() -> None:
    repository = FakeRepository(new_job())
    original_id = repository.job.job_id
    invalid = response(repository.job).replace('"species_id":"species-a"', '"species_id":42')
    provider = FakeProvider([invalid, response(repository.job)])
    result = await NarrativeWorker(repository, provider, clock=FakeClock()).run_once("worker")
    assert result is not None and result.status == JobStatus.READY
    assert result.job_id == original_id
    assert len(provider.calls) == 2
    assert provider.calls[1][2] is not None
    assert "input" not in provider.calls[1][2]
    assert len(result.validation_errors) == 1


async def test_invalid_repair_fails_with_stable_fallback_without_more_requests() -> None:
    repository = FakeRepository(new_job())
    provider = FakeProvider(["not JSON", '{"population": 999}'])
    worker = NarrativeWorker(repository, provider, clock=FakeClock())
    result = await worker.run_once("worker")
    assert result is not None and result.status == JobStatus.FAILED
    assert result.fallback_used
    assert result.result == fallback_result(result.spec)
    assert len(result.validation_errors) == 2 and len(provider.calls) == 2
    assert await worker.run_once("worker") is None
    assert repository.finishes == 0


@pytest.mark.parametrize(
    "error", [TimeoutError(), ConnectionError(), RetryableProviderError("429")]
)
async def test_transient_failure_requeues_and_total_attempts_are_bounded(error: Exception) -> None:
    repository = FakeRepository(new_job(max_attempts=2))
    provider = FakeProvider([error, error])
    worker = NarrativeWorker(repository, provider, clock=FakeClock())
    first = await worker.run_once("worker")
    assert first is not None and first.status == JobStatus.QUEUED
    assert first.result is None and not first.fallback_used
    second = await worker.run_once("worker")
    assert second is not None and second.status == JobStatus.FAILED
    assert second.attempts == second.max_attempts == 2
    assert second.job_id == first.job_id and second.fallback_used
    assert await worker.run_once("worker") is None
    assert len(provider.calls) == 2


async def test_nonretryable_provider_failure_falls_back_and_bounds_diagnostics() -> None:
    repository = FakeRepository(new_job())
    provider = FakeProvider([ValueError("x" * 5000)])
    result = await NarrativeWorker(repository, provider, clock=FakeClock()).run_once("worker")
    assert result is not None and result.status == JobStatus.FAILED
    assert result.fallback_used and len(cast(str, result.error)) <= 2000
    assert len(provider.calls) == 1


async def test_timeout_wrapper_uses_the_configured_deadline_without_sleep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_timeouts: list[float] = []

    async def expired(request: Coroutine[object, object, str], *, timeout: float) -> str:
        observed_timeouts.append(timeout)
        request.close()
        raise TimeoutError("Fake timer deadline reached")

    monkeypatch.setattr("app.ai.jobs.worker.asyncio.wait_for", expired)
    repository = FakeRepository(new_job(max_attempts=1))
    provider = FakeProvider([])
    worker = NarrativeWorker(
        repository,
        provider,
        config=WorkerConfig(request_timeout=2.0, lease_seconds=5.0),
        clock=FakeClock(),
    )
    result = await worker.run_once("worker")
    assert observed_timeouts == [2.0]
    assert result is not None and result.status == JobStatus.FAILED and result.fallback_used


async def test_cancellation_reaches_provider_and_propagates_without_finishing() -> None:
    repository = FakeRepository(new_job())
    provider = GatedProvider(response(repository.job))
    worker = NarrativeWorker(repository, provider, clock=FakeClock())
    task = asyncio.create_task(worker.run_once("worker"))
    await provider.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert provider.cancelled
    assert repository.job.status == JobStatus.CANCELLED
    assert repository.finishes == repository.failures == 0


async def test_expired_worker_cannot_finish_after_a_new_lease_wins() -> None:
    clock = FakeClock()
    repository = FakeRepository(new_job())
    slow_provider = GatedProvider(response(repository.job))
    slow = NarrativeWorker(repository, slow_provider, clock=clock)
    old_task = asyncio.create_task(slow.run_once("old-worker"))
    await slow_provider.started.wait()
    old_token = repository.job.lease_token
    clock.now += 40
    fast_provider = FakeProvider([response(repository.job)])
    fresh = await NarrativeWorker(repository, fast_provider, clock=clock).run_once("new-worker")
    assert fresh is not None and fresh.lease_token > old_token
    slow_provider.release.set()
    assert await old_task is None
    assert repository.finishes == 1 and repository.job == fresh


async def test_repair_budget_survives_requeue_and_repository_round_trip() -> None:
    repository = FakeRepository(new_job())
    provider = FakeProvider(["not JSON", RetryableProviderError("429"), "still not JSON"])
    worker = NarrativeWorker(repository, provider, clock=FakeClock())
    first = await worker.run_once("worker")
    assert first is not None and first.status == JobStatus.QUEUED
    repository.job = AIJob.from_dict(first.to_dict())
    result = await worker.run_once("worker")
    assert result is not None and result.status == JobStatus.FAILED
    assert len(provider.calls) == 3 and len(result.validation_errors) == 2


@pytest.mark.parametrize(
    "config",
    [
        {"request_timeout": 0.0},
        {"request_timeout": float("nan")},
        {"request_timeout": 400.0},
        {"lease_seconds": 30.0},
        {"lease_seconds": float("inf")},
    ],
)
def test_worker_enforces_a_finite_timeout_and_lease_budget(config: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        WorkerConfig(**config)

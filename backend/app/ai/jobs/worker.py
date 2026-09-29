"""Bounded narrative orchestration with injected storage/provider dependencies only."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from app.simulation.v2.values import JsonValue, freeze_mapping

from .models import AIJob
from .schemas import fallback_result, result_schema, validate_result


class JobRepository(Protocol):
    """All writes must atomically fence lease tokens and preserve terminal states.

    Claim increments attempts/token, reclaims expired leases, and enforces the job's
    maximum attempt count. Finish is the narrative commit boundary: implementations
    must compare the complete expected world version in their write transaction.
    None means no eligible claim or an obsolete/rejected lease, never an overwrite.
    """

    def claim(self, worker_id: str, *, now: float, lease_seconds: float) -> AIJob | None: ...

    def finish(
        self,
        job_id: str,
        lease_token: int,
        *,
        result: Mapping[str, JsonValue],
        now: float,
        validation_errors: tuple[str, ...] = (),
    ) -> AIJob | None: ...

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
    ) -> AIJob | None: ...

    def cancel(self, job_id: str, lease_token: int, *, now: float, reason: str) -> AIJob | None: ...


class NarrativeProvider(Protocol):
    async def generate(
        self,
        *,
        schema: Mapping[str, object],
        payload: Mapping[str, JsonValue],
        repair_error: str | None = None,
    ) -> str:
        """Return a complete JSON document; no session, repository or world is supplied."""
        ...


class RetryableProviderError(Exception):
    """An adapter may map transient network/rate-limit failures to this exception."""


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    request_timeout: float = 15.0
    lease_seconds: float = 35.0

    def __post_init__(self) -> None:
        if not 0 < self.request_timeout <= 300:
            raise ValueError("request_timeout must be between 0 and 300 seconds")
        if not 2 * self.request_timeout < self.lease_seconds <= 3600:
            raise ValueError("lease_seconds must exceed the two-request budget and be at most 3600")


class NarrativeWorker:
    """One claim per call, at most one schema repair and two timed provider requests.

    Durable attempts count lease executions. Thus a job with max_attempts=N makes
    at most 2*N requests; every request has a configured timeout. Transient retries
    are requeued through the repository for an external scheduler, never busy-looped.
    The worker owns no untracked background tasks and propagates task cancellation.
    """

    def __init__(
        self,
        repository: JobRepository,
        provider: NarrativeProvider,
        *,
        config: WorkerConfig | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.repository = repository
        self.provider = provider
        self.config = config or WorkerConfig()
        self.clock = clock

    async def run_once(self, worker_id: str) -> AIJob | None:
        job = self.repository.claim(
            worker_id, now=self.clock(), lease_seconds=self.config.lease_seconds
        )
        if job is None:
            return None
        errors = list(job.validation_errors)
        try:
            result = await self._generate(job, errors)
        except asyncio.CancelledError:
            self.repository.cancel(
                job.job_id, job.lease_token, now=self.clock(), reason="Worker task cancelled"
            )
            raise
        except (RetryableProviderError, TimeoutError, ConnectionError) as error:
            return self._fail(job, _diagnostic(error), errors, retryable=True)
        except Exception as error:
            return self._fail(job, _diagnostic(error), errors)
        if result is None:
            return self._fail(job, "Narrative validation failed after one repair", errors)
        return self.repository.finish(
            job.job_id,
            job.lease_token,
            result=result,
            now=self.clock(),
            validation_errors=tuple(errors),
        )

    async def _generate(self, job: AIJob, errors: list[str]) -> Mapping[str, JsonValue] | None:
        for _ in range(2 - len(errors)):
            output = await asyncio.wait_for(
                self.provider.generate(
                    schema=result_schema(job.spec.job_type),
                    payload=freeze_mapping(job.spec.to_dict()),
                    repair_error=errors[-1] if errors else None,
                ),
                timeout=self.config.request_timeout,
            )
            try:
                return validate_result(job.spec, output)
            except (ValueError, TypeError) as error:
                errors.append(_diagnostic(error))
        return None

    def _fail(
        self, job: AIJob, error: str, errors: list[str], *, retryable: bool = False
    ) -> AIJob | None:
        can_retry = retryable and job.attempts < job.max_attempts
        return self.repository.fail(
            job.job_id,
            job.lease_token,
            error=error,
            now=self.clock(),
            retryable=can_retry,
            fallback_result=None if can_retry else fallback_result(job.spec),
            validation_errors=tuple(errors),
        )


def _diagnostic(error: Exception) -> str:
    # Provider output is never persisted verbatim. Pydantic normally includes input
    # excerpts, so expose only error locations/types for structural validation.
    from pydantic import ValidationError

    if isinstance(error, ValidationError):
        return str(error.errors(include_input=False, include_url=False))[:2000].strip()
    return f"{type(error).__name__}: {str(error).strip()}"[:2000].strip()

"""Durable AI leases and exactly-once local narrative application in one transaction."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable, Mapping
from dataclasses import replace

from app.ai.jobs.models import TERMINAL_STATUSES, AIJob, JobSpec, JobStatus
from app.ai.jobs.schemas import fallback_result, validate_result
from app.simulation.v2.values import JsonValue, digest, freeze_mapping
from app.simulation.v2.version import WorldVersion

from .codec import decode, encode, version_from
from .database import Database, IdempotencyConflict
from .history import commit_id, head_row


class SQLiteJobRepository:
    def __init__(self, db: Database, *, clock: Callable[[], float] = time.time) -> None:
        self.db, self.clock = db, clock

    def enqueue_in(
        self, connection: sqlite3.Connection, specs: tuple[JobSpec, ...]
    ) -> tuple[AIJob, ...]:
        """Called by the world commit transaction, never from provider callbacks."""
        result: list[AIJob] = []
        for spec in specs:
            previous = self._get(connection, spec.job_id)
            if previous is not None:
                if previous.spec.input_hash != spec.input_hash:
                    raise IdempotencyConflict("AI idempotency key reused with different input")
                result.append(previous)
                continue
            version = spec.expected_world_version
            row = head_row(connection, version.world_id, version.timeline_id)
            version.require(version_from(dict(row)))
            snapshot_id = digest([version.to_dict(), row["turn_id"], row["state_hash"]])
            if spec.snapshot_id != snapshot_id or spec.turn_id != row["turn_id"]:
                raise ValueError("Job does not reference the current immutable commit")
            for event_id in spec.source_event_ids:
                event = connection.execute(
                    "SELECT commit_id FROM events WHERE event_id=?", (event_id,)
                ).fetchone()
                if event is None or event["commit_id"] != row["commit_id"]:
                    raise ValueError("AI source event must belong to the committed proposal")
            job = AIJob(spec, created_at=self.clock())
            connection.execute(
                "INSERT INTO ai_jobs VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    job.job_id,
                    *spec.idempotency_scope[:3],
                    spec.idempotency_key,
                    spec.input_hash,
                    job.status.value,
                    None,
                    job.created_at,
                    encode(job.to_dict()),
                ),
            )
            self._notify(connection, job, "AIJobQueued")
            result.append(job)
        return tuple(result)

    def get(self, job_id: str) -> AIJob | None:
        with self.db.connection() as connection:
            return self._get(connection, job_id)

    @staticmethod
    def _get(connection: sqlite3.Connection, job_id: str) -> AIJob | None:
        row = connection.execute("SELECT payload FROM ai_jobs WHERE job_id=?", (job_id,)).fetchone()
        return AIJob.from_dict(decode(row["payload"])) if row else None

    @staticmethod
    def _current(connection: sqlite3.Connection, job: AIJob) -> bool:
        version = job.spec.expected_world_version
        return version == version_from(
            dict(head_row(connection, version.world_id, version.timeline_id))
        )

    @staticmethod
    def _save(connection: sqlite3.Connection, job: AIJob) -> AIJob:
        connection.execute(
            "UPDATE ai_jobs SET status=?,lease_until=?,payload=? WHERE job_id=?",
            (
                job.status.value,
                job.lease_until,
                encode(job.to_dict()),
                job.job_id,
            ),
        )
        return job

    @staticmethod
    def _notify(connection: sqlite3.Connection, job: AIJob, kind: str) -> None:
        version = job.spec.expected_world_version
        connection.execute(
            "INSERT INTO outbox(message_id,world_id,timeline_id,commit_id,kind,payload) VALUES "
            "(?,?,?,?,?,?)",
            (
                digest([job.job_id, kind, job.lease_token]),
                version.world_id,
                version.timeline_id,
                commit_id(version),
                kind,
                encode(
                    {"job_id": job.job_id, "version": version.to_dict(), "status": job.status.value}
                ),
            ),
        )

    def _stale(self, connection: sqlite3.Connection, job: AIJob, now: float) -> AIJob:
        stale = replace(
            job,
            status=JobStatus.STALE,
            completed_at=now,
            lease_until=None,
            error="Expected world version is no longer current",
        )
        self._save(connection, stale)
        self._notify(connection, stale, "AIJobStale")
        return stale

    def claim(self, worker_id: str, *, now: float, lease_seconds: float) -> AIJob | None:
        if not worker_id.strip() or not 0 < lease_seconds <= 3600:
            raise ValueError("A bounded worker lease is required")
        with self.db.transaction() as connection:
            rows = connection.execute(
                "SELECT payload FROM ai_jobs WHERE status='QUEUED' OR (status='RUNNING' AND "
                "lease_until<=?) ORDER BY created_at,job_id",
                (now,),
            ).fetchall()
            for row in rows:
                job = AIJob.from_dict(decode(row["payload"]))
                if not self._current(connection, job):
                    self._stale(connection, job, now)
                    continue
                if job.attempts >= job.max_attempts:
                    failed = replace(
                        job,
                        status=JobStatus.FAILED,
                        completed_at=now,
                        lease_until=None,
                        result=fallback_result(job.spec),
                        fallback_used=True,
                        error="Worker lease retry budget exhausted",
                    )
                    self._save(connection, failed)
                    self._notify(connection, failed, "AIJobFailed")
                    continue
                return self._save(
                    connection,
                    replace(
                        job,
                        status=JobStatus.RUNNING,
                        lease_owner=worker_id,
                        lease_until=now + lease_seconds,
                        lease_token=job.lease_token + 1,
                        started_at=now,
                        attempts=job.attempts + 1,
                    ),
                )
        return None

    @staticmethod
    def _owns(job: AIJob | None, token: int, now: float) -> bool:
        return bool(
            job is not None
            and job.status == JobStatus.RUNNING
            and job.lease_token == token
            and job.lease_until is not None
            and job.lease_until > now
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
        with self.db.transaction() as connection:
            job = self._get(connection, job_id)
            if (
                job is not None
                and job.status == JobStatus.APPLIED
                and job.lease_token == lease_token
            ):
                return job
            if not self._owns(job, lease_token, now):
                return None
            assert job is not None
            if not self._current(connection, job):
                return self._stale(connection, job, now)
            validated = validate_result(job.spec, result)
            version = job.spec.expected_world_version
            connection.execute(
                "INSERT INTO narrative_heads VALUES (?,?,1) ON CONFLICT(world_id,timeline_id) "
                "DO UPDATE SET revision=revision+1",
                (version.world_id, version.timeline_id),
            )
            revision = connection.execute(
                "SELECT revision FROM narrative_heads WHERE world_id=? AND timeline_id=?",
                (version.world_id, version.timeline_id),
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO annotations VALUES (?,?,?,?)",
                (job_id, commit_id(version), revision, encode(validated)),
            )
            applied = replace(
                job,
                status=JobStatus.APPLIED,
                result=validated,
                completed_at=now,
                lease_until=None,
                error=None,
                validation_errors=validation_errors,
            )
            self._save(connection, applied)
            self._notify(connection, applied, "NarrativeReady")
            return applied

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
        with self.db.transaction() as connection:
            job = self._get(connection, job_id)
            if not self._owns(job, lease_token, now):
                return None
            assert job is not None
            if not self._current(connection, job):
                return self._stale(connection, job, now)
            retry = retryable and job.attempts < job.max_attempts
            result = (
                validate_result(job.spec, fallback_result) if fallback_result is not None else None
            )
            failed = replace(
                job,
                status=JobStatus.QUEUED if retry else JobStatus.FAILED,
                result=None if retry else result,
                fallback_used=not retry and result is not None,
                error=error[:2000],
                validation_errors=validation_errors,
                lease_until=None,
                completed_at=None if retry else now,
            )
            self._save(connection, failed)
            if not retry:
                self._notify(connection, failed, "AIJobFailed")
            return failed

    def cancel(self, job_id: str, lease_token: int, *, now: float, reason: str) -> AIJob | None:
        with self.db.transaction() as connection:
            job = self._get(connection, job_id)
            if not self._owns(job, lease_token, now):
                return None
            assert job is not None
            cancelled = replace(
                job,
                status=JobStatus.CANCELLED,
                error=reason[:2000],
                completed_at=now,
                lease_until=None,
            )
            self._save(connection, cancelled)
            self._notify(connection, cancelled, "AIJobCancelled")
            return cancelled

    def cancel_scope(self, version: WorldVersion, *, now: float, reason: str) -> int:
        count = 0
        with self.db.transaction() as connection:
            rows = connection.execute(
                "SELECT payload FROM ai_jobs WHERE world_id=? AND timeline_id=? AND generation=?",
                (version.world_id, version.timeline_id, version.generation),
            ).fetchall()
            for row in rows:
                job = AIJob.from_dict(decode(row["payload"]))
                if job.spec.expected_world_version != version or job.status in TERMINAL_STATUSES:
                    continue
                cancelled = replace(
                    job,
                    status=JobStatus.CANCELLED,
                    error=reason[:2000],
                    completed_at=now,
                    lease_until=None,
                )
                self._save(connection, cancelled)
                self._notify(connection, cancelled, "AIJobCancelled")
                count += 1
        return count

    def annotations(self, version: WorldVersion) -> tuple[Mapping[str, JsonValue], ...]:
        with self.db.connection() as connection:
            rows = connection.execute(
                "SELECT job_id,narrative_revision,payload FROM annotations WHERE commit_id=? "
                "ORDER BY narrative_revision",
                (commit_id(version),),
            ).fetchall()
        return tuple(
            freeze_mapping(
                {
                    "job_id": row["job_id"],
                    "narrative_revision": row["narrative_revision"],
                    "result": decode(row["payload"]),
                }
            )
            for row in rows
        )

"""Immutable job contracts. These objects never hold a live world or database session."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal, TypeAlias, cast

from app.simulation.v2.values import (
    JsonValue,
    canonical_bytes,
    digest,
    freeze_mapping,
    natural,
    thaw,
)
from app.simulation.v2.version import WorldVersion

JobKind: TypeAlias = Literal["species", "speciation", "adaptation", "hybridization"]
JOB_KINDS = ("species", "speciation", "adaptation", "hybridization")
MAX_INPUT_BYTES = 65_536


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    VALIDATING = "VALIDATING"
    READY = "READY"
    APPLIED = "APPLIED"
    RETRY_WAIT = "RETRY_WAIT"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    STALE = "STALE"


TERMINAL_STATUSES = frozenset(
    {JobStatus.APPLIED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.STALE}
)


def _text(value: object, name: str, maximum: int = 128) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must be nonempty text of at most {maximum} characters")
    if value != value.strip():
        raise ValueError(f"{name} cannot have surrounding whitespace")
    return value


def _ids(values: object, name: str, maximum: int) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)) or len(values) > maximum:
        raise ValueError(f"{name} must be a bounded sequence")
    result = tuple(_text(value, name) for value in values)
    if len(set(result)) != len(result):
        raise ValueError(f"{name} cannot contain duplicate IDs")
    return result


def _time(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError(f"{name} must be a finite nonnegative timestamp")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{name} must be a finite nonnegative timestamp")
    return result


@dataclass(frozen=True, slots=True)
class JobSpec:
    expected_world_version: WorldVersion
    turn_id: int
    job_type: JobKind
    target_ids: tuple[str, ...]
    source_event_ids: tuple[str, ...]
    payload: Mapping[str, JsonValue] = field(default_factory=dict)
    proposal_id: str | None = None
    snapshot_id: str | None = None
    schema_version: str = "1"
    prompt_version: str = "1"
    provider_config_hash: str = "unconfigured"
    idempotency_key: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.expected_world_version, WorldVersion):
            raise TypeError("expected_world_version must be a full WorldVersion")
        _text(self.expected_world_version.world_id, "world_id")
        _text(self.expected_world_version.timeline_id, "timeline_id")
        natural(self.turn_id, "turn_id")
        if self.job_type not in JOB_KINDS:
            raise ValueError("Unsupported narrative job type")
        targets = _ids(self.target_ids, "target_ids", 16)
        events = _ids(self.source_event_ids, "source_event_ids", 64)
        if not targets or (self.job_type != "speciation" and len(targets) != 1):
            raise ValueError("Job target count does not match its kind")
        if self.job_type == "speciation" and len(targets) < 2:
            raise ValueError("Speciation requires at least two frozen children")
        for name in ("proposal_id", "snapshot_id"):
            value = getattr(self, name)
            if value is not None:
                _text(value, name)
        if self.job_type != "species" and self.proposal_id is None:
            raise ValueError("This job kind requires a frozen proposal_id")
        for name in ("schema_version", "prompt_version", "provider_config_hash"):
            _text(getattr(self, name), name)
        if self.schema_version != "1":
            raise ValueError("Unsupported narrative schema version")
        payload = freeze_mapping(self.payload)
        if len(canonical_bytes(payload)) > MAX_INPUT_BYTES:
            raise ValueError("Frozen job payload is too large")
        _ids(payload.get("organ_ids", ()), "organ_ids", 32)
        object.__setattr__(self, "target_ids", targets)
        object.__setattr__(self, "source_event_ids", events)
        object.__setattr__(self, "payload", payload)
        if not isinstance(self.idempotency_key, str):
            raise ValueError("idempotency_key must be a string")
        if not self.idempotency_key:
            identity = self._identity()
            identity.pop("provider_config_hash")
            object.__setattr__(self, "idempotency_key", "narrative:" + digest(identity))
        _text(self.idempotency_key, "idempotency_key", 256)

    def _identity(self) -> dict[str, object]:
        return {
            "expected_world_version": self.expected_world_version.to_dict(),
            "turn_id": self.turn_id,
            "job_type": self.job_type,
            "target_ids": self.target_ids,
            "source_event_ids": self.source_event_ids,
            "proposal_id": self.proposal_id,
            "snapshot_id": self.snapshot_id,
            "schema_version": self.schema_version,
            "prompt_version": self.prompt_version,
            "provider_config_hash": self.provider_config_hash,
        }

    @property
    def idempotency_scope(self) -> tuple[str, str, int, str]:
        version = self.expected_world_version
        return (version.world_id, version.timeline_id, version.generation, self.idempotency_key)

    @property
    def job_id(self) -> str:
        return "ai-job:" + digest(self.idempotency_scope)

    @property
    def input_hash(self) -> str:
        return digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return cast(
            dict[str, object],
            thaw(
                freeze_mapping(
                    {
                        **self._identity(),
                        "payload": self.payload,
                        "idempotency_key": self.idempotency_key,
                    }
                )
            ),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> JobSpec:
        record = dict(value)
        version = record.pop("expected_world_version")
        if not isinstance(version, Mapping):
            raise TypeError("Serialized expected_world_version must be an object")
        if set(version) != {"world_id", "timeline_id", "generation", "revision"}:
            raise ValueError("Serialized expected_world_version must contain the complete identity")
        return cls(
            expected_world_version=WorldVersion(**cast(dict[str, Any], dict(version))),
            **cast(dict[str, Any], record),
        )


@dataclass(frozen=True, slots=True)
class AIJob:
    spec: JobSpec
    status: JobStatus = JobStatus.QUEUED
    created_at: float = 0.0
    max_attempts: int = 3
    attempts: int = 0
    started_at: float | None = None
    completed_at: float | None = None
    lease_owner: str | None = None
    lease_until: float | None = None
    lease_token: int = 0
    result: Mapping[str, JsonValue] | None = None
    error: str | None = None
    validation_errors: tuple[str, ...] = ()
    fallback_used: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.spec, JobSpec):
            raise TypeError("AIJob requires an immutable JobSpec")
        object.__setattr__(self, "status", JobStatus(self.status))
        for name in ("max_attempts", "attempts", "lease_token"):
            natural(getattr(self, name), name)
        if not 1 <= self.max_attempts <= 10 or self.attempts > self.max_attempts:
            raise ValueError("Job attempts must fit a maximum budget of 1..10")
        for name in ("created_at", "started_at", "completed_at", "lease_until"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _time(value, name))
        if self.created_at is None:
            raise ValueError("created_at is required")
        if self.lease_owner is not None:
            _text(self.lease_owner, "lease_owner")
        if self.result is not None:
            result = freeze_mapping(self.result)
            if len(canonical_bytes(result)) > MAX_INPUT_BYTES:
                raise ValueError("Narrative result is too large")
            object.__setattr__(self, "result", result)
        if self.error is not None:
            _text(self.error, "error", 2000)
        if not isinstance(self.validation_errors, (tuple, list)):
            raise ValueError("validation_errors must be a sequence")
        if len(self.validation_errors) > 2:
            raise ValueError("At most two bounded validation errors may be retained")
        errors = tuple(_text(item, "validation_error", 2000) for item in self.validation_errors)
        object.__setattr__(self, "validation_errors", errors)
        if not isinstance(self.fallback_used, bool):
            raise ValueError("fallback_used must be a boolean")

    @property
    def job_id(self) -> str:
        return self.spec.job_id

    def to_dict(self) -> dict[str, object]:
        return {
            "spec": self.spec.to_dict(),
            "status": self.status.value,
            "created_at": self.created_at,
            "max_attempts": self.max_attempts,
            "attempts": self.attempts,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "lease_owner": self.lease_owner,
            "lease_until": self.lease_until,
            "lease_token": self.lease_token,
            "result": None if self.result is None else thaw(self.result),
            "error": self.error,
            "validation_errors": list(self.validation_errors),
            "fallback_used": self.fallback_used,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> AIJob:
        record = dict(value)
        spec = record.pop("spec")
        if not isinstance(spec, Mapping):
            raise TypeError("Serialized spec must be an object")
        return cls(spec=JobSpec.from_dict(spec), **cast(dict[str, Any], record))

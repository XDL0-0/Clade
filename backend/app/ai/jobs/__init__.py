"""Standalone narrative contracts; deliberately not wired into the legacy runtime."""

from .models import AIJob, JobKind, JobSpec, JobStatus
from .schemas import (
    AdaptationNarrativeResult,
    HybridizationNarrativeResult,
    SpeciationNarrativeResult,
    SpeciesNarrativeResult,
    fallback_result,
    result_schema,
    validate_result,
)
from .worker import (
    JobRepository,
    NarrativeProvider,
    NarrativeWorker,
    RetryableProviderError,
    WorkerConfig,
)

__all__ = [
    "AIJob",
    "AdaptationNarrativeResult",
    "HybridizationNarrativeResult",
    "JobKind",
    "JobRepository",
    "JobSpec",
    "JobStatus",
    "NarrativeProvider",
    "NarrativeWorker",
    "RetryableProviderError",
    "SpeciesNarrativeResult",
    "SpeciationNarrativeResult",
    "WorkerConfig",
    "fallback_result",
    "result_schema",
    "validate_result",
]

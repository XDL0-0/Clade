"""Extracted legacy resource kernels; these are not registered simulation stages."""

from .legacy import (
    MODEL_VERSION,
    LegacyNppParameters,
    LegacyResourceParameters,
    LegacyResourceResult,
    calculate_legacy_npp,
    calculate_legacy_predation_pressure,
    transition_legacy_resources,
)

__all__ = [
    "MODEL_VERSION",
    "LegacyNppParameters",
    "LegacyResourceParameters",
    "LegacyResourceResult",
    "calculate_legacy_npp",
    "calculate_legacy_predation_pressure",
    "transition_legacy_resources",
]

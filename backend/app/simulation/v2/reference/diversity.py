"""Population-weighted variance of deme means, not allele heterozygosity."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np

from ..context import TurnContext
from .common import FloatArray, IntArray
from .evolution_contracts import EvolutionBudget, weighted_traits
from .world import TRAITS


def deme_diversity(context: TurnContext, population: IntArray) -> float:
    raw = context.snapshot.arrays["deme_traits"].numpy()
    if raw.dtype != np.dtype("float64") or raw.shape != (*population.shape, len(TRAITS)):
        raise ValueError("Genetic diversity requires seven float64 deme trait coordinates")
    deme: FloatArray = np.asarray(raw, dtype=np.float64)
    EvolutionBudget().validate(deme)
    slots: set[int] = set()
    weighted = 0.0
    total = int(population.sum(dtype=object))
    for metadata in context.species_state.values():
        assert isinstance(metadata, Mapping)
        slot = metadata["slot"]
        assert isinstance(slot, int)
        slots.add(slot)
        count = int(population[slot].sum(dtype=object))
        if not count:
            continue
        mean = weighted_traits(deme[slot], population[slot])
        variance = ((deme[slot] - mean) ** 2).mean(axis=1)
        weighted += float((variance * (population[slot].astype(np.float64) / total)).sum())
    unused = sorted(set(range(population.shape[0])) - slots)
    if np.any(deme[unused]):
        raise ValueError("Unused genetic diversity rows must be zero")
    return weighted

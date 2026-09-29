"""Population-weighted phenotype histograms; no world mutation or inferred genotypes."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np

from ..context import WorldSnapshot
from ..reference.world import TRAITS
from ..values import JsonValue


def trait_distribution(snapshot: WorldSnapshot) -> Mapping[str, JsonValue]:
    population = snapshot.arrays["population"].numpy()
    if (
        population.dtype != np.dtype("int64")
        or population.ndim != 2
        or np.any(np.less(population, 0))
    ):
        raise ValueError("Trait distribution requires nonnegative int64 species/tile counts")
    deme = snapshot.arrays.get("deme_traits")
    values = deme.numpy() if deme is not None else None
    if values is not None and (
        values.shape != (*population.shape, len(TRAITS))
        or values.dtype != np.dtype("float64")
        or not np.isfinite(values).all()
        or np.any(np.less(values, 0) | np.greater(values, 1))
    ):
        raise ValueError("Trait distribution requires finite bounded seven-axis deme means")
    counts: dict[str, dict[str, JsonValue]] = {
        name: {str(index): 0 for index in range(10)} for name in TRAITS
    }
    total = 0
    slots: set[int] = set()
    edges = np.arange(1, 11, dtype=np.float64) / 10
    for metadata in snapshot.domain("species").values():
        if not isinstance(metadata, Mapping):
            raise ValueError("Invalid species metadata for trait distribution")
        slot = metadata.get("slot")
        if type(slot) is not int or not 0 <= slot < population.shape[0] or slot in slots:
            raise ValueError("Trait distribution requires unique species slots")
        slots.add(slot)
        traits = metadata.get("traits")
        if values is None and not isinstance(traits, Mapping):
            raise ValueError("Species trait means are missing")
        fallback: Mapping[str, JsonValue] = traits if isinstance(traits, Mapping) else {}
        for tile in np.flatnonzero(population[slot]):
            size = int(population[slot, tile])
            total += size
            for axis, name in enumerate(TRAITS):
                trait: object = values[slot, tile, axis] if values is not None else fallback[name]
                if isinstance(trait, bool) or not isinstance(trait, (int, float, np.floating)):
                    raise ValueError("Trait distribution requires numerical trait values")
                value = float(trait)
                if not np.isfinite(value) or not 0 <= value <= 1:
                    raise ValueError("Trait value lies outside [0,1]")
                # searchsorted makes bin-edge semantics explicit, including 1.0.
                index = min(9, int(np.searchsorted(edges, value, side="right")))
                previous = counts[name][str(index)]
                assert isinstance(previous, int)
                counts[name][str(index)] = previous + size
    if any(np.any(population[slot]) for slot in set(range(population.shape[0])) - slots):
        raise ValueError("Unassigned species slot has population")
    return {
        "basis": "population_weighted_deme_means" if values is not None else "species_means",
        "interpretation": "deme phenotype means, not individual or allele genotypes",
        "bin_edges": tuple(index / 10 for index in range(11)),
        "interval": "left_closed_right_open_except_last_includes_one",
        "population": total,
        "weighted_counts": counts,
    }

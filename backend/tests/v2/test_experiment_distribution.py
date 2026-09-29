"""Whole-population histograms preserve local divergence and integer counts."""

from collections.abc import Mapping
from dataclasses import replace
from typing import cast

import numpy as np
import pytest

from app.simulation.v2.context import WorldSnapshot
from app.simulation.v2.experiments.distribution import trait_distribution
from app.simulation.v2.values import FrozenArray, JsonValue
from app.simulation.v2.version import WorldVersion


def fixture() -> WorldSnapshot:
    traits = dict.fromkeys(
        ("armor", "speed", "attack", "cooperation", "toxin", "detox", "engineering"), 0.5
    )
    return WorldSnapshot(
        WorldVersion("histogram", "main"),
        0,
        {"species": {"a": {"slot": 0, "traits": traits}}},
        {
            "population": FrozenArray.from_numpy(np.array([[2**53 + 1, 3]], dtype=np.int64)),
            "deme_traits": FrozenArray.from_numpy(np.array([[[0.0] * 7, [1.0] * 7]])),
        },
    )


def test_histogram_preserves_deme_divergence_and_large_integer_weights() -> None:
    snapshot = fixture()
    result = trait_distribution(snapshot)
    assert result["basis"] == "population_weighted_deme_means"
    assert result["population"] == 2**53 + 4
    axes = cast(Mapping[str, Mapping[str, int]], result["weighted_counts"])
    for counts in axes.values():
        assert counts["0"] == 2**53 + 1 and counts["9"] == 3
        assert sum(counts.values()) == 2**53 + 4
    fallback = replace(snapshot, arrays={"population": snapshot.arrays["population"]})
    summary = trait_distribution(fallback)
    assert summary["basis"] == "species_means"
    axes = cast(Mapping[str, Mapping[str, int]], summary["weighted_counts"])
    assert axes["armor"]["5"] == 2**53 + 4


@pytest.mark.parametrize("bad", [-0.1, 1.1, float("nan")])
def test_invalid_trait_is_rejected(bad: float) -> None:
    snapshot = fixture()
    species: dict[str, JsonValue] = {
        "a": {
            "slot": 0,
            "traits": {
                name: bad
                for name in (
                    "armor",
                    "speed",
                    "attack",
                    "cooperation",
                    "toxin",
                    "detox",
                    "engineering",
                )
            },
        }
    }
    with pytest.raises(ValueError):
        invalid = replace(
            snapshot,
            state={"species": species},
            arrays={"population": snapshot.arrays["population"]},
        )
        trait_distribution(invalid)

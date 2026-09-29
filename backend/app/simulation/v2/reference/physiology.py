"""Shared physiological responses used by ecology and its counterfactuals."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from numpy.typing import NDArray

from .common import FloatArray

if TYPE_CHECKING:
    from .ecology import Species


def thermal_response(item: Species, temperature: FloatArray) -> FloatArray:
    return np.asarray(
        np.exp(
            -np.square(
                (temperature - item.optimum) / (item.width * (1 + 0.25 * item.traits["armor"]))
            )
        ),
        dtype=np.float64,
    )


def water_response(item: Species, soil: FloatArray, land: NDArray[np.bool_]) -> FloatArray:
    need = 40 * item.water_need * (1 - 0.5 * item.traits["engineering"])
    hydration = np.divide(soil, soil + need, out=np.ones_like(soil), where=soil + need > 0)
    return np.asarray(np.where(land, hydration, 1.0), dtype=np.float64)

"""Conservative, one-pass downhill water routing in ecology-reference-v1.

Stores and fluxes are mm over equal-area tiles. All positive runoff, however
small, propagates in descending terrain order. Local terrain minima retain
lakes; ocean water exits the simulated terrestrial stores as an explicit export.
"""

from __future__ import annotations

import math

import numpy as np

from ..context import TurnContext
from ..contracts import SimulationStage, StageContract, StageResult
from .common import domain, number, result
from .environment import checked_field, finite_outputs, layout, years
from .topology import neighbor_graph


class HydrologyStage(SimulationStage):
    contract = StageContract(
        "reference_hydrology",
        "1",
        dependencies=("reference_geology",),
        reads=(
            "state.geometry",
            "state.environment.sea_level",
            "state.environment.ecological_years_per_turn",
            "arrays.elevation",
            "arrays.temperature",
            "arrays.soil_quality",
            "arrays.soil_water",
            "arrays.surface_water",
            "arrays.rainfall",
            "arrays.humidity",
            "arrays.river_flux",
        ),
        writes=(
            "arrays.soil_water",
            "arrays.surface_water",
            "arrays.humidity",
            "arrays.river_flux",
        ),
    )

    def execute(self, context: TurnContext) -> StageResult:
        width, height = layout(context)
        graph = neighbor_graph(width, height)
        sea = number(domain(context, "environment")["sea_level"], "sea_level")
        months = 12 * years(context, "ecological")
        elevation = checked_field(context, "elevation")
        temperature = checked_field(context, "temperature")
        quality = checked_field(context, "soil_quality", low=0, high=1)
        soil = checked_field(context, "soil_water", low=0)
        surface = checked_field(context, "surface_water", low=0)
        rain = checked_field(context, "rainfall", low=0)
        checked_field(context, "humidity", low=0, high=1)
        checked_field(context, "river_flux", low=0)
        initial_soil, initial_surface = math.fsum(soil), math.fsum(surface)
        rain_total = math.fsum(rain)
        capacity = 150 + 100 * quality
        ocean = elevation <= sea
        surface += rain
        excess = np.maximum(soil - capacity, 0)
        soil -= excess
        surface += excess
        infiltration = np.minimum(surface, capacity - soil)
        infiltration[ocean] = 0
        soil += infiltration
        surface -= infiltration
        potential_evaporation = np.clip(0.5 + 0.08 * temperature, 0, 8) * months
        potential_evaporation[ocean] = 0
        surface_evaporation = np.minimum(surface, potential_evaporation)
        surface -= surface_evaporation
        soil_evaporation = np.minimum(soil, potential_evaporation - surface_evaporation)
        soil -= soil_evaporation
        evaporated = math.fsum(surface_evaporation) + math.fsum(soil_evaporation)
        exported_parts = [float(soil[index] + surface[index]) for index in np.flatnonzero(ocean)]
        soil[ocean], surface[ocean] = 0, 0
        river = np.zeros_like(surface)
        # Stable elevation order makes each downstream tile receive every upstream
        # contribution before it routes. Equal elevations never exchange water.
        for value in np.argsort(-elevation, kind="stable"):
            index = int(value)
            if ocean[index]:
                continue
            flow = float(surface[index])
            river[index] = flow
            downstream = [other for other in graph[index] if elevation[other] < elevation[index]]
            if not downstream or flow == 0:
                continue
            target = min(downstream, key=lambda other: (elevation[other], other))
            surface[index] = 0
            if ocean[target]:
                exported_parts.append(flow)
                river[target] += flow
            else:
                surface[target] += flow
        exported = math.fsum(exported_parts)
        humidity = np.clip(soil / capacity, 0, 1)
        humidity[ocean] = 1
        finite_outputs(soil, surface, river, humidity)
        if np.any(soil < 0) or np.any(surface < 0) or np.any(river < 0):
            raise ValueError("Water stores and fluxes must be nonnegative")
        final_soil, final_surface = math.fsum(soil), math.fsum(surface)
        residual = math.fsum(
            (
                initial_soil,
                initial_surface,
                rain_total,
                -evaporated,
                -exported,
                -final_soil,
                -final_surface,
            )
        )
        if not math.isfinite(residual) or abs(residual) > 1e-8:
            raise ValueError(f"Water conservation failed: residual {residual} mm")
        return result(
            context,
            self.contract.name,
            arrays={
                "soil_water": soil,
                "surface_water": surface,
                "humidity": humidity,
                "river_flux": river,
            },
            metrics={
                "initial_soil_water_mm": initial_soil,
                "initial_surface_water_mm": initial_surface,
                "rainfall_input_mm": rain_total,
                "evaporation_mm": evaporated,
                "ocean_export_mm": exported,
                "final_soil_water_mm": final_soil,
                "final_surface_water_mm": final_surface,
                "water_balance_residual_mm": residual,
                "infiltration_mm": math.fsum(infiltration),
                "capacity_overflow_mm": math.fsum(excess),
            },
        )

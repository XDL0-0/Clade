"""Ecology-reference-v1 climate, moving plates and deterministic biome rules.

This CPU model is intentionally new behavior, not parity with the tensor model.
Elevations/sea level are metres, temperature Celsius, rain mm/turn, and plate
velocities grid cells/geological year. Plant biomass is an edible carbon stock.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import cast

import numpy as np
from numpy.typing import NDArray

from ..context import TurnContext
from ..contracts import SimulationStage, StageContract, StageResult
from ..events import WorldEvent
from ..values import JsonValue
from .common import (
    FloatArray,
    IntArray,
    domain,
    event,
    field,
    geometry,
    number,
    result,
    state_patch,
)
from .topology import neighbor_graph

MODEL_VERSION = "ecology-reference-v1"


def layout(context: TurnContext) -> tuple[int, int]:
    width, height = geometry(context)
    if width % 2:
        raise ValueError("Reference cylindrical geometry requires even width")
    return width, height


def checked_field(
    context: TurnContext, name: str, *, low: float | None = None, high: float | None = None
) -> FloatArray:
    width, height = layout(context)
    original = context.snapshot.arrays[name].numpy()
    if original.dtype != np.dtype("float64") or original.shape != (width * height,):
        raise ValueError(f"{name} must be float64 with shape ({width * height},)")
    value = field(context, name)
    if not np.all(np.isfinite(value)):
        raise ValueError(f"{name} must be finite")
    if (low is not None and np.any(value < low)) or (high is not None and np.any(value > high)):
        raise ValueError(f"{name} is outside its allowed range")
    return value


def checked_codes(context: TurnContext, name: str, high: int | None = None) -> IntArray:
    width, height = layout(context)
    value = context.snapshot.arrays[name].numpy()
    if value.dtype != np.dtype("int64") or value.shape != (width * height,):
        raise ValueError(f"{name} must be int64 with shape ({width * height},)")
    codes = np.array(value, dtype=np.int64, copy=True)
    if np.any(codes < 0) or (high is not None and np.any(codes > high)):
        raise ValueError(f"{name} is outside its allowed range")
    return codes


def years(context: TurnContext, kind: str) -> float:
    name = f"{kind}_years_per_turn"
    value = number(domain(context, "environment")[name], name)
    if value < 0 or (kind == "ecological" and value == 0):
        raise ValueError(f"{name} must be {'positive' if kind == 'ecological' else 'nonnegative'}")
    return value


def finite_outputs(*arrays: NDArray[np.generic]) -> None:
    if any(not np.all(np.isfinite(value)) for value in arrays):
        raise ValueError("Reference outputs must be finite")


class ClimateStage(SimulationStage):
    contract = StageContract(
        "reference_climate",
        "1",
        reads=(
            "state.geometry",
            "state.environment.baseline_temperature",
            "state.environment.global_temperature",
            "state.environment.co2_ppm",
            "state.environment.warming_offset",
            "state.environment.sea_level",
            "state.environment.ecological_years_per_turn",
            "arrays.elevation",
            "arrays.plant_biomass",
            "arrays.temperature",
            "arrays.rainfall",
        ),
        writes=(
            "state.environment.global_temperature",
            "state.environment.co2_ppm",
            "state.environment.warming_offset",
            "state.environment.sea_level",
            "arrays.temperature",
            "arrays.rainfall",
        ),
    )

    def execute(self, context: TurnContext) -> StageResult:
        width, height = layout(context)
        env = domain(context, "environment")
        baseline = number(env["baseline_temperature"], "baseline_temperature")
        previous = number(env["global_temperature"], "global_temperature")
        old_co2 = number(env["co2_ppm"], "co2_ppm")
        old_offset = number(env["warming_offset"], "warming_offset")
        sea = number(env["sea_level"], "sea_level")
        co2 = number(context.command.get("co2_ppm", old_co2), "co2_ppm")
        offset = number(context.command.get("warming_offset", old_offset), "warming_offset")
        if not 10 <= old_co2 <= 5000 or not 10 <= co2 <= 5000:
            raise ValueError("co2_ppm must be between 10 and 5000")
        months = 12 * years(context, "ecological")
        elevation = checked_field(context, "elevation")
        biomass = checked_field(context, "plant_biomass", low=0)
        checked_field(context, "temperature")
        checked_field(context, "rainfall", low=0)
        target = number(baseline + 3 * math.log2(co2 / 280) + offset, "target_temperature")
        change = number(0.1 * (target - previous), "temperature_delta")
        global_temperature = number(previous + change, "global_temperature")
        new_sea = number(sea + 2.5 * change, "sea_level")
        latitude = np.repeat((0.5 - (np.arange(height) + 0.5) / height) * math.pi, width)
        seasonal = 8 * np.sin(latitude) * math.sin(2 * math.pi * ((context.turn_id - 1) % 12) / 12)
        temperature = (
            global_temperature
            + 20 * (np.cos(latitude) - 2 / math.pi)
            + seasonal
            - 0.0065 * np.maximum(elevation - new_sea, 0)
        )
        forest_feedback = 0.25 * (biomass / (100 + biomass))
        rainfall = 30 * months * np.cos(latitude) * (1 + forest_feedback)
        finite_outputs(temperature, rainfall)
        patches = tuple(
            state_patch(context, ("environment", key), value)
            for key, value in (
                ("global_temperature", global_temperature),
                ("co2_ppm", co2),
                ("warming_offset", offset),
                ("sea_level", new_sea),
            )
        )
        events: tuple[WorldEvent, ...] = ()
        if change != 0 or co2 != old_co2 or offset != old_offset:
            events = (
                event(
                    context,
                    self.contract.name,
                    "ClimateShift",
                    payload={
                        "reason": "co2_and_warming_offset_relaxation",
                        "co2_ppm": co2,
                        "warming_offset_c": offset,
                        "target_temperature_c": target,
                        "temperature_delta_c": change,
                        "sea_level_delta_m": new_sea - sea,
                    },
                ),
            )
        return result(
            context,
            self.contract.name,
            arrays={
                "temperature": temperature,
                "rainfall": rainfall,
            },
            state=patches,
            events=events,
            metrics={
                "global_temperature_c": global_temperature,
                "temperature_delta_c": change,
                "rainfall_mm": float(np.sum(rainfall)),
                "forest_feedback_max": float(np.max(forest_feedback)),
            },
        )


def _plates(context: TurnContext, width: int, height: int) -> list[dict[str, JsonValue]]:
    values = domain(context, "environment")["plates"]
    if not isinstance(values, tuple) or not values:
        raise ValueError("plates must be a nonempty sequence")
    plates: list[dict[str, JsonValue]] = []
    seen: set[int] = set()
    for value in values:
        if not isinstance(value, Mapping):
            raise ValueError("Each plate must be a mapping")
        identity = value["id"]
        if type(identity) is not int or not 0 <= identity <= np.iinfo(np.int64).max:
            raise ValueError("Plate id must be a nonnegative int64")
        if identity in seen:
            raise ValueError("Plate ids must be unique")
        seen.add(identity)
        x, y = number(value["x"], "plate.x"), number(value["y"], "plate.y")
        if not 0 <= x < width or not 0 <= y <= height - 1:
            raise ValueError("Plate center is outside the cylindrical grid")
        plates.append(
            {
                "id": identity,
                "x": x,
                "y": y,
                "vx": number(value["vx"], "plate.vx"),
                "vy": number(value["vy"], "plate.vy"),
            }
        )
    return sorted(plates, key=lambda plate: cast(int, plate["id"]))


def _periodic_dx(value: float, width: int) -> float:
    return (value + width / 2) % width - width / 2


class GeologyStage(SimulationStage):
    contract = StageContract(
        "reference_geology",
        "1",
        dependencies=("reference_climate",),
        reads=(
            "state.geometry",
            "state.environment.plates",
            "state.environment.geological_years_per_turn",
            "arrays.elevation",
            "arrays.plate_id",
            "arrays.volcanic_stress",
        ),
        writes=(
            "state.environment.plates",
            "arrays.elevation",
            "arrays.plate_id",
            "arrays.volcanic_stress",
        ),
    )

    def execute(self, context: TurnContext) -> StageResult:
        width, height = layout(context)
        graph = neighbor_graph(width, height)
        elapsed = years(context, "geological")
        elevation = checked_field(context, "elevation")
        old_ids = checked_codes(context, "plate_id")
        stress = checked_field(context, "volcanic_stress", low=0, high=1) * 0.8
        plates = _plates(context, width, height)
        if not set(old_ids.tolist()) <= {plate["id"] for plate in plates}:
            raise ValueError("plate_id must refer to a declared plate")
        for plate in plates:
            x = number(plate["x"], "x") + number(plate["vx"], "vx") * elapsed
            y = number(plate["y"], "y") + number(plate["vy"], "vy") * elapsed
            plate["x"] = number(x, "advanced plate.x") % width
            plate["y"] = float(np.clip(number(y, "advanced plate.y"), 0, height - 1))
        xs, ys = np.arange(width * height) % width, np.arange(width * height) // width
        distance = np.stack(
            [
                ((xs - number(p["x"], "x") + width / 2) % width - width / 2) ** 2
                + (ys - number(p["y"], "y")) ** 2
                for p in plates
            ]
        )
        owners = np.argmin(distance, axis=0)
        identifiers = np.array([p["id"] for p in plates], dtype=np.int64)[owners]
        velocities = np.array([[p["vx"], p["vy"]] for p in plates], dtype=np.float64)
        uplift = np.zeros_like(elevation)
        erosion = np.zeros_like(elevation)
        boundary = np.zeros(width * height, dtype=np.bool_)
        for source, neighbors in enumerate(graph):
            movement: list[float] = []
            for target in neighbors:
                if owners[source] != owners[target]:
                    boundary[source] = True
                    dx = _periodic_dx(float(xs[target] - xs[source]), width)
                    dy = float(ys[target] - ys[source])
                    relative = velocities[owners[target]] - velocities[owners[source]]
                    separation = (relative[0] * dx + relative[1] * dy) / math.hypot(dx, dy)
                    movement.append(float(np.clip(-separation * elapsed * 50, -100, 100)))
                if target > source:
                    transfer = (
                        0.002
                        * (elevation[target] - elevation[source])
                        / max(len(neighbors), len(graph[target]))
                    )
                    erosion[source] += transfer
                    erosion[target] -= transfer
            if movement:
                uplift[source] = math.fsum(movement) / len(movement)
                stress[source] = min(1.0, stress[source] + abs(uplift[source]) / 500)
        probability = min(0.05, 0.0005 * elapsed / 1000)
        volcanoes = []
        volcanic_uplift = np.zeros_like(elevation)
        for index in np.flatnonzero(boundary):
            tile = int(index)
            stream = context.seeds.stream(
                self.contract.name, self.contract.version, entity=f"tile:{tile}", purpose="volcano"
            )
            if stream.uniform(0) < probability:
                volcanic_uplift[tile] = 50 + 150 * stream.uniform(1)
                stress[tile] = 1
                volcanoes.append(
                    event(
                        context,
                        self.contract.name,
                        "Volcano",
                        ordinal=tile,
                        target=f"tile:{tile}",
                        location=(tile % width, tile // width),
                        payload={
                            "reason": "plate_boundary_volcanism",
                            "tile_id": tile,
                            "plate_id": int(identifiers[tile]),
                            "uplift_m": float(volcanic_uplift[tile]),
                        },
                    )
                )
        new_elevation = elevation + uplift + erosion + volcanic_uplift
        finite_outputs(new_elevation, stress)
        return result(
            context,
            self.contract.name,
            arrays={
                "elevation": new_elevation,
                "plate_id": identifiers,
                "volcanic_stress": stress,
            },
            state=(state_patch(context, ("environment", "plates"), cast(JsonValue, plates)),),
            events=tuple(volcanoes),
            metrics={
                "geological_years": elapsed,
                "boundary_tiles": int(np.sum(boundary)),
                "tectonic_elevation_delta_m": float(np.sum(uplift)),
                "erosion_elevation_delta_m": float(np.sum(erosion)),
                "volcanic_elevation_delta_m": float(np.sum(volcanic_uplift)),
                "volcano_probability": probability,
            },
        )


class BiomeStage(SimulationStage):
    contract = StageContract(
        "reference_biome",
        "1",
        dependencies=("reference_hydrology",),
        reads=(
            "state.geometry",
            "state.environment.sea_level",
            "arrays.elevation",
            "arrays.temperature",
            "arrays.humidity",
            "arrays.soil_quality",
            "arrays.surface_water",
            "arrays.plant_biomass",
            "arrays.biome",
        ),
        writes=("arrays.biome",),
    )

    def execute(self, context: TurnContext) -> StageResult:
        width, _ = layout(context)
        sea = number(domain(context, "environment")["sea_level"], "sea_level")
        elevation = checked_field(context, "elevation")
        temperature = checked_field(context, "temperature")
        humidity = checked_field(context, "humidity", low=0, high=1)
        quality = checked_field(context, "soil_quality", low=0, high=1)
        water = checked_field(context, "surface_water", low=0)
        biomass = checked_field(context, "plant_biomass", low=0)
        previous = checked_codes(context, "biome", 6)
        biome = np.full(previous.shape, 4, dtype=np.int64)
        reasons = ["temperate_grassland"] * len(previous)
        for index in range(len(previous)):
            if elevation[index] <= sea:
                code, reason = 0, "elevation_at_or_below_sea_level"
            elif water[index] >= 20:
                code, reason = 1, "surface_water_storage"
            elif elevation[index] - sea >= 2000:
                code, reason = 6, "high_elevation"
            elif temperature[index] < 0:
                code, reason = 2, "freezing_temperature"
            elif humidity[index] < 0.2 or quality[index] < 0.1:
                code, reason = 3, "arid_or_infertile_soil"
            elif humidity[index] >= 0.6 and quality[index] >= 0.3 and biomass[index] >= 20:
                code, reason = 5, "moist_fertile_vegetated_soil"
            else:
                code, reason = 4, "temperate_grassland"
            biome[index], reasons[index] = code, reason
        events = tuple(
            event(
                context,
                self.contract.name,
                "BiomeChanged",
                ordinal=int(index),
                target=f"tile:{index}",
                location=(int(index) % width, int(index) // width),
                payload={
                    "tile_id": int(index),
                    "from": int(previous[index]),
                    "to": int(biome[index]),
                    "reason": reasons[index],
                },
            )
            for index in np.flatnonzero(biome != previous)
        )
        return result(
            context,
            self.contract.name,
            arrays={"biome": biome},
            events=events,
            metrics={"changed_tiles": len(events)},
        )

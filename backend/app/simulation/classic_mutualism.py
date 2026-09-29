"""Conservative seed transport hook for classic ecology's CPU result snapshot."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Sequence

import numpy as np


def apply_seed_dispersal(
    population: np.ndarray,
    species_codes: Sequence[str],
    tiles: Sequence[Any],
    ecological_realism_data: dict[str, Any],
    sea_level: float = 0.0,
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Move at most 2% of local plants via actual co-occurring dispersers.

    Call once after classic mortality/reproduction and before tensor state sync.
    Pass the current MapState sea level after climate/tectonic updates.
    Input/output are [species, y, x]; the input is not mutated. The source debit
    equals the destination credit. No disperser presence, shared source, suitable
    adjacent destination, or whole individual available means no transport.
    Event metadata and the existing report payload are updated in place.
    """
    if ecological_realism_data.get("mutualism_seed_dispersal_applied"):
        return population, []
    ecological_realism_data["mutualism_seed_dispersal_applied"] = True
    links = [link for link in ecological_realism_data.get("mutualism_links", []) if link.get("relationship_type") == "seed_dispersal"]
    if not links or population.ndim != 3:
        return population, []
    indices = {code: index for index, code in enumerate(species_codes)}
    height, width = population.shape[1:]
    tile_map = {
        (int(tile.y), int(tile.x)): tile for tile in tiles
        if 0 <= int(tile.y) < height and 0 <= int(tile.x) < width
    }
    profiles = ecological_realism_data.get("mutualism_seed_plants", {})
    providers: dict[str, set[str]] = defaultdict(set)
    for link in links:
        visitor, plant = link.get("species_a"), link.get("species_b")
        if visitor in indices and plant in indices and plant in profiles:
            providers[plant].add(visitor)
    result = population.copy()
    events: list[dict[str, Any]] = []
    for plant, visitors in sorted(providers.items()):
        plant_index = indices[plant]
        profile = profiles[plant]
        for (y, x), source_tile in sorted(tile_map.items()):
            source_count = float(population[plant_index, y, x])
            if not np.isfinite(source_count) or source_count < 50.0:
                continue
            local_visitors = [indices[visitor] for visitor in visitors if population[indices[visitor], y, x] > 0.0]
            if not local_visitors:
                continue
            destinations = []
            for target_y, target_x in {(y - 1, x), (y + 1, x), (y, (x - 1) % width), (y, (x + 1) % width)}:
                target = tile_map.get((target_y, target_x))
                if target is None or (target_y, target_x) == (y, x):
                    continue
                habitat_type = profile.get("habitat_type", "terrestrial")
                biome = str(getattr(target, "biome", "")).lower()
                is_lake = bool(getattr(target, "is_lake", False)) or "湖" in biome or "lake" in biome
                submerged = target.elevation < sea_level
                if habitat_type in {"terrestrial", "aerial"} and (submerged or is_lake):
                    continue
                if habitat_type in {"marine", "deep_sea"} and (not submerged or is_lake):
                    continue
                if habitat_type == "freshwater" and not (getattr(target, "is_lake", False) or getattr(target, "has_river", False)):
                    continue
                low = 5.0 - 40.0 * profile["cold_tolerance"]
                high = 30.0 + 20.0 * profile["heat_tolerance"]
                if not low <= target.temperature <= high:
                    continue
                humidity = float(target.humidity)
                if humidity > 1.0:
                    humidity /= 100.0
                if humidity < 0.3 * (1.0 - profile["drought_tolerance"]):
                    continue
                carriers = sum(min(float(population[index, y, x]), float(population[index, target_y, target_x])) for index in local_visitors)
                if carriers > 0.0 and np.isfinite(carriers):
                    destinations.append((target_y, target_x, carriers, target))
            if not destinations:
                continue
            total_carriers = sum(item[2] for item in destinations)
            budget = min(source_count * 0.02, total_carriers * 2.0)
            # Integer moves avoid manufacturing a viable population from a
            # fractional remnant; distribute only the finite source budget.
            remaining = int(budget)
            for target_y, target_x, carriers, target in sorted(destinations, key=lambda item: (-item[2], item[0], item[1])):
                moved = min(remaining, int(budget * carriers / total_carriers))
                if moved <= 0:
                    continue
                result[plant_index, y, x] -= moved
                result[plant_index, target_y, target_x] += moved
                remaining -= moved
                events.append({
                    "species_code": plant,
                    "source_tile_id": source_tile.id,
                    "target_tile_id": target.id,
                    "population": moved,
                })
    ecological_realism_data["mutualism_seed_dispersal"] = {
        "moved_population": sum(event["population"] for event in events),
        "routes": len(events),
    }
    return result, events

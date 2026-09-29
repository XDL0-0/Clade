"""Linear-time water connectivity at the world's current sea level."""

from collections import deque
from typing import Any, Callable, Sequence


def classify_water_bodies(tiles: Sequence[Any], sea_level: float, height: int,
                          neighbors: Callable[[int, int], list[tuple[int, int]]]) -> None:
    by_coord = {(tile.x, tile.y): tile for tile in tiles}
    water = {coord for coord, tile in by_coord.items() if tile.elevation < sea_level}
    unseen = set(water)
    components = []
    while unseen:
        start = unseen.pop()
        component = {start}
        queue = deque([start])
        while queue:
            x, y = queue.popleft()
            for coord in neighbors(x, y):
                if coord in unseen:
                    unseen.remove(coord)
                    component.add(coord)
                    queue.append(coord)
        components.append(component)
    # The largest connected water body is the ocean even if continents isolate
    # it from both poles; polar-connected components are oceans as before.
    largest = max(components, key=lambda component: (len(component), min(component)), default=set())
    ocean = set(largest)
    for component in components:
        if any(y in (0, height - 1) for _, y in component):
            ocean.update(component)
    for tile in tiles:
        tile.relative_elevation = tile.elevation - sea_level
        coord = (tile.x, tile.y)
        if coord not in water:
            tile.is_lake = False
            tile.salinity = 0.0
            continue
        tile.has_river = False
        tile.is_lake = coord not in ocean
        if tile.is_lake:
            tile.biome = "湖泊"
            tile.salinity = min(35.0, 15.0 + (0.3 - tile.humidity) * 50.0) if tile.humidity < 0.3 else 0.5
            tile.cover = "冰湖" if tile.temperature < -5.0 else "水域"
        else:
            coastal = any(c in by_coord and c not in water for c in neighbors(tile.x, tile.y))
            tile.biome = ("海岸" if tile.relative_elevation >= -200 else "浅海") if coastal else (
                "深海" if tile.relative_elevation < -500 else "浅海")
            tile.salinity = 35.0
            tile.cover = "海冰" if tile.temperature < -10.0 else "水域"

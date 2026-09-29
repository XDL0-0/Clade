"""Raster transport of the existing plate model, including sub-cell motion.

Tiles remain Eulerian habitat cells; crust and elevation move between them.
Convergence consumes the denser/lower crust and divergence creates ocean crust.
This is a coarse grid model, not a mantle fluid solver.
"""

from __future__ import annotations

import math

import numpy as np

from .models import Plate, SimpleTile


class CrustTransport:
    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height
        self.residual = np.zeros((height, width, 2), dtype=float)
        self.destinations: dict[int, int] = {}

    def advance(self, plates: list[Plate], plate_map: np.ndarray,
                tiles: list[SimpleTile], time_scale: float = 1.0) -> None:
        by_id = {p.id: p for p in plates}
        by_coord = {(t.x, t.y): t for t in tiles}
        incoming: dict[tuple[int, int], tuple[SimpleTile, float, float]] = {}
        self.destinations = {}
        # Read every source before writing any destination.
        for tile in tiles:
            plate = by_id[tile.plate_id]
            dx = (tile.x - plate.rotation_center_x + self.width / 2) % self.width - self.width / 2
            dy = tile.y - plate.rotation_center_y
            angle = plate.angular_velocity * time_scale
            move_x = plate.velocity_x * time_scale + dx * (math.cos(angle) - 1) - dy * math.sin(angle)
            move_y = plate.velocity_y * time_scale + dx * math.sin(angle) + dy * (math.cos(angle) - 1)
            rx, ry = self.residual[tile.y, tile.x]
            rx, ry = rx + move_x, ry + move_y
            sx, sy = math.floor(rx + 0.5), math.floor(ry + 0.5)
            nx, ny = (tile.x + sx) % self.width, max(0, min(self.height - 1, tile.y + sy))
            # Do not accumulate attempted transport through the poles.
            ry = ry - sy if ny == tile.y + sy else 0.0
            existing = incoming.get((nx, ny))
            if existing is None or (tile.elevation, -plate.density, -tile.id) > (
                    existing[0].elevation, -by_id[existing[0].plate_id].density, -existing[0].id):
                incoming[(nx, ny)] = (tile, rx - sx, ry)
            self.destinations[tile.id] = by_coord[(nx, ny)].id

        # Snapshot only transported material properties. Climate belongs to the
        # destination latitude and receives the elevation lapse-rate correction.
        material = {coord: (src.elevation, src.plate_id, src.volcanic_potential, rx, ry)
                    for coord, (src, rx, ry) in incoming.items()}
        residual = np.zeros_like(self.residual)
        for tile in tiles:
            old_elevation = tile.elevation
            data = material.get((tile.x, tile.y))
            if data is None:
                neighbors = [material[((tile.x + dx) % self.width, tile.y + dy)]
                             for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1))
                             if ((tile.x + dx) % self.width, tile.y + dy) in material]
                if neighbors and len({row[1] for row in neighbors}) == 1:
                    # Raster rotation may leave holes inside a rigid plate;
                    # interpolate those instead of creating spurious oceans.
                    tile.elevation = sum(row[0] for row in neighbors) / len(neighbors)
                    tile.plate_id = neighbors[0][1]
                else:
                    # An opening between separating plates is new ocean crust.
                    tile.elevation = -1800.0
                    tile.volcanic_potential = max(tile.volcanic_potential, 0.25)
            else:
                tile.elevation, tile.plate_id, tile.volcanic_potential, rx, ry = data
                residual[tile.y, tile.x] = (rx, ry)
            tile.temperature -= (max(0.0, tile.elevation) - max(0.0, old_elevation)) * 0.006
            plate_map[tile.y, tile.x] = tile.plate_id
        self.residual = residual
        counts = np.bincount(plate_map.ravel(), minlength=len(plates))
        for plate in plates:
            plate.tile_count = int(counts[plate.id])

    def to_dict(self) -> dict:
        return {"residual": self.residual.tolist()}

    def restore(self, state: dict) -> None:
        data = np.asarray(state.get("residual", []), dtype=float)
        if data.shape == self.residual.shape and np.isfinite(data).all():
            self.residual = data

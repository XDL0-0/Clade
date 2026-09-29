"""Browser projections only; canonical storage remains SQLite and NPZ."""

from __future__ import annotations

from collections.abc import Mapping

from app.simulation.v2.context import WorldSnapshot
from app.simulation.v2.values import JsonValue
from app.storage.database import StorageCorruption

TILE_FIELDS = ("elevation", "temperature", "biome", "plant_biomass", "soil_water", "surface_water")
SPECIES_FIELDS = (
    "slot",
    "role",
    "body_mass",
    "habitat",
    "traits",
    "status",
    "thermal_optimum",
    "thermal_width",
    "water_need",
    "fertility",
    "lifespan",
    "trophic_level",
    "trait_budget",
    "ancestor",
    "descendants",
    "created_turn",
    "lifecycle_turn",
    "declining_turns",
    "last_population",
    "last_nonzero_population",
    "current_habitat_runs",
    "last_habitat",
    "extinction_turn",
    "extinction_cause",
)


def identity(snapshot: WorldSnapshot) -> dict[str, JsonValue]:
    return {
        "version": snapshot.version.to_dict(),
        "turn": snapshot.turn_id,
        "snapshot_id": snapshot.snapshot_id,
        "state_hash": snapshot.state_hash,
        "model": snapshot.manifest.get("model"),
    }


def species(snapshot: WorldSnapshot, species_id: str) -> dict[str, JsonValue]:
    metadata = snapshot.domain("species")[species_id]
    if not isinstance(metadata, Mapping):
        raise StorageCorruption("Invalid species metadata")
    slot = metadata.get("slot")
    population = snapshot.arrays["population"].numpy()
    if type(slot) is not int or not 0 <= slot < population.shape[0]:
        raise StorageCorruption("Invalid species slot")
    return {
        **{key: metadata.get(key) for key in SPECIES_FIELDS},
        "species_id": species_id,
        "population": int(population[slot].sum(dtype=object)),
    }


def snapshot_view(snapshot: WorldSnapshot) -> dict[str, JsonValue]:
    fields: dict[str, JsonValue] = {}
    names: tuple[str, ...] = TILE_FIELDS
    if "habitat_complexity" in snapshot.arrays:
        names += ("soil_quality", "humidity", "habitat_complexity")
    for name in names:
        array = snapshot.arrays[name].numpy()
        fields[name] = (
            tuple(int(v) for v in array) if name == "biome" else tuple(float(v) for v in array)
        )
    return {
        **identity(snapshot),
        "geometry": snapshot.domain("geometry"),
        "environment": snapshot.domain("environment"),
        "species": tuple(species(snapshot, key) for key in snapshot.domain("species")),
        "map": fields,
    }

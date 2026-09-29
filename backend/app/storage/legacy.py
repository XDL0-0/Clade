"""Read-only legacy 2.0 import; publication requires Linux renameat2 no-clobber.

source_hash identifies canonical JSON content, independent of gzip and whitespace.
The original payload is archived once; names/narratives never enter numeric state.
"""

from __future__ import annotations

import ctypes
import errno
import fcntl
import gzip
import json
import math
import os
import shutil
import tempfile
import zlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.simulation.v2.context import WorldSnapshot
from app.simulation.v2.values import (
    FrozenArray,
    JsonValue,
    canonical_bytes,
    digest,
    freeze_mapping,
)
from app.simulation.v2.version import WorldVersion

from .store import WorldStore

MAX_BYTES = 512 * 1024 * 1024
_INT64_MAX = 2**63 - 1
_SPECIES_FIELDS = frozenset(
    "id lineage_code status parent_code created_turn is_background trophic_level "
    "ecological_vector diet_type habitat_type genus_code taxonomic_rank hybrid_parent_codes "
    "hybrid_fertility gene_diversity_radius gene_stability explored_directions capabilities "
    "plasticity_buffer accumulated_adaptation_score last_description_update_turn "
    "dependency_strength symbiosis_type is_protected protection_turns is_suppressed "
    "suppression_turns life_form_stage growth_form achieved_milestones".split()
)
_TILE_FIELDS = frozenset(
    "id x y q r biome cover has_river salinity is_lake plate_id relative_elevation "
    "crust_thickness volcanic_potential earthquake_risk boundary_type distance_to_boundary "
    "pressures".split()
)
_MAP_FIELDS = frozenset(
    (
        "id turn_index stage_name stage_progress stage_duration sea_level global_avg_temperature"
    ).split()
)


@dataclass(frozen=True, slots=True)
class LegacyImport:
    snapshot: WorldSnapshot
    warnings: tuple[str, ...]
    source_hash: str


def _mapping(value: object, label: str) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return freeze_mapping(value)


def _sequence(value: JsonValue, label: str) -> tuple[JsonValue, ...]:
    if not isinstance(value, tuple):
        raise ValueError(f"{label} must be a list")
    return value


def _integer(value: JsonValue, label: str, *, signed: bool = False) -> int:
    if type(value) is not int or (not signed and value < 0):
        raise ValueError(f"{label} must be an integer" + ("" if signed else " >= 0"))
    return value


def _number(value: JsonValue, label: str, *, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError(f"{label} is outside the numeric range") from exc
    if not math.isfinite(number) or (nonnegative and number < 0):
        raise ValueError(f"{label} must be finite" + (" and nonnegative" if nonnegative else ""))
    return number


def _population(value: JsonValue) -> int:
    _number(value, "population", nonnegative=True)
    if not isinstance(value, (int, float)) or value != int(value) or value > _INT64_MAX:
        raise ValueError("population must be an integer within int64")
    return int(value)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def _format(payload: Mapping[str, JsonValue]) -> None:
    if payload.get("version") != "2.0" or "format" in payload or "schema_version" in payload:
        raise ValueError("Only legacy version='2.0' is supported; V2/future formats are rejected")


def read_legacy(source: Path, max_bytes: int = MAX_BYTES) -> Mapping[str, JsonValue]:
    """Detect gzip by magic and cap both stored and decompressed bytes before JSON parsing."""
    if type(max_bytes) is not int or max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    path = Path(source)
    if path.is_dir():
        candidates = (path / "game_state.json.gz", path / "game_state.json")
        path = next((candidate for candidate in candidates if candidate.is_file()), candidates[0])
    try:
        with path.open("rb") as stream:
            if os.fstat(stream.fileno()).st_size > max_bytes:
                raise ValueError("Legacy source exceeds max_bytes")
            compressed = stream.read(2) == b"\x1f\x8b"
            stream.seek(0)
            if compressed:
                with gzip.GzipFile(fileobj=stream) as reader:
                    data = reader.read(max_bytes + 1)
            else:
                data = stream.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError("Decompressed legacy source exceeds max_bytes")
        payload = _mapping(
            json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object), "legacy save"
        )
    except (OSError, EOFError, UnicodeError, RecursionError, zlib.error) as exc:
        raise ValueError(f"Cannot read legacy save: {exc}") from exc
    _format(payload)
    return payload


def _entities(payload: Mapping[str, JsonValue], name: str) -> dict[int, Mapping[str, JsonValue]]:
    result = {}
    for item in _sequence(payload.get(name), name):
        entity = _mapping(item, name)
        key = _integer(entity.get("id"), f"{name}.id")
        if key in result:
            raise ValueError(f"Duplicate {name} ID: {key}")
        result[key] = entity
    return dict(sorted(result.items()))


def _food_web(species: Mapping[int, Mapping[str, JsonValue]]) -> dict[str, object]:
    codes: dict[str, int] = {}
    for key, item in species.items():
        code = item.get("lineage_code")
        if not isinstance(code, str) or not code or code in codes:
            raise ValueError("Species lineage_code must be nonempty and unique")
        codes[code] = key
    links: dict[str, object] = {}
    for key, item in species.items():
        prey = _sequence(item.get("prey_species", ()), "prey_species")
        dependencies = _sequence(item.get("symbiotic_dependencies", ()), "symbiotic_dependencies")
        preferences = _mapping(item.get("prey_preferences", {}), "prey_preferences")
        for reference in (*prey, *dependencies, *preferences):
            if not isinstance(reference, str) or reference not in codes:
                raise ValueError(f"Dangling prey/dependency reference: {reference}")
        if len(prey) != len(set(prey)) or len(dependencies) != len(set(dependencies)):
            raise ValueError("Duplicate food web reference")
        if set(preferences) - set(prey):
            raise ValueError("prey_preferences reference species absent from prey_species")
        for value in preferences.values():
            _number(value, "prey preference", nonnegative=True)
        links[str(key)] = {
            "prey_species": prey,
            "prey_preferences": preferences,
            "symbiotic_dependencies": dependencies,
        }
    return links


def legacy_snapshot(
    payload: Mapping[str, JsonValue], world_id: str, timeline_id: str = "main"
) -> LegacyImport:
    """Validate all source data before allocating an isolated genesis snapshot."""
    payload = _mapping(payload, "legacy save")
    _format(payload)
    turn = _integer(payload.get("turn_index"), "turn_index")
    if turn > _INT64_MAX:
        raise ValueError("turn_index exceeds SQLite int64")
    species, tiles = _entities(payload, "species"), _entities(payload, "map_tiles")
    if len(species) * len(tiles) * 16 + len(tiles) * 32 > MAX_BYTES:
        raise ValueError("Legacy species-by-tile arrays exceed the allocation limit")
    warnings = [
        "Legacy RNG trajectory cannot be recovered; the supplied seed starts a new RNG stream.",
        "Legacy isolation durations are unavailable; initialize all durations to zero.",
        "Legacy resource caches are unavailable; initialize caches from imported tile resources.",
        "Past numerical history cannot be reconstructed; replay begins at the imported turn. "
        "Saved history and names remain in legacy-source.json.gz.",
    ]
    food_web = _food_web(species)
    if any("prey_species" not in item for item in species.values()):
        warnings.append("Missing food web links default to empty; no relationships were inferred.")
    coordinates: set[tuple[int, int]] = set()
    environment = {
        name: np.zeros(len(tiles), dtype=np.float64)
        for name in ("temperature", "humidity", "elevation", "resources")
    }
    for index, tile in enumerate(tiles.values()):
        coord = tuple(_integer(tile.get(axis), axis, signed=True) for axis in ("x", "y"))
        coordinate = (coord[0], coord[1])
        if coordinate in coordinates:
            raise ValueError(f"Duplicate tile coordinates: {coordinate}")
        coordinates.add(coordinate)
        for name, values in environment.items():
            value = tile.get(name)
            if name == "resources" and value is None:
                value = 0
                warnings.append(f"Tile {tile['id']} resources missing; initialized to zero.")
            values[index] = _number(value, name, nonnegative=name in {"resources", "humidity"})
    population = np.zeros((len(species), len(tiles)), dtype=np.int64)
    suitability = np.zeros(population.shape, dtype=np.float64)
    species_axis = {key: index for index, key in enumerate(species)}
    tile_axis = {key: index for index, key in enumerate(tiles)}
    occupied: set[tuple[int, int]] = set()
    totals: dict[int, int] = {}
    for item in _sequence(payload.get("habitats", ()), "habitats"):
        habitat = _mapping(item, "habitat")
        sid = _integer(habitat.get("species_id"), "habitat.species_id")
        tid = _integer(habitat.get("tile_id"), "habitat.tile_id")
        if sid not in species_axis or tid not in tile_axis:
            raise ValueError("Dangling habitat species/tile reference")
        if (sid, tid) in occupied:
            raise ValueError("Duplicate habitat species/tile pair")
        occupied.add((sid, tid))
        count = _population(habitat.get("population"))
        row, col = species_axis[sid], tile_axis[tid]
        population[row, col] = count
        suitability[row, col] = _number(
            habitat.get("suitability", 0), "suitability", nonnegative=True
        )
        totals[sid] = totals.get(sid, 0) + count
    metadata: dict[str, object] = {}
    expected_total = 0
    for sid, item in species.items():
        morph = _mapping(item.get("morphology_stats"), "morphology_stats")
        count = _population(morph.get("population"))
        expected_total += count
        if sid in totals and totals[sid] != count:
            raise ValueError(f"Species {sid} population total differs from habitats")
        if sid not in totals:
            if count and not tiles:
                raise ValueError("Nonzero species population requires at least one tile")
            if tiles:
                population[species_axis[sid], 0] = count
            warnings.append(
                f"Species {sid} has no habitats; assigned {count} individuals to "
                "the first tile by ID (if any), suitability zero. "
                "This is deterministic initialization, not a recovered distribution."
            )
        data: dict[str, JsonValue] = {
            key: value for key, value in item.items() if key in _SPECIES_FIELDS
        }
        data["morphology_stats"] = {
            key: value for key, value in morph.items() if key != "population"
        }
        for name in ("morphology_stats", "abstract_traits", "hidden_traits"):
            traits = _mapping(data[name] if name in data else item.get(name, {}), name)
            for value in traits.values():
                _number(value, name)
            data[name] = traits
        metadata[str(sid)] = data
    if sum(int(value) for value in population.flat) != expected_total:
        raise ValueError("Imported population does not conserve the legacy total")
    saved_map = payload.get("map_state")
    map_state = _mapping({} if saved_map is None else saved_map, "map_state")
    if "turn_index" in map_state and map_state["turn_index"] != turn:
        warnings.append(
            "map_state.turn_index differs; the saved top-level turn_index is authoritative."
        )
    global_map = {key: value for key, value in map_state.items() if key in _MAP_FIELDS}
    extra = _mapping(map_state.get("extra_data", {}), "map_state.extra_data")
    if "map_seed" in extra:
        global_map["extra_data"] = {"map_seed": extra["map_seed"]}
    source_hash = digest(payload)
    snapshot = WorldSnapshot(
        WorldVersion(world_id, timeline_id),
        turn,
        freeze_mapping(
            {
                "species": metadata,
                "food_web": food_web,
                "habitat": {"isolation_duration_default": 0},
                "environment": {
                    "map_state": global_map,
                    "tiles": {
                        str(key): {
                            name: value for name, value in tile.items() if name in _TILE_FIELDS
                        }
                        for key, tile in tiles.items()
                    },
                },
            }
        ),
        {
            name: FrozenArray.from_numpy(value)
            for name, value in {
                **environment,
                "population": population,
                "suitability": suitability,
            }.items()
        },
        freeze_mapping(
            {
                "model": "legacy-import-v1",
                "source_hash": source_hash,
                "species_axis": tuple(species),
                "tile_axis": tuple(tiles),
                "earliest_replayable_turn": turn,
                "migration_warnings": warnings,
                "legacy_archive": "legacy-source.json.gz",
            }
        ),
    )
    return LegacyImport(snapshot, tuple(warnings), source_hash)


def _publish(source: Path, destination: Path) -> None:
    """Fail closed when Linux atomic RENAME_NOREPLACE is unavailable."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    if rename is None:
        raise OSError(errno.ENOTSUP, "Legacy publication requires renameat2(RENAME_NOREPLACE)")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))


def import_legacy(
    source: Path, destination: Path, world_id: str, seed: int, timeline_id: str = "main"
) -> LegacyImport:
    """Validate, build and replay privately, then publish without touching live worlds."""
    payload = read_legacy(source)
    result = legacy_snapshot(payload, world_id, timeline_id)
    _integer(seed, "seed")
    if seed > _INT64_MAX:
        raise ValueError("World seed exceeds SQLite int64")
    destination = Path(destination).absolute()
    if os.path.lexists(destination):
        raise FileExistsError(destination)
    # Existing parent is deliberate: no changes outside the owned temporary directory.
    parent_fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temporary: Path | None = None
    try:
        fcntl.flock(parent_fd, fcntl.LOCK_EX)
        if os.path.lexists(destination):
            raise FileExistsError(destination)
        temporary = Path(
            tempfile.mkdtemp(prefix=f".{destination.name}-import-", dir=destination.parent)
        )
        store = WorldStore(temporary)
        store.create(result.snapshot, seed=seed)
        if store.head(world_id, timeline_id).snapshot_id != result.snapshot.snapshot_id:
            raise ValueError("Legacy genesis failed replay verification")
        archive = temporary / "legacy-source.json.gz"
        with archive.open("wb") as stream:
            with gzip.GzipFile(filename="", fileobj=stream, mode="wb", mtime=0) as compressed:
                compressed.write(canonical_bytes(payload))
            stream.flush()
            os.fsync(stream.fileno())
        temporary_fd = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(temporary_fd)
        finally:
            os.close(temporary_fd)
        _publish(temporary, destination)
        temporary = None
        os.fsync(parent_fd)
    finally:
        if temporary is not None:
            shutil.rmtree(temporary)
        os.close(parent_fd)
    return result

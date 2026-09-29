"""Explicit, reproducible genesis for the opt-in CPU reference ecosystem.

Reserved numerical rows permit later species creation without resizing arrays.
Only assigned rows may contain individuals or energy; slot identity lives in
state, never in the immutable engine manifest. This is a new model, not an
implicit conversion of a legacy save.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np

from ..context import WorldSnapshot
from ..seed import SeedManager
from ..values import FrozenArray, JsonValue, freeze_mapping, natural
from ..version import WorldVersion
from .common import number
from .topology import TOPOLOGY_VERSION, neighbor_graph

MODEL_ID = "ecology-reference-v1"
FEEDING_VERSION = "2"
TRAITS = ("armor", "speed", "attack", "cooperation", "toxin", "detox", "engineering")
MORTALITY_CAUSES = (
    "temperature",
    "starvation",
    "predation",
    "competition",
    "disease",
    "disaster",
    "old_age",
    "other",
)


@dataclass(frozen=True, slots=True)
class SpeciesSeed:
    species_id: str
    role: str
    body_mass: float
    population_per_tile: int
    habitat: str = "land"
    thermal_optimum: float = 18.0
    thermal_width: float = 18.0
    water_need: float = 0.3
    fertility: float = 2.0
    lifespan: float = 5.0
    traits: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.species_id, str) or not self.species_id.strip():
            raise ValueError("A nonempty species ID is required")
        if self.role not in ("producer", "herbivore", "carnivore", "decomposer"):
            raise ValueError("Unknown trophic role")
        if self.habitat not in ("land", "water", "amphibious"):
            raise ValueError("Unknown habitat")
        natural(self.population_per_tile, "population_per_tile")
        if self.population_per_tile > 1_000_000:
            raise ValueError("Genesis population exceeds the reference model budget")
        for name in ("body_mass", "thermal_width", "lifespan"):
            if not 0 < number(getattr(self, name), name) <= 1_000_000:
                raise ValueError(f"{name} must be positive and bounded")
        for name in ("water_need", "fertility"):
            if not 0 <= number(getattr(self, name), name) <= 10:
                raise ValueError(f"{name} must be nonnegative and bounded")
        number(self.thermal_optimum, "thermal_optimum")
        if set(self.traits) - set(TRAITS):
            raise ValueError("Unknown evolvable trait")
        traits: dict[str, JsonValue] = {
            name: number(self.traits.get(name, 0.1), name) for name in TRAITS
        }
        values = [float(value) for value in traits.values() if isinstance(value, (int, float))]
        if any(not 0 <= value <= 1 for value in values) or sum(values) > 3:
            raise ValueError("Traits must lie in [0,1] and share a total budget of 3")
        object.__setattr__(self, "traits", freeze_mapping(traits))

    def metadata(self, slot: int, population: int) -> dict[str, JsonValue]:
        return {
            "slot": slot,
            "role": self.role,
            "body_mass": self.body_mass,
            "habitat": self.habitat,
            "thermal_optimum": self.thermal_optimum,
            "thermal_width": self.thermal_width,
            "water_need": self.water_need,
            "fertility": self.fertility,
            "lifespan": self.lifespan,
            "traits": self.traits,
            "trait_budget": 3.0,
            "trophic_level": {
                "producer": 1.0,
                "herbivore": 2.0,
                "carnivore": 3.0,
                "decomposer": 1.0,
            }[self.role],
            "status": "Healthy" if population else "Extinct",
            "ancestor": None,
            "descendants": (),
            "created_turn": 0,
            "last_population": population,
            "declining_turns": 0,
            "extinction_cause": None,
        }


DEFAULT_SPECIES = (
    SpeciesSeed("land-plant", "producer", 1.0, 40, lifespan=10),
    SpeciesSeed("grazer", "herbivore", 2.0, 8),
    SpeciesSeed("hunter", "carnivore", 10.0, 1, fertility=1, lifespan=10),
    SpeciesSeed("recycler", "decomposer", 0.1, 20, habitat="amphibious"),
    SpeciesSeed("algae", "producer", 0.5, 80, habitat="water"),
    SpeciesSeed("plankton", "herbivore", 0.5, 20, habitat="water"),
    SpeciesSeed("fish", "carnivore", 5.0, 2, habitat="water", fertility=1),
)


def create_reference_snapshot(
    version: WorldVersion,
    *,
    seed: int,
    manifest: Mapping[str, JsonValue],
    width: int = 16,
    height: int = 8,
    max_species: int = 32,
    species: Sequence[SpeciesSeed] = DEFAULT_SPECIES,
) -> WorldSnapshot:
    """Construct genesis; a caller must explicitly supply its engine manifest."""
    natural(seed, "seed")
    natural(max_species, "max_species")
    if version.generation or version.revision:
        raise ValueError("Genesis requires generation/revision zero")
    if manifest.get("model") != MODEL_ID:
        raise ValueError("Reference genesis requires its explicit model manifest")
    if type(width) is not int or type(height) is not int:
        raise ValueError("Geometry dimensions must be integers")
    if not 1 <= max_species <= 256 or not 1 <= height <= 256 or not 2 <= width <= 256:
        raise ValueError("Geometry or species capacity exceeds the reference model budget")
    if max_species * width * height > 500_000:
        raise ValueError("Reference species-by-tile budget exceeded")
    neighbor_graph(width, height)
    seeds = sorted(species, key=lambda item: item.species_id)
    if len(seeds) > max_species or len({item.species_id for item in seeds}) != len(seeds):
        raise ValueError("Species IDs must be unique and fit the reserved capacity")
    tiles = width * height
    stage_versions = manifest.get("stages", {})
    has_lifecycle = isinstance(stage_versions, Mapping) and "reference_extinction" in stage_versions
    has_selection = isinstance(stage_versions, Mapping) and "reference_selection" in stage_versions
    has_genetics = isinstance(stage_versions, Mapping) and "reference_mutation" in stage_versions
    rng = SeedManager(seed, "reference-genesis", 0)
    terrain = rng.stream("genesis", "1", purpose="terrain")
    phase = terrain.uniform(0) * 2 * math.pi
    elevation = np.array(
        [
            400 * math.sin(2 * math.pi * (i % width) / width + phase)
            + 180 * math.cos(math.pi * (i // width + 0.5) / height)
            + 80 * (terrain.uniform(i + 1) - 0.5)
            for i in range(tiles)
        ],
        dtype=np.float64,
    )
    land = elevation > 0
    population = np.zeros((max_species, tiles), dtype=np.int64)
    reserve = np.zeros((max_species, tiles), dtype=np.float64)
    metadata: dict[str, JsonValue] = {}
    initial_abundance: dict[str, JsonValue] = {}
    for slot, item in enumerate(seeds):
        occupied = land if item.habitat == "land" else ~land
        if item.habitat == "amphibious":
            occupied = np.ones(tiles, dtype=np.bool_)
        population[slot, occupied] = item.population_per_tile
        reserve[slot] = population[slot] * item.body_mass * 0.1
        phenotype = item.metadata(slot, int(population[slot].sum()))
        if has_lifecycle:
            runs: list[tuple[int, int]] = []
            for tile in np.flatnonzero(population[slot]):
                index = int(tile)
                if runs and runs[-1][1] + 1 == index:
                    runs[-1] = (runs[-1][0], index)
                else:
                    runs.append((index, index))
            phenotype.update(
                current_habitat_runs=tuple(runs),
                lifecycle_turn=0,
                last_nonzero_population=int(population[slot].sum()),
            )
        metadata[item.species_id] = phenotype
        initial_abundance[item.species_id] = int(population[slot].sum())
    plates: list[JsonValue] = []
    for index in range(min(4, tiles)):
        stream = rng.stream("genesis", "1", entity=str(index), purpose="plate")
        plates.append(
            {
                "id": index,
                "x": stream.uniform(0) * width,
                "y": stream.uniform(1) * (height - 1),
                "vx": (stream.uniform(2) - 0.5) * 0.00002,
                "vy": (stream.uniform(3) - 0.5) * 0.00002,
            }
        )
    edges: list[JsonValue] = []
    for predator in seeds:
        for prey in seeds:
            prey_role = {"herbivore": "producer", "carnivore": "herbivore"}.get(predator.role)
            if prey.role == prey_role and (
                predator.habitat == prey.habitat or "amphibious" in (predator.habitat, prey.habitat)
            ):
                edges.append(
                    {"predator": predator.species_id, "prey": prey.species_id, "preference": 1.0}
                )
    state: dict[str, JsonValue] = {
        "geometry": {"width": width, "height": height, "topology": TOPOLOGY_VERSION},
        "environment": {
            "baseline_temperature": 15.0,
            "global_temperature": 15.0,
            "co2_ppm": 280.0,
            "sea_level": 0.0,
            "warming_offset": 0.0,
            "ecological_years_per_turn": 1 / 12,
            "geological_years_per_turn": 1000.0,
            "plates": tuple(plates),
        },
        "species": metadata,
        "food_web": {"edges": tuple(edges)},
        "gene": {},
        "habitat": {},
        "observability": {"abundance": initial_abundance, "turn": 0},
        "evolution": {"isolation": {}},
        "simulation": {
            "max_species": max_species,
            "trait_names": TRAITS,
            "mortality_causes": MORTALITY_CAUSES,
        },
    }
    arrays = {
        "elevation": FrozenArray.from_numpy(elevation),
        "population": FrozenArray.from_numpy(population),
        "energy_reserve": FrozenArray.from_numpy(reserve),
        "biome": FrozenArray.from_numpy(np.where(land, 4, 0).astype(np.int64)),
        "plate_id": FrozenArray.from_numpy(np.zeros(tiles, dtype=np.int64)),
    }
    for name, initial in {
        "temperature": 15.0,
        "soil_water": 100.0,
        "surface_water": 0.0,
        "soil_quality": 0.5,
        "humidity": 0.5,
        "plant_biomass": 200.0,
        "rainfall": 0.0,
        "river_flux": 0.0,
        "volcanic_stress": 0.0,
        "nutrients": 20.0,
        "detritus": 20.0,
        "npp": 0.0,
    }.items():
        arrays[name] = FrozenArray.from_numpy(np.full(tiles, initial, dtype=np.float64))
    for name in (
        "suitability",
        "carrying_capacity",
        "competition",
        "temperature_pressure",
        "water_pressure",
        "food_pressure",
        "predation_pressure",
    ):
        arrays[name] = FrozenArray.from_numpy(np.zeros(population.shape, dtype=np.float64))
    for name in ("predation_deaths", "migration_in", "migration_out", "births", "deaths"):
        arrays[name] = FrozenArray.from_numpy(np.zeros(population.shape, dtype=np.int64))
    arrays["connectivity"] = FrozenArray.from_numpy(np.full(population.shape, -1, dtype=np.int64))
    arrays["mortality"] = FrozenArray.from_numpy(
        np.zeros((len(MORTALITY_CAUSES), *population.shape), dtype=np.int64)
    )
    if has_selection:
        for name in ("selection_pressure", "fitness_gradients"):
            arrays[name] = FrozenArray.from_numpy(np.zeros((max_species, 7), dtype=np.float64))
    if has_genetics:
        deme = np.zeros((max_species, tiles, len(TRAITS)), dtype=np.float64)
        for slot, item in enumerate(seeds):
            deme[slot] = [number(item.traits[name], name) for name in TRAITS]
        for name in ("deme_traits", "trait_proposals"):
            arrays[name] = FrozenArray.from_numpy(deme)
        arrays["gene_population"] = FrozenArray.from_numpy(population)
        arrays["gene_connectivity"] = FrozenArray.from_numpy(
            np.full(population.shape, -1, dtype=np.int64)
        )
        arrays["isolation_age"] = FrozenArray.from_numpy(np.zeros(population.shape, dtype=np.int64))
    return WorldSnapshot(version, 0, state, arrays, manifest)

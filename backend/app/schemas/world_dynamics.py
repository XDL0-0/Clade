"""Classic turn feedback, optional on historical saves."""
from pydantic import BaseModel, Field


class ClimateProgress(BaseModel):
    phase: str
    temperature: float
    temperature_delta: float
    sea_level: float
    sea_level_delta: float
    co2_ppm: float | None = None
    ice_fraction: float | None = None
    summary: str = ""


class GeologicalProgress(BaseModel):
    phase: str
    plate_count: int
    changed_tiles: int
    uplift_tiles: int
    subsidence_tiles: int
    max_uplift_m: float
    eruptions: int
    earthquakes: int
    isolated_species: int
    contacts: int


class MutualismConnection(BaseModel):
    species_a: str
    species_b: str
    relationship_type: str
    strength: float
    description: str = ""


class WorldDynamics(BaseModel):
    climate: ClimateProgress | None = None
    geology: GeologicalProgress | None = None
    mutualism_links: list[MutualismConnection] = Field(default_factory=list)
    mutualism_link_count: int = 0
    seeds_dispersed: int = 0
    dependent_species_at_risk: list[str] = Field(default_factory=list)
    population_rule: str = ""

export interface WorldDynamics {
  climate?: {
    phase: string;
    temperature: number;
    temperature_delta: number;
    sea_level: number;
    sea_level_delta: number;
    co2_ppm?: number | null;
    ice_fraction?: number | null;
    summary: string;
  } | null;
  geology?: {
    phase: string;
    plate_count: number;
    changed_tiles: number;
    uplift_tiles: number;
    subsidence_tiles: number;
    max_uplift_m: number;
    eruptions: number;
    earthquakes: number;
    isolated_species: number;
    contacts: number;
  } | null;
  mutualism_links: {
    species_a: string;
    species_b: string;
    relationship_type: string;
    strength: number;
    description: string;
  }[];
  mutualism_link_count: number;
  seeds_dispersed: number;
  dependent_species_at_risk: string[];
  population_rule: string;
}

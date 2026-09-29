export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export type Values = Record<string, Json>;
export interface Scope {
  world: string;
  timeline: string;
}
export interface Version {
  world_id: string;
  timeline_id: string;
  generation: number;
  revision: number;
}
export interface Identity {
  version: Version;
  turn: number;
  snapshot_id: string;
  state_hash: string;
  model: string;
}
export interface Species {
  species_id: string;
  population: number;
  slot: number;
  status: string;
  role: string;
  habitat: string;
  body_mass: number;
  traits: Record<string, number>;
  ancestor: string | null;
  descendants: string[] | null;
  created_turn: number | null;
  declining_turns: number | null;
  extinction_turn: number | null;
  extinction_cause: Json;
  last_nonzero_population: number | null;
  trait_budget: number;
}
export interface Snapshot extends Identity {
  geometry: { width: number; height: number; topology: string };
  environment: Values;
  species: Species[];
  map: Record<string, number[]>;
}
export interface Timeline {
  world_id: string;
  timeline_id: string;
  version: Version;
  turn: number;
  state_hash: string;
  model: string;
}
export interface Page<T> {
  items: T[];
  next_offset: number | null;
}
export interface WorldEvent {
  event_id: string;
  version: Version;
  turn: number;
  type: string;
  actor: string | null;
  payload: Values;
  cause: string[];
}
export interface SpeciesDetail extends Identity {
  species: Species;
  distribution: number[];
  fossil: Values;
  evolution_traces: WorldEvent[];
}
export interface StageProfile {
  stage_name: string;
  stage_version: string;
  duration_ms: number;
  metrics: Values;
  input_hash: string;
  output_hash: string;
  random_seed: string;
  warnings: string[];
  errors: string[];
  event_ids: string[];
}
export interface Profile extends Identity {
  stages: StageProfile[];
}
export interface MetricPoint {
  revision: number;
  turn: number;
  metrics: Record<string, Values>;
}
export interface MetricPage {
  items: MetricPoint[];
  generation: number;
  next_after_revision: number;
}
export interface Message {
  cursor: number;
  message_id: string;
  kind: string;
  payload: Values;
}
export interface EventPage {
  items: Message[];
  next_cursor: number;
}
export interface CreateWorld {
  world_id: string;
  timeline_id: string;
  seed: number;
  width: number;
  height: number;
  max_species: number;
}
export interface Command {
  label: string;
  scope: Scope;
  path: string;
  body: Values;
}
export const scopeOf = (version: Version): Scope => ({
  world: version.world_id,
  timeline: version.timeline_id,
});

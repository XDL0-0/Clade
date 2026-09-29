import type { Values, Version } from "./types";
export interface Forcing {
  turn: number;
  warming_offset?: number | null;
  co2_ppm?: number | null;
  disaster_severity?: number | null;
  disease_pressure?: number | null;
}
export interface ScenarioBranch {
  id: string;
  name: string;
  scenario: { version: 1; id: string; name: string; description?: string; forcing: Forcing[] };
}
export interface ScenarioPlan {
  version: 1;
  id: string;
  name: string;
  description?: string;
  source: Version;
  turns: number;
  rng_namespace: string;
  control: string;
  branches: ScenarioBranch[];
}
export interface FrozenRun {
  format: "clade.lab.pending.v1";
  raw: string;
  workers: number;
}
export interface ScenarioObservation {
  relative_turn: number;
  turn: number;
  version: Version;
  state_hash: string;
  metrics: Values;
}
export interface ScenarioBranchProgress {
  branch_id: string;
  name?: string;
  timeline_id: string;
  status: "pending" | "partial" | "completed" | "failed" | "conflict";
  completed_turns?: number;
  target_turns: number;
  version?: Version | null;
  observed_head_version?: Version | null;
  turn?: number | null;
  state_hash?: string | null;
  observations?: ScenarioObservation[];
  summary?: Values;
  error?: string | Values | null;
}
export interface ScenarioProgress {
  manifest_hash: string;
  manifest: {
    format: string;
    plan: ScenarioPlan;
    source_snapshot_id: string;
    source_state_hash: string;
    branch_timelines: Record<string, string>;
  };
  branches: ScenarioBranchProgress[];
  completed?: boolean;
  comparisons?: Values;
  interpretation?: string;
}
export interface ScenarioListItem {
  id: string;
  name: string;
  source: Version;
  manifest_hash: string;
  turns: number;
  branch_count: number;
}

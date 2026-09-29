import type { Version } from "./types";

export interface NarrativeChild {
  child_id: string;
  common_name: string;
  latin_name: string;
  description: string;
}
interface NarrativeSources {
  source_event_ids: string[];
}
export type NarrativeResult = NarrativeSources &
  (
    | {
        job_type: "species";
        species_id: string;
        common_name: string;
        latin_name: string;
        description: string;
        organ_descriptions: { organ_id: string; description: string }[];
      }
    | {
        job_type: "adaptation";
        species_id: string;
        proposal_id: string;
        summary: string;
        tradeoff_explanation: string;
      }
    | {
        job_type: "speciation";
        proposal_id: string;
        children: NarrativeChild[];
        explanation: string;
      }
    | {
        job_type: "hybridization";
        proposal_id: string;
        child: NarrativeChild;
        parent_narrative: string;
      }
  );
export interface NarrativeAnnotation {
  job_id: string;
  version: Version;
  turn: number;
  narrative_revision: number;
  fallback_used?: boolean;
  source?: "offline_template" | "fallback_template" | "unspecified_provider";
  result: NarrativeResult;
}
export interface NarrativeGroup {
  version: Version;
  turn: number;
  annotations: NarrativeAnnotation[];
}
export interface NarrativePage {
  version: Version;
  turn: number;
  species_id: string | null;
  page_unit: "annotated_commit";
  items: NarrativeGroup[];
  scanned: number;
  next_offset: number | null;
  truncated: boolean;
}
export interface Diagnostics {
  version: Version;
  turn: number;
  snapshot_id: string;
  observed_at: string;
  scope: { rss_bytes: string; save_bytes: string; simulation: string; ai_jobs: string };
  rss_bytes: number | null;
  gpu_memory_bytes: number | null;
  gpu_note: string;
  save_bytes: number;
  save_bytes_approximate: boolean;
  save_scan_skipped: number;
  species_count: number;
  active_species_count: number;
  population_habitat_records: number;
  species_capacity: number;
  ai_jobs: Record<string, number>;
  ai_token_usage: number | null;
  ai_token_usage_note: string;
}

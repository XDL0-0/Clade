import type { Diagnostics, NarrativeAnnotation, NarrativePage } from "../presentationTypes";
import type { Snapshot } from "../types";

export function annotation(value: Snapshot, id = "job-1"): NarrativeAnnotation {
  return {
    job_id: id,
    version: value.version,
    turn: value.turn,
    narrative_revision: 1,
    source: "offline_template",
    fallback_used: false,
    result: {
      job_type: "species",
      species_id: "grazer",
      common_name: `物种描述 ${id}`,
      latin_name: "Clade test",
      description: `结构化描述 ${id}`,
      organ_descriptions: [{ organ_id: "leg", description: "记录中的附肢说明" }],
      source_event_ids: ["event-1"],
    },
  };
}
export function narrativePage(
  value: Snapshot,
  items: NarrativePage["items"] = [],
  species: string | null = "grazer"
): NarrativePage {
  return {
    version: value.version,
    turn: value.turn,
    species_id: species,
    page_unit: "annotated_commit",
    items,
    scanned: 1,
    next_offset: null,
    truncated: false,
  };
}
export function diagnostics(value: Snapshot): Diagnostics {
  return {
    version: value.version,
    turn: value.turn,
    snapshot_id: value.snapshot_id,
    observed_at: "2026-09-29T12:00:00+00:00",
    scope: {
      rss_bytes: "current_process",
      save_bytes: "entire_store_directory",
      simulation: "selected_snapshot",
      ai_jobs: "selected_snapshot_full_version",
    },
    rss_bytes: 128 * 1024 ** 2,
    gpu_memory_bytes: null,
    gpu_note: "GPU is not measured.",
    save_bytes: 256 * 1024 ** 2,
    save_bytes_approximate: true,
    save_scan_skipped: 2,
    species_count: 7,
    active_species_count: 6,
    population_habitat_records: 50,
    species_capacity: 32,
    ai_jobs: { QUEUED: 1, RUNNING: 0, APPLIED: 2 },
    ai_token_usage: null,
    ai_token_usage_note: "The provider did not report token usage.",
  };
}

import type { ScenarioPlan, ScenarioProgress } from "../scenarioTypes";
import { snapshot } from "./fixtures";
export function scenarioPlan(): ScenarioPlan {
  return {
    version: 1,
    id: "trial",
    name: "情景测试",
    source: snapshot(3, "ancestor").version,
    turns: 2,
    rng_namespace: "paired-trial",
    control: "control",
    branches: [
      {
        id: "control",
        name: "对照",
        scenario: { version: 1, id: "baseline", name: "基线", forcing: [] },
      },
      {
        id: "warm",
        name: "升温",
        scenario: {
          version: 1,
          id: "warm-four",
          name: "升温",
          forcing: [{ turn: 1, warming_offset: 4 }],
        },
      },
    ],
  };
}
export function scenarioProgress(plan = scenarioPlan()): ScenarioProgress {
  return {
    manifest_hash: "manifest-test",
    completed: true,
    manifest: {
      format: "clade.experiment.v1",
      plan,
      source_snapshot_id: "source-id",
      source_state_hash: "source-hash",
      branch_timelines: Object.fromEntries(
        plan.branches.map((branch) => [branch.id, `exp-${branch.id}`])
      ),
    },
    branches: plan.branches.map((branch, index) => {
      const version = {
        ...plan.source,
        timeline_id: `exp-${branch.id}`,
        generation: 0,
        revision: plan.turns,
      };
      return {
        branch_id: branch.id,
        timeline_id: version.timeline_id,
        status: "completed",
        completed_turns: plan.turns,
        target_turns: plan.turns,
        version,
        turn: 3 + plan.turns,
        state_hash: `hash-${branch.id}`,
        observations: Array.from({ length: plan.turns }, (_, offset) => ({
          relative_turn: offset + 1,
          turn: 4 + offset,
          version: { ...version, revision: offset + 1 },
          state_hash: `obs-${offset}`,
          metrics: {
            species_richness: 7 - index,
            total_population: 100 - index * 10 + offset,
            total_biomass: 200 - index * 20 + offset,
            shannon_diversity: 1.3 - index * 0.1,
            mean_trophic_level: 2.1 + index * 0.2,
          },
        })),
        summary: {
          population_by_role: { herbivore: { population: 30, structural_biomass: 90 } },
          population_weighted_traits: { armor: 0.2 + index * 0.1 },
          trait_distribution: {
            basis: "population_weighted_deme_means",
            bin_edges: Array.from({ length: 11 }, (_, i) => i / 10),
            population: 100,
            weighted_counts: {
              armor: Object.fromEntries(
                Array.from({ length: 10 }, (_, i) => [String(i), i === index + 2 ? 100 : 0])
              ),
            },
          },
        },
      };
    }),
    comparisons: {},
  };
}

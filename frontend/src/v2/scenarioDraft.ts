import { forcingFields, parseScenario } from "./scenarioModel";
import type { ScenarioPlan, ScenarioBranch } from "./scenarioTypes";
export type PointDraft = { turn: string } & Record<(typeof forcingFields)[number]["key"], string>;
export type BranchDraft = Omit<ScenarioBranch, "scenario"> & {
  scenario: Omit<ScenarioBranch["scenario"], "forcing"> & { forcing: PointDraft[] };
};
export type PlanDraft = Omit<ScenarioPlan, "turns" | "branches"> & {
  turns: string;
  branches: BranchDraft[];
};
export function toDraft(plan: ScenarioPlan): PlanDraft {
  return {
    ...plan,
    turns: String(plan.turns),
    branches: plan.branches.map((branch) => ({
      ...branch,
      scenario: {
        ...branch.scenario,
        forcing: (branch.scenario.forcing ?? []).map(
          (point) =>
            ({
              turn: String(point.turn),
              ...Object.fromEntries(
                forcingFields.map(({ key }) => [key, point[key] == null ? "" : String(point[key])])
              ),
            }) as PointDraft
        ),
      },
    })),
  };
}
function finite(text: string) {
  const value = Number(text);
  if (!text.trim() || !Number.isFinite(value)) throw new Error("数字不能为空或非有限值。");
  return value;
}
export function draftJSON(draft: PlanDraft) {
  const plan = {
    ...draft,
    turns: finite(draft.turns),
    branches: draft.branches.map((branch) => ({
      ...branch,
      scenario: {
        ...branch.scenario,
        forcing: branch.scenario.forcing.map((point) => ({
          turn: finite(point.turn),
          ...Object.fromEntries(
            forcingFields
              .filter(({ key }) => point[key] !== "")
              .map(({ key }) => [key, finite(point[key])])
          ),
        })),
      },
    })),
  };
  const raw = JSON.stringify(plan, null, 2);
  parseScenario(raw);
  return raw;
}

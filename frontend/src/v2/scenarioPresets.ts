import { makeScenario } from "./scenarioModel";
import type { Snapshot } from "./types";

export const scenarioPresets = [
  { id: "warming", name: "暖化世界", symbol: "☀", description: "在此刻的基础上升温 4°C" },
  { id: "ice", name: "冰河世界", symbol: "❄", description: "在此刻的基础上降温 4°C" },
  { id: "disaster", name: "灾变世界", symbol: "ϟ", description: "第一回合遭遇一场灾害" },
] as const;
export type ScenarioPreset = (typeof scenarioPresets)[number]["id"];

export function makePreset(snapshot: Snapshot, preset: ScenarioPreset, turns: number) {
  const plan = makeScenario(snapshot);
  const choice = scenarioPresets.find((item) => item.id === preset)!;
  const offset = snapshot.environment.warming_offset;
  const change = preset === "warming" ? 4 : -4;
  if (
    preset !== "disaster" &&
    (typeof offset !== "number" ||
      !Number.isFinite(offset) ||
      offset + change < -100 ||
      offset + change > 100)
  )
    throw new Error("这个世界的温度已到预设边界，可以在高级设置中调整。");
  plan.name = `${choice.name} · 起点第 ${snapshot.turn} 回合`;
  plan.turns = turns;
  plan.description = choice.description;
  plan.branches[0].name = "原环境";
  plan.branches[1] = {
    id: preset,
    name: choice.name,
    scenario: {
      version: 1,
      id: preset,
      name: choice.name,
      forcing:
        preset === "disaster"
          ? [{ turn: 1, disaster_severity: 0.65 }]
          : [{ turn: 1, warming_offset: (offset as number) + change }],
    },
  };
  return plan;
}

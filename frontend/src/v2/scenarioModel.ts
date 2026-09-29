import type { Snapshot } from "./types";
import type { FrozenRun, ScenarioPlan } from "./scenarioTypes";

export const PENDING_SCENARIO = "clade:v2:pending-scenario:v1";
export const forcingFields = [
  { key: "warming_offset", label: "升温绝对偏移 °C", min: -100, max: 100 },
  { key: "co2_ppm", label: "CO₂ ppm", min: 10, max: 5000 },
  { key: "disaster_severity", label: "灾害压力", min: 0, max: 1 },
  { key: "disease_pressure", label: "疾病压力", min: 0, max: 1 },
] as const;
function object(value: unknown, allowed: string[], label: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error(`${label} 必须是对象。`);
  const result = value as Record<string, unknown>;
  const extra = Object.keys(result).filter((key) => !allowed.includes(key));
  if (extra.length) throw new Error(`${label} 含未知字段：${extra.join("、")}；未丢弃任何字段。`);
  return result;
}
function text(value: unknown, label: string, max = 120, id = false) {
  if (
    typeof value !== "string" ||
    !value.trim() ||
    value.length > max ||
    (id && !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/.test(value))
  )
    throw new Error(`${label} 不是合法的${id ? "标识" : "文本"}。`);
}
function number(value: unknown, label: string, min: number, max: number, integer = false) {
  if (
    typeof value !== "number" ||
    !Number.isFinite(value) ||
    value < min ||
    value > max ||
    (integer && !Number.isSafeInteger(value))
  )
    throw new Error(`${label} 必须是 ${min}–${max} 的${integer ? "安全整数" : "有限数值"}。`);
}
function description(value: unknown) {
  if (value !== undefined && (typeof value !== "string" || value.length > 2000))
    throw new Error("描述应为不超过2000字的字符串。");
}
// JSON.parse validates syntax; this token walk additionally rejects repeated keys,
// including differently escaped spellings, before a parsed object can discard them.
function uniqueKeys(raw: string) {
  const stack: ({ keys: Set<string>; next: boolean } | null)[] = [];
  const tokens =
    raw.match(
      /"(?:\\[\s\S]|[^"\\])*"|[{},:]|\[|\]|true|false|null|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/g
    ) ?? [];
  for (const token of tokens) {
    if (token === "{") stack.push({ keys: new Set(), next: true });
    else if (token === "[") stack.push(null);
    else if (token === "}" || token === "]") stack.pop();
    else {
      const top = stack.at(-1);
      if (token === "," && top) top.next = true;
      else if (token.startsWith('"') && top?.next) {
        const key = JSON.parse(token) as string;
        if (top.keys.has(key)) throw new Error(`重复 JSON key：${key}；请修正原文。`);
        top.keys.add(key);
        top.next = false;
      }
    }
  }
}
export function parseScenario(raw: string): ScenarioPlan {
  if (new TextEncoder().encode(raw).length > 4_000_000) throw new Error("计划超过4 MB。");
  const parsed: unknown = JSON.parse(raw);
  uniqueKeys(raw);
  const plan = object(
    parsed,
    [
      "version",
      "id",
      "name",
      "description",
      "source",
      "turns",
      "rng_namespace",
      "control",
      "branches",
    ],
    "计划"
  );
  if (plan.version !== 1) throw new Error("计划 version 必须为1。");
  text(plan.id, "实验 ID", 64, true);
  text(plan.name, "实验名称");
  description(plan.description);
  text(plan.rng_namespace, "RNG namespace", 128);
  text(plan.control, "control", 64, true);
  number(plan.turns, "实验回合数", 1, 1000, true);
  const source = object(
    plan.source,
    ["world_id", "timeline_id", "generation", "revision"],
    "source"
  );
  text(source.world_id, "source.world_id", 64, true);
  text(source.timeline_id, "source.timeline_id", 64, true);
  number(source.generation, "generation", 0, Number.MAX_SAFE_INTEGER, true);
  number(source.revision, "revision", 0, Number.MAX_SAFE_INTEGER, true);
  if (!Array.isArray(plan.branches) || plan.branches.length < 2 || plan.branches.length > 8)
    throw new Error("实验须有2–8个分支。");
  const ids = new Set<string>();
  let control = false;
  for (const value of plan.branches) {
    const branch = object(value, ["id", "name", "scenario"], "分支");
    text(branch.id, "分支 ID", 64, true);
    text(branch.name, "分支名称");
    if (ids.has(branch.id as string)) throw new Error("分支 ID 不能重复。");
    ids.add(branch.id as string);
    const scenario = object(
      branch.scenario,
      ["version", "id", "name", "description", "forcing"],
      "情景"
    );
    if (scenario.version !== 1) throw new Error("scenario.version 必须为1。");
    text(scenario.id, "情景 ID", 64, true);
    text(scenario.name, "情景名称");
    description(scenario.description);
    const points = scenario.forcing === undefined ? [] : scenario.forcing;
    if (!Array.isArray(points) || points.length > 1000)
      throw new Error("forcing 应为最多1000条的数组。");
    if (branch.id === plan.control) {
      if (points.length) throw new Error("control 必须保留空 forcing。");
      control = true;
    }
    const turns = new Set<number>();
    for (const value of points) {
      const point = object(value, ["turn", ...forcingFields.map((item) => item.key)], "forcing");
      number(point.turn, "相对回合", 1, plan.turns as number, true);
      if (turns.has(point.turn as number)) throw new Error("同一分支不能重复相对回合。");
      turns.add(point.turn as number);
      let populated = false;
      for (const field of forcingFields)
        if (point[field.key] !== undefined && point[field.key] !== null) {
          number(point[field.key], field.label, field.min, field.max);
          populated = true;
        }
      if (!populated) throw new Error("forcing 至少设置一个参数。");
    }
  }
  if (!control) throw new Error("缺少指定的 control 分支。");
  return parsed as ScenarioPlan;
}
export function makeScenario(snapshot: Snapshot, warming = false): ScenarioPlan {
  const id = `experiment-${crypto.randomUUID()}`;
  const offset = snapshot.environment.warming_offset;
  if (warming && (typeof offset !== "number" || !Number.isFinite(offset) || offset + 4 > 100))
    throw new Error("此源快照不能使用 +4°C 模板。");
  return {
    version: 1,
    id,
    name: warming ? "暖化世界" : "我的平行世界",
    description: "",
    source: { ...snapshot.version },
    turns: 10,
    rng_namespace: `paired-${id}`,
    control: "control",
    branches: [
      {
        id: "control",
        name: "原环境",
        scenario: { version: 1, id: "baseline", name: "原始环境", forcing: [] },
      },
      {
        id: "experiment",
        name: "另一种命运",
        scenario: {
          version: 1,
          id: "changed",
          name: "实验情景",
          forcing: warming ? [{ turn: 1, warming_offset: (offset as number) + 4 }] : [],
        },
      },
    ],
  };
}
export function readPendingScenario(): { command: FrozenRun | null; error: string | null } {
  try {
    const raw = sessionStorage.getItem(PENDING_SCENARIO);
    if (!raw) return { command: null, error: null };
    const value = object(JSON.parse(raw), ["format", "raw", "workers"], "恢复记录");
    if (value.format !== "clade.lab.pending.v1" || typeof value.raw !== "string")
      throw new Error("恢复记录格式不正确。");
    parseScenario(value.raw);
    number(value.workers, "workers", 1, 4, true);
    return { command: value as unknown as FrozenRun, error: null };
  } catch (error) {
    return { command: null, error: `无法恢复会话计划：${(error as Error).message}` };
  }
}
export function restoredScenarioScope() {
  const { command } = readPendingScenario();
  if (!command) return { world: "", timeline: "" };
  const source = parseScenario(command.raw).source;
  return { world: source.world_id, timeline: source.timeline_id };
}
export function persistScenario(command: FrozenRun) {
  try {
    sessionStorage.setItem(PENDING_SCENARIO, JSON.stringify(command));
  } catch {
    throw new Error(
      "sessionStorage 不可用，无法保证刷新恢复；本次尚未提交。请允许会话存储后重试。"
    );
  }
}
export function exportJSON(value: unknown, filename: string, raw = false) {
  const url = URL.createObjectURL(
    new Blob([raw ? String(value) : JSON.stringify(value, null, 2)], { type: "application/json" })
  );
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

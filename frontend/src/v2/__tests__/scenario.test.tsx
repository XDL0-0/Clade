import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { ScenarioPanel } from "../ScenarioPanel";
import { ScenarioResults } from "../ScenarioResults";
import {
  makeScenario,
  parseScenario,
  persistScenario,
  readPendingScenario,
  restoredScenarioScope,
  PENDING_SCENARIO,
} from "../scenarioModel";
import { runScenario, scenarioKeys, useScenarioStatus } from "../scenarioQueries";
import { invalidateLive } from "../queries";
import { scenarioPlan, scenarioProgress } from "./scenarioFixtures";
import { response, snapshot } from "./fixtures";
import type { ScenarioProgress } from "../scenarioTypes";

const clients: QueryClient[] = [];
function setup() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  clients.push(client);
  return {
    client,
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  };
}
let saved: ScenarioProgress | null;
let bodies: string[];
beforeEach(() => {
  saved = null;
  bodies = [];
  sessionStorage.clear();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        bodies.push(String(init.body));
        saved = scenarioProgress(parseScenario(String(init.body)));
        return response(saved);
      }
      const url = new URL(path, "http://localhost");
      if (url.pathname.endsWith("/experiments"))
        return response({
          items: saved
            ? [
                {
                  id: saved.manifest.plan.id,
                  name: saved.manifest.plan.name,
                  source: saved.manifest.plan.source,
                  turns: 2,
                  branch_count: 2,
                  manifest_hash: saved.manifest_hash,
                },
              ]
            : [],
          next_offset: null,
        });
      return saved ? response(saved) : response({ detail: "Experiment not found" }, 404);
    })
  );
});
afterEach(() => {
  cleanup();
  clients.forEach((client) => client.clear());
  clients.length = 0;
  vi.unstubAllGlobals();
});

describe("strict scenario plans and read-only queries", () => {
  it("uses a complete historical ancestor source and an absolute offset for the +4 template", () => {
    const source = snapshot(7, "ancestor");
    source.version.generation = 2;
    source.environment.warming_offset = 2;
    const plan = makeScenario(source, true);
    expect(plan.source).toEqual(source.version);
    expect(plan.branches[1].scenario.forcing[0].warming_offset).toBe(6);
    expect(plan.branches[0].scenario.forcing).toEqual([]);
    expect(parseScenario(JSON.stringify(plan))).toEqual(plan);
  });
  it.each([
    ["unknown fields", (raw: string) => raw.replace('"version":1', '"unknown":7,"version":1')],
    ["duplicate keys", (raw: string) => raw.replace('"version":1', '"version":1,"version":1')],
    [
      "escaped duplicate keys",
      (raw: string) => raw.replace('"version":1', '"version":1,"ver\\u0073ion":1'),
    ],
    ["NaN", (raw: string) => raw.replace('"warming_offset":4', '"warming_offset":NaN')],
    ["overflow", (raw: string) => raw.replace('"warming_offset":4', '"warming_offset":1e400')],
    [
      "unsafe source integer",
      (raw: string) => raw.replace('"revision":3', '"revision":9007199254740993'),
    ],
  ])("rejects %s without silently removing input", (_label, mutate) => {
    expect(() => parseScenario(mutate(JSON.stringify(scenarioPlan())))).toThrow();
  });
  it("rejects nonempty controls, duplicate turns and forcing after the horizon", () => {
    const plan = scenarioPlan();
    plan.branches[0].scenario.forcing = [{ turn: 1, disease_pressure: 0.1 }];
    expect(() => parseScenario(JSON.stringify(plan))).toThrow(/control/);
    plan.branches[0].scenario.forcing = [];
    plan.branches[1].scenario.forcing.push({ turn: 1, disease_pressure: 0.1 });
    expect(() => parseScenario(JSON.stringify(plan))).toThrow(/重复相对回合/);
    plan.branches[1].scenario.forcing = [{ turn: 3, disaster_severity: 0.2 }];
    expect(() => parseScenario(JSON.stringify(plan))).toThrow(/相对回合/);
  });
  it("preserves original JSON bytes for final backend validation", async () => {
    const raw = JSON.stringify(scenarioPlan(), null, 4) + "\n";
    await runScenario({ format: "clade.lab.pending.v1", raw, workers: 3 });
    expect(bodies[0]).toBe(raw);
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("workers=3"),
      expect.objectContaining({ method: "POST", body: raw })
    );
  });
  it("only reads durable status and separates caches by world; SSE invalidation stays in-world", async () => {
    const { client, wrapper } = setup();
    saved = scenarioProgress();
    const hook = renderHook(() => useScenarioStatus("world-37", "trial"), { wrapper });
    await waitFor(() => expect(hook.result.current.isSuccess).toBe(true));
    await act(async () => {
      await hook.result.current.refetch();
    });
    expect(bodies).toHaveLength(0);
    const other = scenarioKeys.status("other", "trial");
    client.setQueryData(other, saved);
    invalidateLive(client, { world: "world-37", timeline: "exp-warm" });
    expect(client.getQueryState(other)?.isInvalidated).toBe(false);
    expect(scenarioKeys.list("world-37")).not.toEqual(scenarioKeys.list("other"));
  });
});

describe("editable and recoverable scenario UI", () => {
  it("edits forcing and freezes the selected historical source with an empty control", async () => {
    const { wrapper } = setup();
    const user = userEvent.setup();
    const source = snapshot(5, "ancestor");
    render(
      <ScenarioPanel snapshot={source} busy={false} onBlockingChange={vi.fn()} onOpen={vi.fn()} />,
      { wrapper }
    );
    fireEvent.change(screen.getByLabelText("相对回合数"), { target: { value: "2" } });
    await act(async () => {
      await user.click(screen.getByRole("button", { name: "添加 experiment forcing" }));
    });
    fireEvent.change(screen.getByLabelText("experiment #1 灾害压力"), { target: { value: "0.3" } });
    fireEvent.change(screen.getByLabelText("并发 workers"), { target: { value: "1" } });
    await act(async () => {
      await user.click(screen.getByRole("button", { name: "冻结并执行计划" }));
    });
    await waitFor(() => expect(bodies).toHaveLength(1));
    const plan = JSON.parse(bodies[0]);
    expect(plan.source).toEqual(source.version);
    expect(plan.branches[0].scenario.forcing).toEqual([]);
    expect(plan.branches[1].scenario.forcing).toEqual([{ turn: 1, disaster_severity: 0.3 }]);
    expect(await screen.findByText(/全部完成/)).toBeInTheDocument();
  });
  it("imports JSON without POST, preserves its pinned source and sends the original text", async () => {
    const { wrapper } = setup();
    const user = userEvent.setup();
    const raw = JSON.stringify(scenarioPlan(), null, 4);
    render(
      <ScenarioPanel
        snapshot={snapshot(9)}
        busy={false}
        onBlockingChange={vi.fn()}
        onOpen={vi.fn()}
      />,
      { wrapper }
    );
    fireEvent.change(screen.getByLabelText("完整 ExperimentPlan JSON"), { target: { value: raw } });
    await act(async () => {
      await user.click(screen.getByRole("button", { name: "导入 JSON 草稿" }));
    });
    expect(bodies).toHaveLength(0);
    expect(screen.getByText(/当前计划源/)).toHaveTextContent("ancestor / g0 / r3");
    await act(async () => {
      await user.click(screen.getByRole("button", { name: "冻结并执行计划" }));
    });
    await waitFor(() => expect(bodies).toEqual([raw]));
  });
  it("persists an uncertain request, restores after refresh, and retries exactly the same plan", async () => {
    const first = setup();
    const user = userEvent.setup();
    const blocking = vi.fn();
    const normal = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (url, init) => {
      if (init?.method === "POST" && bodies.length === 0) {
        bodies.push(String(init.body));
        throw new TypeError("response lost");
      }
      return normal(url, init);
    });
    const panel = render(
      <ScenarioPanel
        snapshot={snapshot(4, "ancestor")}
        busy={false}
        onBlockingChange={blocking}
        onOpen={vi.fn()}
      />,
      { wrapper: first.wrapper }
    );
    fireEvent.change(screen.getByLabelText("相对回合数"), { target: { value: "2" } });
    await act(async () => {
      await user.click(screen.getByRole("button", { name: "冻结并执行计划" }));
    });
    await screen.findByText(/上次结果尚未确认/);
    expect(readPendingScenario().command?.raw).toBe(bodies[0]);
    expect(restoredScenarioScope()).toEqual({ world: "world-37", timeline: "ancestor" });
    expect(blocking).toHaveBeenLastCalledWith(true);
    panel.unmount();
    const second = setup();
    render(
      <ScenarioPanel
        snapshot={snapshot(9, "ancestor")}
        busy={false}
        onBlockingChange={blocking}
        onOpen={vi.fn()}
      />,
      { wrapper: second.wrapper }
    );
    expect(bodies).toHaveLength(1);
    expect(screen.getByLabelText("实验 ID")).toBeDisabled();
    expect(screen.getByText(/当前计划源/)).toHaveTextContent("r4");
    await act(async () => {
      await user.click(screen.getByRole("button", { name: "校验/恢复原计划并获取完整结果" }));
    });
    await waitFor(() => expect(bodies).toHaveLength(2));
    expect(bodies[1]).toBe(bodies[0]);
    await waitFor(() => expect(readPendingScenario().command).toBeNull());
    expect(blocking).toHaveBeenLastCalledWith(false);
  });
  it("shows a 409 without retrying, changing IDs or overwriting a branch", async () => {
    const { wrapper } = setup();
    const user = userEvent.setup();
    const normal = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (url, init) =>
      init?.method === "POST"
        ? (bodies.push(String(init.body)), response({ detail: "Existing plan differs" }, 409))
        : normal(url, init)
    );
    render(
      <ScenarioPanel
        snapshot={snapshot()}
        busy={false}
        onBlockingChange={vi.fn()}
        onOpen={vi.fn()}
      />,
      { wrapper }
    );
    const id = (screen.getByLabelText("实验 ID") as HTMLInputElement).value;
    await act(async () => {
      await user.click(screen.getByRole("button", { name: "冻结并执行计划" }));
    });
    await screen.findByText(/未覆盖已有分支/);
    expect(bodies).toHaveLength(1);
    expect(screen.getByLabelText("实验 ID")).toHaveValue(id);
    expect(readPendingScenario().command).toBeNull();
  });
  it("lists and reads saved experiments without starting a run", async () => {
    const { wrapper } = setup();
    const user = userEvent.setup();
    saved = scenarioProgress();
    render(
      <ScenarioPanel
        snapshot={snapshot()}
        busy={false}
        onBlockingChange={vi.fn()}
        onOpen={vi.fn()}
      />,
      { wrapper }
    );
    await screen.findByRole("option", { name: /情景测试/ });
    await act(async () => {
      await user.selectOptions(screen.getByLabelText("读取实验状态"), "trial");
    });
    await screen.findByRole("region", { name: "情景实验结果" });
    await act(async () => {
      await user.click(screen.getByRole("button", { name: "读取已提交进度（只读）" }));
    });
    expect(bodies).toHaveLength(0);
  });
  it("blocks POST if a pending plan cannot be persisted", async () => {
    const { wrapper } = setup();
    const user = userEvent.setup();
    const fail = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("full");
    });
    render(
      <ScenarioPanel
        snapshot={snapshot()}
        busy={false}
        onBlockingChange={vi.fn()}
        onOpen={vi.fn()}
      />,
      { wrapper }
    );
    await act(async () => {
      await user.click(screen.getByRole("button", { name: "冻结并执行计划" }));
    });
    expect(await screen.findByText(/本次尚未提交/)).toBeInTheDocument();
    expect(bodies).toHaveLength(0);
    fail.mockRestore();
  });
  it("retains partial errors without presenting them as a complete comparison", () => {
    const progress = scenarioProgress();
    progress.completed = false;
    progress.branches[1].status = "partial";
    progress.branches[1].completed_turns = 1;
    progress.branches[1].error = "bounded branch failure";
    render(
      <ScenarioResults progress={progress} result={progress} disabled={false} onOpen={vi.fn()} />
    );
    expect(screen.getByText(/不是完整实验结论/)).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("bounded branch failure");
    expect(screen.getAllByText("未完成或缺观测")).toHaveLength(7);
    expect(screen.queryByText("末回合性状分布")).not.toBeInTheDocument();
  });
  it("compares matching relative turns and labels actual deme-mean histograms", () => {
    const progress = scenarioProgress();
    const open = vi.fn();
    render(
      <ScenarioResults progress={progress} result={progress} disabled={false} onOpen={open} />
    );
    expect(screen.getByText(/deme 均值按人口加权/)).toBeInTheDocument();
    expect(screen.getByText(/不代表逐个体基因型/)).toBeInTheDocument();
    const table = screen.getByRole("table", { name: /同一相对回合 2/ });
    expect(within(table).getByText("-10")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("比较相对回合"), { target: { value: "1" } });
    expect(screen.getByRole("table", { name: /同一相对回合 1/ })).toBeInTheDocument();
    expect(screen.queryByText("末回合性状分布")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看 warm 分支与历史" }));
    expect(open).toHaveBeenCalledWith({ world: "world-37", timeline: "exp-warm" });
  });
  it("corrupt recovery is reported and never submitted automatically", () => {
    sessionStorage.setItem(PENDING_SCENARIO, '{"format":"broken"}');
    expect(readPendingScenario().error).toContain("无法恢复");
    expect(restoredScenarioScope()).toEqual({ world: "", timeline: "" });
    expect(bodies).toHaveLength(0);
    const raw = JSON.stringify(scenarioPlan());
    persistScenario({ format: "clade.lab.pending.v1", raw, workers: 2 });
    expect(readPendingScenario().command?.raw).toBe(raw);
  });
});

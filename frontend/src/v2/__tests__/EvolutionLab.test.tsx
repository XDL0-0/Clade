import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import EvolutionLab from "../EvolutionLab";
import { FakeEventSource, detail, response, snapshot, timeline } from "./fixtures";
import type { Snapshot } from "../types";
import { diagnostics, narrativePage } from "./presentationFixtures";
import { persistScenario } from "../scenarioModel";
import { scenarioPlan, scenarioProgress } from "./scenarioFixtures";

let head: Snapshot | null;
let uncertain = false;
let commands: Record<string, unknown>[];
let seen: Map<string, Snapshot>;
beforeEach(() => {
  head = null;
  uncertain = false;
  commands = [];
  seen = new Map();
  FakeEventSource.instances = [];
  sessionStorage.clear();
  vi.stubGlobal("EventSource", FakeEventSource);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init?: RequestInit) => {
      const url = new URL(path, "http://localhost");
      if (init?.method === "POST") {
        const body = JSON.parse(String(init.body));
        commands.push(body);
        if (url.pathname === "/api/v2/worlds") {
          head = snapshot();
          return response(head, 201);
        }
        if (url.pathname.endsWith("/turns")) {
          if (!seen.has(body.idempotency_key)) {
            head = snapshot((head?.turn ?? 0) + 1);
            seen.set(body.idempotency_key, head);
            if (uncertain) {
              uncertain = false;
              throw new TypeError("response lost");
            }
          }
          return response(seen.get(body.idempotency_key));
        }
      }
      if (url.pathname === "/api/v2/worlds" || url.pathname.endsWith("/timelines"))
        return response({ items: head ? [timeline(head)] : [], next_offset: null });
      const turn = url.searchParams.has("turn")
        ? Number(url.searchParams.get("turn"))
        : (head?.turn ?? 0);
      const value = snapshot(turn);
      if (url.pathname.endsWith("/snapshot")) return response(value);
      if (url.pathname.endsWith("/diagnostics")) return response(diagnostics(value));
      if (url.pathname.endsWith("/narratives"))
        return response(narrativePage(value, [], url.searchParams.get("species_id")));
      if (url.pathname.includes("/species/")) return response(detail(value));
      if (url.pathname.endsWith("/profile"))
        return response({
          ...value,
          stages: turn
            ? [
                {
                  stage_name: "reference_metrics",
                  stage_version: "1",
                  duration_ms: 0.1,
                  metrics: { total_population: 81, species_richness: 1 },
                  warnings: [],
                  errors: [],
                  event_ids: [],
                  random_seed: "11",
                  input_hash: "in",
                  output_hash: "out",
                },
              ]
            : [],
        });
      if (url.pathname.endsWith("/metrics"))
        return response({
          items: head?.turn
            ? [{ turn: 1, revision: 1, metrics: { reference_metrics: { total_population: 81 } } }]
            : [],
          next_after_revision: head?.turn ?? -1,
          generation: 0,
        });
      if (url.pathname.endsWith("/events")) return response({ items: [], next_cursor: 0 });
      if (url.pathname.endsWith("/experiments")) return response({ items: [], next_offset: null });
      if (url.pathname.includes("/experiments/"))
        return response({ detail: "Experiment not found" }, 404);
      throw new Error(`Unexpected request ${path}`);
    })
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("creates an explicit seed world, advances once and browses history with GET only", async () => {
  const user = userEvent.setup();
  render(<EvolutionLab />);
  await screen.findByText("从一个明确的世界开始。");
  await act(async () => {
    await user.click(screen.getByRole("button", { name: "创建世界" }));
  });
  const advance = await screen.findByRole("button", { name: "推进一回合" });
  expect(commands[0]).toEqual({
    world_id: "world-37",
    timeline_id: "control",
    seed: 37,
    width: 8,
    height: 4,
    max_species: 32,
  });
  await waitFor(() => expect(advance).toBeEnabled());
  await act(async () => {
    await user.click(advance);
  });
  await waitFor(() => expect(head?.turn).toBe(1));
  await waitFor(() => expect(screen.getByRole("button", { name: "推进一回合" })).toBeEnabled());
  expect(commands[1].expected_version).toEqual(snapshot().version);
  expect(commands[1].idempotency_key).toEqual(expect.any(String));
  const postsBefore = commands.length;
  fireEvent.change(screen.getByLabelText(/历史快照/), { target: { value: "0" } });
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("snapshot?turn=0"),
      expect.objectContaining({ method: "GET" })
    )
  );
  expect(screen.getByRole("button", { name: "推进一回合" })).toBeDisabled();
  expect(commands).toHaveLength(postsBefore);
  expect(screen.getByText(/正在只读浏览历史/)).toBeInTheDocument();
  expect(await screen.findByRole("button", { name: "地块 0，草原，人口 20" })).toBeInTheDocument();
  await act(async () => {
    await user.click(screen.getByRole("button", { name: "返回最新" }));
  });
  expect(await screen.findByRole("button", { name: "推进一回合" })).toBeEnabled();
});

it("an uncertain commit blocks new commands and retries the original command without a second turn", async () => {
  const user = userEvent.setup();
  render(<EvolutionLab />);
  await act(async () => {
    await user.click(await screen.findByRole("button", { name: "创建世界" }));
  });
  await waitFor(() => expect(screen.getByRole("button", { name: "推进一回合" })).toBeEnabled());
  uncertain = true;
  await act(async () => {
    await user.click(screen.getByRole("button", { name: "推进一回合" }));
  });
  const retry = await screen.findByRole("button", { name: "重试同一请求" });
  expect(screen.getByRole("button", { name: "推进一回合" })).toBeDisabled();
  expect(screen.getByRole("combobox", { name: "世界" })).toBeDisabled();
  await act(async () => {
    await user.click(retry);
  });
  await waitFor(() => expect(screen.getByRole("button", { name: "推进一回合" })).toBeEnabled());
  expect(commands[1]).toEqual(commands[2]);
  expect(head?.turn).toBe(1);
  expect(screen.queryByRole("button", { name: "重试同一请求" })).not.toBeInTheDocument();
});

it("refresh restores the frozen experiment source and locks world, history and other commands until confirmed", async () => {
  const user = userEvent.setup();
  head = snapshot(4);
  const plan = { ...scenarioPlan(), source: head.version };
  const raw = JSON.stringify(plan);
  persistScenario({ format: "clade.lab.pending.v1", raw, workers: 2 });
  const normal = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation(async (url, init) =>
    String(url).includes("/experiments/run") ? response(scenarioProgress(plan)) : normal(url, init)
  );
  render(<EvolutionLab />);
  await screen.findByRole("button", { name: "校验/恢复原计划并获取完整结果" });
  expect(screen.getByRole("combobox", { name: "世界" })).toBeDisabled();
  expect(screen.getByLabelText(/历史快照/)).toBeDisabled();
  expect(screen.getByRole("button", { name: "推进一回合" })).toBeDisabled();
  await act(async () => {
    await user.click(screen.getByRole("button", { name: "校验/恢复原计划并获取完整结果" }));
  });
  await waitFor(() => expect(screen.getByRole("combobox", { name: "世界" })).toBeEnabled());
  expect(screen.getByLabelText(/历史快照/)).toBeEnabled();
  expect(fetch).toHaveBeenCalledWith(
    expect.stringContaining("/experiments/run?workers=2"),
    expect.objectContaining({ body: raw })
  );
});

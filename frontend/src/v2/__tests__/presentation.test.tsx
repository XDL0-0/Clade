import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { keys } from "../queries";
import { useNarratives, useDiagnostics, type PresentationView } from "../presentationQueries";
import { NarrativePanel } from "../NarrativePanel";
import { DiagnosticsPanel } from "../DiagnosticsPanel";
import { TraitMorphology } from "../TraitMorphology";
import { connectStream } from "../stream";
import { FakeEventSource, response, snapshot } from "./fixtures";
import { annotation, diagnostics, narrativePage } from "./presentationFixtures";
import type { NarrativeGroup } from "../presentationTypes";
import type { WorldEvent } from "../types";

const scope = { world: "world-37", timeline: "control" };
const view = (): PresentationView => ({ scope, snapshot: snapshot(3), turn: 3, generation: 0 });
function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return {
    client,
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  };
}
function group(id = "job-1", turn = 3): NarrativeGroup {
  const value = snapshot(turn);
  return { version: value.version, turn, annotations: [annotation(value, id)] };
}
beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
  vi.stubGlobal("EventSource", FakeEventSource);
  FakeEventSource.instances = [];
  sessionStorage.clear();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("read-only presentation query boundaries", () => {
  it("separates world, timeline, generation, selected turn, species and selected snapshot", () => {
    const values = [
      keys.narratives(scope, 0, 3, "grazer", "s1"),
      keys.narratives({ ...scope, world: "other" }, 0, 3, "grazer", "s1"),
      keys.narratives({ ...scope, timeline: "fork" }, 0, 3, "grazer", "s1"),
      keys.narratives(scope, 1, 3, "grazer", "s1"),
      keys.narratives(scope, 0, 2, "grazer", "s1"),
      keys.narratives(scope, 0, null, "grazer", "s1"),
      keys.narratives(scope, 0, 3, "other", "s1"),
      keys.narratives(scope, 0, 3, null, "s1"),
      keys.narratives(scope, 0, 3, "all", "s1"),
      keys.narratives(scope, 0, 3, "grazer", "s2"),
    ];
    expect(new Set(values.map((value) => JSON.stringify(value))).size).toBe(values.length);
    expect(keys.diagnostics(scope, 0, 3, "s1")).not.toEqual(keys.diagnostics(scope, 1, 3, "s1"));
  });
  it("uses GET and the semantic next_offset even after an empty scan, retaining all commit annotations", async () => {
    const { client, wrapper } = setup();
    const selected = view();
    const complete = group();
    complete.annotations.push({ ...annotation(selected.snapshot, "job-2"), narrative_revision: 2 });
    vi.mocked(fetch)
      .mockResolvedValueOnce(
        response({
          ...narrativePage(selected.snapshot),
          scanned: 500,
          next_offset: 500,
          truncated: true,
        })
      )
      .mockResolvedValueOnce(response(narrativePage(selected.snapshot, [complete])));
    const hook = renderHook(() => useNarratives(selected, "grazer"), { wrapper });
    await waitFor(() => expect(hook.result.current.hasNextPage).toBe(true));
    await act(async () => {
      await hook.result.current.fetchNextPage();
    });
    await waitFor(() =>
      expect(
        hook.result.current.data?.pages
          .flatMap((page) => page.items)
          .flatMap((entry) => entry.annotations)
      ).toHaveLength(2)
    );
    const urls = vi
      .mocked(fetch)
      .mock.calls.map(([path]) => new URL(String(path), "http://localhost"));
    expect(urls[0].searchParams.get("turn")).toBe("3");
    expect(urls[0].searchParams.get("species_id")).toBe("grazer");
    expect(urls[1].searchParams.get("offset")).toBe("500");
    expect(
      vi
        .mocked(fetch)
        .mock.calls.every(([, init]) => init?.method === "GET" && init.body === undefined)
    ).toBe(true);
    client.clear();
  });
  it("rejects a mismatched future response rather than mixing it into the selected history", async () => {
    const { client, wrapper } = setup();
    vi.mocked(fetch).mockResolvedValue(
      response(narrativePage(snapshot(9), [group("discarded-future", 9)]))
    );
    const hook = renderHook(() => useNarratives(view(), "grazer"), { wrapper });
    await waitFor(() => expect(hook.result.current.isError).toBe(true));
    expect(hook.result.current.data).toBeUndefined();
    expect(hook.result.current.error?.message).toContain("所选快照不一致");
    client.clear();
  });
  it("rewind switches cache identity and drops abandoned future annotations", async () => {
    const { client, wrapper } = setup();
    const current = { ...view(), turn: null };
    const restored = snapshot(1);
    restored.version = { ...restored.version, generation: 1, revision: 0 };
    restored.snapshot_id = "rewound-g1";
    vi.mocked(fetch)
      .mockResolvedValueOnce(response(narrativePage(current.snapshot, [group("discarded-future")])))
      .mockResolvedValueOnce(response(narrativePage(restored, [group("ancestor", 1)])));
    const hook = renderHook(({ selection }) => useNarratives(selection, "grazer"), {
      wrapper,
      initialProps: { selection: current },
    });
    await waitFor(() => expect(hook.result.current.isSuccess).toBe(true));
    hook.rerender({ selection: { ...current, snapshot: restored, generation: 1 } });
    expect(hook.result.current.data).toBeUndefined();
    await waitFor(() => expect(hook.result.current.isSuccess).toBe(true));
    expect(JSON.stringify(hook.result.current.data)).not.toContain("discarded-future");
    expect(JSON.stringify(hook.result.current.data)).toContain("ancestor");
    client.clear();
  });
  it("NarrativeReady invalidates related annotations and diagnostics without invalidating numerical views", () => {
    const { client } = setup();
    const narrativeKeys = [
      keys.narratives(scope, 0, null, "grazer", "s1"),
      keys.narratives(scope, 0, 1, null, "s2"),
      keys.narratives({ ...scope, timeline: "fork" }, 0, 1, null, "s2"),
    ];
    const numerical = [
      keys.snapshot(scope),
      keys.snapshot(scope, 1),
      keys.detail(scope, "grazer", null, 0),
      keys.metrics(scope, 0),
      keys.profile(scope, 1, 0),
    ];
    const unrelated = keys.narratives({ ...scope, world: "other" }, 0, null, null, "s1");
    const diagnostic = keys.diagnostics(scope, 0, 1, "s2");
    [...narrativeKeys, ...numerical, unrelated, diagnostic].forEach((key) =>
      client.setQueryData(key, {})
    );
    const close = connectStream(client, scope, vi.fn());
    FakeEventSource.instances[0].emit("NarrativeReady");
    [...narrativeKeys, diagnostic].forEach((key) =>
      expect(client.getQueryState(key)?.isInvalidated).toBe(true)
    );
    [...numerical, unrelated].forEach((key) =>
      expect(client.getQueryState(key)?.isInvalidated).toBe(false)
    );
    close();
    client.clear();
  });
  it("diagnostics request the selected historical turn and reject a different snapshot", async () => {
    const { client, wrapper } = setup();
    vi.mocked(fetch).mockResolvedValue(response(diagnostics(snapshot(4))));
    const hook = renderHook(() => useDiagnostics(view()), { wrapper });
    await waitFor(() => expect(hook.result.current.isError).toBe(true));
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("diagnostics?turn=3"),
      expect.objectContaining({ method: "GET" })
    );
    expect(hook.result.current.data).toBeUndefined();
    client.clear();
  });
});

describe("honest narrative, diagnostics and morphology rendering", () => {
  it("renders whole narrative groups, structured result fields and source labels across pages", async () => {
    const { client, wrapper } = setup();
    const user = userEvent.setup();
    const selected = view(),
      first = group();
    first.annotations.push({
      ...annotation(selected.snapshot, "job-2"),
      narrative_revision: 2,
      source: "fallback_template",
      fallback_used: true,
      result: {
        job_type: "adaptation",
        species_id: "grazer",
        proposal_id: "p1",
        summary: "适应摘要",
        tradeoff_explanation: "增加护甲需要代价",
        source_event_ids: ["event-2"],
      },
    });
    const older = group("job-3", 1);
    older.annotations[0].source = "unspecified_provider";
    vi.mocked(fetch)
      .mockResolvedValueOnce(
        response({ ...narrativePage(selected.snapshot, [first]), next_offset: 4, truncated: true })
      )
      .mockResolvedValueOnce(response(narrativePage(selected.snapshot, [older])));
    render(<NarrativePanel {...selected} species="grazer" />, { wrapper });
    expect(await screen.findByText("结构化描述 job-1")).toBeInTheDocument();
    expect(screen.getByText("增加护甲需要代价")).toBeInTheDocument();
    expect(screen.getByText("离线模板")).toBeInTheDocument();
    expect(screen.getByText("回退模板")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "回合 3 叙事组" })).toHaveTextContent("2 条");
    await act(async () => {
      await user.click(screen.getByRole("button", { name: "加载更早的整组叙事" }));
    });
    expect(await screen.findByText("结构化描述 job-3")).toBeInTheDocument();
    expect(screen.getByText("结构化描述 job-1")).toBeInTheDocument();
    expect(screen.getByText("来源未标记")).toBeInTheDocument();
    expect(screen.getByText(/不能据此确认使用了真实 LLM/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "加载更早的整组叙事" })).not.toBeInTheDocument();
    client.clear();
  });
  it("species filter changes issue an isolated read and honest empty state", async () => {
    const { client, wrapper } = setup();
    const user = userEvent.setup();
    vi.mocked(fetch).mockImplementation(async (url) =>
      response(
        narrativePage(
          snapshot(3),
          [],
          new URL(String(url), "http://localhost").searchParams.get("species_id")
        )
      )
    );
    render(<NarrativePanel {...view()} species="grazer" />, { wrapper });
    await screen.findByText(/已检查的历史中暂无已应用叙事/);
    await act(async () => {
      await user.selectOptions(screen.getByRole("combobox", { name: "叙事范围" }), "all");
    });
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    expect(String(vi.mocked(fetch).mock.calls[1][0])).not.toContain("species_id");
    expect(
      client.getQueryData(keys.narratives(scope, 0, 3, "grazer", snapshot(3).snapshot_id))
    ).toBeDefined();
    client.clear();
  });
  it("diagnostics show service/store scopes and unknown GPU/token rather than zero", async () => {
    const { client, wrapper } = setup();
    vi.mocked(fetch).mockResolvedValue(response(diagnostics(snapshot(3))));
    render(<DiagnosticsPanel {...view()} />, { wrapper });
    await screen.findByText("128.00 MiB");
    expect(screen.getByText("CPU RSS · 服务进程")).toBeInTheDocument();
    expect(screen.getByText("存档 · 整个 store 目录")).toBeInTheDocument();
    expect(screen.getByText("约 256.00 MiB")).toBeInTheDocument();
    expect(screen.getByText("GPU 显存").parentElement).toHaveTextContent("未测量");
    expect(screen.getByText("AI token 用量").parentElement).toHaveTextContent("未提供");
    expect(screen.getByText(/扫描跳过 2/)).toBeInTheDocument();
    expect(screen.getByText(/所选完整版本的 AI 任务/)).toBeInTheDocument();
    client.clear();
  });
  it("morphology is a deterministic encoding of actual traits and trace deltas, with no network", () => {
    const species = snapshot().species[0];
    const before = JSON.stringify(species);
    const trace: WorldEvent = {
      event_id: "e1",
      version: snapshot().version,
      turn: 0,
      type: "SpeciesAdapted",
      actor: "grazer",
      cause: [],
      payload: { trait_changes: { armor: 0.02 } },
    };
    const ui = render(<TraitMorphology species={species} traces={[trace]} />);
    const diagram = screen.getByRole("img", { name: /grazer 的性状形态示意/ });
    expect(diagram).toHaveAccessibleDescription();
    expect(diagram.querySelector('[data-trait="armor"]')).toHaveAttribute("stroke-width", "1.9");
    expect(within(screen.getByRole("table")).getByText("+0.020")).toBeInTheDocument();
    ui.rerender(
      <TraitMorphology species={{ ...species, traits: { ...species.traits, armor: 0.8 } }} />
    );
    expect(diagram.querySelector('[data-trait="armor"]')).toHaveAttribute("stroke-width", "8.2");
    expect(screen.getByText("0.800")).toBeInTheDocument();
    expect(JSON.stringify(species)).toBe(before);
    expect(fetch).not.toHaveBeenCalled();
  });
  it("missing traits suppress the schematic instead of inventing values", () => {
    render(<TraitMorphology species={{ ...snapshot().species[0], traits: { armor: 0.2 } }} />);
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByText(/七轴性状不完整或超出范围/)).toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
  });
});

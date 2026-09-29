import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { acceptSnapshot, invalidateLive, keys, useCommand, useSnapshot } from "../queries";
import { advanceCommand, ApiError, rewindCommand } from "../api";
import { connectStream, useWorldStream } from "../stream";
import { executeExperiment, planExperiment } from "../experiment";
import { FakeEventSource, response, snapshot } from "./fixtures";
const scope = { world: "world-37", timeline: "control" };
function setup() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return {
    client,
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  };
}
beforeEach(() => {
  vi.stubGlobal("EventSource", FakeEventSource);
  FakeEventSource.instances = [];
  sessionStorage.clear();
  vi.stubGlobal("fetch", vi.fn());
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("isolated V2 queries and streams", () => {
  it("keys separate worlds, timelines, generations and immutable historical turns", () => {
    expect(
      new Set(
        [
          keys.snapshot(scope),
          keys.snapshot(scope, 1, 0),
          keys.snapshot(scope, 2, 0),
          keys.snapshot(scope, 1, 1),
          keys.snapshot({ ...scope, world: "other" }),
          keys.snapshot({ ...scope, timeline: "warm" }),
        ].map((key) => JSON.stringify(key))
      ).size
    ).toBe(6);
  });
  it("SSE invalidates only the selected live scope and leaves history caches intact", () => {
    const { client } = setup();
    const head = keys.snapshot(scope),
      history = keys.snapshot(scope, 1),
      other = keys.snapshot({ ...scope, timeline: "warm" });
    [head, history, other].forEach((key) => client.setQueryData(key, snapshot(1)));
    const status = vi.fn();
    const close = connectStream(client, scope, status);
    const source = FakeEventSource.instances[0];
    source.emit("TurnCommitted", "17");
    expect(client.getQueryState(head)?.isInvalidated).toBe(true);
    expect(client.getQueryState(history)?.isInvalidated).toBe(false);
    expect(client.getQueryState(other)?.isInvalidated).toBe(false);
    source.emit("error");
    expect(status).toHaveBeenLastCalledWith("reconnecting");
    expect(FakeEventSource.instances).toHaveLength(1); // Native reconnect retains Last-Event-ID.
    close();
    expect(source.closed).toBe(true);
    expect([...source.listeners.values()].every((items) => items.size === 0)).toBe(true);
    connectStream(client, scope, status)();
    expect(FakeEventSource.instances[1].url).toContain("after=17");
    client.clear();
  });
  it("switching timeline closes the old stream and unmount closes the new one", () => {
    const { client, wrapper } = setup();
    const hook = renderHook(({ timeline }) => useWorldStream({ world: scope.world, timeline }), {
      wrapper,
      initialProps: { timeline: "control" },
    });
    hook.rerender({ timeline: "warm" });
    expect(FakeEventSource.instances[0].closed).toBe(true);
    expect(FakeEventSource.instances[1].url).toContain("/warm/stream");
    hook.unmount();
    expect(FakeEventSource.instances[1].closed).toBe(true);
    client.clear();
  });
  it("IDs named head cannot cause historical species or snapshot invalidation", () => {
    const { client } = setup();
    const namedScope = { world: "head", timeline: "head" };
    const historical = [keys.snapshot(namedScope, 2), keys.detail(namedScope, "head", 2, 0)];
    const live = keys.detail(namedScope, "head", null, 0);
    [...historical, live].forEach((key) => client.setQueryData(key, {}));
    invalidateLive(client, namedScope);
    historical.forEach((key) => expect(client.getQueryState(key)?.isInvalidated).toBe(false));
    expect(client.getQueryState(live)?.isInvalidated).toBe(true);
    client.clear();
  });
  it("history fetches use GET, turn keys and no mutation even when latest head changes", async () => {
    const { client, wrapper } = setup();
    vi.mocked(fetch).mockResolvedValue(response(snapshot(2)));
    const hook = renderHook(() => useSnapshot(scope, 2, 0), { wrapper });
    await waitFor(() => expect(hook.result.current.isSuccess).toBe(true));
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("snapshot?turn=2"),
      expect.objectContaining({ method: "GET", body: undefined })
    );
    act(() => acceptSnapshot(client, snapshot(9)));
    expect(client.getQueryData(keys.snapshot(scope, 2, 0))).toEqual(snapshot(2));
    expect(fetch).toHaveBeenCalledTimes(1);
    client.clear();
  });
});

describe("explicit commands", () => {
  it("409 refreshes head and never automatically retries with a new command", async () => {
    const { client, wrapper } = setup();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    vi.mocked(fetch).mockResolvedValue(response({ detail: "Expected version differs" }, 409));
    const hook = renderHook(useCommand, { wrapper });
    const command = advanceCommand(snapshot(2));
    act(() => hook.result.current.mutate(command));
    await waitFor(() => expect(hook.result.current.error).toBeInstanceOf(ApiError));
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(invalidate).toHaveBeenCalled();
    expect(hook.result.current.variables).toBe(command);
    client.clear();
  });
  it("uncertain network retries retain the complete version and exactly the same idempotency key", async () => {
    const { client, wrapper } = setup();
    vi.mocked(fetch)
      .mockRejectedValueOnce(new TypeError("network lost"))
      .mockResolvedValueOnce(response(snapshot(3)));
    const hook = renderHook(useCommand, { wrapper });
    const command = advanceCommand(snapshot(2));
    act(() => hook.result.current.mutate(command));
    await waitFor(() => expect(hook.result.current.isError).toBe(true));
    act(() => hook.result.current.mutate(hook.result.current.variables!));
    await waitFor(() => expect(hook.result.current.isSuccess).toBe(true));
    const bodies = vi.mocked(fetch).mock.calls.map(([, init]) => JSON.parse(String(init?.body)));
    expect(bodies[0]).toEqual(bodies[1]);
    expect(bodies[0].expected_version).toEqual(snapshot(2).version);
    expect(bodies[0].idempotency_key).toMatch(/^[0-9a-f-]{36}$/);
    client.clear();
  });
  it("replayed older commits cannot overwrite a newer cached head", () => {
    const { client } = setup();
    client.setQueryData(keys.snapshot(scope), snapshot(9));
    acceptSnapshot(client, snapshot(3));
    expect(client.getQueryData(keys.snapshot(scope))).toEqual(snapshot(9));
    client.clear();
  });
  it("rewind targets current timeline but carries exact ancestor source version", () => {
    const command = rewindCommand(snapshot(8, "warm"), snapshot(3, "control"));
    expect(command.path).toContain("/warm/rewind");
    expect(command.body.source_version).toEqual(snapshot(3).version);
    expect(command.body.expected_version).toEqual(snapshot(8, "warm").version);
  });
  it("paired forks use the ancestor version and shared control namespace; uncertain advance resumes same key", async () => {
    const source = snapshot(3, "ancestor");
    const plan = planExperiment(source, "trial");
    vi.mocked(fetch).mockImplementation(async (url, init) => {
      const body = JSON.parse(String(init?.body));
      if (String(url).endsWith("forks")) {
        const value = snapshot(3, body.child_timeline_id);
        value.version.revision = 0;
        return response(value);
      }
      return response(snapshot(4, body.expected_version.timeline_id));
    });
    await executeExperiment(plan);
    const calls = vi.mocked(fetch).mock.calls;
    expect(calls[0][0]).toContain("/ancestor/forks");
    expect(JSON.parse(String(calls[0][1]?.body)).parent).toEqual(source.version);
    const control = JSON.parse(String(calls[2][1]?.body)),
      warm = JSON.parse(String(calls[3][1]?.body));
    expect(control.rng_namespace).toBe("trial-control");
    expect(warm.rng_namespace).toBe(control.rng_namespace);
    expect(warm.warming_offset).toBe(4);
    expect(control).not.toHaveProperty("warming_offset");
    plan.warm.result = undefined;
    vi.mocked(fetch).mockRejectedValueOnce(new TypeError("network"));
    await expect(executeExperiment(plan)).rejects.toThrow("network");
    await executeExperiment(plan);
    expect(JSON.parse(String(vi.mocked(fetch).mock.calls.at(-1)?.[1]?.body)).idempotency_key).toBe(
      warm.idempotency_key
    );
  });
  it("lost fork responses recover the same named child before sending any turn commands", async () => {
    const source = snapshot(3, "ancestor");
    const plan = planExperiment(source, "recover");
    vi.mocked(fetch).mockRejectedValueOnce(new TypeError("fork response lost"));
    await expect(executeExperiment(plan)).rejects.toThrow("fork response lost");
    vi.mocked(fetch).mockImplementation(async (url, init) => {
      if (String(url).endsWith("snapshot")) {
        const value = {
          ...source,
          version: { ...source.version, timeline_id: "recover-control", revision: 0 },
        };
        return response(value);
      }
      const body = JSON.parse(String(init?.body));
      const value = snapshot(
        String(url).endsWith("forks") ? 3 : 4,
        body.child_timeline_id ?? body.expected_version.timeline_id
      );
      if (String(url).endsWith("forks")) value.version.revision = 0;
      return response(value);
    });
    await executeExperiment(plan);
    const calls = vi.mocked(fetch).mock.calls;
    expect(calls[1][0]).toContain("/recover-control/snapshot");
    expect(calls[1][1]?.method).toBe("GET");
    expect(calls.filter(([url]) => String(url).endsWith("forks"))).toHaveLength(2);
    expect(plan.control.result?.turn).toBe(4);
  });
});

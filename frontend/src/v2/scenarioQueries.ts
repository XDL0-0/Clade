import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { API, ApiError, request } from "./api";
import { keys } from "./queries";
import { parseScenario } from "./scenarioModel";
import type { FrozenRun, ScenarioListItem, ScenarioProgress } from "./scenarioTypes";
import type { Page } from "./types";
export const scenarioKeys = {
  list: (world: string) => ["v2-lab", world, "experiments"] as const,
  status: (world: string, id: string) => ["v2-lab", world, "experiments", id] as const,
};
const worldPath = (world: string) => `${API}/worlds/${encodeURIComponent(world)}/experiments`;
export function useScenarios(world: string) {
  return useInfiniteQuery({
    queryKey: scenarioKeys.list(world),
    initialPageParam: 0,
    queryFn: ({ pageParam, signal }) =>
      request<Page<ScenarioListItem>>(
        `${worldPath(world)}?limit=20&offset=${pageParam}`,
        undefined,
        signal
      ),
    getNextPageParam: (page) => page.next_offset ?? undefined,
    enabled: !!world,
    retry: false,
  });
}
export function useScenarioStatus(world: string, id: string) {
  return useQuery({
    queryKey: scenarioKeys.status(world, id),
    queryFn: ({ signal }) =>
      request<ScenarioProgress>(`${worldPath(world)}/${encodeURIComponent(id)}`, undefined, signal),
    enabled: !!world && !!id,
    retry: false,
  });
}
export async function runScenario(command: FrozenRun): Promise<ScenarioProgress> {
  parseScenario(command.raw);
  const response = await fetch(`${API}/experiments/run?workers=${command.workers}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: command.raw,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const detail =
      typeof payload.detail === "string"
        ? payload.detail
        : JSON.stringify(payload.detail ?? "请求失败");
    throw new ApiError(
      response.status,
      response.status === 409 ? `实验计划或分支冲突：${detail}。未覆盖已有分支。` : detail
    );
  }
  return response.json();
}
export function invalidateScenarios(client: QueryClient, world: string) {
  void client.invalidateQueries({ queryKey: scenarioKeys.list(world) });
  void client.invalidateQueries({ queryKey: keys.timelines(world) });
  void client.invalidateQueries({ queryKey: keys.worlds });
}
export function useScenarioRun() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: runScenario,
    retry: false,
    onSuccess: (result) => {
      const plan = result.manifest.plan;
      client.setQueryData(scenarioKeys.status(plan.source.world_id, plan.id), result);
      invalidateScenarios(client, plan.source.world_id);
    },
    onError: (_error, command) =>
      invalidateScenarios(client, parseScenario(command.raw).source.world_id),
  });
}

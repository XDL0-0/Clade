import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { API, executeCommand, pathFor, request, turnQuery, ApiError } from "./api";
import type {
  Command,
  EventPage,
  MetricPage,
  Page,
  Profile,
  Scope,
  Snapshot,
  SpeciesDetail,
  Timeline,
} from "./types";

export const keys = {
  worlds: ["v2-lab", "worlds"] as const,
  timelines: (world: string) => ["v2-lab", world, "timelines"] as const,
  scope: ({ world, timeline }: Scope) => ["v2-lab", world, timeline] as const,
  snapshot: (scope: Scope, turn: number | null = null, generation = 0) =>
    [
      ...keys.scope(scope),
      "snapshot",
      ...(turn === null ? ["head"] : ["turn", turn, generation]),
    ] as const,
  detail: (scope: Scope, id: string, turn: number | null, generation: number) =>
    [
      ...keys.scope(scope),
      "species",
      id,
      ...(turn === null ? ["head"] : ["turn", turn, generation]),
    ] as const,
  profile: (scope: Scope, turn: number | null, generation: number) =>
    [
      ...keys.scope(scope),
      "profile",
      ...(turn === null ? ["head"] : ["turn", turn, generation]),
    ] as const,
  metrics: (scope: Scope, generation: number) =>
    [...keys.scope(scope), "metrics", generation] as const,
  events: (scope: Scope) => [...keys.scope(scope), "events"] as const,
  narratives: (
    scope: Scope,
    generation: number,
    turn: number | null,
    species: string | null,
    snapshot: string
  ) => [...keys.scope(scope), "narratives", generation, turn ?? "head", species, snapshot] as const,
  diagnostics: (scope: Scope, generation: number, turn: number | null, snapshot: string) =>
    [...keys.scope(scope), "diagnostics", generation, turn ?? "head", snapshot] as const,
};

export function invalidateLive(client: QueryClient, scope: Scope, kind = "TurnCommitted") {
  const annotation = kind === "NarrativeReady";
  void client.invalidateQueries({
    predicate: ({ queryKey: key }) => {
      if (key[0] !== "v2-lab" || key[1] !== scope.world) return false;
      if (key[2] === "experiments") return !annotation;
      // A late annotation can belong to an ancestor shared by another branch.
      if (annotation && key[3] === "narratives") return true;
      if (key[2] !== scope.timeline) return false;
      if (key[3] === "diagnostics")
        return annotation || key[5] === "head" || kind === "WorldReplaced";
      if (key[3] === "narratives") return key[5] === "head" || kind === "WorldReplaced";
      const head = key[3] === "species" ? key[5] === "head" : key[4] === "head";
      return key[3] === "events" || (!annotation && (key[3] === "metrics" || head));
    },
  });
  if (!annotation) {
    void client.invalidateQueries({ queryKey: keys.timelines(scope.world) });
    void client.invalidateQueries({ queryKey: keys.worlds });
  }
}
export function acceptSnapshot(client: QueryClient, snapshot: Snapshot) {
  const scope = { world: snapshot.version.world_id, timeline: snapshot.version.timeline_id };
  client.setQueryData<Snapshot>(keys.snapshot(scope), (current) => {
    if (
      current &&
      (current.version.generation > snapshot.version.generation ||
        (current.version.generation === snapshot.version.generation &&
          current.version.revision > snapshot.version.revision))
    )
      return current;
    return snapshot;
  });
  invalidateLive(client, scope);
}

export function useWorlds(world?: string) {
  return useInfiniteQuery({
    queryKey: world ? keys.timelines(world) : keys.worlds,
    initialPageParam: 0,
    queryFn: ({ pageParam, signal }) =>
      request<Page<Timeline>>(
        `${API}/worlds${world ? `/${encodeURIComponent(world)}/timelines` : ""}?offset=${pageParam}&limit=100`,
        undefined,
        signal
      ),
    getNextPageParam: (page) => page.next_offset ?? undefined,
    retry: false,
  });
}
export function useSnapshot(scope: Scope, turn: number | null = null, generation = 0) {
  return useQuery({
    queryKey: keys.snapshot(scope, turn, generation),
    queryFn: ({ signal }) =>
      request<Snapshot>(`${pathFor(scope)}/snapshot${turnQuery(turn)}`, undefined, signal),
    enabled: !!scope.world && !!scope.timeline,
    retry: false,
    staleTime: turn === null ? 0 : Infinity,
  });
}
export function useSpecies(scope: Scope, id: string, turn: number | null, generation: number) {
  return useQuery({
    queryKey: keys.detail(scope, id, turn, generation),
    queryFn: ({ signal }) =>
      request<SpeciesDetail>(
        `${pathFor(scope)}/species/${encodeURIComponent(id)}${turnQuery(turn)}`,
        undefined,
        signal
      ),
    enabled: !!scope.world && !!scope.timeline && !!id,
    staleTime: turn === null ? 0 : Infinity,
    retry: false,
  });
}
export function useProfile(scope: Scope, turn: number | null, generation: number) {
  return useQuery({
    queryKey: keys.profile(scope, turn, generation),
    queryFn: ({ signal }) =>
      request<Profile>(`${pathFor(scope)}/profile${turnQuery(turn)}`, undefined, signal),
    enabled: !!scope.world && !!scope.timeline,
    staleTime: turn === null ? 0 : Infinity,
    retry: false,
  });
}
export function useMetrics(scope: Scope, generation: number) {
  return useInfiniteQuery({
    queryKey: keys.metrics(scope, generation),
    initialPageParam: -1,
    queryFn: ({ pageParam, signal }) =>
      request<MetricPage>(
        `${pathFor(scope)}/metrics?generation=${generation}&after_revision=${pageParam}&limit=1000`,
        undefined,
        signal
      ),
    getNextPageParam: (page) => (page.items.length === 1000 ? page.next_after_revision : undefined),
    enabled: !!scope.world && !!scope.timeline,
    retry: false,
  });
}
export function useEvents(scope: Scope) {
  return useInfiniteQuery({
    queryKey: keys.events(scope),
    initialPageParam: 0,
    queryFn: ({ pageParam, signal }) =>
      request<EventPage>(
        `${pathFor(scope)}/events?after=${pageParam}&limit=100`,
        undefined,
        signal
      ),
    getNextPageParam: (page) => (page.items.length === 100 ? page.next_cursor : undefined),
    enabled: !!scope.world && !!scope.timeline,
    retry: false,
  });
}
export function useCommand() {
  const client = useQueryClient();
  return useMutation<Snapshot, Error, Command>({
    mutationFn: executeCommand,
    retry: false,
    onSuccess: (snapshot) => acceptSnapshot(client, snapshot),
    onError: (error, command) => {
      if (error instanceof ApiError && error.status === 409) invalidateLive(client, command.scope);
    },
  });
}

import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { pathFor, request, turnQuery } from "./api";
import { keys } from "./queries";
import type { Diagnostics, NarrativePage } from "./presentationTypes";
import type { Scope, Snapshot, Version } from "./types";

export interface PresentationView {
  scope: Scope;
  snapshot: Snapshot;
  turn: number | null;
  generation: number;
}
function sameVersion(left: Version, right: Version) {
  return (
    left.world_id === right.world_id &&
    left.timeline_id === right.timeline_id &&
    left.generation === right.generation &&
    left.revision === right.revision
  );
}
export function useNarratives(view: PresentationView, species: string | null) {
  const { scope, snapshot, turn, generation } = view;
  return useInfiniteQuery({
    queryKey: keys.narratives(scope, generation, turn, species, snapshot.snapshot_id),
    initialPageParam: 0,
    queryFn: async ({ pageParam, signal }) => {
      const query = new URLSearchParams({ limit: "20", offset: String(pageParam) });
      if (turn !== null) query.set("turn", String(turn));
      if (species !== null) query.set("species_id", species);
      const page = await request<NarrativePage>(
        `${pathFor(scope)}/narratives?${query}`,
        undefined,
        signal
      );
      // A head may change between snapshot and presentation requests. Never merge
      // annotations from a different selection, especially across a rewind.
      if (
        !sameVersion(page.version, snapshot.version) ||
        page.turn !== snapshot.turn ||
        page.species_id !== species
      )
        throw new Error("叙事响应与所选快照不一致，请重新读取当前快照。");
      return page;
    },
    // offset counts semantic commits, including empty ones, not annotations.
    getNextPageParam: (page) => page.next_offset ?? undefined,
    retry: false,
    staleTime: 0,
  });
}
export function useDiagnostics({ scope, snapshot, turn, generation }: PresentationView) {
  return useQuery({
    queryKey: keys.diagnostics(scope, generation, turn, snapshot.snapshot_id),
    queryFn: async ({ signal }) => {
      const data = await request<Diagnostics>(
        `${pathFor(scope)}/diagnostics${turnQuery(turn)}`,
        undefined,
        signal
      );
      if (data.snapshot_id !== snapshot.snapshot_id || !sameVersion(data.version, snapshot.version))
        throw new Error("诊断响应与所选快照不一致，请重新读取当前快照。");
      return data;
    },
    retry: false,
  });
}

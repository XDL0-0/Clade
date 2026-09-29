import { useEffect, useState } from "react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { pathFor } from "./api";
import { invalidateLive } from "./queries";
import type { Message, Scope } from "./types";

export const STREAM_KINDS = [
  "WorldCreated",
  "WorldCommitted",
  "TurnCommitted",
  "WorldReplaced",
  "TimelineCreated",
  "SpeciesAdapted",
  "SpeciesCreated",
  "SpeciationOccurred",
  "SpeciationDeferred",
  "SpeciesRecovered",
  "SpeciesDeclining",
  "SpeciesCritical",
  "SpeciesFunctionallyExtinct",
  "SpeciesExtinct",
  "MigrationOccurred",
  "ClimateShift",
  "Volcano",
  "BiomeChanged",
  "NarrativeReady",
  "message",
];
export type StreamStatus = "connecting" | "open" | "reconnecting" | "unsupported";
const storageKey = (scope: Scope) => `v2-lab:cursor:${scope.world}:${scope.timeline}`;
function readCursor(scope: Scope) {
  try {
    const value = sessionStorage.getItem(storageKey(scope));
    return value && /^\d+$/.test(value) ? value : "0";
  } catch {
    return "0";
  }
}
export function connectStream(
  client: QueryClient,
  scope: Scope,
  status: (value: StreamStatus) => void
) {
  if (typeof EventSource === "undefined") {
    status("unsupported");
    return () => {};
  }
  let cursor = readCursor(scope);
  let active = true;
  const source = new EventSource(`${pathFor(scope)}/stream?after=${cursor}`);
  status("connecting");
  const onOpen = () => {
    if (active) status("open");
  };
  // Keep the same native EventSource: its reconnect sends Last-Event-ID automatically.
  const onError = () => {
    if (active) status("reconnecting");
  };
  const onMessage = (event: Event) => {
    if (!active) return;
    const message = event as MessageEvent<string>;
    try {
      const payload = JSON.parse(message.data) as Message;
      if (typeof payload.kind !== "string") return;
      if (/^\d+$/.test(message.lastEventId) && BigInt(message.lastEventId) > BigInt(cursor)) {
        cursor = message.lastEventId;
        try {
          sessionStorage.setItem(storageKey(scope), cursor);
        } catch {
          /* Session storage is optional. */
        }
      }
      invalidateLive(client, scope, payload.kind);
    } catch {
      /* A malformed notification cannot become numerical state. */
    }
  };
  source.addEventListener("open", onOpen);
  source.addEventListener("error", onError);
  STREAM_KINDS.forEach((kind) => source.addEventListener(kind, onMessage));
  return () => {
    active = false;
    source.removeEventListener("open", onOpen);
    source.removeEventListener("error", onError);
    STREAM_KINDS.forEach((kind) => source.removeEventListener(kind, onMessage));
    source.close();
  };
}
export function useWorldStream(scope: Scope) {
  const client = useQueryClient();
  const [status, setStatus] = useState<StreamStatus>("connecting");
  useEffect(() => {
    if (!scope.world || !scope.timeline) return;
    return connectStream(client, { world: scope.world, timeline: scope.timeline }, setStatus);
  }, [client, scope.world, scope.timeline]);
  return status;
}

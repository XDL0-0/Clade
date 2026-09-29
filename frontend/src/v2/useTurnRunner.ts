import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { advanceCommand, ApiError, executeCommand, isUncertain } from "./api";
import { acceptSnapshot, invalidateLive } from "./queries";
import { clearPendingTurn, readPendingTurn, savePendingTurn } from "./playSession";
import type { Command, Scope, Snapshot, Values } from "./types";

export interface Journey {
  first: Snapshot;
  last: Snapshot;
  completed: number;
  target: number;
}

/** Each step awaits a committed turn; pausing never abandons an in-flight write. */
export function useTurnRunner(scope: Scope) {
  const client = useQueryClient();
  const [pending, setPending] = useState<Command | null>(() => {
    const restored = readPendingTurn();
    return restored?.scope.world === scope.world && restored.scope.timeline === scope.timeline
      ? restored : null;
  });
  const [running, setRunning] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [journey, setJourney] = useState<Journey | null>(null);
  const active = useRef(false);
  const stopRequested = useRef(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; stopRequested.current = true; };
  }, []);

  async function send(command: Command) {
    // Persist the exact command before the request, so reload/retry cannot add another turn.
    savePendingTurn(command);
    if (mounted.current) setPending(command);
    try {
      const snapshot = await executeCommand(command);
      clearPendingTurn(command);
      acceptSnapshot(client, snapshot);
      if (mounted.current) setPending(null);
      return snapshot;
    } catch (caught) {
      const failure = caught instanceof Error ? caught : new Error("世界暂时没有回应。");
      if (!isUncertain(failure)) {
        clearPendingTurn(command);
        if (mounted.current) setPending(null);
      }
      if (failure instanceof ApiError && failure.status === 409)
        invalidateLive(client, command.scope);
      throw failure;
    }
  }

  async function start(snapshot: Snapshot, turns: number, parameters: Values = {}) {
    if (active.current || pending) return;
    active.current = true;
    stopRequested.current = false;
    setRunning(true);
    setStopping(false);
    setError(null);
    let current = snapshot;
    setJourney({ first: snapshot, last: snapshot, completed: 0, target: turns });
    try {
      for (let step = 0; step < turns && !stopRequested.current; step++) {
        current = await send(advanceCommand(current, step === 0 ? parameters : {}));
        if (mounted.current)
          setJourney({ first: snapshot, last: current, completed: step + 1, target: turns });
      }
    } catch (caught) {
      if (mounted.current) setError(caught as Error);
    } finally {
      active.current = false;
      if (mounted.current) { setRunning(false); setStopping(false); }
    }
  }

  async function retry() {
    if (!pending || active.current) return;
    active.current = true;
    setRunning(true);
    setError(null);
    try {
      const snapshot = await send(pending);
      if (mounted.current) setJourney((current) => current
        ? { ...current, last: snapshot, completed: current.completed + 1 }
        : null);
    } catch (caught) {
      if (mounted.current) setError(caught as Error);
    } finally {
      active.current = false;
      if (mounted.current) setRunning(false);
    }
  }

  return {
    running, stopping, error, journey,
    busy: running || pending !== null,
    needsRecovery: pending !== null && !running,
    start, retry,
    pause() { stopRequested.current = true; setStopping(true); },
  };
}

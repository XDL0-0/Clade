import { useMutation, useQueryClient } from "@tanstack/react-query";
import { advanceCommand, ApiError, executeCommand, isUncertain, pathFor, request } from "./api";
import { acceptSnapshot, invalidateLive } from "./queries";
import { scopeOf, type Command, type Snapshot } from "./types";

interface BranchStep {
  id: string;
  fork?: Snapshot;
  command?: Command;
  result?: Snapshot;
  uncertainFork?: boolean;
}
export interface Experiment {
  source: Snapshot;
  control: BranchStep;
  warm: BranchStep;
  warming: number;
}
export interface PairResult {
  control: Snapshot;
  warm: Snapshot;
  sourceTurn: number;
  namespace: string;
}
export function planExperiment(source: Snapshot, prefix: string): Experiment {
  if (!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,51}$/.test(prefix))
    throw new Error("实验标识需为1–52位字母、数字、点、下划线或连字符。");
  const offset = source.environment.warming_offset;
  if (typeof offset !== "number" || !Number.isFinite(offset) || offset + 4 > 100)
    throw new Error("无法在这个基线的升温偏移上再增加4°C。");
  return {
    source,
    control: { id: `${prefix}-control` },
    warm: { id: `${prefix}-warm` },
    warming: offset + 4,
  };
}
async function ensureFork(plan: Experiment, step: BranchStep) {
  if (step.fork) return step.fork;
  const childScope = { world: plan.source.version.world_id, timeline: step.id };
  if (step.uncertainFork) {
    try {
      const found = await request<Snapshot>(`${pathFor(childScope)}/snapshot`);
      if (
        found.turn !== plan.source.turn ||
        found.state_hash !== plan.source.state_hash ||
        found.version.revision !== 0 ||
        found.version.generation !== 0
      ) {
        throw new ApiError(409, "同名实验分支已有不同状态，请检查时间线列表。");
      }
      step.fork = found;
      return found;
    } catch (error) {
      if (!(error instanceof ApiError && error.status === 404)) throw error;
    }
  }
  try {
    step.fork = await request<Snapshot>(`${pathFor(scopeOf(plan.source.version))}/forks`, {
      parent: plan.source.version,
      child_timeline_id: step.id,
    });
    return step.fork;
  } catch (error) {
    if (isUncertain(error as Error)) step.uncertainFork = true;
    throw error;
  }
}
export async function executeExperiment(plan: Experiment): Promise<PairResult> {
  const control = await ensureFork(plan, plan.control);
  const warm = await ensureFork(plan, plan.warm);
  plan.control.command ??= advanceCommand(control, { rng_namespace: plan.control.id });
  plan.warm.command ??= advanceCommand(warm, {
    rng_namespace: plan.control.id,
    warming_offset: plan.warming,
  });
  plan.control.result ??= await executeCommand(plan.control.command);
  plan.warm.result ??= await executeCommand(plan.warm.command);
  return {
    control: plan.control.result,
    warm: plan.warm.result,
    sourceTurn: plan.source.turn,
    namespace: plan.control.id,
  };
}
export function useExperiment() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: executeExperiment,
    retry: false,
    onSuccess: (pair) => {
      acceptSnapshot(client, pair.control);
      acceptSnapshot(client, pair.warm);
    },
    onError: (_error, plan) => {
      invalidateLive(client, scopeOf(plan.source.version));
      invalidateLive(client, { world: plan.source.version.world_id, timeline: plan.control.id });
      invalidateLive(client, { world: plan.source.version.world_id, timeline: plan.warm.id });
    },
  });
}

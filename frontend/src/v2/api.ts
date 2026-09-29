import type { Command, CreateWorld, Scope, Snapshot, Values, Version } from "./types";

export const API = "/api/v2";
export const pathFor = ({ world, timeline }: Scope) =>
  `${API}/worlds/${encodeURIComponent(world)}/${encodeURIComponent(timeline)}`;
export const turnQuery = (turn: number | null) => (turn === null ? "" : `?turn=${turn}`);

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string
  ) {
    super(message);
    this.name = "ApiError";
  }
}
export const isUncertain = (error: Error | null) =>
  error !== null && (!(error instanceof ApiError) || error.status >= 500);

export async function request<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    signal,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    const value = (await response.json().catch(() => ({}))) as { detail?: unknown };
    const detail =
      typeof value.detail === "string" ? value.detail : JSON.stringify(value.detail ?? "请求失败");
    throw new ApiError(
      response.status,
      response.status === 409
        ? `版本或标识冲突：${detail}。已刷新最新状态，请重新检查后操作。`
        : detail
    );
  }
  return response.json() as Promise<T>;
}

function validateVersion(version: Version) {
  if (![version.generation, version.revision].every(Number.isSafeInteger)) {
    throw new Error("版本号超出浏览器整数精度，无法安全提交。");
  }
}
export function advanceCommand(snapshot: Snapshot, parameters: Values = {}): Command {
  validateVersion(snapshot.version);
  const scope = { world: snapshot.version.world_id, timeline: snapshot.version.timeline_id };
  return {
    label: "推进一回合",
    scope,
    path: `${pathFor(scope)}/turns`,
    body: {
      expected_version: { ...snapshot.version },
      idempotency_key: crypto.randomUUID(),
      ...parameters,
    },
  };
}
export function rewindCommand(head: Snapshot, source: Snapshot): Command {
  validateVersion(head.version);
  validateVersion(source.version);
  const scope = { world: head.version.world_id, timeline: head.version.timeline_id };
  return {
    label: `回退至回合 ${source.turn}`,
    scope,
    path: `${pathFor(scope)}/rewind`,
    body: {
      expected_version: { ...head.version },
      source_version: { ...source.version },
      idempotency_key: crypto.randomUUID(),
    },
  };
}
export function createCommand(values: CreateWorld): Command {
  return {
    label: "创建世界",
    scope: { world: values.world_id, timeline: values.timeline_id },
    path: `${API}/worlds`,
    body: { ...values },
  };
}
export const executeCommand = (command: Command) => request<Snapshot>(command.path, command.body);

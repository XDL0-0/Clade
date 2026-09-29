import { pathFor } from "./api";
import type { Command, Scope } from "./types";

const KEY = "clade:v2:pending-turn:v1";
const LAST_WORLD = "clade:v2:last-world:v1";

export function readPendingTurn(): Command | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return null;
    const value = JSON.parse(raw) as Command;
    const version = value.body?.expected_version;
    if (
      !value.scope || typeof value.scope.world !== "string" ||
      typeof value.scope.timeline !== "string" ||
      value.path !== `${pathFor(value.scope)}/turns` ||
      !version || typeof version !== "object" || Array.isArray(version) ||
      version.world_id !== value.scope.world || version.timeline_id !== value.scope.timeline ||
      typeof value.body.idempotency_key !== "string"
    ) return null;
    return value;
  } catch {
    return null;
  }
}

export function savePendingTurn(command: Command) {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(command));
  } catch {
    throw new Error("浏览器未能记住这次操作。请允许会话存储后再推进。");
  }
}

export function clearPendingTurn(command: Command) {
  try {
    if (readPendingTurn()?.body.idempotency_key === command.body.idempotency_key)
      sessionStorage.removeItem(KEY);
  } catch { /* An old command is safe to repeat. */ }
}

export function rememberWorld(scope: Scope) {
  try { localStorage.setItem(LAST_WORLD, JSON.stringify(scope)); } catch { /* Optional preference. */ }
}

export function lastWorld(): Scope {
  const pending = readPendingTurn();
  if (pending) return pending.scope;
  try {
    const scope = JSON.parse(localStorage.getItem(LAST_WORLD) ?? "null") as Scope | null;
    if (scope && typeof scope.world === "string" && typeof scope.timeline === "string") return scope;
  } catch { /* Start at the welcome screen. */ }
  return { world: "", timeline: "" };
}

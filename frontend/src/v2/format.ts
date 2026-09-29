import type { Json } from "./types";
export function formatValue(value: Json | undefined): string {
  if (value === null || value === undefined) return "未提供";
  return typeof value === "number"
    ? Number.isInteger(value)
      ? value.toLocaleString()
      : value.toFixed(4)
    : typeof value === "object"
      ? JSON.stringify(value)
      : String(value);
}

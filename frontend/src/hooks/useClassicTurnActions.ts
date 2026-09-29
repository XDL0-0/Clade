import { useEffect, useRef, useState } from "react";
import { runTurn } from "@/services/api";
import { dispatchEnergyChanged } from "@/components/EnergyBar";
import type { PressureDraft, TurnReport } from "@/services/api.types";

interface Actions {
  addReports: (reports: TurnReport[]) => void;
  setCurrentTurnIndex: (turn: number) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
  setBatchProgress: (progress: { current: number; total: number; message: string } | null) => void;
  refreshMap: () => Promise<unknown>;
  refreshSpeciesList: () => Promise<unknown>;
  refreshQueue: () => Promise<unknown>;
  invalidateLineage: () => void;
  closePressure: () => void;
  openSummary: () => void;
}

/** Classic-world actions. Only a completed request advances the batch loop. */
export function useClassicTurnActions(actions: Actions) {
  const active = useRef(false);
  const stop = useRef(false);
  const mounted = useRef(true);
  const [pauseRequested, setPauseRequested] = useState(false);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; stop.current = true; };
  }, []);

  function begin() {
    if (active.current) return false;
    active.current = true;
    stop.current = false;
    setPauseRequested(false);
    actions.setLoading(true);
    actions.setError(null);
    return true;
  }

  function publish(reports: TurnReport[]) {
    if (!mounted.current || !reports.length) return;
    actions.addReports(reports);
    actions.setCurrentTurnIndex(reports[reports.length - 1].turn_index + 1);
    actions.invalidateLineage();
    dispatchEnergyChanged();
  }

  async function refreshWorld() {
    if (!mounted.current) return;
    await Promise.allSettled([
      actions.refreshMap(), actions.refreshSpeciesList(), actions.refreshQueue(),
    ]);
  }

  function finish() {
    active.current = false;
    if (!mounted.current) return;
    actions.setLoading(false);
    actions.setBatchProgress(null);
    setPauseRequested(false);
  }

  async function executeTurn(drafts: PressureDraft[], rounds = 1) {
    if (!begin()) return;
    let completed = false;
    try {
      const reports = await runTurn(drafts, rounds);
      publish(reports);
      completed = reports.length > 0;
      if (mounted.current) actions.closePressure();
    } catch (error) {
      if (mounted.current)
        actions.setError(`推演未完成：${error instanceof Error ? error.message : "暂时无法取得结果"}`);
    } finally {
      await refreshWorld();
      finish();
      if (completed && mounted.current) actions.openSummary();
    }
  }

  async function executeBatch(rounds: number, pressures: PressureDraft[], _randomEnergy: number) {
    if (!Number.isInteger(rounds) || rounds <= 0 || !begin()) return;
    let completed = 0;
    actions.setBatchProgress({ current: 0, total: rounds, message: "准备开始演化" });
    actions.closePressure();
    try {
      for (let index = 0; index < rounds && !stop.current; index++) {
        if (!mounted.current) break;
        actions.setBatchProgress({
          current: completed, total: rounds,
          message: `已完成 ${completed} 回合，正在推进下一回合…`,
        });
        const reports = await runTurn(pressures, 1, false);
        if (!reports.length) throw new Error("这一回合没有返回报告，自动演化已停止。");
        if (!mounted.current) break;
        // Retain each completed turn immediately, including when a later request fails.
        publish(reports);
        completed++;
        actions.setBatchProgress({
          current: completed, total: rounds,
          message: stop.current ? "正在整理已完成的回合…" : `已完成 ${completed} 回合`,
        });
        await refreshWorld();
      }
    } catch (error) {
      if (mounted.current)
        actions.setError(`自动演化已停下，已显示 ${completed} 个完成回合。${error instanceof Error ? error.message : "暂时无法取得下一回合结果"}`);
    } finally {
      await refreshWorld();
      finish();
      if (completed > 0 && mounted.current) actions.openSummary();
    }
  }

  return {
    executeTurn, executeBatch, pauseRequested,
    pauseAfterTurn() { stop.current = true; setPauseRequested(true); },
  };
}

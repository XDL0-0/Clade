import { useEffect, useState, type FormEvent } from "react";
import { ApiError, isUncertain } from "./api";
import { ErrorNotice } from "./Forms";
import { ScenarioJSON } from "./ScenarioJSON";
import { ScenarioPlanForm } from "./ScenarioPlanForm";
import { ScenarioResults } from "./ScenarioResults";
import { draftJSON, toDraft, type PlanDraft } from "./scenarioDraft";
import {
  exportJSON,
  makeScenario,
  parseScenario,
  persistScenario,
  PENDING_SCENARIO,
  readPendingScenario,
} from "./scenarioModel";
import { makePreset, scenarioPresets, type ScenarioPreset } from "./scenarioPresets";
import { useScenarioRun, useScenarios, useScenarioStatus } from "./scenarioQueries";
import type { FrozenRun, ScenarioProgress } from "./scenarioTypes";
import type { Scope, Snapshot } from "./types";

function clearRecovery(command: FrozenRun) {
  try {
    if (readPendingScenario().command?.raw === command.raw)
      sessionStorage.removeItem(PENDING_SCENARIO);
  } catch {
    /* The same command is safe to resume if storage becomes unavailable. */
  }
}
export function ScenarioPanel({
  snapshot,
  busy,
  onBlockingChange,
  onOpen,
}: {
  snapshot: Snapshot;
  busy: boolean;
  onBlockingChange: (value: boolean) => void;
  onOpen: (scope: Scope) => void;
}) {
  const [recovery] = useState(readPendingScenario);
  const restored =
    recovery.command &&
    parseScenario(recovery.command.raw).source.world_id === snapshot.version.world_id
      ? recovery.command
      : null;
  const [frozen, setFrozen] = useState<FrozenRun | null>(restored);
  const [uncertain, setUncertain] = useState(!!restored);
  const [draft, setDraft] = useState(() =>
    toDraft(restored ? parseScenario(restored.raw) : makeScenario(snapshot))
  );
  const [imported, setImported] = useState<string | null>(null);
  const [pinnedSource, setPinnedSource] = useState(false);
  const [quickTurns, setQuickTurns] = useState(10);
  const [workers, setWorkers] = useState(String(restored?.workers ?? 2));
  const [error, setError] = useState<Error | null>(
    recovery.error ? new Error(recovery.error) : null
  );
  const [result, setResult] = useState<ScenarioProgress | null>(null);
  const [selected, setSelected] = useState(restored ? parseScenario(restored.raw).id : "");
  const run = useScenarioRun();
  const listing = useScenarios(snapshot.version.world_id);
  const status = useScenarioStatus(snapshot.version.world_id, selected);
  const blocked = run.isPending || uncertain;
  useEffect(() => {
    onBlockingChange(blocked);
    return () => onBlockingChange(false);
  }, [blocked, onBlockingChange]);
  useEffect(() => {
    if (!frozen && !pinnedSource)
      setDraft((current) => ({ ...current, source: { ...snapshot.version } }));
  }, [snapshot.version, frozen, pinnedSource]);
  const change = (next: PlanDraft) => {
    setImported(null);
    setDraft(next);
    setError(null);
  };
  const serialize = () => frozen?.raw ?? imported ?? draftJSON(draft);
  const execute = (command: FrozenRun) => {
    setError(null);
    try {
      const plan = parseScenario(command.raw);
      if (plan.source.world_id !== snapshot.version.world_id)
        throw new Error("请先回到这个计划所属的世界。");
      if (!Number.isInteger(command.workers) || command.workers < 1 || command.workers > 4)
        throw new Error("并发数应为 1–4 的整数。");
      persistScenario(command);
      setDraft(toDraft(plan));
      setFrozen(command);
      setSelected(plan.id);
      setUncertain(true);
      run.mutate(command, {
        onSuccess: (data) => {
          setResult(data);
          setUncertain(false);
          clearRecovery(command);
        },
        onError: (caught) => {
          if (!isUncertain(caught)) {
            setUncertain(false);
            clearRecovery(command);
          }
        },
      });
    } catch (caught) {
      setError(caught as Error);
    }
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    try {
      execute(frozen ?? {
        format: "clade.lab.pending.v1",
        raw: serialize(),
        workers: Number(workers),
      });
    } catch (caught) {
      setError(caught as Error);
    }
  };
  const startPreset = (preset: ScenarioPreset) => {
    try {
      const plan = makePreset(snapshot, preset, quickTurns);
      setImported(null);
      setPinnedSource(false);
      execute({
        format: "clade.lab.pending.v1",
        raw: JSON.stringify(plan, null, 2),
        workers: Number(workers),
      });
    } catch (caught) {
      setError(caught as Error);
    }
  };
  const reset = () => {
    setDraft(toDraft(makeScenario(snapshot)));
    setFrozen(null);
    setImported(null);
    setPinnedSource(false);
    setError(null);
    run.reset();
  };
  const importPlan = (raw: string) => {
    try {
      const plan = parseScenario(raw);
      if (plan.source.world_id !== snapshot.version.world_id)
        throw new Error("这份计划属于另一个世界，请先切换世界。");
      setDraft(toDraft(plan));
      setImported(raw);
      setPinnedSource(true);
      setFrozen(null);
      setError(null);
      run.reset();
    } catch (caught) {
      setError(caught as Error);
    }
  };
  const exportPlan = () => {
    try {
      const raw = serialize();
      parseScenario(raw);
      exportJSON(raw, `${draft.id}.plan.json`, true);
    } catch (caught) {
      setError(caught as Error);
    }
  };
  const progress = status.data ?? (result?.manifest.plan.id === selected ? result : undefined);
  const completed = progress?.branches.every((item) => item.status === "completed");
  const conflict = progress?.branches.some((item) => item.status === "conflict");
  const draftConflict = conflict && progress?.manifest.plan.id === draft.id;
  const continueSaved = () => {
    if (progress)
      execute({
        format: "clade.lab.pending.v1",
        raw: JSON.stringify(progress.manifest.plan, null, 2),
        workers: Number(workers),
      });
  };
  return (
    <section className="lab-card lab-scenario" aria-label="平行世界">
      <h2>如果世界换一种命运</h2>
      <p className="lab-note">
        从正在查看的第 {snapshot.turn} 回合出发。选一种变化，看看生命会走向哪里。
        每次都会留下一个原环境分支，当前世界不受影响。
      </p>
      <label className="lab-scenario-duration">
        向前演化
        <select value={quickTurns} disabled={busy || blocked} onChange={(e) => setQuickTurns(Number(e.target.value))}>
          <option value={10}>10 回合 · 看看变化</option>
          <option value={30}>30 回合 · 多走一段</option>
          <option value={100}>100 回合 · 漫长岁月</option>
        </select>
      </label>
      <div className="lab-scenario-presets">
        {scenarioPresets.map((preset) => (
          <button
            key={preset.id}
            className={`lab-scenario-preset lab-scenario-preset-${preset.id}`}
            disabled={busy || blocked}
            onClick={() => startPreset(preset.id)}
          >
            <span aria-hidden="true">{preset.symbol}</span>
            <strong>{preset.name}</strong>
            <small>{preset.description}</small>
            <b>开始演化 →</b>
          </button>
        ))}
      </div>
      {run.isPending && <p role="status" className="lab-history-note">世界正在演化…可以在下方查看已发生的回合。</p>}
      {uncertain && !run.isPending && frozen && (
        <div className="lab-history-note" role="status">
          <p>上次旅程还没收到结果。继续会沿用同一个世界，不会重建分支。</p>
          <button className="lab-primary" disabled={busy} onClick={() => execute(frozen)}>继续演化</button>
        </div>
      )}
      <ErrorNotice error={error ?? run.error} />
      <details className="lab-scenario-advanced">
        <summary>高级设置：自定义世界、回合与 JSON</summary>
        <p className="lab-note">
          自定义起点：{draft.source.timeline_id} / g{draft.source.generation} / r{draft.source.revision}
          {pinnedSource ? "（来自导入文件）" : ""}。可创建 2–8 个分支，保留原环境分支。
        </p>
        <button disabled={busy || blocked} onClick={reset}>从当前回合自定义新世界</button>
        <form onSubmit={submit}>
          <ScenarioPlanForm value={draft} onChange={change} disabled={busy || blocked || !!frozen} />
          <label>
            并发数（1–4）
            <input type="number" min={1} max={4} step={1} value={workers} disabled={busy || blocked || !!frozen} onChange={(e) => setWorkers(e.target.value)} />
          </label>
          <p className="lab-note">
            温度填写绝对偏移；暖化预设是在起点上 +4°C。温度与 CO₂ 会延续，灾害与疾病只作用于填写的回合，后续归零。
          </p>
          <button type="submit" className="lab-primary" disabled={busy || run.isPending || !!draftConflict}>
            {run.isPending ? "演化中…" : frozen ? "继续这个计划" : "开始自定义演化"}
          </button>
        </form>
        <ScenarioJSON disabled={busy || blocked} onImport={importPlan} onExport={exportPlan} />
      </details>
      <h3>我的平行世界</h3>
      <ErrorNotice error={listing.error} />
      {listing.isSuccess && listing.data.pages.every((page) => page.items.length === 0) && !selected && (
        <p className="lab-note">还没有平行世界，试试上面的变化。</p>
      )}
      <label>
        查看一次旅程
        <select disabled={busy || blocked} value={selected} onChange={(e) => setSelected(e.target.value)}>
          <option value="">选择已保存的旅程</option>
          {selected && !listing.data?.pages.some((page) => page.items.some((item) => item.id === selected)) && <option value={selected}>{selected}</option>}
          {listing.data?.pages.flatMap((page) => page.items).map((item) => (
            <option key={item.id} value={item.id}>{item.name}</option>
          ))}
        </select>
      </label>
      <div className="lab-scenario-actions">
        <button disabled={!selected || status.isFetching} onClick={() => void status.refetch()}>查看最新进展</button>
        <button disabled={listing.isFetching} onClick={() => void listing.refetch()}>刷新列表</button>
        {listing.hasNextPage && <button disabled={listing.isFetchingNextPage} onClick={() => void listing.fetchNextPage()}>更多旅程</button>}
      </div>
      {status.error instanceof ApiError && status.error.status === 404 ? (
        <p className="lab-note">这次旅程还没有保存记录。</p>
      ) : <ErrorNotice error={status.error} />}
      {status.isFetching && <p role="status">正在读取旅程…</p>}
      {progress && (
        <>
          <ScenarioResults
            key={progress.manifest_hash}
            progress={progress}
            result={result?.manifest_hash === progress.manifest_hash ? result : null}
            disabled={busy || blocked}
            onOpen={onOpen}
          />
          {!completed && !conflict && !uncertain && (
            <button className="lab-primary" disabled={busy || run.isPending} onClick={continueSaved}>继续演化</button>
          )}
          <details className="lab-scenario-advanced">
            <summary>保存与详细结果</summary>
            <div className="lab-scenario-actions">
              <button onClick={() => exportJSON({ current: progress, last_execution: result?.manifest_hash === progress.manifest_hash ? result : null }, `${selected}.result.json`)}>导出结果 JSON</button>
              <button disabled={busy || blocked || !!conflict} onClick={continueSaved}>载入详细结果</button>
            </div>
            <p className="lab-note">载入详细结果会继续尚未走完的旅程，已完成的回合不会重复演化。</p>
          </details>
        </>
      )}
    </section>
  );
}

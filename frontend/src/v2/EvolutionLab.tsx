import { useEffect, useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ArrowRight, GitBranch, Leaf } from "lucide-react";
import { pathFor, rewindCommand, isUncertain } from "./api";
import { CreateWorldForm, ErrorNotice, RewindConfirm } from "./Forms";
import { useCommand, useProfile, useSnapshot, useSpecies, useWorlds } from "./queries";
import { useWorldStream } from "./stream";
import { MapPanel } from "./MapPanel";
import { SpeciesPanel } from "./SpeciesPanel";
import { NarrativePanel } from "./NarrativePanel";
import { DiagnosticsPanel } from "./DiagnosticsPanel";
import { ComparisonPanel, EventPanel, MetricsHistory, ProfilePanel } from "./Observations";
import { scopeOf, type Scope, type Snapshot } from "./types";
import { ScenarioPanel } from "./ScenarioPanel";
import { readPendingScenario, restoredScenarioScope } from "./scenarioModel";
import { lastWorld, readPendingTurn, rememberWorld } from "./playSession";
import { PlayControls } from "./PlayControls";
import { useTurnRunner } from "./useTurnRunner";
import "./lab.css";
import "./play.css";

function ScopeLab({
  scope,
  onBusy,
  onOpen,
  externalBusy,
}: {
  scope: Scope;
  onBusy: (value: boolean) => void;
  onOpen: (scope: Scope) => void;
  externalBusy: boolean;
}) {
  const [turn, setTurn] = useState<number | null>(null);
  const [species, setSpecies] = useState("");
  const [actionError, setActionError] = useState<Error | null>(null);
  const [advanced, setAdvanced] = useState(false);
  const head = useSnapshot(scope);
  const generation = head.data?.version.generation ?? 0;
  const historical = useSnapshot(scope, turn, generation);
  const snapshot = turn === null ? head.data : historical.data;
  const selected = snapshot?.species.some((item) => item.species_id === species)
    ? species
    : snapshot?.species[0]?.species_id || "";
  const detail = useSpecies(scope, selected, turn, generation);
  const profile = useProfile(scope, turn, generation);
  const visibleDetail =
    detail.data?.snapshot_id === snapshot?.snapshot_id ? detail.data : undefined;
  const visibleProfile =
    profile.data?.snapshot_id === snapshot?.snapshot_id ? profile.data : undefined;
  const timelines = useWorlds(scope.world);
  const stream = useWorldStream(scope);
  const command = useCommand();
  const runner = useTurnRunner(scope);
  const [experimentBusy, setExperimentBusy] = useState(() => !!readPendingScenario().command);
  const blocked = command.isPending || isUncertain(command.error) || experimentBusy || runner.busy || externalBusy;
  useEffect(() => {
    onBusy(blocked);
    return () => onBusy(false);
  }, [blocked, onBusy]);
  const status = {
    connecting: "正在连接世界…",
    open: "世界已连接",
    reconnecting: "正在重新连接…",
    unsupported: "实时消息暂不可用",
  }[stream];
  const fork = () => {
    if (!snapshot) return;
    setActionError(null);
    try {
      command.mutate({
        label: "开启另一种未来",
        scope,
        path: `${pathFor(scope)}/forks`,
        body: {
          parent: { ...snapshot.version },
          child_timeline_id: `future-${crypto.randomUUID().slice(0, 8)}`,
        },
      }, { onSuccess: (created) => onOpen(scopeOf(created.version)) });
    } catch (error) {
      setActionError(error as Error);
    }
  };
  const rewind = () => {
    if (!head.data || !snapshot) return;
    setActionError(null);
    try {
      command.mutate(rewindCommand(head.data, snapshot), { onSuccess: () => setTurn(null) });
    } catch (error) {
      setActionError(error as Error);
    }
  };
  if (head.isPending)
    return (
      <div className="lab-card" role="status">
        正在打开世界…
      </div>
    );
  if (!head.data) return <ErrorNotice error={head.error} />;
  return (
    <div className="lab-workspace">
      <section className="lab-card lab-toolbar">
        <div>
          <span className="lab-eyebrow">
            {scope.world} / {scope.timeline}
          </span>
          <h2>
            回合 {snapshot?.turn ?? turn ?? head.data.turn}
            {turn !== null && <span className="lab-muted"> · 最新回合 {head.data.turn}</span>}
          </h2>
          <p className="lab-note">
            <span role="status">{status}</span>
          </p>
        </div>
        <span className="play-current">{turn === null ? "现在" : "回到过去"}</span>
        <div className="lab-history">
          <label htmlFor="lab-turn">
            时光机 <strong>{turn ?? head.data.turn}</strong>
          </label>
          <input
            id="lab-turn"
            type="range"
            min="0"
            max={head.data.turn}
            value={turn ?? head.data.turn}
            disabled={head.data.turn === 0 || blocked}
            onChange={(event) => setTurn(Number(event.target.value))}
          />
          <button onClick={() => setTurn(null)} disabled={turn === null || blocked}>
            回到现在
          </button>
        </div>
        {turn !== null && (
          <p className="lab-history-note">
            你正在过去的世界。可以从这里开启另一种未来，也可以回到现在继续演化。
          </p>
        )}
        <ErrorNotice
          error={actionError ?? command.error}
          retry={
            !actionError && command.variables
              ? () => command.mutate(command.variables!, { onSuccess: (result) => {
                  if (result.version.timeline_id !== scope.timeline) onOpen(scopeOf(result.version));
                  else setTurn(null);
                } })
              : undefined
          }
        />
      </section>
      {historical.isFetching && turn !== null && <p role="status">正在读取回合 {turn}…</p>}
      <ErrorNotice error={historical.error} />
      {snapshot && (
        <>
          <PlayControls
            snapshot={head.data}
            runner={runner}
            disabled={externalBusy || command.isPending || isUncertain(command.error) || experimentBusy || turn !== null}
          />
          <div className="lab-stat-grid">
            <div>
              <small>生命数量</small>
              <strong>
                {snapshot.species.reduce((sum, item) => sum + item.population, 0).toLocaleString()}
              </strong>
              <span>所有存活个体</span>
            </div>
            <div>
              <small>存活 / 历史物种</small>
              <strong>
                {snapshot.species.filter((item) => item.population > 0).length} /{" "}
                {snapshot.species.length}
              </strong>
              <span>灭绝记录保留</span>
            </div>
            <div>
              <small>全球温度</small>
              <strong>
                {typeof snapshot.environment.global_temperature === "number"
                  ? `${snapshot.environment.global_temperature.toFixed(2)}°C`
                  : "未提供"}
              </strong>
              <span>影响每个物种的生存环境</span>
            </div>
            <div>
              <small>需要关注</small>
              <strong>
                {snapshot.species.filter((item) => item.population > 0 && item.status !== "Healthy").length}
              </strong>
              <span>数量下降或濒危的物种</span>
            </div>
          </div>
          <div className="lab-main-grid">
            <div className="lab-column">
              <MapPanel snapshot={snapshot} detail={visibleDetail} />
              <EventPanel scope={scope} onSelectSpecies={setSpecies} />
              <section className="lab-card play-fork">
                <p>回合 {snapshot.turn}，如果做出不同的选择，会发生什么？</p>
                <button disabled={blocked} onClick={fork}><GitBranch size={16} /> 从这里开启另一种未来</button>
              </section>
            </div>
            <div className="lab-column">
              <ErrorNotice error={detail.error} />
              {detail.isFetching && <p role="status">读取物种分布…</p>}
              <SpeciesPanel
                snapshot={snapshot}
                selected={selected}
                onSelect={setSpecies}
                detail={visibleDetail}
              />
              <NarrativePanel
                scope={scope}
                snapshot={snapshot}
                turn={turn}
                generation={generation}
                species={selected}
              />
              {turn !== null && (
                <section className="lab-card">
                  <h3>重新选择</h3>
                  <RewindConfirm
                    key={snapshot.snapshot_id}
                    source={snapshot}
                    pending={blocked}
                    onConfirm={rewind}
                  />
                </section>
              )}
            </div>
          </div>
          <ScenarioPanel
            snapshot={snapshot}
            busy={externalBusy || command.isPending || isUncertain(command.error) || runner.busy}
            onBlockingChange={setExperimentBusy}
            onOpen={onOpen}
          />
          <details className="lab-card play-advanced" onToggle={(event) => setAdvanced(event.currentTarget.open)}>
            <summary>高级：世界数据与运行信息</summary>
            {advanced && <div className="play-advanced-content">
              <ComparisonPanel scope={scope} snapshot={snapshot} profile={visibleProfile}
                timelines={timelines.data?.pages.flatMap((page) => page.items) ?? []} />
              <MetricsHistory scope={scope} generation={generation} />
              <DiagnosticsPanel scope={scope} snapshot={snapshot} turn={turn} generation={generation} />
              <ProfilePanel profile={visibleProfile}
                loading={profile.isPending || (!!profile.data && !visibleProfile)} error={profile.error} />
              <p className="lab-note lab-version">
                快照 {snapshot.snapshot_id} · {snapshot.state_hash} · 来源 {snapshot.version.timeline_id} / g
                {snapshot.version.generation} / r{snapshot.version.revision}
              </p>
            </div>}
          </details>
        </>
      )}
    </div>
  );
}

export function EvolutionLabContent() {
  const [scope, setScope] = useState<Scope>(() => {
    const scenario = restoredScenarioScope();
    return scenario.world ? scenario : lastWorld();
  });
  const [busy, setBusy] = useState(() => !!readPendingScenario().command || !!readPendingTurn());
  const [creating, setCreating] = useState(false);
  const [recoveryError] = useState(() => readPendingScenario().error);
  useEffect(() => { if (scope.world && scope.timeline) rememberWorld(scope); }, [scope]);
  const worlds = useWorlds();
  const timelines = useWorlds(scope.world || undefined);
  const rows = worlds.data?.pages.flatMap((page) => page.items) ?? [];
  const worldIds = [...new Set(rows.map((row) => row.world_id))];
  if (scope.world && !worldIds.includes(scope.world)) worldIds.push(scope.world);
  const choices =
    timelines.data?.pages
      .flatMap((page) => page.items)
      .filter((item) => item.world_id === scope.world) ?? [];
  const selectSnapshot = (snapshot: Snapshot) => setScope(scopeOf(snapshot.version));
  return (
    <main className="evo-lab">
      <header className="lab-header">
        <div className="lab-brand">
          <Leaf aria-hidden="true" />
          <div>
            <span className="lab-eyebrow">CLADE / A LIVING WORLD</span>
            <h1>生命演化沙盒</h1>
          </div>
        </div>
        <span className="lab-chip">
          每一种选择，都可能改变未来
        </span>
      </header>
      <div className="lab-shell">
        <aside className="lab-sidebar">
          <section className="lab-card">
            <h2>我的世界</h2>
            <label>
              世界
              <select
                disabled={busy || creating}
                value={scope.world}
                onChange={(e) => setScope({ world: e.target.value, timeline: "" })}
              >
                <option value="">选择世界</option>
                {worldIds.map((world) => (
                  <option key={world} value={world}>
                    {world}
                  </option>
                ))}
              </select>
            </label>
            <label>
              世界分支
              <select
                disabled={!scope.world || busy || creating}
                value={scope.timeline}
                onChange={(e) => setScope({ ...scope, timeline: e.target.value })}
              >
                <option value="">选择一个未来</option>
                {scope.timeline && !choices.some((item) => item.timeline_id === scope.timeline) && (
                  <option value={scope.timeline}>{scope.timeline}</option>
                )}
                {choices.map((item) => (
                  <option key={item.timeline_id} value={item.timeline_id}>
                    {item.timeline_id} · 回合 {item.turn}
                  </option>
                ))}
              </select>
            </label>
            <ErrorNotice error={worlds.error ?? timelines.error} />
            {worlds.isPending && <p role="status">读取世界列表…</p>}
            {worlds.hasNextPage && (
              <button onClick={() => void worlds.fetchNextPage()}>更多世界</button>
            )}
            {timelines.hasNextPage && scope.world && (
              <button onClick={() => void timelines.fetchNextPage()}>更多时间线</button>
            )}
          </section>
          <CreateWorldForm onCreated={selectSnapshot} blocked={busy} onBusy={setCreating} />
          <ErrorNotice error={recoveryError ? new Error(recoveryError) : null} />
          <section className="lab-about">
            <h3>观察生命，改变世界</h3>
            <p>关注一个物种，看看它如何求生。改变气候，或回到过去，让世界走向另一种未来。</p>
            <a href="/">
              经典模式 <ArrowRight size={13} aria-hidden="true" />
            </a>
          </section>
        </aside>
        {scope.world && scope.timeline ? (
          <ScopeLab
            key={`${scope.world}:${scope.timeline}`}
            scope={scope}
            onBusy={setBusy}
            onOpen={setScope}
            externalBusy={creating}
          />
        ) : (
          <section className="lab-welcome">
            <Leaf size={42} aria-hidden="true" />
            <span className="lab-eyebrow">LIFE FINDS A WAY</span>
            <h2>让一个世界，长出自己的故事。</h2>
            <p>
              创建新世界，然后让时间流动。物种会竞争、迁徙、适应，也可能消失。你可以旁观，也可以给世界一次改变。
            </p>
            <div>
              <span>01 诞生一个世界</span>
              <span>02 关注喜欢的物种</span>
              <span>03 探索不同的未来</span>
            </div>
          </section>
        )}
      </div>
    </main>
  );
}
export default function EvolutionLab() {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { retry: false, refetchOnWindowFocus: true },
          mutations: { retry: false },
        },
      })
  );
  return (
    <QueryClientProvider client={client}>
      <EvolutionLabContent />
    </QueryClientProvider>
  );
}

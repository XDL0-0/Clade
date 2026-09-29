import { useState } from "react";
import { useEvents, useMetrics, useProfile, useSnapshot } from "./queries";
import { ErrorNotice } from "./Forms";
import { formatValue } from "./format";
import { chronicle } from "./chronicleCopy";
import type { Profile, Scope, Snapshot, Timeline, Values } from "./types";
import "./fieldJournal.css";

function metricsOf(profile?: Profile): Values {
  return profile?.stages.find((stage) => stage.stage_name === "reference_metrics")?.metrics ?? {};
}
export function ComparisonPanel({
  scope,
  snapshot,
  profile,
  timelines,
  initialTimeline = "",
}: {
  scope: Scope;
  snapshot: Snapshot;
  profile?: Profile;
  timelines: Timeline[];
  initialTimeline?: string;
}) {
  const [other, setOther] = useState(initialTimeline);
  const otherScope = { world: scope.world, timeline: other };
  const otherHead = useSnapshot(otherScope);
  const otherView = useSnapshot(otherScope, snapshot.turn, otherHead.data?.version.generation ?? 0);
  const otherProfile = useProfile(
    otherScope,
    snapshot.turn,
    otherHead.data?.version.generation ?? 0
  );
  const current = metricsOf(profile),
    comparison = metricsOf(
      otherProfile.data?.snapshot_id === otherView.data?.snapshot_id ? otherProfile.data : undefined
    );
  const rows: [string, number | null | undefined, number | null | undefined][] = [
    [
      "总人口",
      snapshot.species.reduce((sum, item) => sum + item.population, 0),
      otherView.data?.species.reduce((sum, item) => sum + item.population, 0),
    ],
    [
      "存活物种",
      snapshot.species.filter((item) => item.population > 0).length,
      otherView.data?.species.filter((item) => item.population > 0).length,
    ],
    ...(
      [
        ["Shannon 多样性", "shannon_diversity"],
        ["总生物量", "total_biomass"],
        ["NPP", "npp"],
        ["相邻回合稳定性", "ecosystem_stability"],
      ] as const
    ).map(
      ([label, key]) =>
        [
          label,
          typeof current[key] === "number" ? (current[key] as number) : null,
          typeof comparison[key] === "number" ? (comparison[key] as number) : null,
        ] as [string, number | null, number | null]
    ),
  ];
  return (
    <section className="lab-card">
      <div className="lab-section-head">
        <h2>同回合对比</h2>
        <label>
          比较时间线
          <select value={other} onChange={(e) => setOther(e.target.value)}>
            <option value="">选择时间线</option>
            {timelines
              .filter((item) => item.timeline_id !== scope.timeline)
              .map((item) => (
                <option key={item.timeline_id} value={item.timeline_id}>
                  {item.timeline_id}
                </option>
              ))}
          </select>
        </label>
      </div>
      <p className="lab-note">
        两侧均读取回合 {snapshot.turn}；若另一分支尚未提交此回合，显示不可用。
      </p>
      <ErrorNotice error={otherView.error ?? otherProfile.error} />
      {other && (otherView.isFetching || otherProfile.isFetching) && (
        <p role="status">正在读取对照…</p>
      )}
      <div className="lab-table-wrap">
        <table>
          <caption>回合 {snapshot.turn} 的真实快照与阶段指标</caption>
          <thead>
            <tr>
              <th>指标</th>
              <th>{scope.timeline}</th>
              <th>{other || "未选择"}</th>
              <th>对照 − 当前</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([label, left, right]) => (
              <tr key={label}>
                <th>{label}</th>
                <td>{formatValue(left)}</td>
                <td>{other ? formatValue(right) : "—"}</td>
                <td>
                  {other && typeof left === "number" && typeof right === "number"
                    ? formatValue(right - left)
                    : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
export function MetricsHistory({ scope, generation }: { scope: Scope; generation: number }) {
  const query = useMetrics(scope, generation);
  const points =
    query.data?.pages
      .flatMap((page) => page.items)
      .flatMap((point) => {
        const population = point.metrics.reference_metrics?.total_population;
        return typeof population === "number"
          ? [{ turn: point.turn, population, revision: point.revision }]
          : [];
      }) ?? [];
  const min = points.length ? Math.min(...points.map((p) => p.population)) : 0;
  const max = points.length ? Math.max(...points.map((p) => p.population)) : 0;
  const first = points[0]?.turn ?? 0,
    last = points.at(-1)?.turn ?? 0;
  const line = points
    .map(
      (point) =>
        `${25 + (450 * (point.turn - first)) / Math.max(1, last - first)},${90 - (65 * (point.population - min)) / Math.max(1, max - min)}`
    )
    .join(" ");
  return (
    <section className="lab-card">
      <h2>
        人口轨迹 <span className="lab-muted">generation {generation}</span>
      </h2>
      <ErrorNotice error={query.error} />
      {query.isPending ? (
        <p role="status">读取指标…</p>
      ) : points.length === 0 ? (
        <p className="lab-empty">尚无已提交的 metrics；初始快照不补造回合数据。</p>
      ) : (
        <>
          <svg
            className="lab-trend"
            viewBox="0 0 500 115"
            role="img"
            aria-label={`已加载人口轨迹：回合${first}到${last}，人口${min}到${max}`}
          >
            <line x1="25" x2="475" y1="90" y2="90" stroke="#d8e0dc" />
            <polyline points={line} fill="none" stroke="#257266" strokeWidth="2" />
            {points.map((p) => (
              <circle
                key={p.revision}
                cx={25 + (450 * (p.turn - first)) / Math.max(1, last - first)}
                cy={90 - (65 * (p.population - min)) / Math.max(1, max - min)}
                r="2.5"
                fill="#257266"
              >
                <title>
                  回合{p.turn}：{p.population}
                </title>
              </circle>
            ))}
            <text x="25" y="109" fontSize="10">
              回合 {first}
            </text>
            <text x="420" y="109" fontSize="10">
              回合 {last}
            </text>
            <text x="25" y="15" fontSize="10">
              人口 {min.toLocaleString()}–{max.toLocaleString()}
            </text>
          </svg>
          <p className="lab-note">仅绘制已加载的 {points.length} 个真实观测点。</p>
        </>
      )}
      {query.hasNextPage && (
        <button disabled={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()}>
          加载更多指标
        </button>
      )}
    </section>
  );
}
export function ProfilePanel({
  profile,
  loading,
  error,
}: {
  profile?: Profile;
  loading: boolean;
  error: Error | null;
}) {
  const total = profile?.stages.reduce((sum, stage) => sum + stage.duration_ms, 0) ?? 0;
  return (
    <section className="lab-card">
      <h2>
        Stage profiler <span className="lab-muted">{total.toFixed(2)} ms</span>
      </h2>
      <p className="lab-note">所选提交的阶段耗时；不包含网络耗时，不影响数值世界哈希。</p>
      <ErrorNotice error={error} />
      {loading ? (
        <p role="status">读取性能记录…</p>
      ) : !profile?.stages.length ? (
        <p className="lab-empty">此快照没有阶段运行记录。</p>
      ) : (
        <div className="lab-profile">
          {profile.stages.map((stage) => (
            <details key={stage.stage_name}>
              <summary>
                <span>{stage.stage_name.replace("reference_", "")}</span>
                <meter
                  min="0"
                  max={Math.max(total, 0.001)}
                  value={stage.duration_ms}
                  aria-label={`${stage.stage_name}耗时`}
                />
                <b>{stage.duration_ms.toFixed(2)} ms</b>
              </summary>
              <p>
                版本 {stage.stage_version} · RNG seed <code>{stage.random_seed}</code>
              </p>
              <pre>{JSON.stringify(stage.metrics, null, 2)}</pre>
              {[...stage.warnings, ...stage.errors].map((line) => (
                <p key={line}>{line}</p>
              ))}
            </details>
          ))}
        </div>
      )}
    </section>
  );
}
export function EventPanel({ scope, onSelectSpecies }: {
  scope: Scope;
  onSelectSpecies?: (id: string) => void;
}) {
  const query = useEvents(scope);
  const events = query.data?.pages.flatMap((page) => page.items) ?? [];
  return (
    <section className="lab-card journal-chronicle">
      <h2>
        世界纪事 <span className="lab-muted">{events.length} 条已翻阅</span>
      </h2>
      <p className="lab-note">这条时间线留下的足迹。纪事按发生顺序保留，不随历史回合选择改变。</p>
      <ErrorNotice error={query.error} />
      {query.isPending ? (
        <p role="status">正在翻开纪事…</p>
      ) : events.length === 0 ? (
        <p className="lab-empty">这条时间线还没有留下纪事。</p>
      ) : (
        <ol className="lab-events journal-events">
          {[...events].reverse().map((event) => {
            const entry = chronicle(event);
            return (
              <li key={event.message_id}>
                <article>
                  <div className="journal-event-heading">
                    <span className="journal-kicker">回合 {formatValue(event.payload.turn)}</span>
                    <h3>{entry.title}</h3>
                  </div>
                  <p>{entry.body}</p>
                  {entry.pressure && <p className="journal-context">{entry.pressure}</p>}
                  {entry.cost && <p className="journal-context"><strong>代价：</strong>{entry.cost}</p>}
                  {entry.species.length > 0 && (
                    <div className="journal-related" aria-label="相关物种">
                      {entry.species.map((id) => onSelectSpecies ? (
                        <button key={id} type="button" onClick={() => onSelectSpecies(id)}
                          aria-label={`观察物种 ${id}`}>观察 {id} <span aria-hidden="true">↗</span></button>
                      ) : <span key={id}>{id}</span>)}
                    </div>
                  )}
                  <details className="journal-details">
                    <summary>详细数据</summary>
                    <p className="lab-note">事件 {event.kind} · 记录 #{event.cursor}</p>
                    <pre>{JSON.stringify(event.payload, null, 2)}</pre>
                  </details>
                </article>
              </li>
            );
          })}
        </ol>
      )}
      {query.hasNextPage && (
        <button disabled={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()}>
          翻阅后续纪事
        </button>
      )}
    </section>
  );
}

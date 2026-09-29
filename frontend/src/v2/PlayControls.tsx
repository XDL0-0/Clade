import { useState } from "react";
import { Pause, Play, Snowflake, Sun, Zap } from "lucide-react";
import { ErrorNotice } from "./Forms";
import type { useTurnRunner } from "./useTurnRunner";
import type { Snapshot, Values } from "./types";

type Runner = ReturnType<typeof useTurnRunner>;
const population = (snapshot: Snapshot) => snapshot.species.reduce((sum, item) => sum + item.population, 0);

export function PlayControls({ snapshot, runner, disabled }: {
  snapshot: Snapshot;
  runner: Runner;
  disabled: boolean;
}) {
  const [turns, setTurns] = useState(10);
  const [power, setPower] = useState("");
  const offset = typeof snapshot.environment.warming_offset === "number"
    ? snapshot.environment.warming_offset : 0;
  const powers = [
    { id: "warm", title: "暖化", icon: Sun, detail: "气候持续变暖，看看谁能适应。", parameters: { warming_offset: Math.min(100, offset + 4) } },
    { id: "cold", title: "冰河", icon: Snowflake, detail: "气候持续变冷，让物种寻找新的栖息地。", parameters: { warming_offset: Math.max(-100, offset - 4) } },
    { id: "disaster", title: "灾变", icon: Zap, detail: "这一回合施加灾害压力，再观察生态如何恢复。", parameters: { disaster_severity: 0.45 } },
  ];
  const selected = powers.find((item) => item.id === power);
  const blocked = disabled || runner.busy;
  const journey = runner.journey;
  const delta = journey ? population(journey.last) - population(journey.first) : 0;
  const newcomers = journey?.last.species.filter((item) => !journey.first.species.some((old) => old.species_id === item.species_id)) ?? [];
  return (
    <section className="lab-card play-controls" aria-label="演化控制">
      <div className="play-controls-heading">
        <div><h2>让生命继续演化</h2><p className="lab-note">每回合自动保存。可以随时在当前回合结束后停下。</p></div>
        <div className="play-speed" aria-label="推进回合数">
          {[1, 10, 50].map((count) => (
            <button key={count} aria-pressed={turns === count} disabled={blocked} onClick={() => setTurns(count)}>
              {count} 回合
            </button>
          ))}
        </div>
      </div>
      <div className="play-actions">
        {runner.running ? (
          <button onClick={runner.pause} disabled={runner.stopping}>
            <Pause size={16} /> {runner.stopping ? "这一回合结束后暂停…" : "暂停演化"}
          </button>
        ) : (
          <button className="lab-primary" disabled={blocked} onClick={() => { setPower(""); void runner.start(snapshot, turns); }}>
            <Play size={16} /> 推进 {turns} 回合
          </button>
        )}
        {journey && <span role="status">已推进 {journey.completed} / {journey.target} 回合</span>}
      </div>
      <div className="play-powers" aria-label="改变世界">
        {powers.map(({ id, title, icon: Icon }) => (
          <button key={id} aria-pressed={id === power} disabled={blocked} onClick={() => setPower(id === power ? "" : id)}>
            <Icon size={17} /> {title}
          </button>
        ))}
      </div>
      {selected && <div className="play-power-detail">
        <p>{selected.detail}</p>
        <button disabled={blocked} onClick={() => { void runner.start(snapshot, 1, selected.parameters as Values); setPower(""); }}>
          施加{selected.title}并推进一回合
        </button>
        <small>想保留当前走向？可在下方“平行世界”中尝试。</small>
      </div>}
      {runner.needsRecovery && <div className="play-recovery" role="status">
        <p>上一回合的结果还没收到，先取回它，再继续游玩。</p>
        <button disabled={disabled} onClick={() => void runner.retry()}>取回上一回合</button>
      </div>}
      <ErrorNotice error={runner.error} />
      {journey && journey.completed > 0 && !runner.running && (
        <div className="play-recap" aria-live="polite">
          <strong>回合 {journey.first.turn} → {journey.last.turn}</strong>
          <span>生命数量 {delta >= 0 ? "+" : ""}{delta.toLocaleString()}</span>
          <span>现存 {journey.last.species.filter((item) => item.population > 0).length} 个物种</span>
          {newcomers.length > 0 && <span>出现 {newcomers.length} 个新物种</span>}
        </div>
      )}
    </section>
  );
}

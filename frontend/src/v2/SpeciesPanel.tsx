import { useEffect, useState } from "react";
import { TraitMorphology } from "./TraitMorphology";
import {
  extinctionStory, pressureStory, roleNames, statusNames, tradeoffStory, traitChangeStory,
} from "./chronicleCopy";
import type { Snapshot, SpeciesDetail } from "./types";
import "./fieldJournal.css";

function readFollowing(key: string): string[] {
  try {
    const saved: unknown = JSON.parse(window.localStorage.getItem(key) ?? "[]");
    return Array.isArray(saved) ? saved.filter((id): id is string => typeof id === "string") : [];
  } catch {
    return [];
  }
}

export function SpeciesPanel({
  snapshot, selected, onSelect, detail,
}: {
  snapshot: Snapshot;
  selected: string;
  onSelect: (id: string) => void;
  detail?: SpeciesDetail;
}) {
  const storageKey = `clade:following:v1:${JSON.stringify([
    snapshot.version.world_id, snapshot.version.timeline_id,
  ])}`;
  const [following, setFollowing] = useState(() => ({ key: storageKey, ids: readFollowing(storageKey) }));
  const [localOnly, setLocalOnly] = useState(false);
  useEffect(() => {
    setFollowing({ key: storageKey, ids: readFollowing(storageKey) });
    setLocalOnly(false);
    const sync = (event: StorageEvent) => {
      if (event.key === storageKey || event.key === null) {
        setFollowing({ key: storageKey, ids: readFollowing(storageKey) });
      }
    };
    window.addEventListener("storage", sync);
    return () => window.removeEventListener("storage", sync);
  }, [storageKey]);
  const followed = new Set(following.key === storageKey ? following.ids : []);
  const toggleFollow = (id: string) => {
    const next = new Set(followed);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    const ids = [...next];
    setFollowing({ key: storageKey, ids });
    try {
      window.localStorage.setItem(storageKey, JSON.stringify(ids));
      setLocalOnly(false);
    } catch {
      setLocalOnly(true);
    }
  };
  const ordered = [...snapshot.species].sort((left, right) =>
    Number(followed.has(right.species_id)) - Number(followed.has(left.species_id))
  );
  const selectedSpecies = snapshot.species.find((item) => item.species_id === selected);
  const currentDetail = detail?.snapshot_id === snapshot.snapshot_id
    && detail.species.species_id === selected ? detail : undefined;
  const traces = currentDetail?.evolution_traces ?? [];
  return (
    <section className="lab-card journal-species">
      <div className="lab-section-head">
        <h2>生灵图鉴 <span className="lab-muted">{snapshot.species.length}</span></h2>
        <span className="journal-kicker">回合 {snapshot.turn}</span>
      </div>
      <p className="lab-note">点亮星标，关注它在这条时间线中的命运。</p>
      {localOnly && <p className="lab-note" role="status">浏览器暂不允许保存，关注仅在本次浏览中保留。</p>}
      <div className="lab-species-list journal-roster" aria-label="选择物种，关注的物种优先">
        {ordered.map((item) => (
          <div className="journal-species-row" key={item.species_id}>
            <button
              type="button"
              className={`journal-follow ${followed.has(item.species_id) ? "is-followed" : ""}`}
              aria-label={`${followed.has(item.species_id) ? "取消关注" : "关注"} ${item.species_id}`}
              aria-pressed={followed.has(item.species_id)}
              title={followed.has(item.species_id) ? "取消关注" : "关注这个物种"}
              onClick={() => toggleFollow(item.species_id)}
            >
              <span aria-hidden="true">{followed.has(item.species_id) ? "★" : "☆"}</span>
            </button>
            <button
              type="button"
              className={`journal-species-select ${selected === item.species_id ? "selected" : ""}`}
              aria-pressed={selected === item.species_id}
              onClick={() => onSelect(item.species_id)}
            >
              <span>
                <strong>{item.species_id}</strong>
                <small>{statusNames[item.status] ?? "状态未标记"} · {roleNames[item.role] ?? "角色未标记"}</small>
              </span>
              <b>{item.population.toLocaleString()}<small>个体</small></b>
            </button>
          </div>
        ))}
      </div>
      {selectedSpecies && (
        <div className="lab-species-detail">
          <div className="journal-species-heading">
            <span className="journal-kicker">{followed.has(selected) ? "★ 正在关注" : "当前观察"}</span>
            <h3>{selectedSpecies.species_id}</h3>
            <p>
              {roleNames[selectedSpecies.role] ?? "角色未标记"} · {statusNames[selectedSpecies.status] ?? "状态未标记"}
              {selectedSpecies.created_turn !== null && ` · 诞生于回合 ${selectedSpecies.created_turn}`}
            </p>
          </div>
          {selectedSpecies.status === "Extinct" ? (
            <div className="journal-story journal-memorial">
              <strong>这支生灵已留在历史中</strong>
              <p>
                {selectedSpecies.extinction_turn !== null && `在回合 ${selectedSpecies.extinction_turn}，`}
                最后一个个体消失了。{extinctionStory(selectedSpecies.extinction_cause)}
              </p>
              {selectedSpecies.last_nonzero_population !== null && (
                <p className="lab-note">最后一次仍有存活个体的记录：{selectedSpecies.last_nonzero_population.toLocaleString()} 个体。</p>
              )}
            </div>
          ) : (
            <p className="journal-population">
              现有 <strong>{selectedSpecies.population.toLocaleString()}</strong> 个体
              {(selectedSpecies.declining_turns ?? 0) > 0 && `，已连续 ${selectedSpecies.declining_turns} 回合减少`}。
            </p>
          )}
          <div className="lab-lineage">
            <span>源自</span>
            {selectedSpecies.ancestor ? (
              <button type="button" onClick={() => onSelect(selectedSpecies.ancestor!)}>{selectedSpecies.ancestor}</button>
            ) : <span>最初的生灵</span>}
            {Boolean(selectedSpecies.descendants?.length) && <span>· 后代</span>}
            {selectedSpecies.descendants?.map((id) => (
              <button key={id} type="button" onClick={() => onSelect(id)}>{id}</button>
            ))}
          </div>
          <h3>它正在经历什么</h3>
          {!currentDetail ? (
            <p className="lab-empty">选择物种后，这里的观察记录会随所选回合更新。</p>
          ) : traces.length === 0 ? (
            <p className="lab-empty">这一回合没有留下显著适应的记录。</p>
          ) : [...traces].reverse().map((event) => (
            <article key={event.event_id} className="journal-story">
              <span className="journal-kicker">回合 {event.turn} · 适应记录</span>
              <p className="journal-story-lead">{traitChangeStory(event.payload)}</p>
              <dl className="journal-story-facts">
                <div><dt>可能的压力</dt><dd>{pressureStory(event.payload)}</dd></div>
                <div><dt>付出的代价</dt><dd>{tradeoffStory(event.payload)}</dd></div>
              </dl>
              <details className="journal-details">
                <summary>详细数据</summary>
                <p className="lab-note">适应评分（fitness_gain）是性状变化与局部梯度的线性估计，不是实测存活率。压力记录不能单独证明因果。</p>
                <pre>{JSON.stringify(event.payload, null, 2)}</pre>
              </details>
            </article>
          ))}
          <TraitMorphology species={selectedSpecies} traces={traces} />
          <details className="journal-details">
            <summary>详细数据 · 物种与化石档案</summary>
            <p className="lab-note">关注星标只保存在此浏览器中，按世界和时间线分开，不改变物种或模拟。</p>
            <pre>{JSON.stringify({ species: selectedSpecies, fossil: currentDetail?.fossil }, null, 2)}</pre>
          </details>
        </div>
      )}
    </section>
  );
}

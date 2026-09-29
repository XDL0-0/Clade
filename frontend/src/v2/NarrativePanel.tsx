import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { API, request } from "./api";
import { ErrorNotice } from "./Forms";
import { useNarratives, type PresentationView } from "./presentationQueries";
import type { NarrativeAnnotation, NarrativeChild, NarrativeResult } from "./presentationTypes";
import "./fieldJournal.css";

const jobLabels = {
  species: "认识一支生灵", adaptation: "生存的新办法",
  speciation: "谱系的新枝", hybridization: "相遇后的新生",
};
const sourceLabels = {
  local_model: "本地 Qwen",
  offline_template: "离线叙述", fallback_template: "备用叙述", unspecified_provider: "来源未标记",
};
function ChildDescription({ child }: { child: NarrativeChild }) {
  return (
    <div className="lab-narrative-child">
      <h4>{child.common_name} <i>{child.latin_name}</i></h4>
      <p>{child.description}</p>
    </div>
  );
}
function ResultFields({ result }: { result: NarrativeResult }) {
  switch (result.job_type) {
    case "species":
      return (
        <>
          <h4>{result.common_name} <i>{result.latin_name}</i></h4>
          <p>{result.description}</p>
          {result.organ_descriptions.length > 0 && (
            <details className="journal-details">
              <summary>再看仔细一些</summary>
              {result.organ_descriptions.map((organ) => <p key={organ.organ_id}>{organ.description}</p>)}
            </details>
          )}
        </>
      );
    case "adaptation":
      return (
        <>
          <p>{result.summary}</p>
          <div className="journal-narrative-cost">
            <strong>生存的代价</strong>
            <p>{result.tradeoff_explanation}</p>
          </div>
        </>
      );
    case "speciation":
      return (
        <>
          <p>{result.explanation}</p>
          {result.children.map((child) => <ChildDescription key={child.child_id} child={child} />)}
        </>
      );
    case "hybridization":
      return (
        <>
          <ChildDescription child={result.child} />
          <p>{result.parent_narrative}</p>
        </>
      );
  }
}
function Annotation({ annotation }: { annotation: NarrativeAnnotation }) {
  const { result } = annotation;
  const source = annotation.fallback_used === true ? "fallback_template" : annotation.source;
  const providerDetails = [annotation.provider_name, annotation.provider_model].filter(Boolean).join(" · ");
  return (
    <article className="lab-narrative-entry journal-narrative-entry">
      <div className="lab-narrative-labels">
        <strong>{jobLabels[result.job_type]}</strong>
        <span title={source === "local_model" ? providerDetails || undefined : undefined}>
          {(source && sourceLabels[source]) || "来源未标记"}
        </span>
      </div>
      <ResultFields result={result} />
      <details className="journal-details">
        <summary>详细数据 · 故事来源{result.source_event_ids.length > 0 ? `（${result.source_event_ids.length} 条事件）` : ""}</summary>
        <p className="lab-note">
          {source === "local_model" ? `这段文字由本地 Qwen 生成${providerDetails ? `（${providerDetails}）` : ""}。`
            : source === "offline_template" ? "这段文字由离线模板整理。"
            : source === "fallback_template" ? "这段文字使用了备用模板（默认文案）。"
              : "记录未标明生成来源。"}
          叙述用于解读历史，不改变物种或地图。
        </p>
        <p className="lab-note">叙事修订 {annotation.narrative_revision} · 任务 {annotation.job_id}</p>
        {result.source_event_ids.length > 0 ? (
          <ul>{result.source_event_ids.map((id) => <li key={id}><code>{id}</code></li>)}</ul>
        ) : <p className="lab-note">没有附带来源事件。</p>}
        <pre>{JSON.stringify(result, null, 2)}</pre>
      </details>
    </article>
  );
}
export function NarrativePanel({ species, ...view }: PresentationView & { species: string }) {
  const [filter, setFilter] = useState<"selected" | "all">("selected");
  const provider = useQuery({
    queryKey: ["v2", "narrative-provider"],
    queryFn: ({ signal }) => request<{
      enabled: boolean;
      provider_name: string | null;
      provider_model: string | null;
    }>(`${API}/narrative-provider`, undefined, signal),
    retry: false,
    staleTime: 60_000,
  });
  const query = useNarratives(view, filter === "selected" && species ? species : null);
  const pages = query.data?.pages ?? [];
  const groups = pages.flatMap((page) => page.items);
  const count = groups.reduce((total, group) => total + group.annotations.length, 0);
  return (
    <section className="lab-card lab-narratives journal-narratives" aria-label="生灵故事">
      <div className="lab-section-head">
        <h2>生灵故事 <span className="lab-muted">{count} 篇</span></h2>
        <button disabled={query.isFetching} onClick={() => void query.refetch()}>重读故事</button>
      </div>
      <p className="lab-note" title={provider.data?.provider_name ?? undefined}>
        {provider.isError ? "暂时无法读取叙事模型配置。"
          : provider.isPending ? "正在读取叙事模型配置…"
            : provider.data.enabled
              ? `本地 Qwen${provider.data.provider_model ? ` · ${provider.data.provider_model}` : ""}`
              : "叙事模型未启用"}
      </p>
      <label>
        阅读谁的故事
        <select value={filter} onChange={(event) => setFilter(event.target.value as "selected" | "all")}>
          <option value="selected">当前物种 {species || "未选择"}</option>
          <option value="all">所有生灵</option>
        </select>
      </label>
      <p className="lab-note">读到回合 {view.snapshot.turn}。只翻阅这段历史中已留下的故事。</p>
      <ErrorNotice error={query.error} />
      {query.isPending ? <p role="status">正在翻开故事…</p> : groups.length === 0 && (
        <p className="lab-empty">已翻阅的历史中还没有故事。你仍可以在物种观察与世界纪事中看到它们的经历。</p>
      )}
      {groups.map((group) => (
        <section
          key={`${group.version.timeline_id}:${group.version.generation}:${group.version.revision}`}
          className="lab-narrative-group"
          aria-label={`回合 ${group.turn} 的故事`}
        >
          <h3>回合 {group.turn} <span className="lab-muted">{group.annotations.length} 篇</span></h3>
          {group.annotations.map((annotation) => (
            <Annotation key={`${annotation.job_id}:${annotation.narrative_revision}`} annotation={annotation} />
          ))}
          <details className="journal-details">
            <summary>详细数据 · 历史位置</summary>
            <p className="lab-note">时间线 {group.version.timeline_id} · 历史段 {group.version.generation} · 版本 {group.version.revision}</p>
          </details>
        </section>
      ))}
      {query.isFetching && !query.isPending && <p role="status">正在更新故事…</p>}
      {query.hasNextPage && (
        <button disabled={query.isFetching} onClick={() => void query.fetchNextPage()}>翻阅更早的故事</button>
      )}
      {query.data && (
        <details className="journal-details">
          <summary>详细数据 · 阅读进度</summary>
          <p className="lab-note">
            已检查 {pages.reduce((total, page) => total + page.scanned, 0)} 个历史提交；
            {query.hasNextPage ? "尚有更早历史未加载。" : "已到达当前历史末端。"}
            分页保留完整回合记录，回退后已丢弃的未来不在这段历史中。
          </p>
        </details>
      )}
    </section>
  );
}

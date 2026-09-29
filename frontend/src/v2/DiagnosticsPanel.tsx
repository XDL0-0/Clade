import { ErrorNotice } from "./Forms";
import { formatValue } from "./format";
import { useDiagnostics, type PresentationView } from "./presentationQueries";

function memory(value: number | null) {
  return value === null ? "未测量" : `${(value / 1024 ** 2).toFixed(2)} MiB`;
}
const statuses: Record<string, string> = {
  QUEUED: "待执行",
  RUNNING: "执行中",
  VALIDATING: "校验中",
  READY: "待应用",
  APPLIED: "已应用",
  RETRY_WAIT: "等待重试",
  FAILED: "失败",
  STALE: "版本过期",
  CANCELLED: "已取消",
};
export function DiagnosticsPanel(view: PresentationView) {
  const query = useDiagnostics(view);
  const data = query.data;
  return (
    <section className="lab-card lab-diagnostics" aria-label="运行诊断">
      <div className="lab-section-head">
        <h2>运行诊断</h2>
        <button disabled={query.isFetching} onClick={() => void query.refetch()}>
          刷新诊断
        </button>
      </div>
      <p className="lab-note">内存和磁盘为查询时的服务状态；物种与任务计数对应所选快照。</p>
      <ErrorNotice error={query.error} />
      {query.isPending ? (
        <p role="status">正在读取诊断…</p>
      ) : (
        data && (
          <>
            <dl className="lab-facts">
              <div>
                <dt>CPU RSS · 服务进程</dt>
                <dd>{memory(data.rss_bytes)}</dd>
              </div>
              <div>
                <dt>GPU 显存</dt>
                <dd>{memory(data.gpu_memory_bytes)}</dd>
              </div>
              <div>
                <dt>存档 · 整个 store 目录</dt>
                <dd>
                  {data.save_bytes_approximate ? "约 " : ""}
                  {memory(data.save_bytes)}
                </dd>
              </div>
              <div>
                <dt>存活 / 历史物种</dt>
                <dd>
                  {data.active_species_count} / {data.species_count}
                </dd>
              </div>
              <div>
                <dt>物种容量</dt>
                <dd>{data.species_capacity}</dd>
              </div>
              <div>
                <dt>有个体的物种–地块记录</dt>
                <dd>{data.population_habitat_records}</dd>
              </div>
              <div>
                <dt>AI token 用量</dt>
                <dd>{formatValue(data.ai_token_usage)}</dd>
              </div>
            </dl>
            <p className="lab-note">
              CPU RSS 包含整个 API 服务进程；存档大小包含共享 store
              的全部世界，不是当前物种或当前世界独占用量。
            </p>
            {data.save_scan_skipped > 0 && (
              <p className="lab-note">
                扫描跳过 {data.save_scan_skipped} 个不可读目录或文件，大小可能低估。
              </p>
            )}
            {data.gpu_memory_bytes === null && (
              <p className="lab-note">GPU 未测量：{data.gpu_note}</p>
            )}
            {data.ai_token_usage === null && (
              <p className="lab-note">Token 用量未知：{data.ai_token_usage_note}</p>
            )}
            <h3>所选完整版本的 AI 任务</h3>
            <dl className="lab-facts">
              {Object.entries(data.ai_jobs).map(([status, count]) => (
                <div key={status}>
                  <dt>{statuses[status] ?? status}</dt>
                  <dd>{count}</dd>
                </div>
              ))}
            </dl>
            <p className="lab-note">
              回合 {data.turn} · {data.version.timeline_id} / g{data.version.generation} / r
              {data.version.revision}
              <br />
              观测时间 <time dateTime={data.observed_at}>{data.observed_at}</time>
            </p>
          </>
        )
      )}
    </section>
  );
}

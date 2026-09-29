import { useState } from "react";
import { formatValue } from "./format";
import { traitDisplay } from "./traitDisplay";
import type { Json, Scope, Values } from "./types";
import type { ScenarioBranchProgress, ScenarioProgress } from "./scenarioTypes";

const statuses = {
  pending: "还未开始",
  partial: "演化未完成",
  completed: "已完成",
  failed: "暂时停下了",
  conflict: "分支已有新变化",
};
function record(value: Json | undefined): Values {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}
function difference(a: Json | undefined, b: Json | undefined) {
  return typeof a === "number" && typeof b === "number" ? formatValue(b - a) : "未提供";
}
function verifiedSummary(
  branch: ScenarioBranchProgress | undefined,
  result: ScenarioProgress | null
) {
  const original = result?.branches.find((item) => item.branch_id === branch?.branch_id);
  return branch?.status === "completed" &&
    original?.status === "completed" &&
    branch.state_hash === original.state_hash &&
    branch.version?.generation === original.version?.generation &&
    branch.version?.revision === original.version?.revision
    ? original.summary
    : undefined;
}
function TraitComparison({ control, branch }: { control: Values; branch: Values }) {
  const [trait, setTrait] = useState("armor");
  const first = record(control.trait_distribution),
    second = record(branch.trait_distribution);
  const left = record(record(first.weighted_counts)[trait]),
    right = record(record(second.weighted_counts)[trait]);
  const basis = (value: Json | undefined) =>
    value === "population_weighted_deme_means"
      ? "deme 均值按人口加权"
      : value === "species_means"
        ? "物种均值按人口加权"
        : "未提供";
  return (
    <section className="lab-scenario-traits">
      <h4>末回合性状分布</h4>
      <p className="lab-note">
        对照：{basis(first.basis)}；实验：{basis(second.basis)}。分箱统计不代表逐个体基因型。
      </p>
      <label>
        比较性状
        <select value={trait} onChange={(e) => setTrait(e.target.value)}>
          {traitDisplay.map((item) => (
            <option key={item.key} value={item.key}>
              {item.label}
            </option>
          ))}
        </select>
      </label>
      {!Object.keys(left).length || !Object.keys(right).length ? (
        <p>此结果未提供可比较的分布。</p>
      ) : (
        <div className="lab-table-wrap">
          <table>
            <caption>性状分箱的人口权重</caption>
            <thead>
              <tr>
                <th>区间</th>
                <th>对照</th>
                <th>实验</th>
                <th>实验 − 对照</th>
              </tr>
            </thead>
            <tbody>
              {Array.from({ length: 10 }, (_, i) => (
                <tr key={i}>
                  <th>
                    [{(i / 10).toFixed(1)}, {((i + 1) / 10).toFixed(1)}
                    {i === 9 ? "]" : ")"}
                  </th>
                  <td>{formatValue(left[String(i)])}</td>
                  <td>{formatValue(right[String(i)])}</td>
                  <td>{difference(left[String(i)], right[String(i)])}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="lab-note">
        人口加权均值：对照 {formatValue(record(control.population_weighted_traits)[trait])}，实验{" "}
        {formatValue(record(branch.population_weighted_traits)[trait])}。
      </p>
    </section>
  );
}
export function ScenarioResults({
  progress,
  result,
  disabled,
  onOpen,
}: {
  progress: ScenarioProgress;
  result: ScenarioProgress | null;
  disabled: boolean;
  onOpen: (scope: Scope) => void;
}) {
  const plan = progress.manifest.plan;
  const [relative, setRelative] = useState(plan.turns);
  const [branchId, setBranchId] = useState(
    plan.branches.find((item) => item.id !== plan.control)?.id ?? ""
  );
  const control = progress.branches.find((item) => item.branch_id === plan.control);
  const branch = progress.branches.find((item) => item.branch_id === branchId);
  const complete = progress.branches.every((item) => item.status === "completed");
  const comparable = control?.status === "completed" && branch?.status === "completed";
  const a = control?.observations?.find((point) => point.relative_turn === relative),
    b = branch?.observations?.find((point) => point.relative_turn === relative);
  const first = verifiedSummary(control, result),
    second = verifiedSummary(branch, result);
  const rows = [
    ["存活物种", "species_richness"],
    ["总人口", "total_population"],
    ["总生物量", "total_biomass"],
    ["Shannon 多样性", "shannon_diversity"],
    ["Simpson 多样性", "simpson_diversity"],
    ["平均营养级", "mean_trophic_level"],
    ["食物网连接度", "food_web_connectivity"],
  ];
  const name = (id: string) => plan.branches.find((item) => item.id === id)?.name ?? id;
  return (
    <section className="lab-scenario-results" aria-label="平行世界结果">
      <h3>
        {plan.name} · {complete ? "演化完成" : "演化中途记录"}
      </h3>
      {!complete && (
        <p role="status" className="lab-history-note">
          有世界还没走完。下方只显示已经发生的回合，双方完成后再比较差异。
        </p>
      )}
      <ul className="lab-scenario-status">
        {progress.branches.map((item) => {
          const lastError =
            item.error ??
            (item.status !== "completed"
              ? result?.branches.find((old) => old.branch_id === item.branch_id)?.error
              : null);
          return (
            <li key={item.branch_id}>
              <div>
                <strong>{name(item.branch_id)}</strong>
                <span>{statuses[item.status]} · {item.completed_turns ?? "—"} / {item.target_turns} 回合</span>
              </div>
              <button
                disabled={disabled || item.status === "pending" || (item.status === "failed" && !item.version)}
                onClick={() => onOpen({ world: plan.source.world_id, timeline: item.timeline_id })}
              >
                进入 {name(item.branch_id)}
              </button>
              {lastError && (
                <p role="alert" className="lab-scenario-branch-error">
                  {item.error ? "" : "上次停下的原因："}
                  {typeof lastError === "string" ? lastError : formatValue(lastError)}
                </p>
              )}
              {item.status === "conflict" && (
                <p className="lab-note lab-scenario-branch-error">
                  这个分支已有其他演化记录。可以进入查看；本次不会覆盖它。
                </p>
              )}
            </li>
          );
        })}
      </ul>
      <div className="lab-fields">
        <label>
          看哪个世界
          <select value={branchId} onChange={(e) => setBranchId(e.target.value)}>
            {plan.branches
              .filter((item) => item.id !== plan.control)
              .map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
          </select>
        </label>
        <label>
          出发后的第 {relative} 回合
          <input
            type="range"
            min={1}
            max={plan.turns}
            step={1}
            value={relative}
            onChange={(e) => {
              const value = e.target.valueAsNumber;
              if (Number.isInteger(value) && value >= 1 && value <= plan.turns) setRelative(value);
            }}
          />
        </label>
      </div>
      <div className="lab-scenario-highlights">
        {rows.slice(0, 3).map(([label, key]) => (
          <div key={key}>
            <small>{label}</small>
            <strong>{formatValue(b?.metrics[key])}</strong>
            <span>{name(plan.control)}：{formatValue(a?.metrics[key])}</span>
            {comparable && a && b && <span>差异 {difference(a.metrics[key], b.metrics[key])}</span>}
          </div>
        ))}
      </div>
      {(!a || !b) && <p className="lab-note">这一回合还没有双方的记录，可以往前看看。</p>}
      <details className="lab-scenario-advanced">
        <summary>高级数据：多样性、食物网与性状分布</summary>
        <p className="lab-note">
          计划 {progress.manifest_hash} · 随机序列 {plan.rng_namespace}
        </p>
      <div className="lab-table-wrap">
        <table>
          <caption>
            出发后第 {relative} 回合；世界回合 {a?.turn ?? "—"} / {b?.turn ?? "—"}
          </caption>
          <thead>
            <tr>
              <th>指标</th>
              <th>{name(plan.control)}</th>
              <th>{name(branchId)}</th>
              <th>实验 − 对照</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([label, key]) => (
              <tr key={key}>
                <th>{label}</th>
                <td>{formatValue(a?.metrics[key])}</td>
                <td>{formatValue(b?.metrics[key])}</td>
                <td>
                  {comparable && a && b
                    ? difference(a.metrics[key], b.metrics[key])
                    : "未完成或缺观测"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {relative === plan.turns && first && second && comparable ? (
        <>
          <TraitComparison control={first} branch={second} />
          <details>
            <summary>末回合营养角色差异（真实人口与结构生物量）</summary>
            <div className="lab-table-wrap">
              <table>
                <caption>营养角色：实验 − 对照</caption>
                <thead>
                  <tr>
                    <th>角色 / 指标</th>
                    <th>对照</th>
                    <th>实验</th>
                    <th>差值</th>
                  </tr>
                </thead>
                <tbody>
                  {[
                    ...new Set([
                      ...Object.keys(record(first.population_by_role)),
                      ...Object.keys(record(second.population_by_role)),
                    ]),
                  ].flatMap((role) =>
                    (
                      [
                        ["population", "人口"],
                        ["structural_biomass", "结构生物量"],
                        ["species", "物种数"],
                      ] as const
                    ).map(([metric, label]) => {
                      const left = record(record(first.population_by_role)[role])[metric],
                        right = record(record(second.population_by_role)[role])[metric];
                      return (
                        <tr key={`${role}:${metric}`}>
                          <th>
                            {role} / {label}
                          </th>
                          <td>{formatValue(left)}</td>
                          <td>{formatValue(right)}</td>
                          <td>{difference(left, right)}</td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>
          </details>
        </>
      ) : (
        <p className="lab-note">
          完整性状分布会在双方演化结束后显示。旧记录可通过“载入详细结果”读取。
        </p>
      )}
      </details>
    </section>
  );
}

import { forcingFields } from "./scenarioModel";
import type { PlanDraft, PointDraft } from "./scenarioDraft";
export function ScenarioPlanForm({
  value,
  onChange,
  disabled,
}: {
  value: PlanDraft;
  onChange: (draft: PlanDraft) => void;
  disabled: boolean;
}) {
  const branchChange = (index: number, patch: Partial<PlanDraft["branches"][number]>) =>
    onChange({
      ...value,
      branches: value.branches.map((branch, i) => (i === index ? { ...branch, ...patch } : branch)),
    });
  const pointsChange = (index: number, forcing: PointDraft[]) =>
    branchChange(index, { scenario: { ...value.branches[index].scenario, forcing } });
  return (
    <fieldset disabled={disabled} className="lab-scenario-fields">
      <div className="lab-fields">
        <label>
          实验 ID
          <input
            required
            maxLength={64}
            value={value.id}
            onChange={(e) => onChange({ ...value, id: e.target.value })}
          />
        </label>
        <label>
          旅程名称
          <input
            required
            maxLength={120}
            value={value.name}
            onChange={(e) => onChange({ ...value, name: e.target.value })}
          />
        </label>
      </div>
      <label>
        共享 RNG namespace
        <input
          required
          maxLength={128}
          value={value.rng_namespace}
          onChange={(e) => onChange({ ...value, rng_namespace: e.target.value })}
        />
      </label>
      <label>
        旅程描述
        <textarea
          maxLength={2000}
          value={value.description ?? ""}
          onChange={(e) => onChange({ ...value, description: e.target.value })}
        />
      </label>
      <div className="lab-fields">
        <label>
          相对回合数
          <input
            required
            type="number"
            min={1}
            max={1000}
            step={1}
            value={value.turns}
            onChange={(e) => onChange({ ...value, turns: e.target.value })}
          />
        </label>
        <label>
          原环境分支（不添加变化）
          <select
            value={value.control}
            onChange={(e) => onChange({ ...value, control: e.target.value })}
          >
            {value.branches.map((branch, i) => (
              <option key={i} value={branch.id} disabled={branch.scenario.forcing.length > 0}>
                {branch.id}
              </option>
            ))}
          </select>
        </label>
      </div>
      {value.branches.map((branch, index) => (
        <details key={index} className="lab-scenario-branch" open>
          <summary>
            {branch.name || branch.id || `分支 ${index + 1}`}{" "}
            {branch.id === value.control ? "· 原环境" : ""}
          </summary>
          <div className="lab-fields">
            <label>
              分支 {index + 1} ID
              <input
                required
                maxLength={64}
                value={branch.id}
                onChange={(e) => {
                  const id = e.target.value;
                  onChange({
                    ...value,
                    control: branch.id === value.control ? id : value.control,
                    branches: value.branches.map((item, i) =>
                      i === index ? { ...item, id } : item
                    ),
                  });
                }}
              />
            </label>
            <label>
              分支 {index + 1} 名称
              <input
                required
                maxLength={120}
                value={branch.name}
                onChange={(e) => branchChange(index, { name: e.target.value })}
              />
            </label>
          </div>
          <p className="lab-note">
            情景 {branch.scenario.id} · {branch.scenario.name}；高级名称和描述可通过 JSON 编辑。
          </p>
          {branch.id === value.control ? (
            <p className="lab-note">对照保持空 forcing；只继承源环境，每回合灾害/疾病压力归零。</p>
          ) : (
            <>
              {branch.scenario.forcing.map((point, p) => (
                <div className="lab-forcing-row" key={p}>
                  <label>
                    {branch.id} forcing {p + 1} 相对回合
                    <input
                      required
                      type="number"
                      min={1}
                      max={value.turns || 1000}
                      step={1}
                      value={point.turn}
                      onChange={(e) =>
                        pointsChange(
                          index,
                          branch.scenario.forcing.map((old, j) =>
                            j === p ? { ...old, turn: e.target.value } : old
                          )
                        )
                      }
                    />
                  </label>
                  <div className="lab-fields">
                    {forcingFields.map((field) => (
                      <label key={field.key}>
                        {branch.id} #{p + 1} {field.label}
                        <input
                          type="number"
                          min={field.min}
                          max={field.max}
                          step="any"
                          placeholder="省略"
                          value={point[field.key]}
                          onChange={(e) =>
                            pointsChange(
                              index,
                              branch.scenario.forcing.map((old, j) =>
                                j === p ? { ...old, [field.key]: e.target.value } : old
                              )
                            )
                          }
                        />
                      </label>
                    ))}
                  </div>
                  <button
                    type="button"
                    onClick={() =>
                      pointsChange(
                        index,
                        branch.scenario.forcing.filter((_, j) => j !== p)
                      )
                    }
                  >
                    删除 {branch.id} forcing {p + 1}
                  </button>
                </div>
              ))}
              <button
                type="button"
                disabled={branch.scenario.forcing.length >= Math.min(1000, Number(value.turns))}
                onClick={() => {
                  let next = 1;
                  while (branch.scenario.forcing.some((point) => Number(point.turn) === next))
                    next++;
                  pointsChange(index, [
                    ...branch.scenario.forcing,
                    {
                      turn: String(next),
                      warming_offset: "",
                      co2_ppm: "",
                      disaster_severity: "",
                      disease_pressure: "",
                    },
                  ]);
                }}
              >
                添加 {branch.id} forcing
              </button>
            </>
          )}
          <button
            type="button"
            disabled={branch.id === value.control || value.branches.length <= 2}
            onClick={() =>
              onChange({ ...value, branches: value.branches.filter((_, i) => i !== index) })
            }
          >
            删除分支 {index + 1}
          </button>
        </details>
      ))}
      <button
        type="button"
        disabled={value.branches.length >= 8}
        onClick={() => {
          let index = value.branches.length + 1;
          while (value.branches.some((branch) => branch.id === `branch-${index}`)) index++;
          onChange({
            ...value,
            branches: [
              ...value.branches,
              {
                id: `branch-${index}`,
                name: `实验分支 ${index}`,
                scenario: {
                  version: 1,
                  id: `scenario-${index}`,
                  name: `情景 ${index}`,
                  forcing: [],
                },
              },
            ],
          });
        }}
      >
        添加分支（最多8个）
      </button>
    </fieldset>
  );
}

import { useId } from "react";
import { traitDisplay } from "./traitDisplay";
import { record } from "./chronicleCopy";
import type { Species, WorldEvent } from "./types";
import "./fieldJournal.css";

export function TraitMorphology({
  species,
  traces = [],
}: {
  species: Species;
  traces?: WorldEvent[];
}) {
  const identity = useId();
  const traits = species.traits;
  const valid = traitDisplay.every(
    ({ key }) => Number.isFinite(traits[key]) && traits[key] >= 0 && traits[key] <= 1
  );
  const changes = record(traces.filter((event) => event.payload.trait_changes).at(-1)?.payload.trait_changes);
  const strongest = traitDisplay.filter(({ key }) => Number.isFinite(traits[key]) && traits[key] > 0)
    .sort((left, right) => traits[right.key] - traits[left.key]).slice(0, 2);
  const description =
    "抽象性状编码，不代表真实解剖结构或 AI 生成形态。护甲对应外壳线宽，速度对应附肢长度，攻击对应前端尖角，合作对应伴随圆点，毒素对应紫色斑点，解毒对应外环线宽，环境改造对应底部方块高度。数值越大，对应符号越大。";
  return (
    <section className="lab-morphology journal-morphology" aria-label="生存本领与性状图示">
      <h3>生存本领</h3>
      <p className="lab-note">
        {valid && strongest.length ? `当前较突出的性状：${strongest.map(({ label }) => label).join("、")}。` : "观察它在这个回合的性状。"}
        图示随性状数值变化。
      </p>
      {valid ? (
        <svg
          className="lab-morphology-svg"
          viewBox="0 0 320 210"
          role="img"
          aria-labelledby={`${identity}-title`}
          aria-describedby={`${identity}-description`}
        >
          <title id={`${identity}-title`}>{species.species_id} 的性状形态示意</title>
          <desc id={`${identity}-description`}>{description}</desc>
          <ellipse
            cx="159"
            cy="92"
            rx="92"
            ry="63"
            fill="none"
            stroke="#8cbdbc"
            strokeWidth={1 + 6 * traits.detox}
            strokeDasharray="4 5"
            data-trait="detox"
          />
          {[-1, 1].flatMap((direction) =>
            [115, 145, 175].map((x) => (
              <path
                key={`${direction}:${x}`}
                d={`M ${x} ${92 + direction * 24} l ${direction * 8} ${direction * (12 + 21 * traits.speed)}`}
                stroke="#4c7770"
                strokeWidth="5"
                strokeLinecap="round"
                data-trait="speed"
              />
            ))
          )}
          <path
            d={`M 216 79 L ${226 + 30 * traits.attack} 93 L 216 106`}
            fill="#dda679"
            stroke="#896042"
            strokeWidth="2"
            data-trait="attack"
          />
          <ellipse
            cx="157"
            cy="92"
            rx="66"
            ry="34"
            fill="#d6e4d4"
            stroke="#486b57"
            strokeWidth={1 + 9 * traits.armor}
            data-trait="armor"
          />
          {[128, 154, 180].map((x) => (
            <circle
              key={x}
              cx={x}
              cy="91"
              r={1 + 7 * traits.toxin}
              fill="#9773aa"
              data-trait="toxin"
            />
          ))}
          {[
            [-1, 44, 67],
            [1, 276, 128],
          ].map(([direction, x, y]) => (
            <g key={direction}>
              <line
                x1={x}
                y1={y}
                x2={x + (direction < 0 ? 30 : -30)}
                y2="93"
                stroke="#789688"
                strokeDasharray="3 4"
              />
              <circle
                cx={x}
                cy={y}
                r={2 + 9 * traits.cooperation}
                fill="#9fb79b"
                data-trait="cooperation"
              />
            </g>
          ))}
          {[122, 149, 176].map((x) => (
            <rect
              key={x}
              x={x}
              y={184 - 20 * traits.engineering}
              width="20"
              height={2 + 20 * traits.engineering}
              rx="2"
              fill="#c9a879"
              data-trait="engineering"
            />
          ))}
          <text x="160" y="205" textAnchor="middle" fontSize="11" fill="#63756c">
            生存本领的符号图示
          </text>
        </svg>
      ) : (
        <p className="lab-empty">当前性状数据不完整，暂时无法绘制。</p>
      )}
      {valid && (
        <div className="journal-traits" aria-label="当前性状强度">
          {traitDisplay.map(({ key, label }) => (
            <div key={key} className="journal-trait">
              <span>{label}</span>
              <meter min="0" max="1" value={traits[key]} aria-label={`${label} ${traits[key].toFixed(3)}`} />
            </div>
          ))}
        </div>
      )}
      <details className="journal-details">
        <summary>详细数据 · 性状与变化</summary>
      <div className="lab-table-wrap">
        <table className="lab-trait-table">
          <caption>当前性状与这一回合最后一条适应记录的变化量</caption>
          <thead>
            <tr>
              <th>性状 / 符号</th>
              <th>当前值</th>
              <th>变化</th>
            </tr>
          </thead>
          <tbody>
            {traitDisplay.map(({ key, label, symbol }) => {
              const value = traits[key],
                delta = changes?.[key];
              return (
                <tr key={key}>
                  <th>
                    {label}
                    <small>{symbol}</small>
                  </th>
                  <td>{Number.isFinite(value) ? value.toFixed(3) : "未提供"}</td>
                  <td>
                    {typeof delta === "number" && Number.isFinite(delta)
                      ? `${delta > 0 ? "+" : ""}${delta.toFixed(3)}`
                      : "未提供"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="lab-note">
        七种性状均为 0–1；符号按固定线性比例绘制，不代表真实解剖结构。没有适应记录时不推断变化量。
      </p>
      </details>
    </section>
  );
}

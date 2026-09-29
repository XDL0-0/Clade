import type { TurnReport } from "@/services/api.types";
import "./TurnHighlights.css";

interface Props {
  report: TurnReport;
  previousReport: TurnReport | null;
  onSelectSpecies?: (code: string) => void;
}

function isCount(value: number | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}

function formatCount(value: number): string {
  return value.toLocaleString("zh-CN", { maximumFractionDigits: 0 });
}

export function TurnHighlights({ report, previousReport, onSelectSpecies }: Props) {
  const consecutivePrevious = previousReport?.turn_index === report.turn_index - 1
    ? previousReport
    : null;
  const previousByCode = new Map(consecutivePrevious?.species.map(species => [species.lineage_code, species]) ?? []);
  const currentByCode = new Map(report.species.map(species => [species.lineage_code, species]));
  const highlights = Array.from(currentByCode.values()).map(species => {
    const previousPopulation = previousByCode.get(species.lineage_code)?.population;
    // Older saves use zero for a missing initial population.
    const initialPopulation = isCount(species.initial_population) && species.initial_population > 0
      ? species.initial_population
      : isCount(previousPopulation) ? previousPopulation
      : species.initial_population === 0 && species.population === 0 ? 0 : null;
    const change = initialPopulation !== null && isCount(species.population)
      ? species.population - initialPopulation
      : null;
    const noRefuge = species.status === "alive" && species.has_refuge === false && isCount(species.critical_tiles) && species.critical_tiles > 0;
    const highMortality = species.status === "alive" && isCount(species.death_rate) && species.death_rate >= 0.5;
    return { species, initialPopulation, change, noRefuge, highMortality, risk: Number(noRefuge) + Number(highMortality) };
  }).filter(item => (item.change !== null && item.change !== 0) || item.risk > 0)
    .sort((a, b) => b.risk - a.risk || Math.abs(b.change ?? 0) - Math.abs(a.change ?? 0) || a.species.lineage_code.localeCompare(b.species.lineage_code))
    .slice(0, 3);

  return (
    <section className="turn-highlights" aria-labelledby="turn-highlights-title">
      <h3 id="turn-highlights-title">本回合值得关注</h3>
      <p className="turn-highlights-note">优先列出无避难所且有危机地块、死亡率至少 50% 的存活物种，其余按种群数量变化排序。</p>
      {highlights.length === 0 ? (
        <p className="turn-highlights-empty">当前记录没有可确认的数量变化或上述风险信号。缺少期初数量时，不推算增减。</p>
      ) : (
        <div className="turn-highlights-list">
          {highlights.map(({ species, initialPopulation, change, noRefuge, highMortality }) => (
            <article className="turn-highlight" key={species.lineage_code}>
              <div className="turn-highlight-heading">
                <div>
                  <h4>{species.common_name || species.latin_name || species.lineage_code}</h4>
                  <span className="turn-highlight-code">{species.lineage_code}</span>
                </div>
                {onSelectSpecies && (
                  <button type="button" onClick={() => onSelectSpecies(species.lineage_code)}>查看物种</button>
                )}
              </div>
              <div className={`turn-highlight-change ${change !== null && change < 0 ? "declining" : change !== null && change > 0 ? "growing" : ""}`}>
                {change === null ? "缺少可靠期初数量，暂不计算变化" : `种群 ${change > 0 ? "+" : ""}${formatCount(change)} 个体`}
              </div>
              <p className="turn-highlight-population">
                {initialPopulation !== null && `期初 ${formatCount(initialPopulation)} → `}
                {isCount(species.population) ? `期末 ${formatCount(species.population)}` : "期末数量未记录"}
                {species.status === "extinct" && " · 已灭绝"}
              </p>
              <div className="turn-highlight-details">
                {isCount(species.births) && <span>出生 {formatCount(species.births)}</span>}
                {isCount(species.deaths) && <span>死亡 {formatCount(species.deaths)}</span>}
                {isCount(species.resource_pressure) && <span>资源压力 {(species.resource_pressure * 100).toFixed(0)}%</span>}
                {isCount(species.predation_pressure) && <span>捕食压力 {(species.predation_pressure * 100).toFixed(0)}%</span>}
              </div>
              {(noRefuge || highMortality) && (
                <p className="turn-highlight-risk">
                  {noRefuge && `无避难所 · ${formatCount(species.critical_tiles ?? 0)} 个危机地块`}
                  {noRefuge && highMortality && "；"}
                  {highMortality && `死亡率 ${(species.death_rate * 100).toFixed(1)}%`}
                </p>
              )}
            </article>
          ))}
        </div>
      )}
      <p className="turn-highlights-note">变化优先依据本回合期初数量，缺失时仅对照连续的上一回合。压力是独立指标，不代表死亡人数或已证实的死因。</p>
    </section>
  );
}

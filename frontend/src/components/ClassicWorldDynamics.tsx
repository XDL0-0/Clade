import type { TurnReport } from "@/services/api.types";
import "./ClassicWorldDynamics.css";

const relationNames: Record<string, string> = {
  pollination: "传粉", seed_dispersal: "种子散布", mutualism: "互利共生",
  cleaning: "清洁共生", nutrient_exchange: "养分交换", explicit_mutualism: "共生依赖",
};
const signed = (value: number, digits = 1) => `${value > 0 ? "+" : ""}${value.toFixed(digits)}`;

export function ClassicWorldDynamics({ report, onSelectSpecies }: {
  report: TurnReport;
  onSelectSpecies?: (code: string) => void;
}) {
  const data = report.world_dynamics;
  if (!data) return null;
  const { climate, geology } = data;
  const names = new Map(report.species.map(species => [species.lineage_code, species.common_name]));
  const speciesLink = (code: string) => onSelectSpecies
    ? <button type="button" onClick={() => onSelectSpecies(code)}>{names.get(code) || code}</button>
    : <span>{names.get(code) || code}</span>;

  return <section className="classic-world" aria-label="世界正在变化">
    <h3>世界正在变化</h3>
    <div className="classic-world-grid">
      {geology && <article>
        <h4>🌋 大陆与山脉 · {geology.phase}</h4>
        <p>{geology.plate_count} 个板块 · {geology.changed_tiles} 个地块改变海拔</p>
        <dl>
          <div><dt>隆起 / 沉降</dt><dd>{geology.uplift_tiles} / {geology.subsidence_tiles} 格</dd></div>
          <div><dt>最大隆起</dt><dd>{geology.max_uplift_m.toFixed(1)} 米</dd></div>
          <div><dt>火山 / 地震</dt><dd>{geology.eruptions} / {geology.earthquakes} 次</dd></div>
          <div><dt>地理隔离 / 再次接触</dt><dd>{geology.isolated_species} 种 / {geology.contacts} 对</dd></div>
        </dl>
        <p className="classic-world-hint">海陆与山脉变化会改变栖息地和迁徙路线。</p>
      </article>}
      {climate && <article>
        <h4>🌡️ 气候 · {climate.phase}</h4>
        <dl>
          <div><dt>全球均温</dt><dd>{climate.temperature.toFixed(1)}°C（{signed(climate.temperature_delta)}）</dd></div>
          <div><dt>海平面</dt><dd>{climate.sea_level.toFixed(1)} 米（{signed(climate.sea_level_delta)}）</dd></div>
          {climate.co2_ppm != null && <div><dt>大气 CO₂</dt><dd>{climate.co2_ppm.toFixed(0)} ppm</dd></div>}
          {climate.ice_fraction != null && <div><dt>冰盖强度</dt><dd>{(climate.ice_fraction * 100).toFixed(1)}%</dd></div>}
        </dl>
        <p className="classic-world-hint">{climate.summary}</p>
      </article>}
    </div>
    <article className="classic-world-network">
      <h4>🤝 共生伙伴 · {data.mutualism_link_count} 条关系</h4>
      {data.mutualism_links.length ? <ul>
        {data.mutualism_links.map(link => <li key={`${link.species_a}:${link.species_b}:${link.relationship_type}`}>
          <div>{speciesLink(link.species_a)} <span>↔</span> {speciesLink(link.species_b)}</div>
          <span>{relationNames[link.relationship_type] || link.relationship_type} · 活跃度 {(link.strength * 100).toFixed(0)}%</span>
          {link.description && <small>{link.description}</small>}
        </li>)}
      </ul> : <p>还没有活跃的共生关系。拥有相应能力的物种需要生活在同一片栖息地。</p>}
      {data.mutualism_link_count > data.mutualism_links.length && <p>显示最活跃的 {data.mutualism_links.length} 条关系。</p>}
      {data.seeds_dispersed > 0 && <p>动物帮助 {data.seeds_dispersed.toLocaleString("zh-CN")} 个植物个体扩散到邻近地块。</p>}
      {data.dependent_species_at_risk.length > 0 && <p className="classic-world-risk">伙伴短缺：{data.dependent_species_at_risk.map(code => names.get(code) || code).join("、")}，繁殖或存活受到影响。</p>}
    </article>
    {data.population_rule && <p className="classic-world-hint">{data.population_rule}</p>}
  </section>;
}

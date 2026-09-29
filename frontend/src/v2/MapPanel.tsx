import { useRef, useState } from "react";
import type { Snapshot, SpeciesDetail } from "./types";
import "./mapPlay.css";

const colors = ["#86b7ce", "#a6d6d7", "#c8d3c1", "#e0c38e", "#a5c876", "#54866a", "#a5aaa5"];
const names = ["海洋", "湖泊", "苔原", "沙漠", "草原", "森林", "高山"];
const layers = [
  { id: "terrain", label: "地形" },
  { id: "population", label: "物种分布" },
  { id: "temperature", label: "温度" },
  { id: "vegetation", label: "植被" },
] as const;
type Layer = (typeof layers)[number]["id"];

function valueAt(values: number[] | undefined, index: number) {
  const value = values?.[index];
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function extent(values: number[] | undefined) {
  const finite = values?.filter(Number.isFinite) ?? [];
  if (!finite.length) return undefined;
  return {
    low: finite.reduce((lowest, value) => Math.min(lowest, value), Infinity),
    high: finite.reduce((highest, value) => Math.max(highest, value), -Infinity),
  };
}

function display(value: number | undefined, digits = 1) {
  return value === undefined
    ? "暂无数据"
    : value.toLocaleString("zh-CN", { maximumFractionDigits: digits });
}

export function MapPanel({ snapshot, detail }: { snapshot: Snapshot; detail?: SpeciesDetail }) {
  const [tile, setTile] = useState(0);
  const [layer, setLayer] = useState<Layer>("terrain");
  const map = useRef<SVGSVGElement>(null);
  const { width, height } = snapshot.geometry;
  const totalTiles = width * height;
  const selected = Math.max(0, Math.min(tile, totalTiles - 1));
  // Historical maps must never borrow another snapshot's distribution.
  const currentDetail = detail?.snapshot_id === snapshot.snapshot_id ? detail : undefined;
  const distribution = currentDetail?.distribution.length === totalTiles
    ? currentDetail.distribution : undefined;
  const populationRange = extent(distribution);
  const temperatureRange = extent(snapshot.map.temperature);
  const vegetationRange = extent(snapshot.map.plant_biomass);
  const peak = Math.max(1, populationRange?.high ?? 0);
  const vegetationPeak = Math.max(0, vegetationRange?.high ?? 0);
  const temperature = valueAt(snapshot.map.temperature, selected);
  const vegetation = valueAt(snapshot.map.plant_biomass, selected);
  const population = valueAt(distribution, selected);
  const elevation = valueAt(snapshot.map.elevation, selected);
  const selectedName = names[snapshot.map.biome[selected]] ?? "未知地形";
  const points = "14,0 7,12 -7,12 -14,0 -7,-12 7,-12";
  const distributionHint = detail
    ? "这个回合的物种分布尚未载入，选择物种后查看。"
    : "先选择一个物种，看看它在世界的哪些地方生活。";
  const descriptions: Record<Layer, string> = {
    terrain: "从森林到海洋，点一块栖息地看看。",
    population: distribution
      ? `${currentDetail?.species.species_id} · 颜色越深，个体越多。`
      : distributionHint,
    temperature: "寻找温暖与寒冷的栖息地。",
    vegetation: "看看哪里可用植被更丰富。",
  };

  function tileColor(index: number, biome: number) {
    if (layer === "terrain") return colors[biome] ?? "#e7ebe4";
    if (layer === "population") {
      const count = valueAt(distribution, index);
      if (count === undefined) return "#e7ebe4";
      return count > 0 ? `hsl(206 58% ${83 - (47 * count) / peak}%)` : "#e8eef0";
    }
    if (layer === "temperature") {
      const value = valueAt(snapshot.map.temperature, index);
      if (value === undefined || !temperatureRange) return "#e7ebe4";
      const span = temperatureRange.high - temperatureRange.low;
      const warmth = span > 0 ? (value - temperatureRange.low) / span : 0.5;
      return `hsl(${220 - 220 * warmth} 64% 65%)`;
    }
    const value = valueAt(snapshot.map.plant_biomass, index);
    if (value === undefined) return "#e7ebe4";
    const abundance = vegetationPeak > 0 ? value / vegetationPeak : 0;
    return `hsl(112 35% ${91 - 57 * abundance}%)`;
  }

  function vegetationDescription() {
    if (vegetation === undefined) return "暂无数据";
    if (vegetation <= 0) return "暂无植被";
    const share = vegetationPeak > 0 ? vegetation / vegetationPeak : 0;
    return share >= 0.65 ? "较丰富" : share >= 0.25 ? "适中" : "较稀少";
  }

  return (
    <section className="lab-card lab-map map-play-card">
      <div className="lab-section-head">
        <div><h2>世界地图</h2><p>{descriptions[layer]}</p></div>
        <span className="lab-chip">第 {snapshot.turn.toLocaleString()} 回合</span>
      </div>
      <div className="map-play-layers" role="group" aria-label="地图图层">
        {layers.map((option) => (
          <button key={option.id} type="button" aria-pressed={layer === option.id}
            onClick={() => setLayer(option.id)}>{option.label}</button>
        ))}
      </div>
      {layer === "population" && !distribution && (
        <div className="map-play-empty" role="status">
          <strong>选择物种，寻找它的家</strong><span>{distributionHint}</span>
        </div>
      )}
      <svg ref={map} className="map-play-board"
        viewBox={`0 0 ${width * 22 + 10} ${height * 26 + 18}`}
        aria-label={`${layers.find((option) => option.id === layer)?.label}地图，使用方向键探索地块`}
        role="group">
        {snapshot.map.biome.map((biome, index) => {
          const count = valueAt(distribution, index);
          const localTemperature = valueAt(snapshot.map.temperature, index);
          const description = `${names[biome] ?? "未知地形"}，地块 ${index}${
            localTemperature === undefined ? "" : `，${display(localTemperature)}摄氏度`
          }${count === undefined ? "" : `，当前物种 ${display(count, 0)} 个体`}`;
          return (
            <g key={index} data-tile={index}
              transform={`translate(${16 + (index % width) * 22},${14 + Math.floor(index / width) * 26 + ((index % width) % 2) * 13})`}
              role="button" tabIndex={selected === index ? 0 : -1}
              aria-pressed={selected === index} aria-label={description}
              onClick={() => setTile(index)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  setTile(index);
                  return;
                }
                const column = index % width;
                const row = Math.floor(index / width);
                let next = index;
                if (event.key === "ArrowLeft") next = row * width + (column + width - 1) % width;
                else if (event.key === "ArrowRight") next = row * width + (column + 1) % width;
                else if (event.key === "ArrowUp") next = Math.max(0, row - 1) * width + column;
                else if (event.key === "ArrowDown") next = Math.min(height - 1, row + 1) * width + column;
                else return;
                event.preventDefault();
                setTile(next);
                map.current?.querySelector<SVGGElement>(`[data-tile="${next}"]`)?.focus();
              }}>
              <title>{description}</title>
              <polygon points={points} fill={tileColor(index, biome)}
                stroke={selected === index ? "#263e32" : "#f8f9f3"}
                strokeWidth={selected === index ? 2.3 : 0.6} />
              {layer === "population" && count !== undefined && count > 0 && width <= 16 && (
                <text textAnchor="middle" dy="3" fontSize="6"
                  fill={count / peak > 0.5 ? "white" : "#153848"}>{count.toLocaleString()}</text>
              )}
            </g>
          );
        })}
      </svg>
      <div className="map-play-legend" aria-label="当前图层图例">
        {layer === "terrain" ? names.map((name, index) => (
          <span className="map-play-legend-item" key={name}>
            <i style={{ background: colors[index] }} />{name}
          </span>
        )) : layer === "population" ? distribution && (
          <>
            <span className="map-play-legend-item"><i style={{ background: "#e8eef0" }} />无个体</span>
            {populationRange && populationRange.high > 0 ? (
              <span className="map-play-scale">较少
                <i className="map-play-gradient map-play-population-gradient" />
                最多 {display(populationRange.high, 0)} 个体 / 地块
              </span>
            ) : <span>这个回合没有该物种的分布。</span>}
          </>
        ) : layer === "temperature" ? temperatureRange ? (
          <span className="map-play-scale">{display(temperatureRange.low)}°C
            <i className="map-play-gradient map-play-temperature-gradient" />
            {display(temperatureRange.high)}°C
          </span>
        ) : <span>暂无温度数据</span> : (
          <span className="map-play-scale">较少
            <i className="map-play-gradient map-play-vegetation-gradient" />较多
            <small>按本回合的植被量比较</small>
          </span>
        )}
      </div>
      <p className="map-play-help">点击地块探索，也可用键盘方向键移动。地图东西相连。</p>
      <div className="map-play-place" aria-live="polite">
        <div className="map-play-place-title">
          <span className="map-play-biome-dot"
            style={{ background: colors[snapshot.map.biome[selected]] ?? "#b7c1b4" }} />
          <h3>{selectedName}</h3><span>你正在查看的栖息地</span>
        </div>
        <div className="map-play-facts">
          <div><span>温度</span>
            <strong>{temperature === undefined ? "暂无数据" : `${display(temperature)}°C`}</strong></div>
          <div><span>食物 · 可用植被</span><strong>{vegetationDescription()}</strong>
            <small>与本回合其他地块相比</small></div>
          <div><span>当前物种数量</span>
            <strong>{population === undefined ? "选择物种查看" : `${display(population, 0)} 个体`}</strong>
            {distribution && currentDetail && <small>{currentDetail.species.species_id}</small>}
          </div>
        </div>
      </div>
      <details className="map-play-details">
        <summary>土壤、海拔与更多细节</summary>
        <div>
          <span>地块编号 <strong>{selected}</strong></span>
          <span>海拔 <strong>{display(elevation, 0)}{elevation === undefined ? "" : " m"}</strong></span>
          <span>可用植被量 <strong>{display(vegetation, 3)}</strong></span>
          {([
            ["soil_quality", "土壤质量"],
            ["humidity", "湿度"],
            ["habitat_complexity", "栖息地复杂度"],
          ] as const).map(([field, label]) => {
            const value = valueAt(snapshot.map[field], selected);
            return value === undefined ? null : (
              <span key={field}>{label} <strong>{display(value, 3)}</strong></span>
            );
          })}
        </div>
        <p>植被量来自这个回合的模拟数据。</p>
      </details>
    </section>
  );
}

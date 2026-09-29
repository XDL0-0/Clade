import type { Json, Message, Values } from "./types";

export const statusNames: Record<string, string> = {
  Healthy: "平稳", Declining: "持续减少", Critical: "濒危",
  "Functionally Extinct": "难以延续", Extinct: "已灭绝",
};
export const roleNames: Record<string, string> = {
  producer: "生产者", herbivore: "植食者", carnivore: "捕食者", decomposer: "分解者",
};
export const traitNames: Record<string, string> = {
  armor: "护甲", speed: "速度", attack: "攻击", cooperation: "合作",
  toxin: "毒素", detox: "解毒", engineering: "环境改造",
};
const pressureNames: Record<string, string> = {
  temperature: "温度", water: "水分", food: "食物短缺", predation: "捕食",
  competition: "资源竞争", mobility: "迁移", disease: "疾病",
  temperature_pressure: "温度", water_pressure: "水分", food_pressure: "食物短缺",
  predation_pressure: "捕食", overcrowding: "过度拥挤",
};
const mortalityNames: Record<string, string> = {
  temperature: "温度胁迫", starvation: "饥饿", predation: "被捕食",
  competition: "资源竞争", disease: "疾病", disaster: "灾害", old_age: "衰老",
  other: "其他来源", unknown: "原因未明",
};
const biomeNames = ["海洋", "湿地", "冻原", "荒漠", "草原", "森林", "高山"];

export function record(value: Json | undefined): Values {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value : {};
}
export function number(value: Json | undefined): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}
function text(value: Json | undefined): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}
export function amount(value: number): string {
  const magnitude = Math.abs(value);
  return magnitude > 0 && magnitude < 0.001
    ? value.toExponential(1)
    : value.toLocaleString("zh-CN", { maximumFractionDigits: 3 });
}
function numericEntries(value: Json | undefined): [string, number][] {
  return Object.entries(record(value)).flatMap(([key, value]) => {
    const measured = number(value);
    return measured === undefined ? [] : [[key, measured] as [string, number]];
  });
}

export function traitChangeStory(payload: Values): string {
  const changes = numericEntries(payload.trait_changes)
    .filter(([key, value]) => traitNames[key] && value !== 0)
    .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
  if (!changes.length) {
    return (number(payload.max_deme_change) ?? 0) > 0
      ? "局部种群的性状发生变化，物种平均值未变。"
      : "这条记录没有提供可展示的性状变化。";
  }
  return changes.slice(0, 3)
    .map(([key, value]) => `${traitNames[key]}${value > 0 ? "增强" : "减弱"}`)
    .join("，") + (changes.length > 3 ? "，其他性状也有变化。" : "。");
}

export function pressureStory(payload: Values): string {
  const pressures = numericEntries(payload.pressure)
    .filter(([key, value]) => pressureNames[key] && value > 0)
    .sort((a, b) => b[1] - a[1]);
  return pressures.length
    ? `记录中较突出的压力是${pressures.slice(0, 2).map(([key]) => pressureNames[key]).join("和")}；它们可能与这次变化有关。`
    : "这条记录没有显示突出的已知压力。";
}

export function tradeoffStory(payload: Values): string {
  const costs = record(payload.tradeoffs);
  const maintenance = number(costs.annual_maintenance_change_per_individual);
  const armorPayment = number(costs.armor_speed_payment);
  const parts: string[] = [];
  if (maintenance !== undefined && maintenance !== 0) {
    parts.push(`每个个体每年的维持消耗${maintenance > 0 ? "增加" : "减少"} ${amount(Math.abs(maintenance))} 碳单位`);
  }
  if (armorPayment !== undefined && armorPayment > 0) {
    parts.push(`护甲的代价是 ${amount(armorPayment)} 的速度性状损失`);
  }
  if (parts.length) return `${parts.join("；")}。`;
  if (maintenance === 0 && armorPayment === 0) return "记录中没有额外维持消耗或护甲带来的速度代价。";
  if (maintenance === 0) return "记录中的年度维持消耗没有变化。";
  if (armorPayment === 0) return "记录中的护甲速度代价为零。";
  return "这条记录没有提供代价信息。";
}

export function extinctionStory(value: Json | undefined): string {
  const cause = record(value);
  const counts = numericEntries(cause.counts).filter(([, count]) => count > 0)
    .sort((a, b) => b[1] - a[1]);
  if (counts.length) {
    return `最后一回合的死亡记录以${counts.slice(0, 2)
      .map(([key, count]) => `${mortalityNames[key] ?? "未分类来源"}（${amount(count)} 个体）`)
      .join("、")}为主。`;
  }
  const primary = text(cause.primary);
  return primary && primary !== "unknown" && mortalityNames[primary]
    ? `记录的主要死亡来源是${mortalityNames[primary]}。`
    : "这条记录没有给出明确的灭绝原因。";
}

interface Chronicle {
  title: string;
  body: string;
  pressure?: string;
  cost?: string;
  species: string[];
}

export function chronicle(message: Message): Chronicle {
  const envelope = message.payload;
  const payload = record(envelope.payload ?? envelope.data);
  const kind = message.kind;
  const speciesEvent = kind.startsWith("Species") || kind.startsWith("Speciation")
    || kind === "MigrationOccurred" || kind === "EvolutionTrace";
  const species = speciesEvent ? [...new Set([
    text(payload.species), text(payload.species_id), text(payload.parent), text(payload.child_id),
    text(envelope.actor), text(envelope.target),
  ].filter((id): id is string => typeof id === "string" && !id.startsWith("tile:")))] : [];
  const subject = text(payload.species) ?? text(envelope.target) ?? text(envelope.actor) ?? "这个物种";
  const population = number(payload.population);
  const size = population === undefined ? "" : `，现有 ${amount(population)} 个体`;
  const base = { species };
  if (kind === "SpeciesAdapted" || kind === "EvolutionTrace") {
    return { ...base, title: "新的适应", body: `${subject}：${traitChangeStory(payload)}`,
      pressure: pressureStory(payload), cost: tradeoffStory(payload) };
  }
  if (kind === "SpeciesExtinct") {
    return { ...base, title: "一个物种谢幕", body: `${subject} 已灭绝。${extinctionStory(payload.extinction_cause)}` };
  }
  const lifecycle: Record<string, [string, string]> = {
    SpeciesRecovered: ["种群恢复平稳", "恢复到平稳状态"],
    SpeciesDeclining: ["种群正在减少", "进入持续下降状态"],
    SpeciesCritical: ["生存告急", "已进入濒危状态"],
    SpeciesFunctionallyExtinct: ["延续受到威胁", "已难以维持繁衍"],
  };
  if (lifecycle[kind]) {
    const [title, state] = lifecycle[kind];
    const decline = number(payload.declining_turns);
    return { ...base, title, body: `${subject} ${state}${size}。${decline && decline > 0 ? `已连续 ${amount(decline)} 回合减少。` : ""}` };
  }
  if (kind === "SpeciationOccurred" || kind === "SpeciesCreated") {
    const parent = text(payload.parent), child = text(payload.child_id);
    const isolation = number(payload.isolation_turns);
    return { ...base, title: kind === "SpeciesCreated" ? "新物种诞生" : "谱系分出了新枝",
      body: parent && child ? `${parent} 的一个种群分化为 ${child}${size}。` : `新物种已被记录${size}。`,
      pressure: isolation === undefined ? undefined : `这支种群已隔离 ${amount(isolation)} 回合。` };
  }
  if (kind === "SpeciationDeferred") {
    return { ...base, title: "分化暂缓", body: payload.reason === "species_capacity"
      ? "这次分化已达到条件，但世界的物种名额已满。" : "一项物种分化尚未完成，原因见详细数据。" };
  }
  if (kind === "MigrationOccurred") {
    const count = number(payload.count);
    const pressures = Array.isArray(payload.pressures) ? payload.pressures
      .flatMap((value) => typeof value === "string" && pressureNames[value] ? [pressureNames[value]] : []) : [];
    return { ...base, title: "种群迁移", body: `${subject} ${count === undefined ? "发生了迁移" : `有 ${amount(count)} 个体迁往其他地点`}。`,
      pressure: payload.reason === "undirected_dispersal" ? "这次是自然扩散。"
        : pressures.length ? `记录的迁移压力：${pressures.join("、")}。` : undefined };
  }
  if (kind === "ClimateShift") {
    const delta = number(payload.temperature_delta_c);
    const sea = number(payload.sea_level_delta_m);
    return { ...base, title: "气候正在改变", body: delta === undefined ? "世界记录了一次气候变化。"
      : delta === 0 ? "这一回合的全球气温未变。" : `全球气温${delta > 0 ? "升高" : "降低"} ${amount(Math.abs(delta))} °C。`,
      pressure: sea === undefined || sea === 0 ? undefined : `海平面${sea > 0 ? "上升" : "下降"} ${amount(Math.abs(sea))} 米。` };
  }
  if (kind === "BiomeChanged") {
    const from = number(payload.from), to = number(payload.to), tile = number(payload.tile_id);
    const reasons: Record<string, string> = {
      elevation_at_or_below_sea_level: "地面已在海平面之下。", surface_water_storage: "地表积水改变了环境。",
      high_elevation: "海拔形成了高山环境。", freezing_temperature: "低温使这里冻结。",
      arid_or_infertile_soil: "这里缺水，或土壤贫瘠。", moist_fertile_vegetated_soil: "湿润的土壤与植被形成了森林环境。",
      temperate_grassland: "当前水热与植被条件形成了草原。",
    };
    return { ...base, title: "地貌换了模样", body: from !== undefined && to !== undefined && biomeNames[from] && biomeNames[to]
      ? `${tile === undefined ? "一处土地" : `地块 ${tile}`}从${biomeNames[from]}变为${biomeNames[to]}。` : "一处地块的生态环境发生变化。",
      pressure: reasons[text(payload.reason) ?? ""] };
  }
  if (kind === "Volcano") {
    const uplift = number(payload.uplift_m), tile = number(payload.tile_id);
    return { ...base, title: "火山活动", body: `${tile === undefined ? "一处地块" : `地块 ${tile}`}出现火山活动${uplift === undefined ? "。" : `，地面抬升 ${amount(uplift)} 米。`}` };
  }
  if (kind === "NicheConstructed") {
    const work = number(payload.carbon_respired);
    const land = number(payload.work_by_land), water = number(payload.work_by_water);
    const places = [land !== undefined && land > 0 ? "陆地土壤" : "", water !== undefined && water > 0 ? "水中栖息结构" : ""].filter(Boolean);
    return { ...base, title: "生灵改变了家园", body: places.length
      ? `种群投入储备，改善${places.join("与")}。` : "土壤或水中栖息结构随时间发生变化。",
      cost: work !== undefined && work > 0 ? `消耗了 ${amount(work)} 碳单位的能量储备。` : undefined };
  }
  const system: Record<string, [string, string]> = {
    WorldCreated: ["世界苏醒", "初始世界已留下第一份记录。"],
    TurnCommitted: ["时光向前", "这一回合的变化已写入世界历史。"],
    TimelineCreated: ["另一种可能", "一条新的时间线从既有历史中分出。"],
    WorldReplaced: ["回到历史中的一刻", "时间线已回到所选记录，可以从这里继续。"],
  };
  const [title, body] = system[kind] ?? ["世界留下了一条记录", "展开详细数据可查看这条记录的内容。"];
  return { ...base, title, body };
}

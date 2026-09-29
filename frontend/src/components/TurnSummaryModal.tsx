import type { TurnReport } from "@/services/api.types";
import { X } from "lucide-react";
import { useState } from "react";
import { MarkdownRenderer } from "./MarkdownRenderer";
import { EcologicalRealismSummary } from "./EcologicalRealismSummary";
import { TurnHighlights } from "./TurnHighlights";

interface Props {
  report: TurnReport;
  previousReport: TurnReport | null;
  onClose: () => void;
  onSelectSpecies?: (code: string) => void;
}

export function TurnSummaryModal({ report, previousReport, onClose, onSelectSpecies }: Props) {
  const [expandedSection, setExpandedSection] = useState<string | null>("overview");
  const consecutivePrevious = previousReport?.turn_index === report.turn_index - 1
    ? previousReport
    : null;
  
  // 从 report.species 中直接获取物种统计（后端已返回完整族谱数据）
  const currentAliveCount = report.species.filter(s => s.status === "alive").length;
  // 多条分化记录可能指向同一新谱系，只计不同的新物种。
  const newSpeciesCount = new Set(report.branching_events.map(event => event.new_lineage).filter(Boolean)).size;
  
  // 计算上回合存活物种数
  const previousAliveCount = consecutivePrevious
    ? consecutivePrevious.species.filter(s => s.status === "alive").length
    : 0;
  
  // 本回合灭绝的物种（在当前报告中灭绝，但在上一回合还存活的）
  const extinctThisTurn = report.species.filter(s => 
    s.status === "extinct" && 
    consecutivePrevious?.species.some(ps => ps.lineage_code === s.lineage_code && ps.status === "alive")
  );
  
  // 物种变化 = 当前存活数 - 上回合存活数
  const speciesChange = consecutivePrevious
    ? currentAliveCount - previousAliveCount 
    : 0;
  
  const newSpecies = newSpeciesCount;
  
  // population 是种群个体数量，不是质量意义上的生物量。
  const currentPopulation = report.species.reduce((sum, s) => sum + (s.population || 0), 0);
  const previousPopulation = consecutivePrevious
    ? consecutivePrevious.species.reduce((sum, s) => sum + (s.population || 0), 0)
    : 0;
  const populationChange = currentPopulation - previousPopulation;
  
  // 计算百分比变化
  const populationChangePercent = previousPopulation > 0
    ? ((populationChange / previousPopulation) * 100).toFixed(1)
    : "0";
  
  const toggleSection = (section: string) => {
    setExpandedSection(expandedSection === section ? null : section);
  };
  
  return (
    <div style={{
      position: "fixed",
      top: 0,
      left: 0,
      right: 0,
      bottom: 0,
      background: "rgba(0, 0, 0, 0.9)",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      zIndex: 10001,
      backdropFilter: "blur(8px)",
      padding: "20px"
    }}>
      <div style={{
        background: "linear-gradient(135deg, rgba(17, 24, 39, 0.95), rgba(31, 41, 55, 0.95))",
        border: "1px solid rgba(59, 130, 246, 0.3)",
        borderRadius: "24px",
        maxWidth: "900px",
        width: "100%",
        maxHeight: "90vh",
        overflow: "hidden",
        display: "flex",
        flexDirection: "column",
        boxShadow: "0 20px 60px rgba(0, 0, 0, 0.5)"
      }}>
        {/* Header */}
        <div style={{
          padding: "32px",
          borderBottom: "1px solid rgba(255, 255, 255, 0.1)",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          background: "linear-gradient(90deg, rgba(59, 130, 246, 0.1), rgba(16, 185, 129, 0.1))"
        }}>
          <div>
            <h2 style={{
              fontSize: "2rem",
              fontWeight: 700,
              margin: 0,
              marginBottom: "8px",
              color: "#fff"
            }}>
              🌍 回合 #{report.turn_index + 1} 演化总结
            </h2>
            <p style={{
              fontSize: "1rem",
              color: "rgba(255, 255, 255, 0.6)",
              margin: 0
            }}>
              {report.pressures_summary || "环境平稳，无特殊压力"}
            </p>
          </div>
          <button
            onClick={onClose}
            style={{
              background: "rgba(255, 255, 255, 0.1)",
              border: "1px solid rgba(255, 255, 255, 0.2)",
              borderRadius: "12px",
              padding: "12px",
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              transition: "all 0.2s"
            }}
            onMouseEnter={(e) => e.currentTarget.style.background = "rgba(255, 255, 255, 0.2)"}
            onMouseLeave={(e) => e.currentTarget.style.background = "rgba(255, 255, 255, 0.1)"}
          >
            <X size={24} color="#fff" />
          </button>
        </div>
        
        {/* Content */}
        <div style={{
          padding: "32px",
          overflowY: "auto",
          flex: 1
        }}>
          {/* 统计概览 */}
          <section style={{ marginBottom: "32px" }}>
            <h3 
              style={{
                fontSize: "1.3rem",
                fontWeight: 600,
                color: "#3b82f6",
                marginBottom: "16px",
                display: "flex",
                alignItems: "center",
                gap: "8px",
                cursor: "pointer"
              }}
              onClick={() => toggleSection("overview")}
            >
              📊 统计概览 {expandedSection === "overview" ? "▼" : "▶"}
            </h3>
            {expandedSection === "overview" && (
              <div style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
                gap: "16px"
              }}>
                <StatCard
                  label="物种数量"
                  value={currentAliveCount}
                  change={
                    // 显示格式: +新增 -灭绝
                    newSpecies > 0 || extinctThisTurn.length > 0
                      ? `${newSpecies > 0 ? `+${newSpecies}` : ""}${extinctThisTurn.length > 0 ? ` -${extinctThisTurn.length}` : ""}`.trim()
                      : null
                  }
                  icon="🧬"
                  color={speciesChange > 0 ? "#10b981" : speciesChange < 0 ? "#ef4444" : "#3b82f6"}
                />
                <StatCard
                  label="本回合灭绝"
                  value={consecutivePrevious ? extinctThisTurn.length : "未确认"}
                  change={consecutivePrevious ? "与上一回合存活物种对照" : "缺少连续的上一回合记录"}
                  icon="💀"
                  color="#ef4444"
                />
                <StatCard
                  label="新增物种"
                  value={newSpecies}
                  change={null}
                  icon="✨"
                  color="#10b981"
                />
                <StatCard
                  label="种群总数量"
                  value={currentPopulation.toLocaleString("zh-CN")}
                  change={
                    !consecutivePrevious ? "缺少连续的上一回合记录" :
                    previousPopulation === 0 ? (currentPopulation > 0 ? "从0增长" : "持平") :
                    populationChange > 0 ? `+${populationChangePercent}%` :
                    populationChange < 0 ? `${populationChangePercent}%` :
                    "持平"
                  }
                  icon="⚖️"
                />
              </div>
            )}
          </section>

          <TurnHighlights report={report} previousReport={consecutivePrevious} onSelectSpecies={onSelectSpecies} />
          
          {/* 生态拟真统计 */}
          {report.ecological_realism && (
            <section style={{ marginBottom: "32px" }}>
              <h3 
                style={{
                  fontSize: "1.3rem",
                  fontWeight: 600,
                  color: "#22c55e",
                  marginBottom: "16px",
                  display: "flex",
                  alignItems: "center",
                  gap: "8px",
                  cursor: "pointer"
                }}
                onClick={() => toggleSection("ecological")}
              >
                🌍 生态拟真 {expandedSection === "ecological" ? "▼" : "▶"}
              </h3>
              {expandedSection === "ecological" && (
                <EcologicalRealismSummary data={report.ecological_realism} />
              )}
            </section>
          )}
          
          {/* 回合叙事 */}
          <section style={{ marginBottom: "32px" }}>
            <h3 
              style={{
                fontSize: "1.3rem",
                fontWeight: 600,
                color: "#3b82f6",
                marginBottom: "16px",
                display: "flex",
                alignItems: "center",
                gap: "8px",
                cursor: "pointer"
              }}
              onClick={() => toggleSection("narrative")}
            >
              📖 演化叙事 {expandedSection === "narrative" ? "▼" : "▶"}
            </h3>
            {expandedSection === "narrative" && (
              <div style={{
                background: "rgba(59, 130, 246, 0.05)",
                border: "1px solid rgba(59, 130, 246, 0.2)",
                borderRadius: "12px",
                padding: "20px",
                fontSize: "1rem",
                overflow: "hidden",
                maxWidth: "100%"
              }}>
                <MarkdownRenderer content={report.narrative} />
              </div>
            )}
          </section>
          
          {/* 物种故事 - 显示有AI叙事的物种 */}
          {report.species.filter(s => s.ai_narrative).length > 0 && (
            <section style={{ marginBottom: "32px" }}>
              <h3 
                style={{
                  fontSize: "1.3rem",
                  fontWeight: 600,
                  color: "#10b981",
                  marginBottom: "16px",
                  display: "flex",
                  alignItems: "center",
                  gap: "8px",
                  cursor: "pointer"
                }}
                onClick={() => toggleSection("species_stories")}
              >
                🦎 物种故事 {expandedSection === "species_stories" ? "▼" : "▶"}
              </h3>
              {expandedSection === "species_stories" && (
                <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
                  {report.species.filter(s => s.ai_narrative).map((species, idx) => (
                    <div 
                      key={`story-${idx}`}
                      style={{
                        background: "rgba(16, 185, 129, 0.05)",
                        border: "1px solid rgba(16, 185, 129, 0.2)",
                        borderRadius: "12px",
                        padding: "16px",
                      }}
                    >
                      <div style={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                        marginBottom: "12px"
                      }}>
                        <span style={{
                          fontWeight: 600,
                          color: "#10b981",
                          fontSize: "1.1rem"
                        }}>
                          {species.common_name}
                        </span>
                        <span style={{
                          color: "rgba(255, 255, 255, 0.5)",
                          fontSize: "0.85rem"
                        }}>
                          {species.tier || ""} · 死亡率 {(species.death_rate * 100).toFixed(1)}%
                        </span>
                      </div>
                      <div style={{
                        margin: 0,
                        fontSize: "0.95rem"
                      }}>
                        <MarkdownRenderer 
                          content={species.ai_narrative || ""} 
                          style={{ lineHeight: "1.7", color: "rgba(255, 255, 255, 0.85)" }}
                        />
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </section>
          )}
          
          {/* 重大事件 */}
          {(report.major_events.length > 0 || report.branching_events.length > 0 || extinctThisTurn.length > 0) && (
            <section style={{ marginBottom: "32px" }}>
              <h3 
                style={{
                  fontSize: "1.3rem",
                  fontWeight: 600,
                  color: "#f59e0b",
                  marginBottom: "16px",
                  display: "flex",
                  alignItems: "center",
                  gap: "8px",
                  cursor: "pointer"
                }}
                onClick={() => toggleSection("events")}
              >
                🔥 重大事件 {expandedSection === "events" ? "▼" : "▶"}
              </h3>
              {expandedSection === "events" && (
                <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
                  {/* 环境灾难 */}
                  {report.major_events.map((event, idx) => (
                    <EventCard
                      key={`event-${idx}`}
                      icon="⚠️"
                      title={event.severity}
                      description={event.description}
                      color="#f59e0b"
                    />
                  ))}
                  
                  {/* 物种分化 */}
                  {report.branching_events.map((event, idx) => (
                    <EventCard
                      key={`branch-${idx}`}
                      icon="🔀"
                      title={`物种分化`}
                      description={event.description || `${event.parent_lineage} 分化为 ${event.new_lineage}`}
                      color="#10b981"
                    />
                  ))}
                  
                  {/* 物种灭绝 */}
                  {extinctThisTurn.map((species, idx) => (
                    <EventCard
                      key={`extinct-${idx}`}
                      icon="💀"
                      title={`物种灭绝`}
                      description={`${species.common_name} (${species.lineage_code}) - 死亡率 ${(species.death_rate * 100).toFixed(1)}%`}
                      color="#ef4444"
                    />
                  ))}
                </div>
              )}
            </section>
          )}
          
          {/* 地图变化 */}
          {report.map_changes.length > 0 && (
            <section style={{ marginBottom: "32px" }}>
              <h3 
                style={{
                  fontSize: "1.3rem",
                  fontWeight: 600,
                  color: "#8b5cf6",
                  marginBottom: "16px",
                  display: "flex",
                  alignItems: "center",
                  gap: "8px",
                  cursor: "pointer"
                }}
                onClick={() => toggleSection("map")}
              >
                🌍 地图变化 {expandedSection === "map" ? "▼" : "▶"}
              </h3>
              {expandedSection === "map" && (
                <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
                  <div style={{
                    background: "rgba(139, 92, 246, 0.05)",
                    border: "1px solid rgba(139, 92, 246, 0.2)",
                    borderRadius: "12px",
                    padding: "16px"
                  }}>
                    <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "12px" }}>
                      <span style={{ color: "rgba(255, 255, 255, 0.6)" }}>全球温度</span>
                      <span style={{ color: "#fff", fontWeight: 600 }}>
                        {report.global_temperature.toFixed(1)}°C
                        {previousReport && ` (${report.global_temperature > previousReport.global_temperature ? '↑' : '↓'}${Math.abs(report.global_temperature - previousReport.global_temperature).toFixed(1)}°C)`}
                      </span>
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "12px" }}>
                      <span style={{ color: "rgba(255, 255, 255, 0.6)" }}>海平面</span>
                      <span style={{ color: "#fff", fontWeight: 600 }}>
                        {report.sea_level.toFixed(0)}m
                        {previousReport && ` (${report.sea_level > previousReport.sea_level ? '↑' : '↓'}${Math.abs(report.sea_level - previousReport.sea_level).toFixed(0)}m)`}
                      </span>
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between" }}>
                      <span style={{ color: "rgba(255, 255, 255, 0.6)" }}>地质阶段</span>
                      <span style={{ color: "#fff", fontWeight: 600 }}>{report.tectonic_stage || "稳定期"}</span>
                    </div>
                  </div>
                  
                  {report.map_changes.map((change, idx) => (
                    <EventCard
                      key={`map-${idx}`}
                      icon={getMapChangeIcon(change.change_type)}
                      title={change.stage}
                      description={`${change.affected_region}: ${change.description}`}
                      color="#8b5cf6"
                    />
                  ))}
                </div>
              )}
            </section>
          )}
          
          {/* 物种迁徙 */}
          {report.migration_events.length > 0 && (
            <section style={{ marginBottom: "32px" }}>
              <h3 
                style={{
                  fontSize: "1.3rem",
                  fontWeight: 600,
                  color: "#06b6d4",
                  marginBottom: "16px",
                  display: "flex",
                  alignItems: "center",
                  gap: "8px",
                  cursor: "pointer"
                }}
                onClick={() => toggleSection("migration")}
              >
                🗺️ 物种迁徙 {expandedSection === "migration" ? "▼" : "▶"}
              </h3>
              {expandedSection === "migration" && (
                <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
                  {report.migration_events.map((event, idx) => (
                    <EventCard
                      key={`migration-${idx}`}
                      icon="➡️"
                      title={event.lineage_code}
                      description={`${event.origin} → ${event.destination}: ${event.rationale}`}
                      color="#06b6d4"
                    />
                  ))}
                </div>
              )}
            </section>
          )}
        </div>
        
        {/* Footer */}
        <div style={{
          padding: "24px 32px",
          borderTop: "1px solid rgba(255, 255, 255, 0.1)",
          display: "flex",
          justifyContent: "center"
        }}>
          <button
            onClick={onClose}
            style={{
              background: "linear-gradient(90deg, #3b82f6, #10b981)",
              border: "none",
              borderRadius: "12px",
              padding: "12px 48px",
              color: "#fff",
              fontSize: "1.1rem",
              fontWeight: 600,
              cursor: "pointer",
              transition: "transform 0.2s",
              boxShadow: "0 4px 12px rgba(59, 130, 246, 0.3)"
            }}
            onMouseEnter={(e) => e.currentTarget.style.transform = "scale(1.05)"}
            onMouseLeave={(e) => e.currentTarget.style.transform = "scale(1)"}
          >
            继续演化 →
          </button>
        </div>
      </div>
    </div>
  );
}

// 统计卡片
function StatCard({ label, value, change, icon, color = "#3b82f6" }: {
  label: string;
  value: string | number;
  change: string | number | null;
  icon: string;
  color?: string;
}) {
  return (
    <div style={{
      background: "rgba(255, 255, 255, 0.03)",
      border: `1px solid ${color}40`,
      borderRadius: "12px",
      padding: "20px",
      display: "flex",
      flexDirection: "column",
      gap: "8px"
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
        <span style={{ fontSize: "1.5rem" }}>{icon}</span>
        <span style={{ fontSize: "0.85rem", color: "rgba(255, 255, 255, 0.6)" }}>{label}</span>
      </div>
      <div style={{ display: "flex", alignItems: "baseline", gap: "8px" }}>
        <span style={{ fontSize: "1.8rem", fontWeight: 700, color }}>{value}</span>
        {change !== null && (
          <span style={{
            fontSize: "0.9rem",
            color: (typeof change === 'number' && change > 0) || (typeof change === 'string' && !change.startsWith('-')) ? "#10b981" : "#ef4444"
          }}>
            {typeof change === 'number' ? (change > 0 ? `+${change}` : change) : change}
          </span>
        )}
      </div>
    </div>
  );
}

// 事件卡片
function EventCard({ icon, title, description, color }: {
  icon: string;
  title: string;
  description: string;
  color: string;
}) {
  return (
    <div style={{
      background: `${color}10`,
      border: `1px solid ${color}40`,
      borderRadius: "12px",
      padding: "16px",
      display: "flex",
      gap: "12px",
      alignItems: "start"
    }}>
      <span style={{ fontSize: "1.5rem", flexShrink: 0 }}>{icon}</span>
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: "1rem", fontWeight: 600, color, marginBottom: "4px" }}>
          {title}
        </div>
        <div style={{ fontSize: "0.9rem", color: "rgba(255, 255, 255, 0.8)", lineHeight: "1.5" }}>
          {description}
        </div>
      </div>
    </div>
  );
}

// 地图变化图标
function getMapChangeIcon(changeType: string | undefined): string {
  if (!changeType) return "🌍";
  const icons: Record<string, string> = {
    uplift: "⛰️",
    erosion: "🌊",
    volcanic: "🌋",
    subsidence: "⬇️",
    glaciation: "❄️",
    warming: "🔥",
    cooling: "🧊",
    continental_drift: "🌏"
  };
  return icons[changeType] || "🌍";
}

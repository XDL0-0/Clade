/**
 * GameGuideModal.tsx - 游戏说明模态框
 * 
 * 分页式游戏指南，带有精美的玻璃效果和动态动画
 */

import { useState, useCallback, useEffect, useMemo } from "react";
import {
  X,
  ChevronLeft,
  ChevronRight,
  Sparkles,
  Clock,
  Globe,
  Leaf,
  Users,
  TrendingUp,
  Navigation,
  GitBranch,
  Zap,
  BookOpen,
  Target,
  Layers,
  Activity,
  AlertTriangle,
  Heart,
} from "lucide-react";
import "./GameGuideModal.css";

interface Props {
  isOpen: boolean;
  onClose: () => void;
}

// 页面数据
const GUIDE_PAGES = [
  {
    id: "welcome",
    title: "欢迎来到 Clade",
    icon: <Sparkles size={24} />,
    color: "#2dd4bf",
    gradient: "linear-gradient(135deg, rgba(45, 212, 191, 0.15), rgba(34, 197, 94, 0.1))",
  },
  {
    id: "time",
    title: "时间法则",
    icon: <Clock size={24} />,
    color: "#f59e0b",
    gradient: "linear-gradient(135deg, rgba(245, 158, 11, 0.15), rgba(234, 179, 8, 0.1))",
  },
  {
    id: "environment",
    title: "环境系统",
    icon: <Globe size={24} />,
    color: "#38bdf8",
    gradient: "linear-gradient(135deg, rgba(56, 189, 248, 0.15), rgba(14, 165, 233, 0.1))",
  },
  {
    id: "ecology",
    title: "物种生态",
    icon: <Leaf size={24} />,
    color: "#4ade80",
    gradient: "linear-gradient(135deg, rgba(74, 222, 128, 0.15), rgba(34, 197, 94, 0.1))",
  },
  {
    id: "population",
    title: "种群动态",
    icon: <Users size={24} />,
    color: "#a78bfa",
    gradient: "linear-gradient(135deg, rgba(167, 139, 250, 0.15), rgba(139, 92, 246, 0.1))",
  },
  {
    id: "migration",
    title: "迁徙法则",
    icon: <Navigation size={24} />,
    color: "#22d3ee",
    gradient: "linear-gradient(135deg, rgba(34, 211, 238, 0.15), rgba(6, 182, 212, 0.1))",
  },
  {
    id: "evolution",
    title: "演化机制",
    icon: <GitBranch size={24} />,
    color: "#f472b6",
    gradient: "linear-gradient(135deg, rgba(244, 114, 182, 0.15), rgba(236, 72, 153, 0.1))",
  },
  {
    id: "powers",
    title: "玩家权能",
    icon: <Zap size={24} />,
    color: "#fbbf24",
    gradient: "linear-gradient(135deg, rgba(251, 191, 36, 0.15), rgba(245, 158, 11, 0.1))",
  },
  {
    id: "advanced",
    title: "进阶系统",
    icon: <Target size={24} />,
    color: "#f43f5e",
    gradient: "linear-gradient(135deg, rgba(244, 63, 94, 0.15), rgba(225, 29, 72, 0.1))",
  },
];

// 页面内容组件
function WelcomePage() {
  return (
    <div className="guide-page-content">
      {/* 开发中提示 */}
      <div className="dev-notice">
        <div className="dev-notice-icon">🚧</div>
        <div className="dev-notice-content">
          <strong>游戏仍在开发中</strong>
          <p>
            Clade 目前处于 <strong>Beta 开发阶段</strong>。经典模式中的这些系统会随回合共同演化：
          </p>
          <ul>
            <li><strong>地质演变系统</strong>：板块带动地壳漂移，碰撞造山、张裂沉降和火山活动改变地图与栖息地</li>
            <li><strong>气候长期变化</strong>：冰期、温室效应和海平面相互影响，改变各地的温度与湿度</li>
            <li><strong>互利共生网络</strong>：相遇的传粉者和开花植物互相受益，果食动物帮助植物向邻近地块扩散</li>
            <li><strong>种群平衡</strong>：保留逐地块张量模拟，统一结算死亡、繁殖和迁徙，避免重复扣除或凭空补回种群</li>
          </ul>
          <p className="dev-notice-footer">
            感谢你的参与！如遇到问题或有建议，欢迎向口袋反馈。
          </p>
        </div>
      </div>

      <div className="guide-intro">
        <h3>你是谁？</h3>
        <p>
          在 Clade 的世界里，你扮演的是一位<strong>造物主</strong>——既是观察者，也是干预者。
          你站在时间长河之外，俯瞰着生命在这颗星球上的起起落落。
        </p>
        <p>
          你将见证生命的诞生、繁衍、挣扎与灭绝。有时你会袖手旁观，看自然法则自行运转；
          有时你会降下天灾，考验生命的极限；有时你会创造全新的物种，打破现有的生态平衡。
        </p>
        <p>
          你不是在"玩"某个角色——你是在<strong>塑造整个世界的命运</strong>。
        </p>
      </div>

      <div className="guide-intro">
        <h3>这是一个怎样的世界？</h3>
        <p>
          这是一个由 <strong>128×40 个六边形地块</strong>组成的动态世界，横跨赤道到两极。
          每个地块都有自己的气候、地形、资源禀赋。
        </p>
        <p>
          在这片土地上，物种们遵循着真实的<strong>生态学法则</strong>竞争、捕食、繁衍、演化。
          没有脚本，没有预设剧情——每一次游戏都是独一无二的演化史诗。
        </p>
        <p>
          你可能会看到一个物种从默默无闻崛起为霸主，也可能目睹一场突如其来的灾难导致大规模灭绝。
          这一切都是<strong>涌现</strong>的——不是我们预设的，而是生态系统自然演化的结果。
        </p>
      </div>

      <div className="guide-intro">
        <h3>核心特色</h3>
      </div>

      <div className="guide-features">
        <div className="feature-card">
          <div className="feature-icon" style={{ background: "rgba(251, 146, 60, 0.2)", color: "#fb923c" }}>
            <Activity size={20} />
          </div>
          <div className="feature-text">
            <strong>张量计算引擎</strong>
            <span>NumPy + SciPy 驱动的高性能生态模拟，毫秒级计算数百物种</span>
          </div>
        </div>
        <div className="feature-card">
          <div className="feature-icon" style={{ background: "rgba(34, 197, 94, 0.2)", color: "#22c55e" }}>
            <BookOpen size={20} />
          </div>
          <div className="feature-text">
            <strong>科学驱动</strong>
            <span>基于逻辑斯谛增长、能量金字塔、生态位理论等真实模型</span>
          </div>
        </div>
        <div className="feature-card">
          <div className="feature-icon" style={{ background: "rgba(56, 189, 248, 0.2)", color: "#38bdf8" }}>
            <Sparkles size={20} />
          </div>
          <div className="feature-text">
            <strong>AI 叙事生成</strong>
            <span>AI 生成新物种、撰写叙事，规则引擎处理背景物种节省 Token</span>
          </div>
        </div>
        <div className="feature-card">
          <div className="feature-icon" style={{ background: "rgba(251, 191, 36, 0.2)", color: "#fbbf24" }}>
            <Zap size={20} />
          </div>
          <div className="feature-text">
            <strong>神力干预</strong>
            <span>创造物种、降下天灾、诱导杂交——你是造物主</span>
          </div>
        </div>
      </div>

      <div className="guide-intro">
        <h3>游玩建议</h3>
        <ul className="guide-list">
          <li><strong>先观察，后干预</strong>：生态系统有自己的智慧，不必急于出手</li>
          <li><strong>灭绝是常态</strong>：99% 的物种最终都会灭绝，这是正常的演化规律</li>
          <li><strong>多样性是财富</strong>：生态位越丰富，系统越稳定</li>
          <li><strong>善用存档</strong>：在关键节点存档，探索不同的"如果"</li>
        </ul>
      </div>
    </div>
  );
}

function TimePage() {
  return (
    <div className="guide-page-content">
      <div className="guide-intro">
        <h3>回合与地质时间</h3>
        <p>
          游戏以<strong>回合</strong>为单位推进。每个回合代表的不是一天、一年，而是<strong>数十万年到数千万年</strong>的地质时间。
          这个时间尺度足够让显著的演化发生。
        </p>
        <ul className="guide-list">
          <li>你不会看到单个生物的出生与死亡——那太微观了</li>
          <li>你看到的是<strong>种群</strong>层面的变化——数量的涨落、分布的迁移、物种的分化</li>
          <li>演化需要时间积累，而每个回合都足够让基因发生漂变、自然选择发挥作用</li>
          <li>一个物种可能在几个回合内完成从诞生到灭绝的全过程，也可能延续数百回合</li>
        </ul>
      </div>

      <div className="guide-intro">
        <h3>时间的相对性</h3>
        <p>
          不同类型的生物，其"时间感"是不同的：
        </p>
        <ul className="guide-list">
          <li><strong>微生物</strong>：一个回合内可能经历数百万代，演化速度极快</li>
          <li><strong>小型动物</strong>：世代较短，适应能力强</li>
          <li><strong>大型动物</strong>：世代漫长，演化相对缓慢，但单个物种影响力大</li>
          <li><strong>植物</strong>：根据类型差异很大，从快速繁殖的藻类到缓慢生长的树木</li>
        </ul>
      </div>

      <div className="guide-intro">
        <h3>一个回合内发生了什么？</h3>
        <p>每当你点击"下一回合"，世界会按以下顺序运转。这个流程模拟了真实生态系统的运作方式：</p>
      </div>

      <div className="turn-sequence">
        <div className="sequence-item">
          <div className="sequence-number">1</div>
          <div className="sequence-content">
            <strong>环境变迁</strong>
            <span>板块漂移与造山、长期气候变化、冰盖与海平面升降</span>
          </div>
        </div>
        <div className="sequence-item">
          <div className="sequence-number">2</div>
          <div className="sequence-content">
            <strong>物种分档</strong>
            <span>将物种分为 Critical（关键）、Focus（关注）、Background（背景）三档，分配计算资源</span>
          </div>
        </div>
        <div className="sequence-item">
          <div className="sequence-number">3</div>
          <div className="sequence-content">
            <strong>生态拟真计算</strong>
            <span>Allee 效应、密度依赖疾病、环境随机性等高级模块</span>
          </div>
        </div>
        <div className="sequence-item">
          <div className="sequence-number">4</div>
          <div className="sequence-content">
            <strong>张量死亡率计算</strong>
            <span>使用 NumPy 张量并行计算所有地块的死亡率：温度适应、竞争压力、捕食压力、疾病</span>
          </div>
        </div>
        <div className="sequence-item">
          <div className="sequence-number">5</div>
          <div className="sequence-content">
            <strong>迁徙决策</strong>
            <span>物种根据压力梯度、猎物分布、栖息地质量决定是否迁移</span>
          </div>
        </div>
        <div className="sequence-item">
          <div className="sequence-number">6</div>
          <div className="sequence-content">
            <strong>繁殖增长</strong>
            <span>基于存活率、承载力、繁殖策略计算种群增长（逻辑斯谛模型）</span>
          </div>
        </div>
        <div className="sequence-item">
          <div className="sequence-number">7</div>
          <div className="sequence-content">
            <strong>张量分化检测</strong>
            <span>检测地理隔离（scipy 连通区域分析）和生态分歧（环境方差），触发分化信号</span>
          </div>
        </div>
        <div className="sequence-item">
          <div className="sequence-number">8</div>
          <div className="sequence-content">
            <strong>物种分化生成</strong>
            <span>AI 为关键物种生成新物种描述，背景物种使用规则引擎生成（节省 Token）</span>
          </div>
        </div>
      </div>

      <div className="guide-intro">
        <h3>自动演化模式</h3>
        <p>
          如果你想快速推进时间，可以使用<strong>批量演化</strong>功能：
        </p>
        <ul className="guide-list">
          <li>设置要推进的回合数（如 10 回合、50 回合）</li>
          <li>系统会自动执行，并在完成后汇总报告</li>
          <li>重大事件（大规模灭绝、新物种分化）会被高亮提示</li>
          <li>适合观察长期演化趋势，或快速跳过平静期</li>
        </ul>
      </div>
    </div>
  );
}

function EnvironmentPage() {
  return (
    <div className="guide-page-content">
      <div className="guide-intro">
        <h3>地块与栖息地</h3>
        <p>
          世界由 <strong>128×40 个六边形地块</strong>组成，总计超过 5000 个独立的栖息地单元。
          每个地块都有独立的环境属性，共同决定了那里的生命能否繁衍：
        </p>
      </div>

      <div className="env-attributes">
        <div className="env-attr">
          <div className="env-attr-icon" style={{ background: "rgba(239, 68, 68, 0.2)", color: "#ef4444" }}>🌡️</div>
          <div className="env-attr-info">
            <strong>温度</strong>
            <span>从 -40°C 极寒到 50°C 酷热，直接影响物种的生理适宜度和代谢速率</span>
          </div>
        </div>
        <div className="env-attr">
          <div className="env-attr-icon" style={{ background: "rgba(56, 189, 248, 0.2)", color: "#38bdf8" }}>💧</div>
          <div className="env-attr-info">
            <strong>湿度/降水</strong>
            <span>从沙漠 (&lt;250mm) 到雨林 (&gt;2000mm)，影响植被生产力和水生生物分布</span>
          </div>
        </div>
        <div className="env-attr">
          <div className="env-attr-icon" style={{ background: "rgba(139, 92, 246, 0.2)", color: "#8b5cf6" }}>⛰️</div>
          <div className="env-attr-info">
            <strong>海拔/深度</strong>
            <span>从 -11000m 深海到 8848m 高山，决定地形类型、气压、氧气浓度</span>
          </div>
        </div>
        <div className="env-attr">
          <div className="env-attr-icon" style={{ background: "rgba(34, 197, 94, 0.2)", color: "#22c55e" }}>🌿</div>
          <div className="env-attr-info">
            <strong>净初级生产力</strong>
            <span>地块的基础能量输入，决定整个食物链能支撑的生物量上限</span>
          </div>
        </div>
      </div>

      <div className="guide-intro">
        <h3>地形类型</h3>
        <p>海拔决定了地块的基本类型，不同类型的地块有不同的生态特征：</p>
        <ul className="guide-list">
          <li><strong>深海</strong> (-11000m ~ -200m)：高压、黑暗、寒冷，依赖化能合成或沉降有机物</li>
          <li><strong>浅海</strong> (-200m ~ 0m)：光照充足，浮游生物繁茂，是海洋生产力中心</li>
          <li><strong>沿海/湿地</strong> (0m ~ 50m)：海陆交界，生物多样性极高的过渡带</li>
          <li><strong>平原</strong> (50m ~ 500m)：适合大多数陆生生物，生产力高</li>
          <li><strong>丘陵</strong> (500m ~ 1500m)：地形起伏，形成微气候和隔离效应</li>
          <li><strong>山地</strong> (1500m ~ 4000m)：温度递减，垂直生态带分化明显</li>
          <li><strong>高山/冰川</strong> (&gt;4000m)：极端环境，仅特化物种能生存</li>
        </ul>
      </div>

      <div className="guide-intro">
        <h3>净初级生产力（NPP）</h3>
        <p>
          每个地块的<strong>生产力</strong>决定了它能支撑多少生命。系统使用 <strong>Miami 模型</strong>计算，
          这是生态学中广泛使用的经典模型：
        </p>
        <div className="formula-box">
          NPP = min(NPP_温度限制, NPP_降水限制)
        </div>
        <p>不同生态系统的典型生产力（单位：g C/m²/年）：</p>
        <ul className="guide-list">
          <li><strong>热带雨林</strong>：1000-2000，温暖湿润，地球最高生产力</li>
          <li><strong>温带森林</strong>：600-1200，四季分明，季节性生长</li>
          <li><strong>草原</strong>：200-600，降水不足以支撑森林</li>
          <li><strong>沙漠</strong>：0-100，水分严重不足</li>
          <li><strong>苔原</strong>：100-200，低温限制生长</li>
          <li><strong>浅海</strong>：200-600，光合浮游生物为主</li>
          <li><strong>深海</strong>：&lt;50，依赖上层沉降物或热泉</li>
        </ul>
      </div>

      <div className="guide-intro">
        <h3>环境变化</h3>
        <p>环境不是静态的，它会随时间发生变化：</p>
        <ul className="guide-list">
          <li><strong>气候波动</strong>：长期周期、温室气体和地质活动共同改变温度，湿度随之响应</li>
          <li><strong>海平面变化</strong>：全球温度影响冰川体积，进而影响海平面</li>
          <li><strong>极端事件</strong>：火山爆发、陨石撞击等会导致剧烈的环境改变</li>
        </ul>
      </div>

      <div className="guide-intro">
        <h3>板块构造</h3>
        <p>
          板块位置、地幔活动和火山状态会保存在当前经典世界中。
        </p>
        <p>大陆随回合漂移，实际改变地图海拔、海陆边界和物种的相遇机会：</p>
        <ul className="guide-list">
          <li><strong>大陆分裂</strong>：原本连续的种群被隔离，加速异域物种分化</li>
          <li><strong>大陆碰撞</strong>：原本隔离的物种相遇，引发竞争、捕食或杂交</li>
          <li><strong>造山运动</strong>：板块碰撞处山脉隆起，形成地理屏障，海拔变化也会改变当地温度</li>
          <li><strong>海沟/火山岛弧</strong>：板块俯冲处形成深海沟和火山岛链</li>
        </ul>
      </div>
    </div>
  );
}

function EcologyPage() {
  return (
    <div className="guide-page-content">
      <div className="guide-intro">
        <h3>营养级与食物链</h3>
        <p>
          每个物种都有一个<strong>营养级</strong>（Trophic Level），表示它在食物链中的位置。
          营养级不仅是分类标签，它直接影响物种的能量获取、承载力和生态功能：
        </p>
      </div>

      <div className="trophic-levels">
        <div className="trophic-item" style={{ "--trophic-color": "#4ade80" } as React.CSSProperties}>
          <div className="trophic-badge">T1</div>
          <div className="trophic-info">
            <strong>生产者</strong>
            <span>植物、藻类、化能自养菌 —— 将无机物转化为有机物，是生态系统的能量基础</span>
          </div>
        </div>
        <div className="trophic-item" style={{ "--trophic-color": "#a78bfa" } as React.CSSProperties}>
          <div className="trophic-badge">T1.5</div>
          <div className="trophic-info">
            <strong>分解者/腐食者</strong>
            <span>真菌、细菌、食腐动物 —— 分解死亡有机物，回收营养物质</span>
          </div>
        </div>
        <div className="trophic-item" style={{ "--trophic-color": "#facc15" } as React.CSSProperties}>
          <div className="trophic-badge">T2</div>
          <div className="trophic-info">
            <strong>初级消费者</strong>
            <span>草食动物、滤食动物 —— 直接消费生产者</span>
          </div>
        </div>
        <div className="trophic-item" style={{ "--trophic-color": "#fb923c" } as React.CSSProperties}>
          <div className="trophic-badge">T3</div>
          <div className="trophic-info">
            <strong>次级消费者</strong>
            <span>小型捕食者、杂食动物 —— 以初级消费者为主要食物</span>
          </div>
        </div>
        <div className="trophic-item" style={{ "--trophic-color": "#f43f5e" } as React.CSSProperties}>
          <div className="trophic-badge">T4-5</div>
          <div className="trophic-info">
            <strong>顶级捕食者</strong>
            <span>大型捕食者 —— 食物链终端，几乎没有天敌</span>
          </div>
        </div>
      </div>

      <div className="guide-intro">
        <h3>能量传递效率（林德曼定律）</h3>
        <p>
          能量在食物链中<strong>逐级递减</strong>，通常只有约 10-12% 的能量能传递到下一营养级。
          其余的能量在代谢、排泄、未被捕食等过程中损失。
        </p>
        <div className="energy-flow">
          <div className="energy-level" style={{ width: "100%", background: "rgba(74, 222, 128, 0.3)" }}>T1: 100%</div>
          <div className="energy-level" style={{ width: "12%", background: "rgba(250, 204, 21, 0.3)" }}>T2: 12%</div>
          <div className="energy-level" style={{ width: "1.4%", background: "rgba(251, 146, 60, 0.3)", minWidth: "60px" }}>T3: 1.4%</div>
        </div>
        <p>这意味着：</p>
        <ul className="guide-list">
          <li>顶级捕食者的<strong>承载力</strong>远低于生产者——这是数学上的必然</li>
          <li>高营养级物种更容易受到食物链断裂的影响——它们离能量源太远了</li>
          <li>一个健康的生态系统呈<strong>金字塔结构</strong>：底层生物量最大</li>
          <li>游戏中使用<strong>同化效率</strong>参数来模拟不同物种的能量转化能力</li>
        </ul>
      </div>

      <div className="guide-intro">
        <h3>生态位与竞争</h3>
        <p>
          <strong>生态位</strong>（Ecological Niche）描述了物种"如何谋生"——它吃什么、住在哪里、
          如何获取资源、在什么时间活动。在 Clade 中，生态位通过<strong>语义嵌入向量</strong>来表示。
        </p>
        <ul className="guide-list">
          <li><strong>生态位重叠</strong>：两个物种的生态位越相似（向量越接近），竞争越激烈</li>
          <li><strong>竞争排斥原则</strong>：完全相同生态位的物种无法长期共存——一方必将被淘汰</li>
          <li><strong>生态位分化</strong>：物种通过特化来减少竞争，如不同的觅食时间、不同的猎物偏好</li>
          <li><strong>垂直分层</strong>：占据不同的垂直空间（树冠层 vs 林下层 vs 地面）来减少竞争</li>
        </ul>
      </div>

      <div className="guide-intro">
        <h3>食物网动态</h3>
        <p>
          食物链并非静态的"谁吃谁"关系表，而是一个动态的网络：
        </p>
        <ul className="guide-list">
          <li><strong>捕食关系建立</strong>：当两个物种在同一地块相遇，系统会评估它们是否形成捕食关系</li>
          <li><strong>猎物替代</strong>：当主要猎物减少时，捕食者可能转向替代猎物</li>
          <li><strong>关键种效应</strong>：某些物种的灭绝会引发级联效应，导致整个食物网重组</li>
          <li><strong>营养级级联</strong>：顶级捕食者减少 → 中层消费者爆发 → 生产者被过度消费</li>
        </ul>
      </div>

      <div className="guide-intro">
        <h3>语义生态分析（AI 特色）</h3>
        <p>
          Clade 使用 AI 嵌入技术进行生态关系判断，而非硬编码规则：
        </p>
        <ul className="guide-list">
          <li>通过物种描述的<strong>语义相似度</strong>判断生态位重叠程度</li>
          <li>自动推断合理的捕食关系，无需手动配置食物网</li>
          <li>能够处理玩家创造的任意新物种，即使是幻想生物</li>
        </ul>
      </div>
    </div>
  );
}

function PopulationPage() {
  return (
    <div className="guide-page-content">
      <div className="guide-intro">
        <h3>承载力（K）</h3>
        <p>
          每个物种在每个地块都有一个<strong>承载力</strong>——该地块在当前条件下最多能稳定支持的种群数量。
          这是生态学中最核心的概念之一。
        </p>
        <p>承载力由以下因素综合决定：</p>
      </div>

      <div className="factor-grid">
        <div className="factor-item">
          <Layers size={18} />
          <span>地块资源（NPP）—— 能量输入的上限</span>
        </div>
        <div className="factor-item">
          <TrendingUp size={18} />
          <span>营养级 —— 能量金字塔位置决定可用能量</span>
        </div>
        <div className="factor-item">
          <Users size={18} />
          <span>体型 —— 大型生物单位能量需求更高</span>
        </div>
        <div className="factor-item">
          <Activity size={18} />
          <span>竞争 —— 生态位重叠的物种瓜分资源</span>
        </div>
        <div className="factor-item">
          <Globe size={18} />
          <span>环境适宜度 —— 偏离最适环境降低承载力</span>
        </div>
        <div className="factor-item">
          <GitBranch size={18} />
          <span>猎物可用性 —— 消费者依赖猎物种群</span>
        </div>
      </div>

      <div className="guide-intro">
        <h3>繁殖模型（Logistic Growth）</h3>
        <p>
          种群增长遵循经典的<strong>逻辑斯谛模型</strong>，这是数学生态学的基石：
        </p>
        <div className="formula-box">
          dN/dt = r × N × (1 - N/K)
        </div>
        <p>离散形式（游戏中使用）：</p>
        <div className="formula-box">
          N(t+1) = N(t) + r × N(t) × (1 - N(t)/K) × 存活率修正
        </div>
        <ul className="guide-list">
          <li>当 N ≪ K（种群远低于承载力）：增长接近指数级，种群快速扩张</li>
          <li>当 N → K（种群接近承载力）：增长趋缓，资源竞争加剧</li>
          <li>当 N &gt; K（种群超过承载力）：负增长，种群因资源不足而衰减</li>
          <li>平衡态：种群最终会稳定在承载力附近震荡</li>
        </ul>
      </div>

      <div className="guide-intro">
        <h3>繁殖策略（r/K 选择）</h3>
        <p>
          不同物种采取不同的繁殖策略，这深刻影响它们的生态角色：
        </p>
      </div>
      <div className="factor-table">
        <div className="factor-row factor-row-header">
          <span className="factor-name">策略类型</span>
          <span className="factor-effect">特征与影响</span>
        </div>
        <div className="factor-row">
          <span className="factor-name">r-选择型</span>
          <span className="factor-effect">高繁殖率、短寿命、小体型 → 快速占领空白生态位，但抗干扰能力弱</span>
        </div>
        <div className="factor-row">
          <span className="factor-name">K-选择型</span>
          <span className="factor-effect">低繁殖率、长寿命、大体型 → 稳定环境中竞争力强，但恢复能力差</span>
        </div>
        <div className="factor-row">
          <span className="factor-name">世代时间</span>
          <span className="factor-effect">短世代 → 每回合更多繁殖周期，演化也更快</span>
        </div>
        <div className="factor-row">
          <span className="factor-name">亲代投资</span>
          <span className="factor-effect">高投资 → 后代存活率高但数量少</span>
        </div>
      </div>

      <div className="guide-intro">
        <h3>张量死亡率计算</h3>
        <p>
          使用 <strong>NumPy 张量</strong>并行计算所有地块的死亡率。
          死亡率是多重压力的叠加，但有上限（通常不超过 95%，除非极端灾难）：
        </p>
        <ul className="guide-list">
          <li><strong>温度适应</strong>：基于物种最适温度和容忍度计算温度压力张量</li>
          <li><strong>竞争压力</strong>：生态位重叠物种的竞争强度 × 相对种群密度</li>
          <li><strong>捕食压力</strong>：捕食者数量 × 捕食效率 × 反捕食能力修正</li>
          <li><strong>资源短缺</strong>：超过承载力时的饥饿，按 (N-K)/N 比例增加</li>
          <li><strong>疾病压力</strong>：高密度时疾病爆发风险增加（密度依赖）</li>
          <li><strong>Allee 效应</strong>：种群过小时额外死亡率（繁殖困难、群体防御崩溃）</li>
          <li><strong>随机事件</strong>：环境随机变异带来的额外压力</li>
        </ul>
        <p>
          <em>张量系统可以在毫秒级完成数百物种在数千地块的死亡率计算。</em>
        </p>
      </div>

      <div className="guide-intro">
        <h3>种群崩溃与恢复</h3>
        <p>种群动态并非总是平滑的，可能出现剧烈波动：</p>
        <ul className="guide-list">
          <li><strong>崩溃</strong>：当死亡率持续高于繁殖率，种群快速衰减</li>
          <li><strong>灭绝漩涡</strong>：小种群 → Allee效应 → 更小种群 → 灭绝</li>
          <li><strong>恢复</strong>：压力解除后，r-选择型物种恢复快，K-选择型恢复慢</li>
          <li><strong>爆发</strong>：竞争者/捕食者减少后，可能出现种群爆发</li>
        </ul>
      </div>
    </div>
  );
}

function MigrationPage() {
  return (
    <div className="guide-page-content">
      <div className="guide-intro">
        <h3>为什么迁徙？</h3>
        <p>
          物种不会坐以待毙。当环境变得不利时，它们会尝试迁移到更适宜的地块。
          迁徙是物种应对环境变化的核心策略之一，也是地理分布演变的驱动力。
        </p>
        <p>
          在 Clade 中，迁徙不是随机的——每个物种都会评估周围地块的吸引力，做出"理性"的迁徙决策。
        </p>
      </div>

      <div className="guide-intro">
        <h3>迁徙触发条件</h3>
        <p>系统会评估多种迁徙动机，每种动机有不同的优先级和迁徙比例：</p>
      </div>

      <div className="migration-types">
        <div className="migration-item priority-highest">
          <div className="migration-badge">最高</div>
          <div className="migration-info">
            <strong>🎯 猎物追踪</strong>
            <span>消费者的猎物迁移或稀缺时触发。捕食者会主动追踪猎物的分布重心。</span>
          </div>
        </div>
        <div className="migration-item priority-high">
          <div className="migration-badge">高</div>
          <div className="migration-info">
            <strong>📉 慢性衰退</strong>
            <span>连续 3+ 回合种群下降时触发。即使当前死亡率不高，持续衰退也预示危机。</span>
          </div>
        </div>
        <div className="migration-item priority-high">
          <div className="migration-badge">高</div>
          <div className="migration-info">
            <strong>🚨 压力逃离</strong>
            <span>当地死亡率超过 40% 时触发。高压力地块的个体会大量外逃。</span>
          </div>
        </div>
        <div className="migration-item priority-medium">
          <div className="migration-badge">中</div>
          <div className="migration-info">
            <strong>📊 梯度迁移</strong>
            <span>相邻地块死亡率差异 &gt;15% 时触发。个体倾向于流向更安全的地块。</span>
          </div>
        </div>
        <div className="migration-item priority-low">
          <div className="migration-badge">低</div>
          <div className="migration-info">
            <strong>🌱 资源扩散</strong>
            <span>种群接近承载力 80% 时触发。健康种群会自然向外扩散。</span>
          </div>
        </div>
      </div>

      <div className="guide-intro">
        <h3>迁徙能力限制</h3>
        <p>不是所有物种都能自由迁徙，迁徙能力受多种因素限制：</p>
        <ul className="guide-list">
          <li><strong>移动能力</strong>：飞行动物迁徙范围大于爬行动物，植物依赖种子传播</li>
          <li><strong>体型</strong>：大型动物单次迁徙距离更远，但迁徙成本也更高</li>
          <li><strong>地形障碍</strong>：海洋阻挡陆生物种，山脉限制低地物种</li>
          <li><strong>生态特化</strong>：高度特化的物种难以适应新环境，迁徙意愿低</li>
          <li><strong>迁徙成本</strong>：迁徙过程中有额外死亡率，尤其是跨越不适宜地块时</li>
        </ul>
      </div>

      <div className="guide-intro">
        <h3>迁徙目标选择</h3>
        <p>当物种决定迁徙时，它会评估相邻地块的吸引力：</p>
        <ul className="guide-list">
          <li><strong>环境适宜度</strong>：温度、湿度是否接近物种最适范围</li>
          <li><strong>资源可用性</strong>：地块 NPP 和现有竞争者数量</li>
          <li><strong>捕食风险</strong>：目标地块是否有活跃的捕食者</li>
          <li><strong>猎物丰度</strong>（对消费者）：目标地块是否有足够猎物</li>
          <li><strong>种群密度</strong>：避免迁入已经过度拥挤的地块</li>
        </ul>
      </div>

      <div className="guide-intro">
        <h3>消费者追踪猎物</h3>
        <p>
          高营养级物种有特殊的迁徙行为：<strong>追踪猎物</strong>。
          这是食物网动态的重要组成部分。
        </p>
        <ul className="guide-list">
          <li>当猎物种群迁移或密度下降至临界值以下</li>
          <li>捕食者会计算猎物分布的"重心"，主动向那里移动</li>
          <li>追踪行为有延迟——捕食者不会立即感知到猎物的变化</li>
          <li>这确保了捕食者不会被困在没有食物的区域，但也可能导致"追逐-逃跑"的动态</li>
        </ul>
      </div>

      <div className="guide-intro">
        <h3>迁徙的生态后果</h3>
        <p>迁徙不仅是个体行为，还会塑造整个生态系统：</p>
        <ul className="guide-list">
          <li><strong>范围扩张</strong>：成功物种逐渐占领更多地块</li>
          <li><strong>基因流动</strong>：迁徙维持不同地块种群间的遗传联系</li>
          <li><strong>入侵与竞争</strong>：迁入者可能与原住民产生竞争</li>
          <li><strong>隔离与分化</strong>：迁徙障碍导致种群隔离，为物种分化创造条件</li>
        </ul>
      </div>
    </div>
  );
}

function EvolutionPage() {
  return (
    <div className="guide-page-content">
      <div className="guide-intro">
        <h3>什么是物种分化？</h3>
        <p>
          <strong>物种分化</strong>（Speciation）是演化的核心过程——一个物种在特定条件下分裂成两个或更多独立的物种。
          这是生物多样性产生的根本机制。
        </p>
        <p>
          在 Clade 中，物种分化不是随机事件，而是环境压力、地理隔离和时间积累的必然结果。
        </p>
      </div>

      <div className="guide-intro">
        <h3>张量分化检测</h3>
        <p>
          Clade 使用<strong>张量系统</strong>（NumPy + SciPy）进行分化检测，完全不消耗 LLM Token。
          当物种满足以下条件时，系统会触发分化信号：
        </p>
      </div>

      <div className="speciation-conditions">
        <div className="condition-item">
          <div className="condition-icon">🏝️</div>
          <div className="condition-text">
            <strong>地理隔离检测</strong>
            <span>使用 scipy.ndimage.label 分析种群分布的连通区域。当种群分布在 ≥2 个不连通地块集群时触发。</span>
          </div>
        </div>
        <div className="condition-item">
          <div className="condition-icon">🌡️</div>
          <div className="condition-text">
            <strong>生态分歧检测</strong>
            <span>计算物种分布区域内的环境方差。当同一物种在不同地块的环境差异超过阈值时触发。</span>
          </div>
        </div>
        <div className="condition-item">
          <div className="condition-icon">👥</div>
          <div className="condition-text">
            <strong>种群规模</strong>
            <span>每个隔离种群都需要足够大（&gt;100 个体），否则会因随机漂变或近交衰退而灭绝。</span>
          </div>
        </div>
        <div className="condition-item">
          <div className="condition-icon">⚡</div>
          <div className="condition-text">
            <strong>分化限制</strong>
            <span>每回合最多 20 个物种进行 AI 分化，背景物种使用规则引擎生成，防止 Token 爆炸。</span>
          </div>
        </div>
      </div>

      <div className="evolution-diagram">
        <div className="evo-stage evo-parent">
          <span>原始种群</span>
          <small>分布在多个地块</small>
        </div>
        <div className="evo-arrow">
          <span>地理隔离 + 选择压力差异</span>
        </div>
        <div className="evo-children">
          <div className="evo-stage evo-child">
            <span>子种群 A</span>
            <small>寒冷高地 → 耐寒 + 小体型</small>
          </div>
          <div className="evo-stage evo-child">
            <span>子种群 B</span>
            <small>温暖低地 → 耐热 + 大体型</small>
          </div>
        </div>
      </div>

      <div className="guide-intro">
        <h3>适应性进化</h3>
        <p>
          分化不是随机变异——新物种会表现出对其起源环境的<strong>定向适应</strong>。
          AI 会分析子种群所在地块的环境特征，生成合理的特化特征：
        </p>
        <ul className="guide-list">
          <li><strong>温度适应</strong>：寒冷地块 → 耐寒特质、更厚的隔热层；炎热地块 → 耐热特质、高效散热</li>
          <li><strong>资源适应</strong>：贫瘠地块 → 更高代谢效率、更小体型；丰富地块 → 更大体型、更高繁殖投资</li>
          <li><strong>捕食适应</strong>：高捕食压力 → 更好的防御能力、更快的逃跑速度、毒性</li>
          <li><strong>竞争适应</strong>：激烈竞争 → 生态位分化、特化的食性</li>
          <li><strong>海拔适应</strong>：高海拔 → 更高的血红蛋白、更大的肺活量</li>
        </ul>
      </div>

      <div className="guide-intro">
        <h3>分化后的命运</h3>
        <p>新物种诞生后，可能有多种命运：</p>
        <ul className="guide-list">
          <li><strong>共存</strong>：如果生态位分化足够，两个物种可以在相邻甚至同一地块共存</li>
          <li><strong>竞争排斥</strong>：如果生态位重叠大，一方可能被淘汰</li>
          <li><strong>继续分化</strong>：大范围分布的新物种可能再次分化</li>
          <li><strong>杂交</strong>：隔离解除后，两个物种可能重新接触并杂交</li>
          <li><strong>灭绝</strong>：新物种可能因种群过小或环境变化而灭绝</li>
        </ul>
      </div>

      <div className="guide-intro">
        <h3>杂交与基因渐渗</h3>
        <p>
          当两个亲缘相近的物种在同一地块相遇时，可能发生<strong>杂交</strong>。
          杂交是演化的双刃剑：
        </p>
        <ul className="guide-list">
          <li><strong>杂交优势</strong>：杂交后代可能继承双亲的优良特征，适应能力更强</li>
          <li><strong>杂交劣势</strong>：杂交后代可能不育或适应性下降（外交配抑制）</li>
          <li><strong>基因渐渗</strong>：通过回交，一个物种的基因可能渗透到另一个物种</li>
          <li><strong>新物种起源</strong>：成功的杂交可能产生全新的杂交种</li>
        </ul>
        <p>玩家可以使用"诱导杂交"能力强制两个物种杂交，创造独特的后代。</p>
      </div>

      <div className="guide-intro">
        <h3>演化树追踪</h3>
        <p>
          Clade 会自动记录所有物种的演化关系，形成<strong>系统发育树</strong>：
        </p>
        <ul className="guide-list">
          <li>追溯任何物种的祖先链，直到最初的原始物种</li>
          <li>查看物种在何时、何地、因何原因分化</li>
          <li>比较不同演化支系的兴衰</li>
          <li>识别"活化石"——古老而幸存的物种</li>
        </ul>
      </div>
    </div>
  );
}

function PowersPage() {
  return (
    <div className="guide-page-content">
      <div className="guide-intro">
        <h3>你的神力</h3>
        <p>
          作为造物主，你拥有超越自然法则的能力。你可以选择袖手旁观，让世界自行演化；
          也可以积极干预，塑造生命的走向。以下是你掌握的权能：
        </p>
      </div>

      <div className="powers-grid">
        <div className="power-card">
          <div className="power-icon">⏩</div>
          <div className="power-content">
            <strong>推进时间</strong>
            <p>
              点击"下一回合"，让世界演化数十万年。可以单回合推进细细观察，
              也可以批量快进跳过平静期。每次推进后会生成回合报告。
            </p>
          </div>
        </div>
        <div className="power-card">
          <div className="power-icon">☄️</div>
          <div className="power-content">
            <strong>降下天灾</strong>
            <p>
              火山爆发、冰河期、陨石撞击...考验生命的极限。
              天灾是大规模灭绝的触发器，也是演化创新的催化剂。
            </p>
          </div>
        </div>
        <div className="power-card">
          <div className="power-icon">✨</div>
          <div className="power-content">
            <strong>创造物种</strong>
            <p>
              描述你想要的生物（或让 AI 自动设计），将其投放到世界中。
              新物种会立即加入生态系统，与现有物种竞争、捕食或共生。
            </p>
          </div>
        </div>
        <div className="power-card">
          <div className="power-icon">🧬</div>
          <div className="power-content">
            <strong>诱导杂交</strong>
            <p>
              选择两个亲缘相近的物种，强制它们产生杂交后代。
              这是创造独特新物种的快捷方式，但杂交后代的命运难以预测。
            </p>
          </div>
        </div>
        <div className="power-card">
          <div className="power-icon">🌳</div>
          <div className="power-content">
            <strong>族谱追溯</strong>
            <p>
              在演化树上追踪任何物种的祖先链，看清亿万年的生命脉络。
              了解物种从何而来，经历了怎样的演化历程。
            </p>
          </div>
        </div>
        <div className="power-card">
          <div className="power-icon">💾</div>
          <div className="power-content">
            <strong>存档分叉</strong>
            <p>
              在关键节点保存进度，创造平行时间线。
              尝试不同的干预策略，探索"如果那时没有灭绝"的可能性。
            </p>
          </div>
        </div>
        <div className="power-card">
          <div className="power-icon">🔍</div>
          <div className="power-content">
            <strong>生态分析</strong>
            <p>
              查看任何地块的详细生态信息：温度、湿度、NPP、栖息的物种、
              食物网关系、死亡率分解等。数据驱动你的决策。
            </p>
          </div>
        </div>
        <div className="power-card">
          <div className="power-icon">📊</div>
          <div className="power-content">
            <strong>全局统计</strong>
            <p>
              追踪物种数量、生物量分布、灭绝率、分化率等全局指标。
              观察生态系统的长期演化趋势。
            </p>
          </div>
        </div>
      </div>

      <div className="guide-intro">
        <h3>天灾详解</h3>
        <p>
          天灾是地球历史上反复发生的极端事件，它们塑造了生命的演化方向。
          在 Clade 中，你可以主动触发这些事件：
        </p>
      </div>

      <div className="disaster-list">
        <div className="disaster-item">
          <span className="disaster-icon">🌋</span>
          <div className="disaster-info">
            <strong>火山爆发</strong>
            <span>
              局部高温、火山灰覆盖，对爆发点及周围地块造成毁灭性打击。
              长期效应：火山灰可能提升土壤肥力，促进恢复后的生产力。
            </span>
          </div>
        </div>
        <div className="disaster-item">
          <span className="disaster-icon">❄️</span>
          <div className="disaster-info">
            <strong>冰河期</strong>
            <span>
              全球降温 5-10°C，冰川从两极向赤道扩张。热带物种大规模死亡，
              寒带物种范围扩大。海平面下降暴露大陆架。持续多个回合。
            </span>
          </div>
        </div>
        <div className="disaster-item">
          <span className="disaster-icon">☄️</span>
          <div className="disaster-info">
            <strong>陨石撞击</strong>
            <span>
              撞击点彻底毁灭，全球性尘埃遮蔽阳光导致"撞击冬季"。
              大规模灭绝事件，但也清空生态位，为幸存者创造机会。
            </span>
          </div>
        </div>
        <div className="disaster-item">
          <span className="disaster-icon">🌊</span>
          <div className="disaster-info">
            <strong>海侵/海退</strong>
            <span>
              海平面上升淹没沿海地块，海退暴露新陆地。
              沿海物种面临栖息地丧失或获得新领地。
            </span>
          </div>
        </div>
        <div className="disaster-item">
          <span className="disaster-icon">🏜️</span>
          <div className="disaster-info">
            <strong>干旱化</strong>
            <span>
              大范围降水减少，森林退化为草原，草原退化为沙漠。
              水生生物和需水物种受严重影响。
            </span>
          </div>
        </div>
        <div className="disaster-item">
          <span className="disaster-icon">🦠</span>
          <div className="disaster-info">
            <strong>大瘟疫</strong>
            <span>
              针对特定类群的传染病爆发，高密度种群受影响最大。
              可能导致优势物种崩溃，改变生态格局。
            </span>
          </div>
        </div>
      </div>

      <div className="guide-intro">
        <h3>干预的艺术</h3>
        <p>一些策略建议：</p>
        <ul className="guide-list">
          <li><strong>少即是多</strong>：频繁干预可能破坏生态系统的自我调节能力</li>
          <li><strong>预见级联</strong>：一个物种的变化会波及整个食物网</li>
          <li><strong>多样性保险</strong>：维持物种多样性能增强系统韧性</li>
          <li><strong>时机选择</strong>：天灾后是引入新物种的好时机——生态位空缺</li>
          <li><strong>存档实验</strong>：不确定后果时，先存档再尝试</li>
        </ul>
      </div>
    </div>
  );
}

function AdvancedPage() {
  return (
    <div className="guide-page-content">
      <div className="guide-intro">
        <h3>生态拟真模块</h3>
        <p>
          Clade 内置了多个源自真实生态学研究的高级模块，让模拟更加贴近自然。
          这些模块相互作用，产生复杂而真实的生态动态：
        </p>
      </div>

      <div className="advanced-modules">
        <div className="module-card">
          <div className="module-header">
            <AlertTriangle size={18} />
            <strong>Allee 效应</strong>
          </div>
          <p>
            以生态学家 W.C. Allee 命名。当种群数量跌至过低时，
            正反馈机制会导致<strong>灭绝漩涡</strong>：
          </p>
          <ul>
            <li><strong>配偶难觅</strong>：稀疏分布的个体难以找到交配对象</li>
            <li><strong>群体防御崩溃</strong>：失去群体保护优势，个体更易被捕食</li>
            <li><strong>近交衰退</strong>：小种群内近亲交配，遗传多样性丧失，有害等位基因累积</li>
            <li><strong>人口统计随机性</strong>：小样本下出生/死亡的随机波动可能致命</li>
          </ul>
          <div className="module-effect">
            <span className="effect-label">游戏表现</span>
            <span>濒危物种（&lt;50个体）的繁殖效率非线性急剧下降，即使环境改善也难以恢复</span>
          </div>
        </div>

        <div className="module-card">
          <div className="module-header">
            <Activity size={18} />
            <strong>密度依赖疾病</strong>
          </div>
          <p>
            种群密度过高时，疾病和寄生虫风险上升，形成自然的<strong>负反馈调节</strong>：
          </p>
          <ul>
            <li><strong>传染病传播</strong>：高密度下个体接触频繁，病原体传播效率高</li>
            <li><strong>寄生虫负担</strong>：拥挤环境中寄生虫更容易找到宿主</li>
            <li><strong>群居物种风险</strong>：社会性动物因紧密接触更容易爆发疫病</li>
            <li><strong>应激免疫抑制</strong>：过度拥挤导致压力激素升高，免疫力下降</li>
          </ul>
          <div className="module-effect">
            <span className="effect-label">游戏表现</span>
            <span>当种群超过承载力 150% 时，疾病死亡率显著上升，阻止种群无限爆发</span>
          </div>
        </div>

        <div className="module-card">
          <div className="module-header">
            <Layers size={18} />
            <strong>营养级联效应</strong>
          </div>
          <p>
            关键物种的变化会沿着食物链<strong>上下传导</strong>，引发连锁反应：
          </p>
          <ul>
            <li><strong>自上而下</strong>：顶级捕食者减少 → 中层消费者爆发 → 生产者被过度啃食</li>
            <li><strong>自下而上</strong>：生产者崩溃 → 草食动物饥荒 → 捕食者食物短缺</li>
            <li><strong>关键种效应</strong>：某些物种的影响力远超其生物量（如海獭、狼）</li>
            <li><strong>营养级瀑布</strong>：移除/引入顶级捕食者可以完全重塑生态系统</li>
          </ul>
          <div className="module-effect">
            <span className="effect-label">游戏表现</span>
            <span>生态系统会自发形成动态平衡；干预一个物种会波及整个食物网</span>
          </div>
        </div>

        <div className="module-card">
          <div className="module-header">
            <Heart size={18} />
            <strong>互利共生网络</strong>
          </div>
          <p>
            物种间不只有竞争和捕食，还有<strong>互利共生</strong>关系：
          </p>
          <ul>
            <li><strong>传粉网络</strong>：传粉者-开花植物的相互依赖关系</li>
            <li><strong>种子散布</strong>：果食动物帮助植物传播种子</li>
            <li><strong>共生依赖</strong>：已有明确依赖关系的物种，需要数量足够且能接触到的伙伴</li>
            <li><strong>能力与分布</strong>：器官、传粉或食果能力、当地数量和栖息地重叠共同决定关系强弱</li>
          </ul>
          <div className="module-effect">
            <span className="effect-label">游戏表现</span>
            <span>伙伴短缺会削弱繁殖或食物供给；强依赖伙伴消失时，存活也会受影响。回合总结可查看双方物种。</span>
          </div>
        </div>

        <div className="module-card">
          <div className="module-header">
            <Clock size={18} />
            <strong>适应滞后</strong>
          </div>
          <p>
            物种的适应不是即时的——当环境变化时，存在<strong>适应延迟</strong>：
          </p>
          <ul>
            <li><strong>表型可塑性</strong>：某些性状可以快速调整（行为、生理）</li>
            <li><strong>遗传适应</strong>：基因层面的适应需要多个世代</li>
            <li><strong>滞后惩罚</strong>：快速环境变化时，来不及适应的物种面临额外压力</li>
            <li><strong>适应债务</strong>：累积的适应缺口需要时间偿还</li>
          </ul>
          <div className="module-effect">
            <span className="effect-label">游戏表现</span>
            <span>急剧的环境变化（如天灾后）会对物种造成额外的适应压力，变化历史会随经典存档保存</span>
          </div>
        </div>

        <div className="module-card">
          <div className="module-header">
            <Navigation size={18} />
            <strong>空间捕食风险</strong>
          </div>
          <p>
            捕食不是均匀的——存在<strong>空间异质性</strong>：
          </p>
          <ul>
            <li><strong>庇护所效应</strong>：某些地形提供躲避捕食者的空间</li>
            <li><strong>边缘效应</strong>：栖息地边缘捕食风险通常更高</li>
            <li><strong>捕食者回避</strong>：猎物可能主动避开高风险区域</li>
            <li><strong>恐惧景观</strong>：即使没有直接捕杀，捕食者存在也影响猎物行为</li>
          </ul>
          <div className="module-effect">
            <span className="effect-label">游戏表现</span>
            <span>猎物的迁徙决策会考虑捕食者分布，形成空间博弈</span>
          </div>
        </div>
      </div>

      <div className="guide-intro">
        <h3>环境随机性</h3>
        <p>
          真实的生态系统充满随机性。Clade 通过以下方式模拟环境的<strong>不可预测性</strong>：
        </p>
        <ul className="guide-list">
          <li><strong>气候波动</strong>：温室气体、冰盖和周期变化叠加，影响各地温湿度</li>
          <li><strong>好年/坏年</strong>：NPP 产出在平均值附近波动</li>
          <li><strong>随机灾害</strong>：即使玩家不主动触发，也有小概率发生自然灾害</li>
          <li><strong>人口统计随机性</strong>：小种群的出生/死亡具有更大的随机波动</li>
        </ul>
      </div>

      <div className="guide-intro">
        <h3>张量计算系统</h3>
        <p>
          Clade 的核心计算引擎基于 <strong>NumPy + SciPy</strong> 张量系统，提供高性能生态模拟：
        </p>
        <ul className="guide-list">
          <li><strong>张量死亡率</strong>：并行计算数百物种在数千地块的死亡率，毫秒级完成</li>
          <li><strong>张量分化检测</strong>：使用连通区域分析检测地理隔离，无 LLM 消耗</li>
          <li><strong>自动代价权衡</strong>：物种特质的增益与代价自动平衡（TradeoffCalculator）</li>
          <li><strong>影子状态</strong>：维护独立的张量状态，与数据库解耦提高效率</li>
        </ul>
      </div>

      <div className="guide-intro">
        <h3>AI 叙事系统</h3>
        <p>
          AI 能力专注于<strong>创意生成</strong>，数值计算交给张量系统：
        </p>
        <ul className="guide-list">
          <li><strong>物种分化</strong>：AI 为新物种生成名称、描述、器官演化（每回合最多 20 个）</li>
          <li><strong>杂交创造</strong>：AI 融合双亲特征，创造独特的混种生物</li>
          <li><strong>回合叙事</strong>：AI 撰写回合报告，记录关键事件和演化故事</li>
          <li><strong>规则引擎</strong>：背景物种使用规则引擎生成，零 Token 消耗</li>
          <li><strong>语义生态位</strong>：使用嵌入向量表示生态位，自动推断物种间关系</li>
        </ul>
      </div>

      <div className="guide-closing">
        <div className="closing-quote">
          "没有什么是永恒的，除了变化本身。"
        </div>
        <p>
          在 Clade 的世界里，生命总会找到出路——有时以你意想不到的方式。
          灭绝不是失败，而是演化故事的一部分。每一次危机都是新的机遇。
        </p>
        <p className="closing-cta">
          准备好见证你的世界了吗？<br />
          <span style={{ fontSize: "0.85em", opacity: 0.7 }}>点击"下一页"或按 Esc 关闭指南，开始你的造物之旅。</span>
        </p>
      </div>
    </div>
  );
}

// 页面组件映射
const PAGE_COMPONENTS: Record<string, React.FC> = {
  welcome: WelcomePage,
  time: TimePage,
  environment: EnvironmentPage,
  ecology: EcologyPage,
  population: PopulationPage,
  migration: MigrationPage,
  evolution: EvolutionPage,
  powers: PowersPage,
  advanced: AdvancedPage,
};

export function GameGuideModal({ isOpen, onClose }: Props) {
  const [currentPage, setCurrentPage] = useState(0);

  // 重置页面
  useEffect(() => {
    if (isOpen) {
      setCurrentPage(0);
    }
  }, [isOpen]);

  // 切换页面
  const goToPage = useCallback((index: number) => {
    if (index === currentPage) return;
    setCurrentPage(index);
  }, [currentPage]);

  const goNext = useCallback(() => {
    if (currentPage < GUIDE_PAGES.length - 1) {
      goToPage(currentPage + 1);
    }
  }, [currentPage, goToPage]);

  const goPrev = useCallback(() => {
    if (currentPage > 0) {
      goToPage(currentPage - 1);
    }
  }, [currentPage, goToPage]);

  // 键盘导航
  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "ArrowRight" || e.key === " ") {
        e.preventDefault();
        goNext();
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        goPrev();
      } else if (e.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, goNext, goPrev, onClose]);

  const currentPageData = useMemo(() => GUIDE_PAGES[currentPage], [currentPage]);
  const PageComponent = useMemo(() => PAGE_COMPONENTS[currentPageData.id], [currentPageData.id]);

  if (!isOpen) return null;

  return (
    <div className="guide-overlay" onClick={onClose}>
      <div className="guide-modal" onClick={(e) => e.stopPropagation()}>
        {/* 背景效果 */}
        <div className="guide-bg">
          <div className="guide-bg-gradient" style={{ background: currentPageData.gradient }} />
          <div className="guide-bg-particles">
            {Array.from({ length: 15 }).map((_, i) => (
              <div
                key={i}
                className="guide-particle"
                style={{
                  left: `${Math.random() * 100}%`,
                  top: `${Math.random() * 100}%`,
                  animationDelay: `${Math.random() * 5}s`,
                  animationDuration: `${8 + Math.random() * 8}s`,
                }}
              />
            ))}
          </div>
          <div className="guide-bg-glow" style={{ background: `radial-gradient(circle at 30% 20%, ${currentPageData.color}20, transparent 50%)` }} />
        </div>

        {/* 头部 */}
        <header className="guide-header">
          <div className="guide-title-section">
            <div className="guide-icon-wrapper" style={{ color: currentPageData.color }}>
              {currentPageData.icon}
            </div>
            <div>
              <h2 className="guide-title">{currentPageData.title}</h2>
              <p className="guide-page-indicator">
                第 {currentPage + 1} 页 / 共 {GUIDE_PAGES.length} 页
              </p>
            </div>
          </div>
          <button className="guide-close" onClick={onClose}>
            <X size={20} />
          </button>
        </header>

        {/* 侧边导航 */}
        <nav className="guide-nav">
          {GUIDE_PAGES.map((page, index) => (
            <button
              key={page.id}
              className={`guide-nav-item ${index === currentPage ? "active" : ""} ${index < currentPage ? "completed" : ""}`}
              onClick={() => goToPage(index)}
              style={{ "--nav-color": page.color } as React.CSSProperties}
            >
              <span className="nav-icon">{page.icon}</span>
              <span className="nav-label">{page.title}</span>
              <span className="nav-indicator" />
            </button>
          ))}
        </nav>

        {/* 内容区域 */}
        <main className="guide-content">
          <PageComponent />
        </main>

        {/* 底部导航 */}
        <footer className="guide-footer">
          <button
            className="guide-nav-btn prev"
            onClick={goPrev}
            disabled={currentPage === 0}
          >
            <ChevronLeft size={18} />
            <span>上一页</span>
          </button>

          <div className="guide-dots">
            {GUIDE_PAGES.map((_, index) => (
              <button
                key={index}
                className={`guide-dot ${index === currentPage ? "active" : ""}`}
                onClick={() => goToPage(index)}
              />
            ))}
          </div>

          <button
            className="guide-nav-btn next"
            onClick={currentPage === GUIDE_PAGES.length - 1 ? onClose : goNext}
          >
            <span>{currentPage === GUIDE_PAGES.length - 1 ? "开始游戏" : "下一页"}</span>
            <ChevronRight size={18} />
          </button>
        </footer>
      </div>
    </div>
  );
}

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime
from sqlmodel import Column, Field, JSON, Relationship, SQLModel


class Species(SQLModel, table=True):
    __tablename__ = "species"

    id: int | None = Field(default=None, primary_key=True)
    lineage_code: str = Field(index=True)
    latin_name: str
    common_name: str
    description: str
    morphology_stats: dict[str, float] = Field(sa_column=Column(JSON))
    abstract_traits: dict[str, float] = Field(sa_column=Column(JSON)) 
    hidden_traits: dict[str, float] = Field(sa_column=Column(JSON))
    ecological_vector: list[float] = Field(sa_column=Column(JSON))
    parent_code: str | None = Field(default=None, index=True)
    status: str = Field(default="alive", index=True)
    created_turn: int = 0
    # Existing SQLite rows and legacy saves use naive UTC, not timezone-aware values.
    updated_at: datetime = Field(default_factory=datetime.utcnow, sa_type=DateTime)
    is_background: bool = Field(default=False, index=True)
    trophic_level: float = Field(default=1.0, index=True)
    # 基因多样性：Embedding 空间中的可达范围半径
    gene_diversity_radius: float = Field(default=0.35)
    # 已探索/激活的方向索引（稀疏记录）
    explored_directions: list[int] = Field(default_factory=list, sa_column=Column(JSON))
    # 基因稳定性（影响丢失概率/衰减率）
    gene_stability: float = Field(default=0.5)
    
    # 结构化器官系统
    organs: dict[str, dict] = Field(default={}, sa_column=Column(JSON))
    # 格式: {"organ_category": {"type": "...", "parameters": {...}, "acquired_turn": int, "is_active": bool}}
    # 示例: {"locomotion": {"type": "flagella", "count": 4, "efficiency": 1.6, "acquired_turn": 5, "is_active": True}}
    
    capabilities: list[str] = Field(default=[], sa_column=Column(JSON))
    # 示例: ["photosynthesis", "flagellar_motion", "light_detection"]
    
    genus_code: str = ""
    taxonomic_rank: str = "species"
    hybrid_parent_codes: list[str] = Field(default=[], sa_column=Column(JSON))
    hybrid_fertility: float = 1.0
    
    # 栖息地类型（生活环境）
    habitat_type: str = Field(default="terrestrial", index=True)
    # 可选值: 
    # - marine (海洋): 生活在海水中，需要盐度
    # - freshwater (淡水): 生活在湖泊、河流等淡水环境
    # - terrestrial (陆生): 生活在陆地上
    # - amphibious (两栖): 能在水陆两栖生活
    # - aerial (空中): 主要在空中活动（飞行生物）
    # - deep_sea (深海): 深海环境，耐高压低温
    # - coastal (海岸): 海岸带、潮间带环境
    
    # [DEPRECATED] 旧版休眠基因系统 - 已被 gene_diversity_radius + Embedding 可达性判断替代
    # 该字段将在未来版本移除，新激活逻辑应使用 GeneDiversityService.is_reachable()
    # 迁移说明: gene_diversity_radius 已替代 hidden_traits["gene_diversity"]
    dormant_genes: dict = Field(default={}, sa_column=Column(JSON))
    # [DEPRECATED] 旧版压力暴露计数 - 新系统使用 Embedding 距离计算压力匹配度
    stress_exposure: dict = Field(default={}, sa_column=Column(JSON))

    # 历史高光时刻（用于LLM Context）
    history_highlights: list[str] = Field(default=[], sa_column=Column(JSON))
    # 累积漂移分数（用于触发描述更新）
    accumulated_adaptation_score: float = 0.0
    # 上次描述更新的回合
    last_description_update_turn: int = 0
    
    # 表型可塑性缓冲 (0.0 - 1.0)
    # 用于抵消突发环境压力的死亡率。
    # 当环境压力过大时，先消耗缓冲值。缓冲值为0时，完全承担死亡率。
    plasticity_buffer: float = Field(default=1.0)
    
    # ========== 捕食关系系统 ==========
    # 该物种捕食的物种代码列表 (prey_species)
    # 示例: ["A1", "B2"] 表示该物种以A1和B2为食
    prey_species: list[str] = Field(default=[], sa_column=Column(JSON))
    # 捕食偏好比例 (与prey_species对应)
    # 示例: {"A1": 0.7, "B2": 0.3} 表示70%吃A1，30%吃B2
    prey_preferences: dict[str, float] = Field(default={}, sa_column=Column(JSON))
    # 食性类型: herbivore(草食), carnivore(肉食), omnivore(杂食), detritivore(腐食), autotroph(自养)
    diet_type: str = Field(default="omnivore")
    
    # ========== 共生/依赖关系系统 ==========
    # 依赖的物种代码列表（该物种依赖于这些物种生存）
    # 示例: ["A1", "B2"] 表示该物种依赖A1和B2
    symbiotic_dependencies: list[str] = Field(default=[], sa_column=Column(JSON))
    # 依赖强度 (0.0-1.0)，当依赖物种灭绝时的死亡率加成
    dependency_strength: float = Field(default=0.0)
    # 共生类型说明
    # mutualism: 互利共生 (双方受益)
    # commensalism: 偏利共生 (一方受益，一方无影响)  
    # parasitism: 寄生 (一方受益，一方受害)
    symbiosis_type: str = Field(default="none")
    
    # ========== 玩家干预系统 ==========
    # 是否受保护 (降低灭绝风险)
    is_protected: bool = Field(default=False)
    # 保护剩余回合数 (0=无保护)
    protection_turns: int = Field(default=0)
    # 是否被压制 (增加死亡率)
    is_suppressed: bool = Field(default=False)
    # 压制剩余回合数
    suppression_turns: int = Field(default=0)
    
    # ========== 植物演化系统 ==========
    # 植物生命形式阶段（仅当 trophic_level < 2.0 时有效）
    # 0=原核光合生物, 1=单细胞真核, 2=群体藻类, 3=苔藓, 4=蕨类, 5=裸子植物, 6=被子植物
    life_form_stage: int = Field(default=0)
    # 生长形式: aquatic(水生), moss(苔藓), herb(草本), shrub(灌木), tree(乔木)
    growth_form: str = Field(default="aquatic")
    # 已达成的演化里程碑
    achieved_milestones: list[str] = Field(default=[], sa_column=Column(JSON))
    
    # ========== 自由器官演化系统 ==========
    # 器官胚芽池：存储 LLM 生成的器官概念，通过语义聚合累积能量
    # 格式: {rudiment_id: {name, description, embedding, accumulated_energy, maturity_threshold, 
    #        recent_contributions, associated_pressures, is_mature, created_turn, last_updated_turn}}
    organ_rudiments: dict[str, dict] = Field(default={}, sa_column=Column(JSON))
    # 已成熟器官：通过升级流程从胚芽池毕业的功能性器官
    # 格式: {organ_id: {name, description, embedding, parameters, tier, evolution_path, 
    #        upgrade_energy, upgrade_threshold, source_rudiment_id}}
    evolved_organs: dict[str, dict] = Field(default={}, sa_column=Column(JSON))

class PopulationSnapshot(SQLModel, table=True):
    __tablename__ = "population_snapshots"

    id: int | None = Field(default=None, primary_key=True)
    species_id: int = Field(foreign_key="species.id")
    turn_index: int = Field(index=True)
    region_id: int = Field(default=0, index=True)
    count: int
    death_count: int
    survivor_count: int
    population_share: float
    ecological_pressure: dict[str, Any] = Field(sa_column=Column(JSON))

class LineageEvent(SQLModel, table=True):
    __tablename__ = "lineage_events"

    id: int | None = Field(default=None, primary_key=True)
    lineage_code: str = Field(index=True)
    event_type: str
    payload: dict[str, Any] = Field(sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.utcnow)

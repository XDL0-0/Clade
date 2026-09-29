"""
张量系统配置

提供张量计算系统的统一配置类，用于控制：
- 张量死亡率计算
- 张量分化检测
- 自动代价计算
- 数值平衡参数
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Any
import yaml

if TYPE_CHECKING:
    from ..core.config import Settings
    from ..models.config import SpeciationConfig


@dataclass
class TensorBalanceConfig:
    """张量系统数值平衡配置
    
    控制张量计算的具体数值参数，用于调节生态平衡。
    所有参数都有合理的默认值，可通过配置文件覆盖。
    
    === 死亡率计算参数 ===
    temp_optimal: 最适温度（°C）
    temp_tolerance: 温度容忍范围（°C）
    temp_channel_idx: 环境张量中温度通道的索引
    temp_optimal_shift_per_100_turns: 每100回合最适温度漂移（°C）
    temp_tolerance_shift_per_100_turns: 每100回合容忍度变化（°C）
    
    === 种群动态参数 ===
    diffusion_rate: 种群扩散率（0-1）
    diffusion_rate_growth_per_100_turns: 扩散率每100回合增加值
    birth_rate: 基础出生率（0-1）
    birth_rate_growth_per_100_turns: 出生率每100回合增加值
    competition_strength: 种间竞争强度
    competition_decay_per_100_turns: 每100回合竞争衰减
    capacity_multiplier: 承载力乘数
    veg_capacity_sensitivity: 承载力对平均植被的敏感度 (avg_veg-0.5)
    
    === 分化检测参数 ===
    divergence_threshold: 环境分歧检测阈值（0-1），超过此值触发生态分化
    divergence_normalizer: 环境方差归一化除数
    
    === 适应度计算参数 ===
    fitness_min: 最低适应度（避免除零）
    """
    
    # 死亡率计算
    temp_optimal: float = 20.0
    temp_tolerance: float = 15.0
    temp_channel_idx: int = 1
    mortality_blend_ratio: float = 0.7
    temp_optimal_shift_per_100_turns: float = 0.0
    temp_tolerance_shift_per_100_turns: float = 0.0
    
    # 种群动态
    diffusion_rate: float = 0.1
    diffusion_rate_growth_per_100_turns: float = 0.0
    birth_rate: float = 0.1
    birth_rate_growth_per_100_turns: float = 0.0
    competition_strength: float = 0.01
    competition_decay_per_100_turns: float = 0.0
    capacity_multiplier: float = 10000.0
    veg_capacity_sensitivity: float = 0.0
    base_mortality: float = 0.06
    temp_mortality_weight: float = 0.25
    competition_weight: float = 0.20
    resource_weight: float = 0.25
    trophic_weight: float = 0.25
    suitability_weight: float = 0.35
    min_mortality: float = 0.0
    max_mortality: float = 0.95
    max_net_growth_ratio: float = 0.60
    max_net_decline_ratio: float = 0.60
    min_suitability_for_reproduction: float = 0.15
    generation_scaling_enabled: bool = True
    
    # 分化检测
    divergence_threshold: float = 0.5
    divergence_normalizer: float = 10.0
    
    # 适应度
    fitness_min: float = 0.1
    
    def to_dict(self) -> dict:
        """转换为字典"""
        return asdict(self)


@dataclass
class TradeoffConfig:
    """演化代价计算配置
    
    控制 TradeoffCalculator 的数值平衡。
    
    === 能量成本权重 ===
    energy_costs: 各属性的能量成本权重
        - 高成本属性（如智力）增益需要更多代价
        - 低成本属性（如耐寒性）增益代价较小
    
    === 属性竞争关系 ===
    competition_map: 属性间的竞争关系
        - 增强某属性时，优先从竞争属性中扣减
        - 基于生物学原理（能量分配、结构权衡等）
    
    === 代价计算参数 ===
    tradeoff_ratio: 代价/增益比例（0.5-1.0），1.0表示完全守恒
    max_single_penalty: 单属性最大扣减量
    penalty_parent_ratio: 从父系值扣减的最大比例
    min_penalty_threshold: 代价量低于此值时忽略
    """
    
    tradeoff_ratio: float = 0.7
    max_single_penalty: float = 2.0
    penalty_parent_ratio: float = 0.3
    min_penalty_threshold: float = 0.1
    
    # 能量成本权重（可覆盖）
    energy_costs: Dict[str, float] = field(default_factory=lambda: {
        "运动能力": 1.5,
        "智力": 2.0,
        "繁殖速度": 1.0,
        "耐寒性": 0.6,
        "耐热性": 0.6,
        "物理防御": 0.7,
        "感知能力": 1.2,
        "社会性": 0.8,
        "体型": 1.0,
    })
    
    # 属性竞争关系（可覆盖）
    competition_map: Dict[str, list] = field(default_factory=lambda: {
        "运动能力": ["物理防御", "体型", "繁殖速度"],
        "物理防御": ["运动能力", "繁殖速度"],
        "耐寒性": ["耐热性", "繁殖速度"],
        "耐热性": ["耐寒性", "繁殖速度"],
        "智力": ["繁殖速度", "体型"],
        "感知能力": ["繁殖速度"],
        "体型": ["运动能力", "繁殖速度"],
    })
    
    # 默认代价候选池
    default_penalty_pool: list = field(default_factory=lambda: [
        "繁殖速度", "运动能力", "社会性"
    ])
    
    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "tradeoff_ratio": self.tradeoff_ratio,
            "max_single_penalty": self.max_single_penalty,
            "penalty_parent_ratio": self.penalty_parent_ratio,
            "min_penalty_threshold": self.min_penalty_threshold,
            "energy_costs": dict(self.energy_costs),
            "competition_map": {k: list(v) for k, v in self.competition_map.items()},
            "default_penalty_pool": list(self.default_penalty_pool),
        }


@dataclass
class TensorConfig:
    """张量系统统一配置
    
    集中管理张量计算系统的所有开关和参数。
    可以从 Settings 和 SpeciationConfig 中提取配置。
    
    === 功能开关 ===
    use_tensor_ecology: 【新】是否使用统一张量生态计算（整合死亡率+扩散+迁徙+繁殖+竞争）
    use_tensor_mortality: 是否使用张量死亡率计算（已被 use_tensor_ecology 替代）
    use_tensor_speciation: 是否使用张量分化检测
    use_auto_tradeoff: 是否使用自动代价计算器
    
    === 数值平衡 ===
    balance: 张量计算数值平衡配置
    tradeoff: 演化代价计算配置
    
    【性能说明】
    use_tensor_ecology=True 时，将使用统一的 TensorEcologyEngine 进行生态计算，
    可获得 10-50x 的性能提升（无 Python 循环，全张量并行）。
    """
    
    # 功能开关
    use_tensor_ecology: bool = True   # 【新】统一张量生态计算（默认开启）
    use_tensor_mortality: bool = True
    use_tensor_speciation: bool = True
    use_auto_tradeoff: bool = True
    
    # 数值平衡配置
    balance: TensorBalanceConfig = field(default_factory=TensorBalanceConfig)
    tradeoff: TradeoffConfig = field(default_factory=TradeoffConfig)
    
    # 向后兼容的属性
    @property
    def tradeoff_ratio(self) -> float:
        return self.tradeoff.tradeoff_ratio
    
    @property
    def divergence_threshold(self) -> float:
        return self.balance.divergence_threshold
    
    @classmethod
    def from_settings(
        cls,
        settings: "Settings",
        speciation_config: "SpeciationConfig | None" = None,
    ) -> "TensorConfig":
        """从全局配置创建 TensorConfig
        
        Args:
            settings: 全局 Settings 对象
            speciation_config: 可选的分化配置（优先级更高）
        
        Returns:
            TensorConfig 实例
        """
        path = getattr(settings, "tensor_balance_path", None)
        if path and Path(path).exists():
            return cls.from_yaml(path, speciation_config)
        
        cfg = cls()
        cfg.use_tensor_ecology = getattr(settings, "use_tensor_ecology", cfg.use_tensor_ecology)
        cfg.use_tensor_mortality = getattr(settings, "use_tensor_mortality", cfg.use_tensor_mortality)
        cfg.use_tensor_speciation = getattr(settings, "use_tensor_speciation", cfg.use_tensor_speciation)
        cfg.use_auto_tradeoff = getattr(settings, "use_auto_tradeoff", cfg.use_auto_tradeoff)
        
        # 数值平衡配置
        for field_name, default_val in cfg.balance.to_dict().items():
            if hasattr(settings, field_name):
                setattr(cfg.balance, field_name, getattr(settings, field_name))
        
        # tradeoff 配置：优先 SpeciationConfig，其次 Settings
        source = speciation_config or settings
        for field_name, default_val in cfg.tradeoff.to_dict().items():
            if hasattr(source, field_name):
                setattr(cfg.tradeoff, field_name, getattr(source, field_name))
        
        return cfg
    
    @classmethod
    def default(cls) -> "TensorConfig":
        """返回默认配置"""
        return cls()
    
    @classmethod
    def disabled(cls) -> "TensorConfig":
        """返回全禁用配置（用于回退模式）"""
        return cls(
            use_tensor_ecology=False,
            use_tensor_mortality=False,
            use_tensor_speciation=False,
            use_auto_tradeoff=False,
        )
    
    def is_any_enabled(self) -> bool:
        """检查是否有任何张量功能启用"""
        return (
            self.use_tensor_ecology
            or self.use_tensor_mortality
            or self.use_tensor_speciation
            or self.use_auto_tradeoff
        )
    
    def to_dict(self) -> dict:
        """转换为字典（用于日志和序列化）"""
        return {
            "use_tensor_ecology": self.use_tensor_ecology,
            "use_tensor_mortality": self.use_tensor_mortality,
            "use_tensor_speciation": self.use_tensor_speciation,
            "use_auto_tradeoff": self.use_auto_tradeoff,
            "balance": self.balance.to_dict(),
            "tradeoff": self.tradeoff.to_dict(),
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "TensorConfig":
        """从字典创建配置（用于 YAML/JSON 加载）"""
        balance_data = data.get("balance", {})
        tradeoff_data = data.get("tradeoff", {})
        
        # 按字段加载，避免 YAML 已提供的增长/衰减参数被手写白名单遗漏。
        balance = TensorBalanceConfig(**{
            item.name: balance_data[item.name]
            for item in fields(TensorBalanceConfig) if item.name in balance_data
        })
        
        tradeoff = TradeoffConfig(
            tradeoff_ratio=tradeoff_data.get("tradeoff_ratio", 0.7),
            max_single_penalty=tradeoff_data.get("max_single_penalty", 2.0),
            penalty_parent_ratio=tradeoff_data.get("penalty_parent_ratio", 0.3),
            min_penalty_threshold=tradeoff_data.get("min_penalty_threshold", 0.1),
            energy_costs=tradeoff_data.get("energy_costs", TradeoffConfig().energy_costs),
            competition_map=tradeoff_data.get("competition_map", TradeoffConfig().competition_map),
            default_penalty_pool=tradeoff_data.get("default_penalty_pool", TradeoffConfig().default_penalty_pool),
        )
        
        return cls(
            use_tensor_ecology=data.get("use_tensor_ecology", True),
            use_tensor_mortality=data.get("use_tensor_mortality", True),
            use_tensor_speciation=data.get("use_tensor_speciation", True),
            use_auto_tradeoff=data.get("use_auto_tradeoff", True),
            balance=balance,
            tradeoff=tradeoff,
        )

    @classmethod
    def from_yaml(cls, path: str | Path, speciation_config: "SpeciationConfig | None" = None) -> "TensorConfig":
        """从 YAML 文件加载配置"""
        path = Path(path)
        data: Dict[str, Any] = {}
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        cfg = cls.from_dict(data)
        # 允许 SpeciationConfig 覆盖 tradeoff 基础参数
        if speciation_config is not None:
            if hasattr(speciation_config, "use_auto_tradeoff"):
                cfg.use_auto_tradeoff = getattr(speciation_config, "use_auto_tradeoff")
            if hasattr(speciation_config, "tradeoff_ratio"):
                cfg.tradeoff.tradeoff_ratio = getattr(speciation_config, "tradeoff_ratio")
            if hasattr(speciation_config, "use_tensor_speciation"):
                cfg.use_tensor_speciation = getattr(speciation_config, "use_tensor_speciation")
            if hasattr(speciation_config, "use_tensor_ecology"):
                cfg.use_tensor_ecology = getattr(speciation_config, "use_tensor_ecology")
        return cfg

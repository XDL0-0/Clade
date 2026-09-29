"""
压力-张量桥接模块

将压力系统的修改器 (ctx.modifiers) 转换为张量格式，
使张量死亡率计算能够响应各种环境压力。

核心组件：
- PressureTensorOverlay: 压力张量叠加层数据结构
- PressureToTensorBridge: 压力修改器→张量转换器
- SpeciesParamsExtractor: 物种特质→参数矩阵提取器
- MultiFactorMortality: 多因子死亡率计算器
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

import numpy as np

if TYPE_CHECKING:
    from ..models.species import Species
    from ..simulation.environment import ParsedPressure
    from .config import TensorBalanceConfig

logger = logging.getLogger(__name__)


# ============================================================================
# 压力通道定义
# ============================================================================

class PressureChannel:
    """压力通道索引常量"""
    THERMAL = 0      # 温度相关 (temperature, volcanic cooling)
    DROUGHT = 1      # 干旱相关 (drought, resource_decline)
    TOXIN = 2        # 毒性相关 (sulfide, acidity, toxin_level)
    OXYGEN = 3       # 氧气相关 (oxygen, anoxic)
    DIRECT = 4       # 直接死亡 (mortality_spike, storm_damage)
    RADIATION = 5    # 辐射相关 (uv_radiation, dna_damage)
    
    NUM_CHANNELS = 6


# ============================================================================
# 修改器-通道映射配置
# ============================================================================

# 【平衡调整v1】大幅降低系数，避免压力爆炸
# 设计目标：
#   - 单压力强度10 → 对无抗性物种约 25-35% 死亡率
#   - 三压力都是10 → 约 60-75% 死亡率（极端但有幸存者）
#
# 修改器 → (通道索引, 系数) 映射
# 正系数表示该修改器增加对应通道的压力值
# 负系数表示反向效果（如火山灰降温）
MODIFIER_CHANNEL_MAP: Dict[str, Tuple[int, float]] = {
    # 温度通道 - 温度变化是渐进的
    "temperature": (PressureChannel.THERMAL, 0.15),
    "volcanic": (PressureChannel.THERMAL, -0.08),  # 火山灰降温（较小）
    "volcano": (PressureChannel.THERMAL, -0.08),
    
    # 干旱通道 - 干旱影响较慢
    "drought": (PressureChannel.DROUGHT, 0.12),
    "resource_decline": (PressureChannel.DROUGHT, 0.06),
    "wildfire_risk": (PressureChannel.DROUGHT, 0.04),
    "wildfire": (PressureChannel.DROUGHT, 0.05),
    "desertification": (PressureChannel.DROUGHT, 0.08),
    "starvation_risk": (PressureChannel.DROUGHT, 0.05),
    
    # 毒性通道 - 毒性是主要杀手
    "sulfide": (PressureChannel.TOXIN, 0.15),
    "sulfide_production": (PressureChannel.TOXIN, 0.10),
    "toxin_level": (PressureChannel.TOXIN, 0.10),
    "toxicity": (PressureChannel.TOXIN, 0.12),
    "acidity": (PressureChannel.TOXIN, 0.08),
    "sulfur_aerosol": (PressureChannel.TOXIN, 0.10),
    "methane": (PressureChannel.TOXIN, 0.06),
    
    # 氧气通道 - 缺氧是致命的
    "oxygen": (PressureChannel.OXYGEN, -0.12),  # oxygen>0表示增氧，通道存反向
    "deep_water_anoxia": (PressureChannel.OXYGEN, 0.15),
    
    # 直接死亡通道 - 灾难性事件
    "mortality_spike": (PressureChannel.DIRECT, 0.08),
    "storm_damage": (PressureChannel.DIRECT, 0.04),
    "landslide_risk": (PressureChannel.DIRECT, 0.03),
    "habitat_loss": (PressureChannel.DIRECT, 0.025),
    "habitat_fragmentation": (PressureChannel.DIRECT, 0.015),
    "coastal_flooding": (PressureChannel.DIRECT, 0.03),
    "flood": (PressureChannel.DIRECT, 0.025),
    
    # 辐射通道 - 辐射影响较慢但持久
    "uv_radiation": (PressureChannel.RADIATION, 0.10),
    "dna_damage": (PressureChannel.RADIATION, 0.08),
    "mutation_rate": (PressureChannel.RADIATION, 0.03),
    "light_reduction": (PressureChannel.RADIATION, 0.02),
}

# 压力类型 → 主要通道映射（用于区域性压力）
# 【平衡调整v1】同样降低系数
PRESSURE_KIND_CHANNELS: Dict[str, List[Tuple[int, float]]] = {
    "glacial_period": [(PressureChannel.THERMAL, -0.15), (PressureChannel.DROUGHT, 0.04)],
    "greenhouse_earth": [(PressureChannel.THERMAL, 0.15)],
    "drought_period": [(PressureChannel.DROUGHT, 0.12)],
    "volcanic_eruption": [(PressureChannel.THERMAL, -0.10), (PressureChannel.TOXIN, 0.12), (PressureChannel.DIRECT, 0.05)],
    "anoxic_event": [(PressureChannel.OXYGEN, 0.15), (PressureChannel.DIRECT, 0.03)],
    "sulfide_event": [(PressureChannel.TOXIN, 0.15), (PressureChannel.OXYGEN, 0.06)],
    "meteor_impact": [(PressureChannel.DIRECT, 0.10), (PressureChannel.THERMAL, -0.12), (PressureChannel.RADIATION, 0.06)],
    "gamma_ray_burst": [(PressureChannel.RADIATION, 0.12), (PressureChannel.DIRECT, 0.08)],
    "extreme_weather": [(PressureChannel.DIRECT, 0.06), (PressureChannel.DROUGHT, 0.04)],
    "wildfire_period": [(PressureChannel.DROUGHT, 0.10), (PressureChannel.DIRECT, 0.04)],
    "pluvial_period": [(PressureChannel.DIRECT, 0.03)],
    "ocean_acidification": [(PressureChannel.TOXIN, 0.08)],
    "methane_release": [(PressureChannel.TOXIN, 0.10), (PressureChannel.THERMAL, 0.08)],
}


# ============================================================================
# 压力张量叠加层
# ============================================================================

@dataclass
class PressureTensorOverlay:
    """压力张量叠加层
    
    存储从压力修改器转换而来的空间分布压力数据。
    
    Attributes:
        overlay: 压力张量 (P, H, W)，P=通道数
        active_pressures: 本回合激活的压力类型列表
        total_intensity: 总压力强度（用于日志）
    """
    overlay: np.ndarray  # (P, H, W)
    active_pressures: List[str] = field(default_factory=list)
    total_intensity: float = 0.0
    
    @property
    def shape(self) -> Tuple[int, ...]:
        return self.overlay.shape
    
    def get_channel(self, channel: int) -> np.ndarray:
        """获取指定通道的压力数据"""
        if channel < 0 or channel >= self.overlay.shape[0]:
            return np.zeros(self.overlay.shape[1:], dtype=np.float32)
        return self.overlay[channel]
    
    @property
    def thermal(self) -> np.ndarray:
        return self.get_channel(PressureChannel.THERMAL)
    
    @property
    def drought(self) -> np.ndarray:
        return self.get_channel(PressureChannel.DROUGHT)
    
    @property
    def toxin(self) -> np.ndarray:
        return self.get_channel(PressureChannel.TOXIN)
    
    @property
    def oxygen(self) -> np.ndarray:
        return self.get_channel(PressureChannel.OXYGEN)
    
    @property
    def direct(self) -> np.ndarray:
        return self.get_channel(PressureChannel.DIRECT)
    
    @property
    def radiation(self) -> np.ndarray:
        return self.get_channel(PressureChannel.RADIATION)


# ============================================================================
# 压力张量化转换器
# ============================================================================

class PressureToTensorBridge:
    """压力到张量的桥接器
    
    将压力修改器字典和压力配置列表转换为空间分布的压力张量。
    
    特性：
    1. 全局压力：从 modifiers 字典映射到对应通道
    2. 区域压力：使用 affected_tiles 生成空间掩码
    3. 强度衰减：区域压力中心强、边缘弱（高斯衰减）
    4. 叠加效应：多个相同类型压力累加
    
    Example:
        bridge = PressureToTensorBridge()
        overlay = bridge.convert(
            modifiers=ctx.modifiers,
            pressures=ctx.pressures,
            map_shape=(64, 64),
        )
    """
    
    def __init__(
        self,
        decay_sigma: float = 2.0,
        max_decay_distance: float = 4.0,
    ):
        """
        Args:
            decay_sigma: 高斯衰减的标准差（格子数）
            max_decay_distance: 最大衰减距离（超出则为0）
        """
        self.decay_sigma = decay_sigma
        self.max_decay_distance = max_decay_distance
    
    def convert(
        self,
        modifiers: Dict[str, float],
        pressures: Optional[List["ParsedPressure"]] = None,
        map_shape: Tuple[int, int] = (64, 64),
        map_width: int = 8,
        map_height: int = 8,
        global_thermal_handled: bool = False,
        tile_coordinates: Optional[Dict[int, Tuple[int, int]]] = None,
    ) -> PressureTensorOverlay:
        """
        将压力修改器转换为空间张量
        
        Args:
            modifiers: 压力修改器字典 {modifier_name: value}
            pressures: 解析后的压力列表（用于区域压力）
            map_shape: 张量形状 (H, W)
            map_width: 地图宽度（格子数，用于坐标转换）
            map_height: 地图高度（格子数）
        
        Returns:
            PressureTensorOverlay 实例
        """
        H, W = map_shape
        overlay = np.zeros((PressureChannel.NUM_CHANNELS, H, W), dtype=np.float32)
        active = []
        total_intensity = 0.0
        
        # 1. 处理全局修改器
        for mod_name, mod_value in modifiers.items():
            if mod_name in MODIFIER_CHANNEL_MAP:
                channel, coeff = MODIFIER_CHANNEL_MAP[mod_name]
                if global_thermal_handled and channel == PressureChannel.THERMAL:
                    continue
                # 全局压力均匀分布
                overlay[channel] += mod_value * coeff
                total_intensity += abs(mod_value)
        
        # 2. 处理区域性压力（如果有）
        if pressures:
            for pressure in pressures:
                if pressure.kind not in active:
                    active.append(pressure.kind)
                
                # 获取该压力类型的通道映射
                channels = PRESSURE_KIND_CHANNELS.get(pressure.kind, [])
                if not channels:
                    continue
                
                # 创建空间掩码（如果是区域性压力）
                if hasattr(pressure, 'affected_tiles') and pressure.affected_tiles:
                    spatial_mask = self._create_spatial_mask(
                        pressure.affected_tiles,
                        map_shape,
                        map_width,
                        tile_coordinates,
                    )
                else:
                    # modifiers 已是全局压力解析结果，不再叠加同一压力的模板。
                    if modifiers:
                        continue
                    spatial_mask = np.ones((H, W), dtype=np.float32)
                
                # 叠加到对应通道
                intensity = pressure.intensity / 10.0  # 归一化到 [0, 1]
                for ch, coeff in channels:
                    if global_thermal_handled and ch == PressureChannel.THERMAL and not getattr(pressure, 'affected_tiles', None):
                        continue
                    overlay[ch] += spatial_mask * intensity * coeff
                
                total_intensity += pressure.intensity
        
        # 记录活跃压力
        if not active:
            active = [k for k, v in modifiers.items() if abs(v) > 0.1]
        
        logger.debug(
            f"[压力桥接] 转换完成: {len(active)} 种压力, "
            f"总强度={total_intensity:.1f}"
        )
        
        return PressureTensorOverlay(
            overlay=overlay,
            active_pressures=active,
            total_intensity=total_intensity,
        )
    
    def _create_spatial_mask(
        self,
        affected_tiles: List[int],
        map_shape: Tuple[int, int],
        map_width: int,
        tile_coordinates: Optional[Dict[int, Tuple[int, int]]] = None,
    ) -> np.ndarray:
        """创建带衰减的空间掩码
        
        Args:
            affected_tiles: 受影响的地块索引列表
            map_shape: 张量形状 (H, W)
            map_width: 地图宽度
        
        Returns:
            (H, W) 空间掩码，中心为1，边缘衰减
        """
        H, W = map_shape
        mask = np.zeros((H, W), dtype=np.float32)
        
        # 将地块索引转换为坐标
        centers = []
        for tile_idx in affected_tiles:
            if tile_coordinates is not None:
                coordinate = tile_coordinates.get(tile_idx)
                if coordinate is None:
                    continue
                ty, tx = coordinate
            else:
                ty, tx = divmod(tile_idx, max(1, map_width))
            if 0 <= ty < H and 0 <= tx < W:
                centers.append((ty, tx))
        
        if not centers:
            return mask
        
        # 计算到最近受影响点的距离，应用高斯衰减
        for y in range(H):
            for x in range(W):
                min_dist = float('inf')
                for cy, cx in centers:
                    dist = np.sqrt((y - cy) ** 2 + (x - cx) ** 2)
                    min_dist = min(min_dist, dist)
                
                if min_dist <= self.max_decay_distance * self.decay_sigma:
                    mask[y, x] = np.exp(-0.5 * (min_dist / self.decay_sigma) ** 2)
        
        # 确保中心点为满强度
        for cy, cx in centers:
            if 0 <= cy < H and 0 <= cx < W:
                mask[cy, cx] = 1.0
        
        return mask


# ============================================================================
# 物种参数提取器
# ============================================================================

class SpeciesParamsExtractor:
    """物种参数提取器
    
    将物种的 abstract_traits 映射到张量参数矩阵，
    用于多因子死亡率计算中的抗性评估。
    
    参数索引：
        [0] cold_res    - 耐寒性 (归一化到 [0,1])
        [1] heat_res    - 耐热性
        [2] drought_res - 耐旱性
        [3] acid_res    - 耐酸碱性
        [4] oxygen_need - 氧气需求
        [5] repro_rate  - 繁殖速度
        [6] mobility    - 运动能力
        [7] toxin_res   - 毒性抗性 (派生)
        [8] sensitivity - 基础敏感度 (派生)
        [9] is_autotroph - 自养生物标记
    """
    
    # 特质名 → 参数索引
    TRAIT_TO_PARAM = {
        "耐寒性": 0,
        "耐热性": 1,
        "耐旱性": 2,
        "耐酸碱性": 3,
        "氧气需求": 4,
        "繁殖速度": 5,
        "运动能力": 6,
    }
    
    # 派生参数索引
    PARAM_TOXIN_RES = 7
    PARAM_SENSITIVITY = 8
    PARAM_IS_AUTOTROPH = 9
    
    NUM_PARAMS = 10
    
    def extract(
        self,
        species_list: List["Species"],
    ) -> Tuple[np.ndarray, Dict[str, int]]:
        """
        提取物种参数矩阵
        
        Args:
            species_list: 物种列表
        
        Returns:
            (params, species_map)
            - params: (S, F) 参数矩阵
            - species_map: {lineage_code: index} 映射
        """
        S = len(species_list)
        params = np.zeros((S, self.NUM_PARAMS), dtype=np.float32)
        species_map = {}
        
        for i, sp in enumerate(species_list):
            species_map[sp.lineage_code] = i
            traits = sp.abstract_traits or {}
            
            # 基础特质映射（归一化到 [0, 1]）
            for trait_name, param_idx in self.TRAIT_TO_PARAM.items():
                raw_value = traits.get(trait_name, 5.0)
                params[i, param_idx] = min(1.0, max(0.0, raw_value / 15.0))
            
            # 派生参数：毒性抗性
            params[i, self.PARAM_TOXIN_RES] = self._compute_toxin_resistance(sp)
            
            # 派生参数：基础敏感度
            params[i, self.PARAM_SENSITIVITY] = self._compute_sensitivity(sp)
            
            # 自养生物标记
            diet_type = getattr(sp, 'diet_type', None) or ""
            params[i, self.PARAM_IS_AUTOTROPH] = 1.0 if diet_type == "autotroph" else 0.0
        
        logger.debug(f"[参数提取] 提取 {S} 个物种的参数矩阵")
        return params, species_map
    
    def _compute_toxin_resistance(self, sp: "Species") -> float:
        """计算毒性抗性（综合多个特质）"""
        traits = sp.abstract_traits or {}
        
        # 基础抗性来自耐酸碱性
        base = traits.get("耐酸碱性", 5.0) / 15.0
        
        # 化能自养 + 深海栖息地加成
        diet_type = getattr(sp, 'diet_type', None) or ""
        habitat = getattr(sp, 'habitat_type', None) or ""
        
        if diet_type == "autotroph" and habitat in ("deep_sea", "marine"):
            base += 0.3
        
        # 厌氧/低氧适应加成
        oxygen_need = traits.get("氧气需求", 5.0)
        if oxygen_need < 3.0:
            base += 0.2
        
        return min(1.0, max(0.0, base))
    
    def _compute_sensitivity(self, sp: "Species") -> float:
        """计算基础敏感度（影响各类压力的响应强度）"""
        traits = sp.abstract_traits or {}
        
        # 高运动能力和繁殖速度降低敏感度（恢复能力强）
        mobility = traits.get("运动能力", 5.0) / 15.0
        repro = traits.get("繁殖速度", 5.0) / 15.0
        
        sensitivity = 1.0 - (0.3 * mobility + 0.2 * repro)
        return max(0.2, min(1.0, sensitivity))
    
    def update_tensor_state_params(
        self,
        species_list: List["Species"],
        existing_params: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        """更新或创建 TensorState.species_params
        
        如果已有参数矩阵，扩展它；否则创建新的。
        """
        new_params, _ = self.extract(species_list)
        
        if existing_params is None or existing_params.shape[1] < self.NUM_PARAMS:
            return new_params
        
        # 合并：保留已有参数，更新/扩展新参数
        S = new_params.shape[0]
        if existing_params.shape[0] >= S:
            existing_params[:S, :self.NUM_PARAMS] = new_params
            return existing_params
        else:
            # 扩展行数
            expanded = np.zeros((S, existing_params.shape[1]), dtype=np.float32)
            expanded[:existing_params.shape[0]] = existing_params
            expanded[:, :self.NUM_PARAMS] = new_params
            return expanded


# ============================================================================
# 多因子死亡率计算器
# ============================================================================

@dataclass
class PressureBridgeConfig:
    """压力桥接配置
    
    【平衡调整v1】大幅降低基础死亡率
    设计目标：
      - 单压力强度10 → 约 25-35% 死亡率（无抗性物种）
      - 三压力都是10 → 约 60-75% 死亡率
    """
    # 温度压力
    thermal_multiplier: float = 3.0        # 每单位压力=多少°C温度变化（降低）
    
    # 毒性压力
    toxin_base_mortality: float = 0.06     # 每单位毒性=基础死亡率（从0.2降到0.06）
    autotroph_toxin_benefit: float = 0.15  # 化能自养受益系数（降低）
    
    # 干旱压力
    drought_base_mortality: float = 0.05   # 每单位干旱=基础死亡率（从0.15降到0.05）
    
    # 缺氧压力
    anoxic_base_mortality: float = 0.08    # 每单位缺氧=基础死亡率（从0.25降到0.08）
    aerobe_sensitivity: float = 0.6        # 需氧生物敏感度（降低）
    
    # 直接死亡
    direct_mortality_rate: float = 0.04    # 每单位=死亡率（从0.1降到0.04）
    
    # 辐射压力
    radiation_base_mortality: float = 0.04 # 每单位辐射=基础死亡率（从0.12降到0.04）
    
    # 多压力衰减（边际递减效应）
    multi_pressure_decay: float = 0.7      # 多压力时的衰减系数
    # 当多个压力通道激活时，每增加一个通道，效果乘以此系数
    # 例如：3个通道激活时，第2个通道效果×0.7，第3个×0.49
    
    @classmethod
    def from_ui_config(cls, ui_config) -> "PressureBridgeConfig":
        """从前端 UIConfig.pressure_intensity 加载配置
        
        Args:
            ui_config: UIConfig 实例或 PressureIntensityConfig 实例
        
        Returns:
            PressureBridgeConfig 实例
        """
        # 获取 pressure_intensity 配置
        if hasattr(ui_config, "pressure_intensity"):
            cfg = ui_config.pressure_intensity
        else:
            cfg = ui_config
        
        return cls(
            thermal_multiplier=getattr(cfg, "thermal_multiplier", 3.0),
            toxin_base_mortality=getattr(cfg, "toxin_base_mortality", 0.06),
            autotroph_toxin_benefit=getattr(cfg, "autotroph_toxin_benefit", 0.15),
            drought_base_mortality=getattr(cfg, "drought_base_mortality", 0.05),
            anoxic_base_mortality=getattr(cfg, "anoxic_base_mortality", 0.08),
            aerobe_sensitivity=getattr(cfg, "aerobe_sensitivity", 0.6),
            direct_mortality_rate=getattr(cfg, "direct_mortality_rate", 0.04),
            radiation_base_mortality=getattr(cfg, "radiation_base_mortality", 0.04),
            multi_pressure_decay=getattr(cfg, "multi_pressure_decay", 0.7),
        )


class MultiFactorMortality:
    """多因子死亡率计算器
    
    综合温度、干旱、毒性、缺氧、直接死亡等多个因子，
    结合物种抗性参数计算最终死亡率。
    
    公式：
        survival = Π(1 - stress_i × (1 - resistance_i))
        mortality = 1 - survival
    
    Example:
        calc = MultiFactorMortality()
        mortality = calc.compute(
            pop=tensor_state.pop,
            env=tensor_state.env,
            pressure=pressure_overlay.overlay,
            params=tensor_state.species_params,
            balance_config=engine.tensor_config.balance,
        )
    """
    
    def __init__(self, config: Optional[PressureBridgeConfig] = None):
        self.config = config or PressureBridgeConfig()
    
    def compute(
        self,
        pop: np.ndarray,              # (S, H, W)
        env: np.ndarray,              # (C, H, W)
        pressure: np.ndarray,         # (P, H, W)
        params: np.ndarray,           # (S, F)
        balance_config: Optional["TensorBalanceConfig"] = None,
    ) -> np.ndarray:
        """
        计算多因子死亡率
        
        Args:
            pop: 种群张量 (S, H, W)
            env: 环境张量 (C, H, W)
            pressure: 压力叠加层 (P, H, W)
            params: 物种参数 (S, F)
            balance_config: 张量平衡配置
        
        Returns:
            死亡率张量 (S, H, W)
        """
        S, H, W = pop.shape
        
        # 默认配置
        if balance_config is None:
            from .config import TensorBalanceConfig
            balance_config = TensorBalanceConfig()
        
        # 确保压力张量形状正确
        if pressure.shape[0] < PressureChannel.NUM_CHANNELS:
            # 扩展通道
            new_pressure = np.zeros((PressureChannel.NUM_CHANNELS, H, W), dtype=np.float32)
            new_pressure[:pressure.shape[0]] = pressure
            pressure = new_pressure
        
        # 确保参数矩阵列数足够
        if params.shape[1] < SpeciesParamsExtractor.NUM_PARAMS:
            new_params = np.zeros((S, SpeciesParamsExtractor.NUM_PARAMS), dtype=np.float32)
            new_params[:, :params.shape[1]] = params
            # 填充默认值
            new_params[:, params.shape[1]:] = 0.5
            params = new_params
        
        # 计算各因子压力
        # 1. 温度压力
        thermal_stress = self._compute_thermal_stress(
            env, pressure[PressureChannel.THERMAL], params, balance_config
        )
        
        # 2. 干旱压力
        drought_stress = self._compute_drought_stress(
            pressure[PressureChannel.DROUGHT], params
        )
        
        # 3. 毒性压力
        toxin_stress = self._compute_toxin_stress(
            pressure[PressureChannel.TOXIN], params
        )
        
        # 4. 缺氧压力
        oxygen_stress = self._compute_oxygen_stress(
            pressure[PressureChannel.OXYGEN], params
        )
        
        # 5. 直接死亡
        direct_mortality = self._compute_direct_mortality(
            pressure[PressureChannel.DIRECT]
        )
        
        # 6. 辐射压力
        radiation_stress = self._compute_radiation_stress(
            pressure[PressureChannel.RADIATION], params
        )
        
        # 【多压力衰减】边际递减效应
        # 统计有多少个压力通道是活跃的（平均值 > 0.01）
        stress_factors = [
            thermal_stress, drought_stress, toxin_stress,
            oxygen_stress, direct_mortality, radiation_stress
        ]
        
        # 对每个因子应用边际递减
        decay = self.config.multi_pressure_decay
        decayed_stresses = []
        active_count = 0
        
        for stress in stress_factors:
            avg_stress = np.abs(stress).mean()
            if avg_stress > 0.005:  # 该通道活跃
                # 应用衰减：第1个通道×1，第2个×decay，第3个×decay²...
                decay_factor = decay ** active_count
                decayed_stresses.append(stress * decay_factor)
                active_count += 1
            else:
                decayed_stresses.append(stress)
        
        # 组合死亡率（乘法模型）
        survival = np.ones((S, H, W), dtype=np.float32)
        for stress in decayed_stresses:
            survival *= (1.0 - stress)
        
        mortality = 1.0 - survival
        
        # 最终保护：即使极端情况也保留一些幸存者
        mortality = np.clip(mortality, 0.01, 0.90)  # 上限90%而非99%
        
        # 只有有种群的地方才有死亡率
        mortality[pop <= 0] = 0
        
        return mortality.astype(np.float32)
    
    def _compute_thermal_stress(
        self,
        env: np.ndarray,
        thermal_pressure: np.ndarray,
        params: np.ndarray,
        balance_config: "TensorBalanceConfig",
    ) -> np.ndarray:
        """计算温度压力
        
        考虑：
        - 基础环境温度
        - 压力叠加的温度变化
        - 物种的耐寒性和耐热性
        """
        S = params.shape[0]
        H, W = thermal_pressure.shape
        
        # 获取温度通道
        temp_idx = balance_config.temp_channel_idx
        if env.shape[0] > temp_idx:
            base_temp = env[temp_idx]
        else:
            base_temp = np.full((H, W), balance_config.temp_optimal, dtype=np.float32)
        
        # 应用压力叠加的温度变化
        effective_temp = base_temp + thermal_pressure * self.config.thermal_multiplier
        
        stress = np.zeros((S, H, W), dtype=np.float32)
        
        for s in range(S):
            cold_res = params[s, 0]  # 耐寒性
            heat_res = params[s, 1]  # 耐热性
            
            # 温度过低造成的压力
            cold_deviation = np.maximum(0, balance_config.temp_optimal - effective_temp)
            cold_stress = cold_deviation / balance_config.temp_tolerance
            cold_stress = cold_stress * (1.0 - cold_res)
            
            # 温度过高造成的压力
            heat_deviation = np.maximum(0, effective_temp - balance_config.temp_optimal)
            heat_stress = heat_deviation / balance_config.temp_tolerance
            heat_stress = heat_stress * (1.0 - heat_res)
            
            # 取两者较大值，并用 sigmoid 压缩
            combined = np.maximum(cold_stress, heat_stress)
            stress[s] = 1.0 - np.exp(-combined)
        
        return np.clip(stress, 0, 0.8)
    
    def _compute_drought_stress(
        self,
        drought_pressure: np.ndarray,
        params: np.ndarray,
    ) -> np.ndarray:
        """计算干旱压力"""
        S = params.shape[0]
        stress = np.zeros((S,) + drought_pressure.shape, dtype=np.float32)
        
        for s in range(S):
            drought_res = params[s, 2]  # 耐旱性
            sensitivity = params[s, 8]  # 基础敏感度
            
            base_stress = drought_pressure * self.config.drought_base_mortality
            stress[s] = base_stress * (1.0 - drought_res) * sensitivity
        
        return np.clip(stress, 0, 0.7)
    
    def _compute_toxin_stress(
        self,
        toxin_pressure: np.ndarray,
        params: np.ndarray,
    ) -> np.ndarray:
        """计算毒性压力
        
        化能自养生物：高毒性反而受益（负压力）
        普通生物：毒性造成死亡
        """
        S = params.shape[0]
        stress = np.zeros((S,) + toxin_pressure.shape, dtype=np.float32)
        
        for s in range(S):
            toxin_res = params[s, 7]  # 毒性抗性
            is_autotroph = params[s, 9] > 0.5
            
            if is_autotroph:
                # 化能自养生物：毒性环境受益
                benefit = toxin_pressure * self.config.autotroph_toxin_benefit * toxin_res
                stress[s] = -benefit  # 负值表示受益
            else:
                # 普通生物：毒性造成死亡
                base_stress = toxin_pressure * self.config.toxin_base_mortality
                stress[s] = base_stress * (1.0 - toxin_res)
        
        return np.clip(stress, -0.3, 0.8)  # 允许负值（受益）
    
    def _compute_oxygen_stress(
        self,
        oxygen_pressure: np.ndarray,
        params: np.ndarray,
    ) -> np.ndarray:
        """计算缺氧压力
        
        oxygen_pressure > 0 表示缺氧
        高氧气需求的物种更敏感
        """
        S = params.shape[0]
        stress = np.zeros((S,) + oxygen_pressure.shape, dtype=np.float32)
        
        for s in range(S):
            oxygen_need = params[s, 4]  # 氧气需求（高=需氧生物）
            is_autotroph = params[s, 9] > 0.5
            
            if is_autotroph:
                # 自养生物对缺氧几乎免疫
                stress[s] = oxygen_pressure * 0.01
            else:
                # 需氧生物：氧气需求越高，缺氧越致命
                # 确保最低敏感度为 0.3（即使低氧需求也受一定影响）
                sensitivity = max(0.3, oxygen_need) * self.config.aerobe_sensitivity
                stress[s] = oxygen_pressure * self.config.anoxic_base_mortality * sensitivity
        
        return np.clip(stress, 0, 0.85)
    
    def _compute_direct_mortality(
        self,
        direct_pressure: np.ndarray,
    ) -> np.ndarray:
        """计算直接死亡率
        
        直接作用于所有物种，无抗性减免
        """
        mortality = direct_pressure * self.config.direct_mortality_rate
        return np.clip(mortality, 0, 0.5)
    
    def _compute_radiation_stress(
        self,
        radiation_pressure: np.ndarray,
        params: np.ndarray,
    ) -> np.ndarray:
        """计算辐射压力
        
        深海/地下生物有天然屏蔽
        """
        S = params.shape[0]
        stress = np.zeros((S,) + radiation_pressure.shape, dtype=np.float32)
        
        for s in range(S):
            # 使用敏感度作为辐射抗性的代理
            sensitivity = params[s, 8]
            
            base_stress = radiation_pressure * self.config.radiation_base_mortality
            stress[s] = base_stress * sensitivity
        
        return np.clip(stress, 0, 0.6)


# ============================================================================
# 便捷函数
# ============================================================================

_global_bridge: Optional[PressureToTensorBridge] = None
_global_extractor: Optional[SpeciesParamsExtractor] = None
_global_mortality: Optional[MultiFactorMortality] = None


def get_pressure_bridge() -> PressureToTensorBridge:
    """获取全局压力桥接器"""
    global _global_bridge
    if _global_bridge is None:
        _global_bridge = PressureToTensorBridge()
    return _global_bridge


def get_params_extractor() -> SpeciesParamsExtractor:
    """获取全局参数提取器"""
    global _global_extractor
    if _global_extractor is None:
        _global_extractor = SpeciesParamsExtractor()
    return _global_extractor


def get_multifactor_mortality(
    config: Optional[PressureBridgeConfig] = None
) -> MultiFactorMortality:
    """获取全局多因子死亡率计算器"""
    global _global_mortality
    if _global_mortality is None or config is not None:
        _global_mortality = MultiFactorMortality(config)
    return _global_mortality


def reset_pressure_bridge() -> None:
    """重置全局实例"""
    global _global_bridge, _global_extractor, _global_mortality
    _global_bridge = None
    _global_extractor = None
    _global_mortality = None

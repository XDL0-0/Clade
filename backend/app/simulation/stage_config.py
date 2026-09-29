"""
Stage Configuration - 阶段配置

该模块定义了流水线中各阶段的配置，包括：
- 阶段是否启用
- 阶段顺序
- 阶段参数
- 多种模拟模式（minimal/standard/full/debug）
- 模式参数（默认回合时长、压力缩放系数等）

支持从 YAML 配置文件或代码配置。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Type, Dict

if TYPE_CHECKING:
    from .stages import BaseStage, StageDependency

logger = logging.getLogger(__name__)


# 支持的模式名称
AVAILABLE_MODES = ["minimal", "standard", "full", "debug"]


class StageConfigError(ValueError):
    """Invalid stage configuration; never fall back to a different pipeline."""


def _validate_mode(mode: str) -> str:
    if mode not in AVAILABLE_MODES:
        raise StageConfigError(
            f"Unknown mode {mode!r}; available modes: {', '.join(AVAILABLE_MODES)}"
        )
    return mode


# ============================================================================
# 模式参数
# ============================================================================

@dataclass
class ModeParameters:
    """模式参数配置
    
    不同模式可以指定不同的默认参数值，
    这些参数会在引擎加载模式时应用到 SimulationContext 或全局配置。
    """
    
    # 默认回合时长（秒）
    default_turn_duration: float = 1.0
    
    # 默认压力强度缩放系数
    pressure_scale: float = 1.0
    
    # 默认物种数量上限
    max_species_count: int = 500
    
    # 分化频率限制（每回合最多分化次数）
    max_speciations_per_turn: int = 5
    
    # 日志详细程度（0=最少, 1=正常, 2=详细, 3=调试）
    log_verbosity: int = 1
    
    # AI 调用超时（秒）
    ai_timeout: float = 30.0
    
    # 是否启用性能统计
    enable_profiling: bool = False
    
    # 是否启用快照自动保存
    auto_snapshot: bool = False
    
    # 自动快照间隔（回合数，0=禁用）
    snapshot_interval: int = 0
    
    # 随机种子（0=不固定）
    random_seed: int = 0
    
    # 额外的自定义参数
    custom_params: Dict[str, Any] = field(default_factory=dict)
    
    @classmethod
    def for_minimal(cls) -> "ModeParameters":
        """minimal 模式的默认参数"""
        return cls(
            default_turn_duration=0.5,
            pressure_scale=0.8,
            max_species_count=100,
            max_speciations_per_turn=2,
            log_verbosity=0,
            ai_timeout=10.0,
            enable_profiling=False,
            auto_snapshot=False,
        )
    
    @classmethod
    def for_standard(cls) -> "ModeParameters":
        """standard 模式的默认参数"""
        return cls(
            default_turn_duration=1.0,
            pressure_scale=1.0,
            max_species_count=300,
            max_speciations_per_turn=5,
            log_verbosity=1,
            ai_timeout=30.0,
            enable_profiling=False,
            auto_snapshot=False,
        )
    
    @classmethod
    def for_full(cls) -> "ModeParameters":
        """full 模式的默认参数"""
        return cls(
            default_turn_duration=2.0,
            pressure_scale=1.0,
            max_species_count=500,
            max_speciations_per_turn=10,
            log_verbosity=2,
            ai_timeout=60.0,
            enable_profiling=False,
            auto_snapshot=True,
            snapshot_interval=50,
        )
    
    @classmethod
    def for_debug(cls) -> "ModeParameters":
        """debug 模式的默认参数"""
        return cls(
            default_turn_duration=0.5,
            pressure_scale=1.0,
            max_species_count=200,
            max_speciations_per_turn=3,
            log_verbosity=3,
            ai_timeout=15.0,
            enable_profiling=True,
            auto_snapshot=True,
            snapshot_interval=10,
        )
    
    @classmethod
    def for_mode(cls, mode: str) -> "ModeParameters":
        """根据模式名称获取默认参数"""
        factories = {
            "minimal": cls.for_minimal,
            "standard": cls.for_standard,
            "full": cls.for_full,
            "debug": cls.for_debug,
        }
        return factories[_validate_mode(mode)]()
    
    def merge(self, overrides: Dict[str, Any]) -> "ModeParameters":
        """合并自定义覆盖参数"""
        import copy
        new_params = copy.copy(self)
        for key, value in overrides.items():
            if hasattr(new_params, key):
                setattr(new_params, key, value)
            else:
                new_params.custom_params[key] = value
        return new_params
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "default_turn_duration": self.default_turn_duration,
            "pressure_scale": self.pressure_scale,
            "max_species_count": self.max_species_count,
            "max_speciations_per_turn": self.max_speciations_per_turn,
            "log_verbosity": self.log_verbosity,
            "ai_timeout": self.ai_timeout,
            "enable_profiling": self.enable_profiling,
            "auto_snapshot": self.auto_snapshot,
            "snapshot_interval": self.snapshot_interval,
            "random_seed": self.random_seed,
            **self.custom_params,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ModeParameters":
        """从字典创建"""
        known_keys = {
            "default_turn_duration", "pressure_scale", "max_species_count",
            "max_speciations_per_turn", "log_verbosity", "ai_timeout",
            "enable_profiling", "auto_snapshot", "snapshot_interval", "random_seed",
        }
        kwargs = {k: v for k, v in data.items() if k in known_keys}
        custom = {k: v for k, v in data.items() if k not in known_keys}
        params = cls(**kwargs)
        params.custom_params = custom
        return params


@dataclass
class StageConfig:
    """Stage ``name`` is a stable registry ID, not the instance's display name.

    ``params`` are constructor arguments. An explicit top-level ``order`` wins
    over the constructed order; omission preserves the constructor's default.
    """
    name: str
    enabled: bool = True
    order: int | None = None
    params: dict[str, Any] = field(default_factory=dict)
    
    # 可选：阶段类（用于动态实例化）
    stage_class: Type[BaseStage] | None = None
    # 可选：阶段工厂函数（用于复杂的实例化逻辑）
    factory: Callable[..., BaseStage] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise StageConfigError("Stage name must be a non-empty registry ID")
        if not isinstance(self.enabled, bool):
            raise StageConfigError(f"Stage {self.name!r}: enabled must be a boolean")
        if self.order is not None and type(self.order) is not int:
            raise StageConfigError(f"Stage {self.name!r}: order must be an integer")
        if not isinstance(self.params, dict) or any(
            not isinstance(key, str) for key in self.params
        ):
            raise StageConfigError(f"Stage {self.name!r}: params must be a mapping with string keys")
    
    @classmethod
    def from_dict(cls, data: dict) -> "StageConfig":
        """从字典创建配置"""
        if not isinstance(data, dict) or "name" not in data:
            raise StageConfigError("Each stage must be a mapping with a registry ID in 'name'")
        unknown_keys = set(data) - {"name", "enabled", "order", "params"}
        if unknown_keys:
            raise StageConfigError(f"Stage {data['name']!r}: unknown keys {sorted(unknown_keys, key=str)}")
        return cls(
            name=data["name"],
            enabled=data.get("enabled", True),
            order=data.get("order"),
            params=data.get("params", {}),
        )


@dataclass 
class PipelineStageConfig:
    """Legacy code configuration, retained for callers; not a loader fallback."""
    
    # 核心阶段（总是启用）
    init: StageConfig = field(default_factory=lambda: StageConfig(
        name="init", enabled=True, order=0
    ))
    parse_pressures: StageConfig = field(default_factory=lambda: StageConfig(
        name="parse_pressures", enabled=True, order=10
    ))
    map_evolution: StageConfig = field(default_factory=lambda: StageConfig(
        name="map_evolution", enabled=True, order=20
    ))
    fetch_species: StageConfig = field(default_factory=lambda: StageConfig(
        name="fetch_species", enabled=True, order=30
    ))
    tiering_and_niche: StageConfig = field(default_factory=lambda: StageConfig(
        name="tiering_and_niche", enabled=True, order=40
    ))
    preliminary_mortality: StageConfig = field(default_factory=lambda: StageConfig(
        name="preliminary_mortality", enabled=True, order=50
    ))
    migration: StageConfig = field(default_factory=lambda: StageConfig(
        name="migration", enabled=True, order=60
    ))
    final_mortality: StageConfig = field(default_factory=lambda: StageConfig(
        name="final_mortality", enabled=True, order=80
    ))
    population_update: StageConfig = field(default_factory=lambda: StageConfig(
        name="population_update", enabled=True, order=90
    ))
    gene_diversity: StageConfig = field(default_factory=lambda: StageConfig(
        name="gene_diversity", enabled=True, order=93
    ))
    
    # 可选阶段（可通过配置禁用）
    tectonic_movement: StageConfig = field(default_factory=lambda: StageConfig(
        name="tectonic_movement", enabled=True, order=25
    ))
    food_web: StageConfig = field(default_factory=lambda: StageConfig(
        name="food_web", enabled=True, order=35
    ))
    gene_activation: StageConfig = field(default_factory=lambda: StageConfig(
        name="gene_activation", enabled=True, order=95
    ))
    gene_flow: StageConfig = field(default_factory=lambda: StageConfig(
        name="gene_flow", enabled=True, order=100
    ))
    genetic_drift: StageConfig = field(default_factory=lambda: StageConfig(
        name="genetic_drift", enabled=True, order=105
    ))
    auto_hybridization: StageConfig = field(default_factory=lambda: StageConfig(
        name="auto_hybridization", enabled=True, order=110
    ))
    subspecies_promotion: StageConfig = field(default_factory=lambda: StageConfig(
        name="subspecies_promotion", enabled=True, order=115
    ))
    background_management: StageConfig = field(default_factory=lambda: StageConfig(
        name="background_management", enabled=True, order=130
    ))
    build_report: StageConfig = field(default_factory=lambda: StageConfig(
        name="build_report", enabled=True, order=140
    ))
    save_map_snapshot: StageConfig = field(default_factory=lambda: StageConfig(
        name="save_map_snapshot", enabled=True, order=150
    ))
    vegetation_cover: StageConfig = field(default_factory=lambda: StageConfig(
        name="vegetation_cover", enabled=True, order=155
    ))
    save_population_snapshot: StageConfig = field(default_factory=lambda: StageConfig(
        name="save_population_snapshot", enabled=True, order=160
    ))
    embedding_hooks: StageConfig = field(default_factory=lambda: StageConfig(
        name="embedding_hooks", enabled=True, order=165
    ))
    save_history: StageConfig = field(default_factory=lambda: StageConfig(
        name="save_history", enabled=True, order=170
    ))
    export_data: StageConfig = field(default_factory=lambda: StageConfig(
        name="export_data", enabled=True, order=175
    ))
    
    def get_enabled_stages(self) -> list[StageConfig]:
        """获取所有启用的阶段配置（按顺序）"""
        all_configs = [
            self.init,
            self.parse_pressures,
            self.map_evolution,
            self.tectonic_movement,
            self.fetch_species,
            self.food_web,
            self.tiering_and_niche,
            self.preliminary_mortality,
            self.migration,
            self.final_mortality,
            self.population_update,
            self.gene_diversity,
            self.gene_activation,
            self.gene_flow,
            self.genetic_drift,
            self.auto_hybridization,
            self.subspecies_promotion,
            self.background_management,
            self.build_report,
            self.save_map_snapshot,
            self.vegetation_cover,
            self.save_population_snapshot,
            self.embedding_hooks,
            self.save_history,
            self.export_data,
        ]
        return sorted(
            [c for c in all_configs if c.enabled],
            key=lambda c: c.order if c.order is not None else 0
        )
    
    def disable_stage(self, name: str) -> None:
        """禁用指定阶段"""
        if hasattr(self, name):
            getattr(self, name).enabled = False
    
    def enable_stage(self, name: str) -> None:
        """启用指定阶段"""
        if hasattr(self, name):
            getattr(self, name).enabled = True


# 默认配置实例
DEFAULT_STAGE_CONFIG = PipelineStageConfig()


def create_stage_config_from_engine_flags(
    use_tectonic: bool = True,
    use_embedding: bool = True,
    use_tile_mortality: bool = True,
) -> PipelineStageConfig:
    """从引擎功能开关创建阶段配置
    
    Args:
        use_tectonic: 是否启用板块系统
        use_embedding: 是否启用 Embedding 集成
        use_tile_mortality: 是否启用地块死亡率
    
    Returns:
        配置好的 PipelineStageConfig
    """
    config = PipelineStageConfig()
    
    # 根据功能开关禁用相应阶段
    if not use_tectonic:
        config.disable_stage("tectonic_movement")
    
    if not use_embedding:
        config.disable_stage("embedding_hooks")
    
    return config


# ============================================================================
# YAML 配置加载
# ============================================================================

def load_stage_config_from_yaml(
    yaml_path: str | Path | None = None,
    mode: str | None = None,
    *,
    include_disabled: bool = False,
) -> list[StageConfig]:
    """Load a mode without silently substituting another pipeline.

    Selection precedence is explicit ``mode`` > YAML ``mode`` > ``standard``.
    The bundled file defaults to standard; full is only selected explicitly by
    the caller or configuration. Missing/invalid files and empty mode lists are
    errors. Omitted stage orders are resolved by StageLoader after construction.
    ``include_disabled`` lets the loader validate disabled registry IDs too;
    callers receive only enabled entries by default.
    """
    if mode is not None:
        _validate_mode(mode)

    import yaml

    config_path = Path(yaml_path) if yaml_path is not None else Path(__file__).with_suffix(".yaml")
    try:
        with config_path.open(encoding="utf-8") as config_file:
            config_data = yaml.safe_load(config_file)
    except (OSError, yaml.YAMLError) as exc:
        raise StageConfigError(f"Cannot load stage configuration {config_path}: {exc}") from exc

    if not isinstance(config_data, dict):
        raise StageConfigError("Stage configuration must be a mapping")
    current_mode = _validate_mode(mode if mode is not None else config_data.get("mode", "standard"))
    modes = config_data.get("modes")
    if not isinstance(modes, dict) or current_mode not in modes:
        raise StageConfigError(f"No configuration defined for mode {current_mode!r}")
    mode_config = modes[current_mode]
    if not isinstance(mode_config, dict):
        raise StageConfigError(f"Configuration for mode {current_mode!r} must be a mapping")
    stages_data = mode_config.get("stages")
    if not isinstance(stages_data, list) or not stages_data:
        raise StageConfigError(f"Mode {current_mode!r} must define a non-empty stages list")

    configs = []
    seen = set()
    for stage_data in stages_data:
        config = StageConfig.from_dict(stage_data)
        if config.name in seen:
            raise StageConfigError(f"Duplicate stage ID {config.name!r} in mode {current_mode!r}")
        seen.add(config.name)
        configs.append(config)
    if not any(config.enabled for config in configs):
        raise StageConfigError(f"Mode {current_mode!r} has no enabled stages")

    # Keep disabled entries for registry validation too: a typo must not become
    # a surprise when the entry is later enabled. The loader filters them out.
    configs.sort(key=lambda config: config.order if config.order is not None else 0)
    logger.info("Loaded mode %r from %s", current_mode, config_path)
    return configs if include_disabled else [config for config in configs if config.enabled]


def get_mode_description(mode: str) -> str:
    """获取模式描述"""
    descriptions = {
        "minimal": "极简模式：核心数据与张量生态阶段",
        "standard": "标准模式：默认数据流与张量生态阶段",
        "full": "扩展模式：显式配置的附加阶段与张量生态阶段",
        "debug": "调试模式：核心阶段、种群快照与详细日志",
    }
    return descriptions.get(mode, f"未知模式: {mode}")


def get_mode_parameters(mode: str, overrides: Dict[str, Any] | None = None) -> ModeParameters:
    """获取模式参数
    
    Args:
        mode: 模式名称
        overrides: 覆盖的参数
    
    Returns:
        模式参数对象
    """
    params = ModeParameters.for_mode(mode)
    if overrides:
        params = params.merge(overrides)
    return params


def load_mode_with_parameters(
    mode: str,
    yaml_path: str | Path | None = None,
    param_overrides: Dict[str, Any] | None = None,
) -> tuple[list[StageConfig], ModeParameters]:
    """加载模式配置和参数
    
    Args:
        mode: 模式名称
        yaml_path: YAML 配置文件路径
        param_overrides: 参数覆盖
    
    Returns:
        (阶段配置列表, 模式参数)
    """
    stages = load_stage_config_from_yaml(yaml_path, mode)
    params = get_mode_parameters(mode, param_overrides)
    
    logger.info(f"加载模式 '{mode}':")
    logger.info(f"  阶段数: {len(stages)}")
    logger.info(f"  压力缩放: {params.pressure_scale}")
    logger.info(f"  物种上限: {params.max_species_count}")
    logger.info(f"  日志详细度: {params.log_verbosity}")
    
    return stages, params


def format_mode_info(mode: str, params: ModeParameters | None = None) -> str:
    """格式化模式信息为可读文本"""
    if params is None:
        params = get_mode_parameters(mode)
    
    lines = [
        f"模式: {mode}",
        f"描述: {get_mode_description(mode)}",
        "",
        "参数:",
        f"  回合时长: {params.default_turn_duration}s",
        f"  压力缩放: {params.pressure_scale}",
        f"  物种上限: {params.max_species_count}",
        f"  分化上限/回合: {params.max_speciations_per_turn}",
        f"  日志详细度: {params.log_verbosity}",
        f"  AI 超时: {params.ai_timeout}s",
        f"  性能分析: {'启用' if params.enable_profiling else '禁用'}",
        f"  自动快照: {'启用' if params.auto_snapshot else '禁用'}",
    ]
    
    if params.auto_snapshot and params.snapshot_interval > 0:
        lines.append(f"  快照间隔: 每 {params.snapshot_interval} 回合")
    
    if params.random_seed > 0:
        lines.append(f"  随机种子: {params.random_seed}")
    
    if params.custom_params:
        lines.append("")
        lines.append("自定义参数:")
        for key, value in params.custom_params.items():
            lines.append(f"  {key}: {value}")
    
    return "\n".join(lines)


# ============================================================================
# 阶段注册表 - 用于插件系统
# ============================================================================

class StageRegistry:
    """阶段注册表
    
    用于集中管理所有可用的阶段类型，支持：
    - 按名称查找阶段类
    - 动态注册新阶段
    - 阶段依赖检查
    """
    
    _instance: "StageRegistry | None" = None
    
    def __new__(cls) -> "StageRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._stages = {}
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if not self._initialized:
            self._stages: dict[str, Type[BaseStage]] = {}
            self._initialized = True
    
    def register(self, name: str, stage_class: Type[BaseStage]) -> None:
        """Register a stable ID; registering the same class twice is harmless.
        
        Args:
            name: 阶段名称
            stage_class: 阶段类
        """
        if not isinstance(name, str) or not name.strip():
            raise StageConfigError("Stage registry ID must be a non-empty string")
        if name in self._stages and self._stages[name] is not stage_class:
            raise StageConfigError(f"Stage ID {name!r} is already registered")
        self._stages[name] = stage_class
    
    def get(self, name: str) -> Type[BaseStage] | None:
        """获取阶段类
        
        Args:
            name: 阶段名称
        
        Returns:
            阶段类，如果不存在则返回 None
        """
        return self._stages.get(name)
    
    def list_stages(self) -> list[str]:
        """列出所有已注册的阶段名称"""
        return list(self._stages.keys())
    
    def create_stage(self, name: str, **kwargs) -> BaseStage | None:
        """创建阶段实例
        
        Args:
            name: 阶段名称
            **kwargs: 传递给阶段构造函数的参数
        
        Returns:
            阶段实例，如果不存在则返回 None
        """
        stage_class = self.get(name)
        if stage_class:
            stage = stage_class(**kwargs)
            stage.stage_id = name
            return stage
        return None


# 全局注册表实例
stage_registry = StageRegistry()


def register_stage(name: str):
    """阶段注册装饰器
    
    使用方式：
    ```python
    @register_stage("my_custom_stage")
    class MyCustomStage(BaseStage):
        ...
    ```
    """
    def decorator(cls: Type[BaseStage]) -> Type[BaseStage]:
        stage_registry.register(name, cls)
        return cls
    return decorator


# ============================================================================
# StageLoader - 阶段加载器
# ============================================================================

@dataclass
class _DependencyStage:
    """Validator view: IDs are names here; runtime display names stay intact."""

    name: str
    order: int
    dependency: StageDependency

    def get_dependency(self) -> StageDependency:
        return self.dependency


def _dependency_stages(stages: list[BaseStage]) -> list[_DependencyStage]:
    """Accept stable IDs and unambiguous legacy display-name dependencies.

    Existing built-ins/plugins declare display names. Resolve those at this
    boundary without mutating plugin instances or weakening field validation.
    New plugins can use registry IDs directly, even when display names change.
    """
    from .stages import DependencyError, StageDependency

    stage_ids = {stage.stage_id for stage in stages}
    display_ids: dict[str, list[str]] = {}
    for stage in stages:
        display_ids.setdefault(stage.name, []).append(stage.stage_id)

    def resolve(dependency: str) -> str:
        if dependency in stage_ids:
            return dependency
        candidates = display_ids.get(dependency, [])
        if len(candidates) > 1:
            raise DependencyError(
                f"Ambiguous display-name dependency {dependency!r}; use a stage registry ID"
            )
        return candidates[0] if candidates else dependency

    views = []
    for stage in stages:
        dependency = stage.get_dependency()
        views.append(_DependencyStage(
            name=stage.stage_id,
            order=stage.order,
            dependency=StageDependency(
                requires_stages={resolve(name) for name in dependency.requires_stages},
                optional_stages={resolve(name) for name in dependency.optional_stages},
                requires_fields=dependency.requires_fields,
                writes_fields=dependency.writes_fields,
            ),
        ))
    return views


class StageLoader:
    """阶段加载器
    
    根据配置文件加载并构建 Stage 实例列表。
    负责:
    - 从配置创建 Stage 实例
    - 验证依赖关系
    - 排序阶段
    """
    
    def __init__(
        self,
        registry: StageRegistry | None = None,
        yaml_path: str | Path | None = None,
    ):
        """初始化 StageLoader
        
        Args:
            registry: 阶段注册表（默认使用全局注册表）
            yaml_path: YAML 配置文件路径
        """
        self.registry = registry or stage_registry
        self.yaml_path = yaml_path
        self._validation_errors: list[str] = []
        self._validation_warnings: list[str] = []
    
    def load_stages_for_mode(
        self,
        mode: str | None = None,
        validate: bool = True,
    ) -> list[BaseStage]:
        """Construct configured stages, then validate their resolved order.

        Configuration/constructor errors always fail, including when
        ``validate=False`` is used to inspect a dependency graph. Dependency
        validation remains enabled by default and accepts stable registry IDs
        as well as legacy display-name declarations.
        """
        from .stages import StageDependencyValidator, DependencyError

        self._validation_errors = []
        self._validation_warnings = []
        try:
            stage_configs = load_stage_config_from_yaml(self.yaml_path, mode, include_disabled=True)
            for config in stage_configs:
                if self.registry.get(config.name) is None:
                    raise StageConfigError(f"Unknown stage ID {config.name!r}")

            stages = [self._create_stage(config) for config in stage_configs if config.enabled]
            stages.sort(key=lambda stage: stage.order)
            if validate:
                result = StageDependencyValidator(_dependency_stages(stages)).validate()
                self._validation_warnings = result.warnings
                if not result.valid:
                    self._validation_errors = result.errors
                    raise DependencyError(
                        f"Mode {mode or 'configured default'!r} dependency validation failed:\n"
                        + "\n".join(result.errors)
                    )
                for warning in result.warnings:
                    logger.warning(warning)
            logger.info("[StageLoader] Loaded %s stages", len(stages))
            return stages
        except (StageConfigError, DependencyError) as exc:
            if not self._validation_errors:
                self._validation_errors = [str(exc)]
            raise

    def _create_stage(self, config: StageConfig) -> BaseStage:
        """Pass params unchanged; apply an explicit scheduling order afterwards.

        Runtime ``name`` remains the constructor's display name. ``stage_id``
        identifies the configuration entry without renaming custom stages.
        """
        constructor = config.factory or config.stage_class or self.registry.get(config.name)
        if constructor is None:
            raise StageConfigError(f"Unknown stage ID {config.name!r}")
        try:
            stage = constructor(**config.params)
            stage.stage_id = config.name
            if config.order is not None:
                # BaseStage exposes order as a read-only property over _order.
                # Do not inject an unsupported order argument into constructors.
                stage._order = config.order
                if stage.order != config.order:
                    raise ValueError("stage does not support the configured order")
            if type(stage.order) is not int:
                raise ValueError("stage order must be an integer")
            return stage
        except Exception as exc:
            raise StageConfigError(f"Cannot construct stage {config.name!r}: {exc}") from exc

    def get_validation_errors(self) -> list[str]:
        """获取验证错误"""
        return self._validation_errors.copy()
    
    def get_validation_warnings(self) -> list[str]:
        """获取验证警告"""
        return self._validation_warnings.copy()
    
    def list_available_stages(self) -> list[str]:
        """列出所有可用的阶段名称"""
        return self.registry.list_stages()
    
    def get_dependency_graph(self, mode: str = "standard") -> str:
        """获取依赖关系图
        
        Args:
            mode: 模式名称
        
        Returns:
            文本形式的依赖图
        """
        from .stages import StageDependencyValidator
        
        try:
            stages = self.load_stages_for_mode(mode, validate=False)
            validator = StageDependencyValidator(_dependency_stages(stages))
            result = validator.validate()
            return result.dependency_graph
        except Exception as e:
            return f"无法生成依赖图: {e}"


# ============================================================================
# 初始化默认阶段注册
# ============================================================================

def _register_default_stages() -> None:
    """注册所有阶段：核心数据阶段 + GPU张量计算阶段
    
    核心阶段（数据加载/持久化）：
    - init, parse_pressures, map_evolution, fetch_species
    - tiering_and_niche, food_web, gene_*, speciation
    - build_report, save_*, export_data
    
    GPU张量阶段（替代遗留CPU计算）：
    - pressure_tensor, tensor_state_init, tensor_ecology
    - tensor_state_sync, tensor_metrics
    """
    # === 核心数据阶段（从 stages.py 导入）===
    from .stages import (
        InitStage,
        ParsePressuresStage,
        MapEvolutionStage,
        TectonicMovementStage,
        FetchSpeciesStage,
        FoodWebStage,
        TieringAndNicheStage,
        PopulationUpdateStage,
        GeneDiversityStage,
        GeneActivationStage,
        GeneFlowStage,
        GeneticDriftStage,
        AutoHybridizationStage,
        SubspeciesPromotionStage,
        SpeciationDataTransferStage,  # 分化数据传递
        SpeciationStage,
        BackgroundManagementStage,
        BuildReportStage,
        SaveMapSnapshotStage,
        VegetationCoverStage,
        SavePopulationSnapshotStage,
        EmbeddingStage,
        EmbeddingPluginsStage,
        SaveHistoryStage,
        ExportDataStage,
        FinalizeStage,
    )
    
    # === GPU张量计算阶段 ===
    from .tensor_stages import (
        PressureTensorStage,
        TensorStateInitStage,
        TensorEcologyStage,
        TensorStateSyncStage,
        TensorMetricsStage,
    )
    
    # 注册核心数据阶段
    stage_registry.register("init", InitStage)
    stage_registry.register("parse_pressures", ParsePressuresStage)
    stage_registry.register("map_evolution", MapEvolutionStage)
    stage_registry.register("tectonic_movement", TectonicMovementStage)
    stage_registry.register("fetch_species", FetchSpeciesStage)
    stage_registry.register("food_web", FoodWebStage)
    stage_registry.register("tiering_and_niche", TieringAndNicheStage)
    stage_registry.register("population_update", PopulationUpdateStage)
    stage_registry.register("gene_diversity", GeneDiversityStage)
    stage_registry.register("gene_activation", GeneActivationStage)
    stage_registry.register("gene_flow", GeneFlowStage)
    stage_registry.register("genetic_drift", GeneticDriftStage)
    stage_registry.register("auto_hybridization", AutoHybridizationStage)
    stage_registry.register("subspecies_promotion", SubspeciesPromotionStage)
    stage_registry.register("speciation_data_transfer", SpeciationDataTransferStage)
    stage_registry.register("speciation", SpeciationStage)
    stage_registry.register("background_management", BackgroundManagementStage)
    stage_registry.register("build_report", BuildReportStage)
    stage_registry.register("save_map_snapshot", SaveMapSnapshotStage)
    stage_registry.register("vegetation_cover", VegetationCoverStage)
    stage_registry.register("save_population_snapshot", SavePopulationSnapshotStage)
    stage_registry.register("embedding_integration", EmbeddingStage)  # Embedding 集成
    stage_registry.register("embedding_hooks", EmbeddingPluginsStage)
    stage_registry.register("save_history", SaveHistoryStage)
    stage_registry.register("export_data", ExportDataStage)
    stage_registry.register("finalize", FinalizeStage)
    
    # 注册GPU张量阶段
    stage_registry.register("pressure_tensor", PressureTensorStage)
    stage_registry.register("tensor_state_init", TensorStateInitStage)
    stage_registry.register("tensor_ecology", TensorEcologyStage)
    stage_registry.register("tensor_state_sync", TensorStateSyncStage)
    stage_registry.register("tensor_metrics", TensorMetricsStage)
    
    logger.debug(f"[StageRegistry] 注册了 {len(stage_registry.list_stages())} 个阶段")


# 自动注册默认阶段
_register_default_stages()

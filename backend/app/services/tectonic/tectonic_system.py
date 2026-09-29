"""板块构造系统主入口

整合所有子模块，提供统一的接口。
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import fields
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from .config import TECTONIC_CONFIG
from .models import (
    Plate, PlateType, BoundaryType, MotionPhase,
    GeologicalFeature, FeatureType,
    SimpleTile, TectonicEvent, TerrainChange,
    IsolationEvent, ContactEvent, TectonicStepResult
)
from .plate_generator import PlateGenerator
from .motion_engine import PlateMotionEngine
from .geological_features import GeologicalFeatureDistributor
from .matrix_engine import TectonicMatrixEngine
from .species_tracker import PlateSpeciesTracker, SimpleSpecies, SimpleHabitat
from .mantle_dynamics import MantleDynamicsEngine, WilsonPhase, MantleDynamicsState, ConvectionCell


class TectonicSystem:
    """板块构造系统
    
    提供完整的板块构造模拟功能，可独立于主游戏系统运行。
    
    使用示例：
    ```python
    # 初始化
    system = TectonicSystem(width=128, height=40, seed=12345)
    
    # 执行一个回合
    result = system.step(pressure_modifiers={"volcanic": 5})
    
    # 获取数据
    plates = system.get_plates()
    volcanoes = system.get_volcanoes()
    tiles = system.get_tiles()
    ```
    """
    
    def __init__(
        self,
        width: int = 128,
        height: int = 40,
        seed: int | None = None,
        _restore: bool = False,
    ):
        self.width = width
        self.height = height
        self.seed = seed if seed is not None else 12345
        
        # 子系统
        self.generator = PlateGenerator(width, height)
        self.motion_engine = PlateMotionEngine(width, height)
        self.feature_distributor = GeologicalFeatureDistributor(width, height)
        self.matrix_engine = TectonicMatrixEngine(width, height)
        self.species_tracker = PlateSpeciesTracker(width, height)
        self.mantle_engine = MantleDynamicsEngine(width, height)
        
        # 状态
        self.plates: list[Plate] = []
        self.plate_map: np.ndarray | None = None
        self.tiles: list[SimpleTile] = []
        self.turn_index: int = 0
        
        # 初始化
        # Legacy generators seed module RNGs; preserve the caller's simulation
        # stream while keeping all subsequent geological randomness saveable.
        py_state, np_state = random.getstate(), np.random.get_state()
        try:
            if not _restore:
                self._initialize()
        finally:
            random.setstate(py_state)
            np.random.set_state(np_state)
        self.motion_engine.rng.seed(self.seed + 17)
        self.mantle_engine.rng.seed(self.seed + 31)
    
    def _initialize(self) -> None:
        """初始化系统"""
        # 生成板块和地块
        self.plates, self.plate_map, self.tiles = self.generator.generate(self.seed)
        
        # 检测边界（使用 motion_engine 的方法）
        self.motion_engine._detect_boundaries(self.plates, self.plate_map, self.tiles)
        
        # 初始化地幔动力学（提供板块运动驱动力）
        self.mantle_engine.initialize(self.seed)
        
        # 初始化地质特征
        self.feature_distributor.initialize(
            self.plates, self.plate_map, self.tiles, self.seed
        )
        
        # 构建矩阵
        self.matrix_engine.build(self.tiles, self.plates, self.plate_map)
    
    def step(
        self,
        pressure_modifiers: dict[str, float] | None = None,
        species_list: Sequence[SimpleSpecies] | None = None,
        habitats: Sequence[SimpleHabitat] | None = None,
        turn_years: float = 500_000,
    ) -> TectonicStepResult:
        """Integrate long classic eras without transporting many cells at once."""
        if not math.isfinite(turn_years) or turn_years <= 0:
            raise ValueError("turn_years must be a positive finite number")
        count = max(1, math.ceil(turn_years / 500_000.0))
        logical_turn = self.turn_index
        before = {t.id: (t.elevation, t.temperature) for t in self.tiles}
        old_coords = np.array([(t.x, t.y) for t in self.tiles], dtype=np.float32)
        crust_destinations = {tile.id: tile.id for tile in self.tiles}
        events, causes, feedback = [], {}, {}
        for index in range(count):
            self.turn_index = logical_turn
            result = self._step_once(
                pressure_modifiers, None, None, turn_years / count,
            )
            destinations = self.motion_engine.transport.destinations
            crust_destinations = {
                source: destinations.get(current, current)
                for source, current in crust_destinations.items()
            }
            events.extend(result.events)
            for change in result.terrain_changes:
                if change.cause in {"plate_drift", "volcanic"} or causes.get(change.tile_id) not in {"plate_drift", "volcanic"}:
                    causes[change.tile_id] = change.cause
            for key, value in result.pressure_feedback.items():
                feedback[key] = max(feedback.get(key, 0.0), value)
        changes = [TerrainChange(
            t.id, t.x, t.y, before[t.id][0], t.elevation,
            causes.get(t.id, "plate_drift"), before[t.id][1], t.temperature,
        ) for t in self.tiles if abs(t.elevation - before[t.id][0]) > 1e-8
            or abs(t.temperature - before[t.id][1]) > 1e-8]
        result.terrain_changes, result.events, result.pressure_feedback = changes, events, feedback
        result.tiles_affected = len(changes)
        result.volcanoes_erupted = sum("volcanic" in e.event_type for e in events)
        result.earthquakes_occurred = sum(e.event_type == "earthquake" for e in events)
        result.crust_destinations = crust_destinations
        if species_list and habitats:
            # Continental habitats follow crust over the whole logical turn;
            # marine populations stay in the water and use ecological dispersal.
            crust_species = {
                species.id for species in species_list
                if species.habitat_type in {"terrestrial", "aquatic", "freshwater", "amphibious", "coastal"}
            }
            moved_habitats = [SimpleHabitat(
                tile_id=crust_destinations.get(h.tile_id, h.tile_id) if h.species_id in crust_species else h.tile_id,
                species_id=h.species_id, population=h.population,
            ) for h in habitats]
            by_id = {tile.id: tile for tile in self.tiles}
            new_coords = np.array([
                (by_id[crust_destinations[t.id]].x, by_id[crust_destinations[t.id]].y)
                for t in self.tiles
            ], dtype=np.float32)
            movement = self.species_tracker.apply_plate_movement(
                old_coords, new_coords, self.tiles, species_list, moved_habitats, self.plates,
            )
            result.isolation_events = movement.isolation_events
            result.contact_events = movement.contact_events
        return result

    def _step_once(
        self,
        pressure_modifiers: dict[str, float] | None = None,
        species_list: Sequence[SimpleSpecies] | None = None,
        habitats: Sequence[SimpleHabitat] | None = None,
        turn_years: float = 500_000,
    ) -> TectonicStepResult:
        """
        执行一个回合的板块运动
        
        Args:
            pressure_modifiers: 环境压力修改器
            species_list: 物种列表（可选，用于追踪物种移动）
            habitats: 栖息地列表（可选）
            
        Returns:
            TectonicStepResult
        """
        pressure_modifiers = pressure_modifiers or {}
        if not np.isfinite(turn_years) or turn_years <= 0:
            raise ValueError("turn_years must be a positive finite number")
        self.motion_engine.time_scale = turn_years / 500_000.0
        self.mantle_engine.time_scale = turn_years / 500_000.0
        
        # 保存旧坐标
        old_coords = np.array([(t.x, t.y) for t in self.tiles], dtype=np.float32)
        
        # === 1. 地幔动力学更新（提供驱动力） ===
        mantle_result = self.mantle_engine.step(self.plates, pressure_modifiers)
        
        # 应用地幔驱动力到板块速度
        self.mantle_engine.apply_velocity_updates(
            self.plates, 
            mantle_result["velocity_updates"]
        )
        
        # 执行板块运动
        terrain_changes, events, pressure_feedback = self.motion_engine.step(
            self.plates,
            self.plate_map,
            self.tiles,
            pressure_modifiers,
            self.turn_index,
        )
        
        # 如果威尔逊周期阶段变化，添加事件
        if mantle_result["phase_changed"]:
            phase_info = self.mantle_engine.get_wilson_phase_info()
            events.append(TectonicEvent(
                event_type="wilson_phase_change",
                x=self.width // 2,
                y=self.height // 2,
                tile_id=0,
                magnitude=0,
                affected_radius=0,
                description=f"威尔逊周期进入{phase_info['phase']}阶段：{phase_info['description']}",
                plate_id=0,
            ))
        
        # 新坐标（实际上地块坐标不变，是属性变化）
        by_id = {t.id: t for t in self.tiles}
        destinations = self.motion_engine.transport.destinations
        new_coords = np.array([
            (by_id[destinations.get(t.id, t.id)].x, by_id[destinations.get(t.id, t.id)].y)
            for t in self.tiles
        ], dtype=np.float32)
        # Crust-bound features follow the moving crust. Mantle hotspots stay fixed.
        for collection in (self.feature_distributor.volcanoes,
                           self.feature_distributor.trenches,
                           self.feature_distributor.ridges,
                           self.feature_distributor.rift_lakes):
            for feature in collection:
                tile = by_id.get(destinations.get(feature.tile_id, feature.tile_id))
                if tile is not None:
                    feature.x, feature.y, feature.tile_id = tile.x, tile.y, tile.id
                    feature.plate_id = tile.plate_id
                    feature.boundary_type = tile.boundary_type
        erupted = {e.tile_id for e in events if e.event_type == "volcanic_eruption"}
        for volcano in self.feature_distributor.volcanoes:
            if volcano.tile_id in erupted:
                volcano.last_eruption_turn = self.turn_index
                volcano.dormant = False
        
        # 物种追踪
        isolation_events: list[IsolationEvent] = []
        contact_events: list[ContactEvent] = []
        
        if species_list and habitats:
            movement_result = self.species_tracker.apply_plate_movement(
                old_coords, new_coords,
                self.tiles, species_list, habitats, self.plates
            )
            isolation_events = movement_result.isolation_events
            contact_events = movement_result.contact_events
        
        # 更新矩阵
        self.matrix_engine.build(self.tiles, self.plates, self.plate_map)
        
        # 统计
        volcanoes_erupted = len([e for e in events if "volcanic" in e.event_type])
        earthquakes = len([e for e in events if e.event_type == "earthquake"])
        
        self.turn_index += 1
        
        return TectonicStepResult(
            turn_index=self.turn_index,
            terrain_changes=terrain_changes,
            events=events,
            isolation_events=isolation_events,
            contact_events=contact_events,
            pressure_feedback=pressure_feedback,
            tiles_affected=len(terrain_changes),
            plates_moved=len([p for p in self.plates if p.speed() > 0.01]),
            volcanoes_erupted=volcanoes_erupted,
            earthquakes_occurred=earthquakes,
        )
    
    def trigger_volcanic_eruption(
        self,
        pressure_type: str = "volcanic_eruption",
        intensity: int = 5,
        target_region: tuple[int, int] | None = None,
        radius: int = 5,
    ) -> list[TectonicEvent]:
        """
        手动触发火山喷发
        
        Args:
            pressure_type: 压力类型
            intensity: 强度 1-10
            target_region: 目标区域
            radius: 搜索半径
            
        Returns:
            触发的事件列表
        """
        candidates = self.feature_distributor.get_eruption_candidates(
            pressure_type, intensity, target_region, radius
        )
        
        events = []
        
        # 确定喷发数量
        if pressure_type == "supervolcano":
            num_eruptions = 1
        elif intensity >= 8:
            num_eruptions = min(3, len(candidates))
        else:
            num_eruptions = min(2, len(candidates))
        
        for volcano in candidates[:num_eruptions]:
            erupt_intensity = volcano.intensity * (intensity / 10)
            event = self.feature_distributor.trigger_eruption(
                volcano, self.turn_index, erupt_intensity
            )
            events.append(event)
            
            # 应用喷发效应
            self._apply_eruption_effects(volcano, erupt_intensity)
        
        return events
    
    def _apply_eruption_effects(
        self,
        volcano: GeologicalFeature,
        intensity: float,
    ) -> None:
        """应用火山喷发效应到周围地块"""
        effect_radius = 2 + int(intensity * 3)
        
        affected_tiles = self.matrix_engine.get_tiles_in_radius(
            volcano.x, volcano.y, effect_radius
        )
        
        tile_map = {t.id: t for t in self.tiles}
        
        for tile_id in affected_tiles:
            tile = tile_map.get(tile_id)
            if not tile:
                continue
            
            # 计算距离
            dx = min(abs(tile.x - volcano.x), self.width - abs(tile.x - volcano.x))
            dy = abs(tile.y - volcano.y)
            dist = (dx*dx + dy*dy) ** 0.5
            
            if dist < 0.1:
                dist = 0.1
            
            # 距离衰减
            factor = 1 - (dist / effect_radius)
            factor = max(0, factor)
            
            # 中心隆起
            if dist < 1:
                tile.elevation += intensity * 50 * factor
            
            # 温度上升
            tile.temperature += intensity * 5 * factor
            
            # 火山潜力增加
            tile.volcanic_potential = min(1.0, tile.volcanic_potential + 0.1 * factor)
    
    # ==================== 查询接口 ====================
    
    def get_plates(self) -> list[Plate]:
        """获取所有板块"""
        return self.plates.copy()
    
    def get_plate(self, plate_id: int) -> Plate | None:
        """获取指定板块"""
        for p in self.plates:
            if p.id == plate_id:
                return p
        return None
    
    def get_tiles(self) -> list[SimpleTile]:
        """获取所有地块"""
        return self.tiles.copy()
    
    def get_tile(self, tile_id: int) -> SimpleTile | None:
        """获取指定地块"""
        for t in self.tiles:
            if t.id == tile_id:
                return t
        return None
    
    def get_tile_at(self, x: int, y: int) -> SimpleTile | None:
        """获取指定坐标的地块"""
        tile_id = y * self.width + x
        return self.get_tile(tile_id)
    
    def get_volcanoes(self) -> list[GeologicalFeature]:
        """获取所有火山"""
        return self.feature_distributor.volcanoes.copy()
    
    def get_hotspots(self) -> list[tuple[int, int]]:
        """获取所有热点"""
        return self.feature_distributor.hotspots.copy()
    
    def get_wilson_phase(self) -> dict:
        """获取威尔逊周期信息"""
        return self.mantle_engine.get_wilson_phase_info()
    
    def get_convection_cells(self) -> list[dict]:
        """获取对流单元信息"""
        return [
            {
                "id": c.id,
                "center_x": c.center_x,
                "center_y": c.center_y,
                "radius": c.radius,
                "strength": c.strength,
                "direction": c.direction,
            }
            for c in self.mantle_engine.state.convection_cells
        ]
    
    def get_trenches(self) -> list[GeologicalFeature]:
        """获取所有海沟"""
        return self.feature_distributor.trenches.copy()
    
    def get_boundary_tiles(self) -> list[SimpleTile]:
        """获取所有边界地块"""
        return [t for t in self.tiles if t.boundary_type != BoundaryType.INTERNAL]
    
    def get_plate_map(self) -> np.ndarray:
        """获取板块ID矩阵"""
        return self.plate_map.copy() if self.plate_map is not None else np.array([])
    
    # ==================== 统计接口 ====================
    
    def get_statistics(self) -> dict[str, Any]:
        """获取系统统计信息"""
        n_tiles = len(self.tiles)
        n_plates = len(self.plates)
        
        # 边界统计
        boundary_counts = {}
        for t in self.tiles:
            bt = t.boundary_type.name
            boundary_counts[bt] = boundary_counts.get(bt, 0) + 1
        
        # 海拔统计
        elevations = [t.elevation for t in self.tiles]
        land_tiles = sum(1 for e in elevations if e >= 0)
        
        # 地质特征统计
        n_volcanoes = len(self.feature_distributor.volcanoes)
        n_hotspots = len(self.feature_distributor.hotspots)
        n_trenches = len(self.feature_distributor.trenches)
        
        # 威尔逊周期信息
        wilson_info = self.mantle_engine.get_wilson_phase_info()
        
        return {
            "turn_index": self.turn_index,
            "dimensions": {"width": self.width, "height": self.height},
            "n_tiles": n_tiles,
            "n_plates": n_plates,
            "land_ratio": land_tiles / n_tiles if n_tiles > 0 else 0,
            "elevation": {
                "mean": sum(elevations) / len(elevations) if elevations else 0,
                "min": min(elevations) if elevations else 0,
                "max": max(elevations) if elevations else 0,
            },
            "boundaries": boundary_counts,
            "features": {
                "volcanoes": n_volcanoes,
                "hotspots": n_hotspots,
                "trenches": n_trenches,
            },
            "wilson_cycle": {
                "phase": wilson_info["phase"],
                "progress": wilson_info["progress"],
                "turns_remaining": wilson_info["turns_remaining"],
                "total_cycles": wilson_info["total_cycles"],
                "aggregation": wilson_info["aggregation"],
            },
            "mantle": {
                "activity": self.mantle_engine.state.mantle_activity,
                "convection_cells": len(self.mantle_engine.state.convection_cells),
            },
            "plates": [
                {
                    "id": p.id,
                    "type": p.plate_type.value,
                    "phase": p.motion_phase.value,
                    "speed": p.speed(),
                    "tile_count": p.tile_count,
                }
                for p in self.plates
            ],
        }
    
    # ==================== 序列化接口 ====================
    
    def to_dict(self) -> dict[str, Any]:
        """序列化为字典"""
        return {
            "width": self.width,
            "height": self.height,
            "seed": self.seed,
            "turn_index": self.turn_index,
            "plates": [p.to_dict() for p in self.plates],
            "plate_map": self.plate_map.tolist() if self.plate_map is not None else [],
            "tiles": [t.to_dict() for t in self.tiles],
            "volcanoes": [v.to_dict() for v in self.feature_distributor.volcanoes],
            "hotspots": self.feature_distributor.hotspots,
            "trenches": [v.to_dict() for v in self.feature_distributor.trenches],
            "ridges": [v.to_dict() for v in self.feature_distributor.ridges],
            "rift_lakes": [v.to_dict() for v in self.feature_distributor.rift_lakes],
            "mantle": self.mantle_engine.state.to_dict(),
            "transport": self.motion_engine.transport.to_dict(),
            "motion_rng": self.motion_engine.rng.getstate(),
            "mantle_rng": self.mantle_engine.rng.getstate(),
            "species_tracking": {
                "connectivity": [[a, b, value] for (a, b), value in self.species_tracker._plate_connectivity.items()],
                "distances": [[a, b, value] for (a, b), value in self.species_tracker._last_plate_distances.items()],
                "isolation": self.species_tracker._isolation_history,
            },
            "version": 2,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TectonicSystem":
        """Restore all evolving state; absent fields retain old-save defaults."""
        complete = all(data.get(name) for name in ("plates", "tiles", "plate_map", "mantle"))
        system = cls(width=data["width"], height=data["height"], seed=data.get("seed", 12345), _restore=complete)
        system.turn_index = int(data.get("turn_index", 0))
        if data.get("plates"):
            system.plates = [Plate.from_dict(p) for p in data["plates"]]
        if data.get("tiles"):
            allowed = {f.name for f in fields(SimpleTile)}
            system.tiles = []
            for row in data["tiles"]:
                values = {k: v for k, v in row.items() if k in allowed}
                values["boundary_type"] = BoundaryType(values.get("boundary_type", 0))
                system.tiles.append(SimpleTile(**values))
        if data.get("plate_map"):
            system.plate_map = np.asarray(data["plate_map"], dtype=np.int32)
        for name in ("volcanoes", "trenches", "ridges", "rift_lakes"):
            if name not in data:
                continue
            features = []
            for row in data[name]:
                values = dict(row)
                values["feature_type"] = FeatureType(values["feature_type"])
                boundary = values.get("boundary_type")
                values["boundary_type"] = BoundaryType(boundary) if boundary is not None else None
                features.append(GeologicalFeature(**values))
            setattr(system.feature_distributor, name, features)
        if "hotspots" in data:
            system.feature_distributor.hotspots = [tuple(point) for point in data["hotspots"]]
        system.feature_distributor._used_names = {
            feature.name for feature in system.feature_distributor.volcanoes if feature.name
        }
        if data.get("mantle"):
            values = dict(data["mantle"])
            values["wilson_phase"] = WilsonPhase(values.get("wilson_phase", "drifting"))
            values["convection_cells"] = [ConvectionCell(**cell) for cell in values.get("convection_cells", [])]
            system.mantle_engine.state = MantleDynamicsState(**values)
        system.motion_engine.transport.restore(data.get("transport", {}))
        tracking = data.get("species_tracking", {})
        system.species_tracker._plate_connectivity = {
            (a, b): value for a, b, value in tracking.get("connectivity", [])
        }
        system.species_tracker._last_plate_distances = {
            (a, b): value for a, b, value in tracking.get("distances", [])
        }
        system.species_tracker._isolation_history = {
            int(key): value for key, value in tracking.get("isolation", {}).items()
        }
        def tuples(value):
            return tuple(tuples(item) for item in value) if isinstance(value, (list, tuple)) else value
        for key, engine in (("motion_rng", system.motion_engine), ("mantle_rng", system.mantle_engine)):
            if key in data:
                engine.rng.setstate(tuples(data[key]))
        system.motion_engine._detect_boundaries(system.plates, system.plate_map, system.tiles)
        system.matrix_engine.build(system.tiles, system.plates, system.plate_map)
        return system
    
    def save(self, path: str | Path) -> None:
        """保存到文件"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        
        data = self.to_dict()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    @classmethod
    def load(cls, path: str | Path) -> "TectonicSystem":
        """从文件加载"""
        path = Path(path)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        return cls.from_dict(data)

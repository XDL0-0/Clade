"""Classic world stages: actual map physics, persisted with the classic save."""
from __future__ import annotations

import hashlib
from collections import Counter

from .stages import BaseStage, StageDependency
from .constants import get_time_config
from ..models.environment import MapState
from ..repositories.environment_repository import environment_repository as maps
from ..repositories.species_repository import species_repository
from ..schemas.responses import MapChange


class ClassicMapEvolutionStage(BaseStage):
    def __init__(self):
        super().__init__(20, "地图演化")

    def get_dependency(self):
        return StageDependency(
            requires_stages={"parse_pressures"}, requires_fields={"turn_index"},
            writes_fields={"current_map_state", "map_changes", "all_tiles"},
        )

    async def execute(self, ctx, engine):
        ctx.current_map_state = maps.get_state() or maps.save_state(MapState())
        ctx.all_tiles = maps.list_tiles()
        ctx.map_changes = [MapChange(
            stage=ctx.current_map_state.stage_name,
            description=event.description,
            affected_region=f"{len(event.affected_tiles)} 个地块" if event.affected_tiles else "全球",
            change_type="major_event",
        ) for event in ctx.major_events]
        ctx.plugin_data["classic_world"] = {
            "old_biomes": {tile.id: tile.biome for tile in ctx.all_tiles},
        }


class ClassicTectonicStage(BaseStage):
    def __init__(self):
        super().__init__(25, "板块构造运动")

    def get_dependency(self):
        return StageDependency(
            requires_stages={"map_evolution"}, requires_fields={"current_map_state", "all_tiles"},
            writes_fields={"tectonic_result", "modifiers", "plugin_data"},
        )

    async def execute(self, ctx, engine):
        from ..services.tectonic import TectonicIntegration

        tiles = ctx.all_tiles
        if not tiles:
            return
        ctx.emit_event("stage", "🌍 板块漂移、造山与火山活动", "地质")
        state = ctx.current_map_state
        saved = (state.extra_data or {}).get("classic_tectonic")
        width, height = max(t.x for t in tiles) + 1, max(t.y for t in tiles) + 1
        if saved:
            engine.tectonic = TectonicIntegration.from_dict(saved)
        else:
            # Older saves acquire a stable tectonic seed from their own map.
            identity = ";".join(f"{t.x},{t.y},{t.elevation:.2f}" for t in sorted(tiles, key=lambda t: (t.y, t.x)))
            seed = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:4], "big")
            engine.tectonic = TectonicIntegration(width, height, seed)
        engine._use_tectonic_system = True
        if (state.extra_data or {}).get("classic_tectonic_turn") == ctx.turn_index:
            return
        species = [sp for sp in species_repository.list_species() if sp.status == "alive"]
        habitats = [
            {"tile_id": h.tile_id, "species_id": h.species_id, "population": h.population}
            for h in maps.latest_habitats()
        ]
        result = engine.tectonic.step(
            species_list=species, habitat_data=habitats, map_tiles=tiles,
            pressure_modifiers=ctx.plugin_data.get("classic_global_modifiers", ctx.modifiers),
            turn_years=get_time_config(ctx.turn_index)["years_per_turn"],
        )
        ctx.tectonic_result = result
        from .classic_crust_population import carry_populations
        carried_population = carry_populations(species, habitats, result.crust_destinations, ctx.turn_index)
        if carried_population:
            ctx.map_changes.append(MapChange(
                stage=result.wilson_phase["phase"], change_type="plate_drift",
                description=f"大陆漂移带动 {carried_population:,} 个陆地及近岸个体随栖息地移动。",
                affected_region="移动板块",
            ))
        by_coord = {(t.x, t.y): t for t in tiles}
        for change in result.terrain_changes:
            tile = by_coord.get((change["x"], change["y"]))
            if tile is not None:
                tile.elevation = change["new_elevation"]
                tile.temperature = change.get("new_temperature", tile.temperature)
        for internal in engine.tectonic.tectonic.tiles:
            tile = by_coord.get((internal.x, internal.y))
            if tile is not None:
                for name in ("plate_id", "crust_thickness", "volcanic_potential", "earthquake_risk", "boundary_type", "distance_to_boundary"):
                    value = getattr(internal, name, None)
                    if value is not None:
                        value = value.name.lower() if name == "boundary_type" and hasattr(value, "name") else value
                        setattr(tile, name, value)
        state.stage_name = result.wilson_phase["phase"]
        state.stage_progress = int(result.wilson_phase.get("progress", 0) * 100)
        state.stage_duration = 100
        state.extra_data = {
            **(state.extra_data or {}), "classic_tectonic": engine.tectonic.tectonic.to_dict(),
            "classic_tectonic_turn": ctx.turn_index,
        }
        maps.upsert_tiles(tiles)
        ctx.current_map_state = maps.save_state(state)
        for key, value in result.pressure_feedback.items():
            ctx.modifiers[key] = ctx.modifiers.get(key, 0.0) + value
            global_modifiers = ctx.plugin_data.setdefault("classic_global_modifiers", {})
            global_modifiers[key] = global_modifiers.get(key, 0.0) + value
        causes = Counter(change["cause"] for change in result.terrain_changes)
        labels = {
            "uplift": "造山隆起", "erosion": "侵蚀", "subsidence": "地壳沉降",
            "volcanic": "火山地貌", "plate_drift": "板块漂移", "collision": "碰撞造山",
            "subduction": "俯冲", "volcanic_arc": "火山弧", "rifting": "大陆张裂",
            "internal": "板块内部调整",
        }
        for cause, count in causes.items():
            ctx.map_changes.append(MapChange(
                stage=state.stage_name, change_type=cause,
                description=f"{labels.get(cause, cause)}改变了 {count} 个地块的海拔。",
                affected_region=f"{count} 个地块",
            ))
        for summary in result.get_major_events_summary():
            ctx.emit_event("info", summary, "地质")
            ctx.map_changes.append(MapChange(stage=state.stage_name, change_type="tectonic_event", description=summary, affected_region="板块边界"))
        resource_manager = engine.resource_manager
        if resource_manager:
            resource_manager.initialize_tiles(tiles)
            for event in result.tectonic_events:
                if "volcanic" not in event["type"]:
                    continue
                radius = max(1, event.get("radius", 1))
                for tile in tiles:
                    dx = abs(tile.x - event["x"])
                    dx = min(dx, width - dx)
                    if dx * dx + (tile.y - event["y"]) ** 2 <= radius * radius:
                        resource_manager.apply_event_pulse(tile.id, "volcanic_ash", duration_turns=5)


class ClassicClimateStage(BaseStage):
    def __init__(self):
        super().__init__(27, "长期气候变化")

    def get_dependency(self):
        return StageDependency(
            requires_stages={"map_evolution", "tectonic_movement"},
            requires_fields={"current_map_state", "all_tiles"},
            writes_fields={"temp_delta", "sea_delta", "current_map_state", "all_tiles", "plugin_data"},
        )

    async def execute(self, ctx, engine):
        from .classic_environment import advance_classic_climate
        from ..services.species.habitat_manager import habitat_manager

        ctx.emit_event("stage", "🌡️ 冰期、温室效应与海平面", "气候")
        result = advance_classic_climate(
            ctx.current_map_state, ctx.all_tiles,
            ctx.plugin_data.get("classic_global_modifiers", ctx.modifiers),
            turn_index=ctx.turn_index,
            turn_years=get_time_config(ctx.turn_index)["years_per_turn"],
            tectonic=engine.tectonic,
        )
        ctx.temp_delta = result.temperature_delta
        ctx.sea_delta = result.sea_level_delta
        ctx.plugin_data["classic_world"]["climate"] = result
        # Temperature/humidity reach actual map tiles before the GPU state is built.
        maps.upsert_tiles(ctx.all_tiles)
        ctx.current_map_state = maps.save_state(ctx.current_map_state)
        engine.map_manager.reclassify_terrain_by_sea_level(ctx.current_map_state.sea_level)
        ctx.all_tiles = maps.list_tiles()
        old_biomes = ctx.plugin_data["classic_world"].pop("old_biomes", {})
        changed = [tile for tile in ctx.all_tiles if old_biomes.get(tile.id) != tile.biome]
        if changed:
            habitat_manager.clear_all_caches()
            # Keep the original populations for the GPU mortality/movement step;
            # the old relocation helper silently killed 30% before accounting.
            ctx.emit_event("info", f"海陆与气候变化：{len(changed)} 个地块改变生态环境，物种将重新寻找适宜栖息地", "生态")
        ctx.map_changes.append(MapChange(
            stage=result.phase, description=result.summary,
            affected_region="全球与各地气候带", change_type="climate_change",
        ))
        ctx.emit_event("info", result.summary, "气候")

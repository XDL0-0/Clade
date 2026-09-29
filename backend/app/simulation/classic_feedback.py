"""Publish classic mechanics even when the player disables AI turn narration."""
from __future__ import annotations

from .stages import BaseStage, StageDependency
from ..schemas.world_dynamics import (
    ClimateProgress, GeologicalProgress, MutualismConnection, WorldDynamics,
)
from ..services.analytics.turn_report import TurnReportService
from ..repositories.environment_repository import environment_repository


class ClassicFeedbackStage(BaseStage):
    def __init__(self):
        super().__init__(145, "世界动态反馈")

    def get_dependency(self):
        return StageDependency(
            requires_stages={"build_report"}, requires_fields={"report"},
            writes_fields={"report"},
            optional_stages={"long_term_climate", "ecological_realism"},
        )

    async def execute(self, ctx, engine):
        report = ctx.report
        if report is None:
            return
        data = WorldDynamics()
        state = ctx.current_map_state
        if state:
            # Includes the short ecological history added after climate saving.
            environment_repository.save_state(state)
            report.global_temperature = state.global_avg_temperature
            report.sea_level = state.sea_level
            report.tectonic_stage = state.stage_name
        report.map_changes = list(ctx.map_changes)
        climate = ctx.plugin_data.get("classic_world", {}).get("climate")
        if climate and state:
            data.climate = ClimateProgress(
                phase=climate.phase, temperature=state.global_avg_temperature,
                temperature_delta=climate.temperature_delta, sea_level=state.sea_level,
                sea_level_delta=climate.sea_level_delta, summary=climate.summary,
                co2_ppm=climate.co2_ppm, ice_fraction=climate.ice_fraction,
            )
        tectonic = ctx.tectonic_result
        if tectonic:
            changes = tectonic.terrain_changes
            data.geology = GeologicalProgress(
                phase=tectonic.wilson_phase["phase"], plate_count=len(engine.tectonic.get_plates()),
                changed_tiles=len(changes), uplift_tiles=sum(c["delta"] > 0 for c in changes),
                subsidence_tiles=sum(c["delta"] < 0 for c in changes),
                max_uplift_m=max((max(0.0, c["delta"]) for c in changes), default=0.0),
                eruptions=tectonic.volcano_eruption_count, earthquakes=tectonic.earthquake_count,
                isolated_species=len(tectonic.isolation_events), contacts=len(tectonic.contact_events),
            )
        ecology = ctx.plugin_data.get("ecological_realism", {})
        links = ecology.get("mutualism_links", [])
        data.mutualism_link_count = len(links)
        descriptions = {
            "pollination": "访花者获得食物，植物获得传粉机会；效果受双方数量和栖息地重叠限制。",
            "seed_dispersal": "食果者获得食物，并可把植物传播到它们到达的邻近地块。",
            "explicit_mutualism": "伙伴提供资源，依赖方在伙伴短缺或消失时面临额外压力。",
        }
        # Reports remain bounded; the complete network is recomputed next turn.
        for link in sorted(links, key=lambda item: (-item["strength"], item["species_a"], item["species_b"]))[:24]:
            data.mutualism_links.append(MutualismConnection(
                species_a=link["species_a"], species_b=link["species_b"],
                relationship_type=link["relationship_type"], strength=link["strength"],
                description=descriptions.get(link["relationship_type"], ""),
            ))
        data.seeds_dispersed = int(ecology.get("mutualism_seed_dispersal", {}).get("moved_population", 0))
        data.dependent_species_at_risk = sorted({
            *ecology.get("mutualism_missing_partners", {}).keys(),
            *(code for code, value in ecology.get("mutualism_reproduction_modifiers", {}).items() if value < 1.0),
        })
        if ecology:
            service = TurnReportService(
                report_builder=engine.report_builder, environment_repository=environment_repository,
                trophic_service=engine.trophic_service, emit_event_fn=ctx.emit_event,
            )
            report.ecological_realism = service._build_ecological_realism_summary([], ecology)
            for snapshot in report.species:
                snapshot.ecological_realism = service._build_ecological_realism_snapshot(snapshot.lineage_code, ecology)
        if ctx.plugin_data.get("tensor_ecology", {}).get("population_resolved"):
            data.population_rule = "种群由地块生态计算统一结算：出生、死亡与迁徙分别记录；食物短缺和拥挤影响恢复速度。"
        report.world_dynamics = data

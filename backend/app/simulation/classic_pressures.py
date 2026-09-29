"""Keep global climate and regional interventions spatially distinct."""
from ..repositories.environment_repository import environment_repository


def prepare_pressure_scope(ctx, engine) -> None:
    tiles = environment_repository.list_tiles()
    ctx.all_tiles = tiles
    global_pressures = []
    active_pressures = []
    for command, parsed in zip(ctx.command.pressures, ctx.pressures):
        if command.target_region is None:
            # Empty affected_tiles is the pressure bridge's global convention.
            parsed.affected_tiles = []
            global_pressures.append(parsed)
        else:
            x, y = command.target_region
            radius = command.radius or 1
            parsed.affected_tiles = [
                tile.id for tile in tiles
                if abs(tile.x - x) <= radius and abs(tile.y - y) <= radius
            ]
            if not parsed.affected_tiles:
                continue
        active_pressures.append(parsed)
    ctx.pressures = active_pressures
    config = getattr(ctx.ui_config, "pressure_intensity", None)
    ctx.modifiers = engine.environment.apply_pressures(active_pressures, config)
    ctx.plugin_data["classic_global_modifiers"] = engine.environment.apply_pressures(global_pressures, config)

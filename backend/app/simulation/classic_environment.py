"""Persistent long-term climate for the existing classic world pipeline.

This augments the classic plate, terrain and ecological models. At the default
500,000-year turn, orbital forcing is averaged over the interval: sampling a
100,000-year sine once per turn would alias every ice age away. Longer orbital
envelopes, ice feedback and a coarse carbon balance remain resolved.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Sequence


CLIMATE_STATE_KEY = "classic_climate"
TECTONIC_STATE_KEY = "classic_tectonic"


@dataclass(frozen=True)
class ClimateEvolutionResult:
    temperature_delta: float
    sea_level_delta: float
    phase: str
    summary: str
    co2_ppm: float
    ice_fraction: float
    elapsed_years: float
    updated: bool = True


def _interval_sine(start: float, duration: float, period: float) -> float:
    omega = 2.0 * math.pi / period
    return (math.cos(omega * start) - math.cos(omega * (start + duration))) / (omega * duration)


def advance_classic_climate(
    map_state: Any,
    tiles: Sequence[Any],
    modifiers: dict[str, float],
    *,
    turn_index: int,
    turn_years: float = 500_000,
    tectonic: Any | None = None,
) -> ClimateEvolutionResult:
    """Mutate map_state and tiles once; caller owns persistence/reclassification.

    ``tectonic`` accepts the integration object, system, or its saved dictionary.
    State lives entirely in MapState.extra_data, including the replay guard.
    Missing state adopts the current save's temperature/sea-level as its baseline.
    Do not also call MapEvolutionService.calculate_climate_changes this turn.
    """
    if not math.isfinite(turn_years) or turn_years <= 0:
        raise ValueError("turn_years must be a positive finite number")
    extra = dict(map_state.extra_data or {})
    state = dict(extra.get(CLIMATE_STATE_KEY) or {})
    old_temp, old_sea = float(map_state.global_avg_temperature), float(map_state.sea_level)
    if state.get("last_turn") == turn_index:
        return ClimateEvolutionResult(0.0, 0.0, state.get("phase", "间冰期"), "",
                                      state.get("co2_ppm", 280.0), state.get("ice_fraction", 0.15),
                                      state.get("elapsed_years", 0.0), updated=False)
    baseline_temp = float(state.get("baseline_temperature", old_temp))
    baseline_sea = float(state.get("baseline_sea_level", old_sea))
    baseline_ice = float(state.get("baseline_ice", max(0.0, min(1.0, (17.0 - old_temp) / 14.0))))
    ice = float(state.get("ice_fraction", baseline_ice))
    co2 = float(state.get("co2_ppm", 280.0))
    elapsed = float(state.get("elapsed_years", 0.0))

    phase_name, mantle_activity = "drifting", 0.5
    if isinstance(tectonic, dict):
        mantle = tectonic.get("mantle", {})
        phase_name = mantle.get("wilson_phase", phase_name)
        mantle_activity = float(mantle.get("mantle_activity", mantle_activity))
    elif tectonic is not None:
        system = getattr(tectonic, "tectonic", tectonic)
        mantle = system.mantle_engine.state
        phase_name, mantle_activity = mantle.wilson_phase.value, mantle.mantle_activity

    # Slow carbon-cycle envelopes plus existing mantle activity/outgassing and
    # silicate weathering on exposed relief. Values are phenomenological knobs,
    # not a reconstruction of a particular geological era.
    land = [t for t in tiles if t.elevation > old_sea]
    relief = sum(max(0.0, t.elevation - old_sea) for t in land) / max(1, len(land))
    baseline_relief = float(state.get("baseline_relief", relief))
    degassing = {"rifting": 0.30, "subduction": 0.20, "orogeny": -0.12,
                 "collision": -0.20, "supercontinent": -0.12}.get(phase_name, 0.0)
    carbon_envelope = 0.65 * _interval_sine(elapsed, turn_years, 60_000_000)
    weathering = (relief - baseline_relief) / 4000.0 + max(0.0, old_temp - baseline_temp) * 0.035
    target_co2 = 280.0 * math.exp(carbon_envelope + degassing + (mantle_activity - 0.5) * 0.4 - weathering)
    co2 += (target_co2 - co2) * -math.expm1(-turn_years / 2_000_000.0)
    co2 = max(80.0, min(4000.0, co2))
    greenhouse = 3.0 * math.log2(co2 / 280.0)
    orbital = (0.8 * _interval_sine(elapsed, turn_years, 100_000.0)
               + 0.4 * _interval_sine(elapsed, turn_years, 41_000.0)
               + 3.5 * _interval_sine(elapsed, turn_years, 8_000_000.0))
    target_ice = max(0.0, min(1.0, baseline_ice - (greenhouse + orbital) / 12.0))
    ice += (target_ice - ice) * -math.expm1(-turn_years / 1_000_000.0)
    equilibrium = baseline_temp + greenhouse + orbital - 6.0 * (ice - baseline_ice)
    natural_delta = (equilibrium - old_temp) * -math.expm1(-turn_years / 750_000.0)

    # Preserve the classic pressure coefficients, applied at this single point.
    forced_delta = (modifiers.get("temperature", 0.0) * 0.3
                    - modifiers.get("volcanic", 0.0) * 0.2
                    - modifiers.get("impact", 0.0) * 0.4
                    + modifiers.get("humidity", 0.0) * 0.05
                    + modifiers.get("drought", 0.0) * 0.1)
    new_temp = max(-60.0, min(65.0, old_temp + natural_delta + forced_delta))
    # Pressure-induced cooling also grows ice; ice is a stock, so a cold world
    # cannot subtract another 50 metres of sea level indefinitely every turn.
    ice = max(0.0, min(1.0, ice - forced_delta / 30.0))
    sea_forcing = float(state.get("sea_forcing", 0.0))
    sea_forcing += modifiers.get("flood", 0.0) * 2.0 + modifiers.get("sea_level", 0.0) * 2.0
    sea_forcing *= math.exp(-turn_years / 5_000_000.0)
    new_sea = baseline_sea - 120.0 * (ice - baseline_ice) + 2.5 * (new_temp - baseline_temp) + sea_forcing
    temp_delta, sea_delta = new_temp - old_temp, new_sea - old_sea
    map_state.global_avg_temperature, map_state.sea_level = new_temp, new_sea

    height = max((t.y for t in tiles), default=0) + 1
    weights = [1.0 + 0.6 * abs((t.y + 0.5) / height * 2.0 - 1.0) for t in tiles]
    average_weight = sum(weights) / max(1, len(weights))
    humidity_delta = modifiers.get("humidity", 0.0) * 0.015 - modifiers.get("drought", 0.0) * 0.02
    for tile, weight in zip(tiles, weights):
        tile.temperature += temp_delta * weight / average_weight
        tile.humidity = max(0.02, min(0.98, tile.humidity + humidity_delta - temp_delta * 0.003))
        tile.relative_elevation = tile.elevation - new_sea

    climate_phase = "冰期" if ice > baseline_ice + 0.12 else "温室期" if greenhouse > 1.5 or new_temp > baseline_temp + 2.5 else "间冰期"
    summary = (f"长期气候进入{climate_phase}：全球均温{new_temp:.2f}°C（{temp_delta:+.2f}°C），"
               f"海平面{new_sea:.1f}米（{sea_delta:+.1f}米），CO₂ {co2:.0f} ppm，冰量指数{ice:.0%}。")
    state.update(version=1, last_turn=turn_index, elapsed_years=elapsed + turn_years,
                 baseline_temperature=baseline_temp, baseline_sea_level=baseline_sea,
                 baseline_ice=baseline_ice, baseline_relief=baseline_relief,
                 co2_ppm=co2, ice_fraction=ice, sea_forcing=sea_forcing, phase=climate_phase)
    extra[CLIMATE_STATE_KEY] = state
    map_state.extra_data = extra
    return ClimateEvolutionResult(temp_delta, sea_delta, climate_phase, summary, co2, ice, elapsed + turn_years)

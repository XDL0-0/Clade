"""Legacy global climate scalars; this does not update tile climate or water cycles.

The kernel preserves MapEvolutionService.calculate_climate_changes arithmetic.
The stage separately preserves MapEvolutionStage's empty-pressure and 0.01 gates.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

from ...context import TurnContext
from ...contracts import SimulationStage, StageContract, StageResult, StateDelta, StatePatch
from ...events import WorldEvent
from ...values import JsonValue, digest

MODEL_VERSION = "legacy-pressure-v1"


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a finite number")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def _modifiers(value: object) -> dict[str, float]:
    if not isinstance(value, Mapping):
        raise TypeError("pressure_modifiers must be a mapping")
    result = {}
    for key, amount in value.items():
        if not isinstance(key, str):
            raise TypeError("pressure_modifiers keys must be strings")
        result[key] = _finite_number(amount, f"pressure_modifiers.{key}")
    return result


def calculate_climate_changes(
    pressure_modifiers: Mapping[str, float], global_avg_temperature: float
) -> tuple[float, float]:
    """Return legacy (temperature change in °C, sea-level change in metres).

    The ice-age term is intentionally uncapped. Unknown finite modifiers,
    including tectonic, have no numerical effect. No random state is consumed.
    """
    modifiers = _modifiers(pressure_modifiers)
    temperature = _finite_number(global_avg_temperature, "global_avg_temperature")
    temp_change = 0.0
    sea_level_modifier = 0.0

    # Keep the old operation order, including multiplication on the right.
    if "temperature" in modifiers:
        temp_change += modifiers["temperature"] * 0.3
    if "volcanic" in modifiers:
        temp_change -= modifiers["volcanic"] * 0.2
    if "impact" in modifiers:
        temp_change -= modifiers["impact"] * 0.4
    if "humidity" in modifiers:
        temp_change += modifiers["humidity"] * 0.05
    if "drought" in modifiers:
        temp_change += modifiers["drought"] * 0.1
    if "flood" in modifiers:
        sea_level_modifier += modifiers["flood"] * 2.0

    sea_level_change = temp_change * 2.5 + sea_level_modifier
    new_temp = temperature + temp_change
    if new_temp < 5.0:
        ice_age_factor = (5.0 - new_temp) / 5.0
        sea_level_change -= ice_age_factor * 50
    if new_temp > 25.0:
        heat_factor = (new_temp - 25.0) / 10.0
        heat_factor = min(1.0, heat_factor)
        sea_level_change += heat_factor * 30

    _finite_number(new_temp, "result.global_avg_temperature")
    return (
        _finite_number(temp_change, "temperature_change"),
        _finite_number(sea_level_change, "sea_level_change"),
    )


def _map_state(context: TurnContext) -> Mapping[str, JsonValue]:
    state = context.environment_state.get("map_state")
    if not isinstance(state, Mapping):
        raise TypeError("environment.map_state must be a mapping")
    for field in ("global_avg_temperature", "sea_level"):
        if field not in state:
            raise ValueError(f"environment.map_state.{field} is required")
        _finite_number(state[field], f"environment.map_state.{field}")
    return state


def _command_id(context: TurnContext) -> str:
    if "command_id" not in context.command:
        return digest(context.command)
    command_id = context.command["command_id"]
    if not isinstance(command_id, str) or not command_id.strip():
        raise ValueError("command_id must be a non-empty string")
    return command_id


class LegacyClimatePressureStage(SimulationStage):
    """Migrate only the legacy global temperature/sea-level update slice.

    This emits no tile-array changes, terrain classifications, geological stage
    transitions, or hydrology. Non-empty pressures and the old 0.01 update gate
    remain part of this stage's versioned semantics.
    """

    contract = StageContract(
        name="legacy_climate_pressure",
        version="1",
        reads=("state.environment.map_state",),
        writes=("state.environment.map_state",),
    )

    def validate_inputs(self, context: TurnContext) -> None:
        super().validate_inputs(context)
        _map_state(context)
        _modifiers(context.command.get("pressure_modifiers", {}))
        _command_id(context)

    def execute(self, context: TurnContext) -> StageResult:
        self.validate_inputs(context)
        state = _map_state(context)
        temperature = _finite_number(state["global_avg_temperature"], "global_avg_temperature")
        sea_level = _finite_number(state["sea_level"], "sea_level")
        modifiers = _modifiers(context.command.get("pressure_modifiers", {}))
        temp_delta, sea_delta = (
            calculate_climate_changes(modifiers, temperature) if modifiers else (0.0, 0.0)
        )
        applied = abs(temp_delta) > 0.01 or abs(sea_delta) > 0.01
        metrics: dict[str, JsonValue] = {
            "model_version": MODEL_VERSION,
            "scope": "global_scalars",
            "temperature_delta_c": temp_delta,
            "sea_level_delta_m": sea_delta,
            "applied": applied,
        }
        if not applied:
            return StageResult(self.contract.name, metrics=metrics)

        new_temperature = _finite_number(temperature + temp_delta, "result.global_avg_temperature")
        new_sea_level = _finite_number(sea_level + sea_delta, "result.sea_level")
        patches = tuple(
            StatePatch(("environment", "map_state", field), "replace", value, digest(state[field]))
            for field, value in (
                ("global_avg_temperature", new_temperature),
                ("sea_level", new_sea_level),
            )
        )
        command_id = _command_id(context)
        event = WorldEvent.create(
            version=context.world_version.advance(),
            turn=context.turn_id,
            command_id=command_id,
            stage=self.contract.name,
            ordinal=0,
            event_type="ClimateShift",
            cause=(command_id,),
            payload={
                **metrics,
                "pressure_modifiers": modifiers,
                "temperature_before_c": temperature,
                "temperature_after_c": new_temperature,
                "sea_level_before_m": sea_level,
                "sea_level_after_m": new_sea_level,
            },
        )
        return StageResult(
            self.contract.name,
            state_delta=StateDelta(state=patches),
            events=(event,),
            metrics=metrics,
        )

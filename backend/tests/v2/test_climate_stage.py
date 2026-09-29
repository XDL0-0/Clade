"""Oracle parity and isolation checks for the migrated global climate slice."""

from __future__ import annotations

import math
import random
import struct
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import numpy as np
import pytest

from app.models.environment import MapState
from app.services.geo.map_evolution import MapEvolutionService
from app.simulation.v2.context import TurnContext, WorldSnapshot
from app.simulation.v2.contracts import SimulationStage, StageContract, StageResult
from app.simulation.v2.pipeline import DeterministicPipeline, StageExecutionError
from app.simulation.v2.stages.environment.climate import (
    MODEL_VERSION,
    LegacyClimatePressureStage,
    calculate_climate_changes,
)
from app.simulation.v2.values import FrozenArray, JsonValue, digest
from app.simulation.v2.version import WorldVersion


def _oracle(modifiers: dict[str, float], temperature: float) -> tuple[float, float]:
    # Do not instantiate the legacy service: its constructor consumes global RNG.
    return MapEvolutionService.calculate_climate_changes(
        cast(MapEvolutionService, None),
        modifiers,
        cast(MapState, SimpleNamespace(global_avg_temperature=temperature)),
    )


def _context(
    command: Mapping[str, JsonValue] | None = None,
    temperature: float = 15.0,
    sea_level: float = 10.0,
) -> TurnContext:
    return TurnContext(
        turn_id=8,
        seed=712,
        command={"pressure_modifiers": {"temperature": 5.0}} if command is None else command,
        snapshot=WorldSnapshot(
            WorldVersion("world", "timeline", 2, 41),
            turn_id=7,
            state={
                "environment": {
                    "map_state": {
                        "global_avg_temperature": temperature,
                        "sea_level": sea_level,
                        "stage_progress": 4,
                    },
                    "geology": {"untouched": True},
                },
                "species": {"private": "untouched"},
            },
            arrays={"temperature": FrozenArray.from_numpy(np.array([12.0, 13.0]))},
        ),
    )


@pytest.mark.parametrize(
    ("temperature", "modifiers", "expected"),
    [
        (
            15.0,
            {
                "temperature": 5.0,
                "volcanic": 2.5,
                "impact": 1.25,
                "humidity": 10.0,
                "drought": 5.0,
                "flood": 2.0,
                "tectonic": 99.0,
            },
            (1.5, 7.75),
        ),
        (4.0, {"temperature": -5.0}, (-1.5, -28.75)),
        (34.0, {"temperature": 5.0}, (1.5, 33.75)),
    ],
)
def test_exact_recorded_fixtures(
    temperature: float, modifiers: dict[str, float], expected: tuple[float, float]
) -> None:
    before = random.getstate()
    assert calculate_climate_changes(modifiers, temperature) == expected
    assert _oracle(modifiers, temperature) == expected
    assert random.getstate() == before


@pytest.mark.parametrize(
    "temperature",
    [
        -40.0,
        0.0,
        math.nextafter(5.0, -math.inf),
        5.0,
        math.nextafter(5.0, math.inf),
        15.0,
        math.nextafter(25.0, -math.inf),
        25.0,
        math.nextafter(25.0, math.inf),
        math.nextafter(35.0, -math.inf),
        35.0,
        math.nextafter(35.0, math.inf),
        80.0,
    ],
)
@pytest.mark.parametrize(
    "modifiers",
    [
        {},
        {"tectonic": 5.0, "unknown": -9.0},
        {"temperature": -10.0},
        {"flood": -2.0},
        {"temperature": 0.1, "volcanic": 0.2, "impact": 0.3},
        {
            "temperature": 8.13,
            "volcanic": -2.19,
            "impact": 1.77,
            "humidity": 3.43,
            "drought": -4.11,
            "flood": 7.97,
        },
    ],
)
def test_kernel_matches_legacy_float_bits_at_boundaries_and_with_combined_pressures(
    temperature: float, modifiers: dict[str, float]
) -> None:
    expected = _oracle(modifiers, temperature)
    actual = calculate_climate_changes(modifiers, temperature)
    assert struct.pack("!dd", *actual) == struct.pack("!dd", *expected)
    assert calculate_climate_changes(dict(reversed(list(modifiers.items()))), temperature) == actual


@pytest.mark.parametrize("value", [True, False, "3", None, math.nan, math.inf, -math.inf, 10**400])
def test_kernel_rejects_invalid_values_including_ignored_modifiers(value: object) -> None:
    with pytest.raises((TypeError, ValueError), match="finite number|finite"):
        calculate_climate_changes({"ignored": cast(float, value)}, 15.0)
    with pytest.raises((TypeError, ValueError), match="finite number|finite"):
        calculate_climate_changes({}, cast(float, value))


@pytest.mark.parametrize("value", [None, [], 1.0, {1: 2.0}])
def test_kernel_requires_string_keyed_mapping(value: object) -> None:
    with pytest.raises(TypeError, match="mapping|strings"):
        calculate_climate_changes(cast(Mapping[str, float], value), 15.0)


def test_overflow_cannot_escape_as_a_nonfinite_result() -> None:
    with pytest.raises(ValueError, match="finite"):
        calculate_climate_changes({"flood": 1e308}, 15.0)
    context = _context({"pressure_modifiers": {"flood": 8e307}}, sea_level=1e308)
    with pytest.raises(StageExecutionError, match="finite"):
        DeterministicPipeline([LegacyClimatePressureStage()]).execute(context)


def test_stage_changes_only_global_scalars_in_a_private_candidate() -> None:
    context = _context()
    before_id = context.snapshot.snapshot_id
    before_command = digest(context.command)
    before_rng = random.getstate()
    stage = LegacyClimatePressureStage()
    candidate = DeterministicPipeline([stage]).execute(context)
    result = candidate.stage_results[0]
    assert stage.contract == StageContract(
        "legacy_climate_pressure",
        "1",
        reads=("state.environment.map_state",),
        writes=("state.environment.map_state",),
    )
    assert MODEL_VERSION == "legacy-pressure-v1"
    assert result.stage_version == "1"
    assert {(p.path, p.operation) for p in result.state_delta.state} == {
        (("environment", "map_state", "global_avg_temperature"), "replace"),
        (("environment", "map_state", "sea_level"), "replace"),
    }
    assert result.state_delta.arrays == ()
    assert candidate.environment_state["map_state"] == {
        "global_avg_temperature": 16.5,
        "sea_level": 13.75,
        "stage_progress": 4,
    }
    assert candidate.environment_state["geology"] == context.environment_state["geology"]
    assert candidate.species_state == context.species_state
    assert candidate.snapshot.arrays == context.snapshot.arrays
    assert context.snapshot.snapshot_id == before_id
    assert digest(context.command) == before_command
    assert random.getstate() == before_rng
    assert context.stage_results == ()


@pytest.mark.parametrize("temperature", [-40.0, 15.0, 80.0])
@pytest.mark.parametrize("command", [{}, {"pressure_modifiers": {}}])
def test_stage_preserves_empty_pressure_noop_even_when_kernel_has_extreme_temperature_term(
    temperature: float, command: Mapping[str, JsonValue]
) -> None:
    context = _context(command, temperature=temperature)
    candidate = DeterministicPipeline([LegacyClimatePressureStage()]).execute(context)
    result = candidate.stage_results[0]
    assert candidate.snapshot == context.snapshot
    assert result.state_delta.state == ()
    assert result.events == ()
    assert result.metrics["temperature_delta_c"] == 0.0
    assert result.metrics["sea_level_delta_m"] == 0.0
    assert result.metrics["applied"] is False
    if temperature != 15.0:
        assert calculate_climate_changes({}, temperature)[1] != 0.0


@pytest.mark.parametrize(
    ("modifiers", "applied"),
    [
        ({"flood": 0.005}, False),
        ({"flood": -0.005}, False),
        ({"flood": math.nextafter(0.005, math.inf)}, True),
        ({"flood": math.nextafter(-0.005, -math.inf)}, True),
        ({"temperature": 0.01}, False),
        ({"temperature": 0.02}, True),
        ({"temperature": 1.0, "flood": -0.375}, True),
        ({"tectonic": 0.0}, False),
    ],
)
def test_stage_preserves_legacy_application_threshold(
    modifiers: dict[str, float], applied: bool
) -> None:
    context = _context({"pressure_modifiers": modifiers})
    expected_temp, expected_sea = _oracle(modifiers, 15.0)
    candidate = DeterministicPipeline([LegacyClimatePressureStage()]).execute(context)
    result = candidate.stage_results[0]
    assert result.metrics["applied"] is applied
    assert bool(result.events) is applied
    state = cast(Mapping[str, JsonValue], candidate.environment_state["map_state"])
    assert state["global_avg_temperature"] == 15.0 + (expected_temp if applied else 0.0)
    assert state["sea_level"] == 10.0 + (expected_sea if applied else 0.0)
    if not applied:
        assert candidate.snapshot == context.snapshot


@pytest.mark.parametrize("explicit_id", [None, "command-123"])
def test_event_identity_cause_units_and_proposed_version(explicit_id: str | None) -> None:
    command: dict[str, JsonValue] = {"pressure_modifiers": {"temperature": 5.0}}
    if explicit_id is not None:
        command["command_id"] = explicit_id
    context = _context(command)
    stage = LegacyClimatePressureStage()
    first = stage.execute(context)
    event = first.events[0]
    command_id = explicit_id or digest(context.command)
    assert event.event_id == digest(
        [context.world_version.advance().to_dict(), command_id, stage.contract.name, 0]
    )
    assert event.type == "ClimateShift"
    assert event.version == context.world_version.advance()
    assert event.turn == context.turn_id
    assert event.cause == (command_id,)
    assert event.payload == {
        "model_version": "legacy-pressure-v1",
        "scope": "global_scalars",
        "pressure_modifiers": {"temperature": 5.0},
        "temperature_before_c": 15.0,
        "temperature_after_c": 16.5,
        "temperature_delta_c": 1.5,
        "sea_level_before_m": 10.0,
        "sea_level_after_m": 13.75,
        "sea_level_delta_m": 3.75,
        "applied": True,
    }
    assert stage.execute(context) == first
    later = replace(context, snapshot=replace(context.snapshot, version=event.version))
    assert stage.execute(later).events[0].event_id != event.event_id


@pytest.mark.parametrize("field", ["global_avg_temperature", "sea_level"])
@pytest.mark.parametrize("invalid", [True, "15", None])
def test_stage_rejects_nonnumeric_required_map_fields(field: str, invalid: JsonValue) -> None:
    context = _context()
    state: dict[str, JsonValue] = {"global_avg_temperature": 15.0, "sea_level": 0.0}
    state[field] = invalid
    context = replace(
        context,
        snapshot=replace(context.snapshot, state={"environment": {"map_state": state}}),
    )
    with pytest.raises(StageExecutionError, match="finite number"):
        DeterministicPipeline([LegacyClimatePressureStage()]).execute(context)


@pytest.mark.parametrize(
    "map_state", [{}, {"global_avg_temperature": 15.0}, {"sea_level": 0.0}, None]
)
def test_stage_rejects_absent_required_fields(map_state: JsonValue) -> None:
    context = _context()
    context = replace(
        context,
        snapshot=replace(context.snapshot, state={"environment": {"map_state": map_state}}),
    )
    with pytest.raises(StageExecutionError, match="required|mapping"):
        DeterministicPipeline([LegacyClimatePressureStage()]).execute(context)


@pytest.mark.parametrize(
    "command",
    [
        {"pressure_modifiers": None},
        {"pressure_modifiers": {"temperature": True}},
        {"command_id": ""},
        {"command_id": " "},
        {"command_id": 1},
    ],
)
def test_stage_validates_explicit_command_inputs(command: Mapping[str, JsonValue]) -> None:
    with pytest.raises(StageExecutionError):
        DeterministicPipeline([LegacyClimatePressureStage()]).execute(_context(command))


def test_integer_state_values_keep_correct_patch_preconditions() -> None:
    context = _context(temperature=15, sea_level=10)
    candidate = DeterministicPipeline([LegacyClimatePressureStage()]).execute(context)
    assert candidate.stage_results[0].state_delta.state[0].expected_hash == digest(15)


def test_later_stage_failure_discards_the_climate_candidate() -> None:
    context = _context()
    before = context.snapshot.snapshot_id

    class FailAfterClimate(SimulationStage):
        contract = StageContract(
            "failure",
            "1",
            dependencies=("legacy_climate_pressure",),
            reads=("state.environment.map_state",),
        )

        def execute(self, view: TurnContext) -> StageResult:
            state = cast(Mapping[str, JsonValue], view.environment_state["map_state"])
            assert state["global_avg_temperature"] == 16.5
            raise RuntimeError("failure after climate")

    candidate = None
    with pytest.raises(StageExecutionError, match="failure after climate"):
        candidate = DeterministicPipeline(
            [FailAfterClimate(), LegacyClimatePressureStage()]
        ).execute(context)
    assert candidate is None
    assert context.snapshot.snapshot_id == before
    assert context.stage_results == ()
    assert context.active_events == ()


def test_climate_import_does_not_load_legacy_gpu_database_or_ai_modules() -> None:
    script = """
import sys
from app.simulation.v2.stages.environment import LegacyClimatePressureStage
blocked = ('taichi', 'app.tensor', 'app.services', 'app.core.database', 'app.ai')
loaded = [name for name in sys.modules if any(
    name == prefix or name.startswith(prefix + '.') for prefix in blocked
)]
assert loaded == [], loaded
assert LegacyClimatePressureStage.contract.side_effects == ()
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr

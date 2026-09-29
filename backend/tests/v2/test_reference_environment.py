"""CPU-only acceptance for the new environment model, not legacy parity."""

from __future__ import annotations

import math
import random
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import replace
from typing import cast

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from numpy.typing import NDArray

from app.simulation.v2.context import TurnContext, WorldSnapshot
from app.simulation.v2.contracts import SimulationStage
from app.simulation.v2.pipeline import DeterministicPipeline, StageExecutionError
from app.simulation.v2.reducer import apply_delta, restrict
from app.simulation.v2.reference.environment import (
    MODEL_VERSION,
    BiomeStage,
    ClimateStage,
    GeologyStage,
)
from app.simulation.v2.reference.hydrology import HydrologyStage
from app.simulation.v2.values import FrozenArray, JsonValue, digest
from app.simulation.v2.version import WorldVersion


def context(
    width: int = 4,
    height: int = 3,
    *,
    seed: int = 71,
    environment: Mapping[str, JsonValue] | None = None,
    arrays: Mapping[str, NDArray[np.generic]] | None = None,
    command: Mapping[str, JsonValue] | None = None,
) -> TurnContext:
    size = width * height
    fields = {
        key: FrozenArray.from_numpy(np.full(size, value, dtype=np.float64))
        for key, value in {
            "temperature": 15,
            "soil_water": 100,
            "surface_water": 0,
            "soil_quality": 0.5,
            "humidity": 0.5,
            "plant_biomass": 80,
            "rainfall": 30,
            "river_flux": 0,
            "volcanic_stress": 0,
        }.items()
    }
    fields.update(
        {
            "elevation": FrozenArray.from_numpy(np.linspace(-50, 350, size)),
            "plate_id": FrozenArray.from_numpy(np.zeros(size, dtype=np.int64)),
            "biome": FrozenArray.from_numpy(np.full(size, 4, dtype=np.int64)),
        }
    )
    fields.update({key: FrozenArray.from_numpy(value) for key, value in (arrays or {}).items()})
    env: dict[str, JsonValue] = {
        "baseline_temperature": 15.0,
        "global_temperature": 15.0,
        "co2_ppm": 280.0,
        "warming_offset": 0.0,
        "sea_level": 0.0,
        "ecological_years_per_turn": 1 / 12,
        "geological_years_per_turn": 1000.0,
        "plates": (
            {"id": 0, "x": 0.0, "y": 0.0, "vx": 0.0005, "vy": 0.0002},
            {"id": 1, "x": width / 2, "y": float(height - 1), "vx": -0.0003, "vy": -0.0001},
        ),
    }
    env.update(environment or {})
    return TurnContext(
        turn_id=1,
        seed=seed,
        command=command or {},
        snapshot=WorldSnapshot(
            WorldVersion("reference-world", "reference-timeline", 0, 0),
            turn_id=0,
            state={"geometry": {"width": width, "height": height}, "environment": env},
            arrays=fields,
            manifest={"model": MODEL_VERSION},
        ),
    )


def single(value: TurnContext, stage: SimulationStage) -> TurnContext:
    view = restrict(value, stage.contract.reads)
    stage.validate_inputs(view)
    proposal = stage.execute(view)
    stage.validate_outputs(view, proposal)
    return replace(
        value,
        snapshot=apply_delta(value.snapshot, proposal.state_delta, writes=stage.contract.writes),
        stage_results=(proposal,),
    )


def field(value: TurnContext, name: str) -> NDArray[np.float64]:
    return np.array(value.snapshot.arrays[name].numpy(), dtype=np.float64)


def stages() -> list[SimulationStage]:
    return [ClimateStage(), GeologyStage(), HydrologyStage(), BiomeStage()]


def test_climate_explicit_co2_warming_and_sea_level_causality() -> None:
    value = context(command={"command_id": "raise-carbon", "co2_ppm": 560.0, "warming_offset": 2.0})
    baseline = value.snapshot.state_hash
    changed = single(value, ClimateStage())
    env = changed.environment_state
    assert env["global_temperature"] == 15.5
    assert env["sea_level"] == 1.25
    assert env["co2_ppm"] == 560
    assert env["warming_offset"] == 2
    event = changed.stage_results[0].events[0]
    assert event.type == "ClimateShift"
    assert event.payload["target_temperature_c"] == 20
    assert event.cause == ("raise-carbon",)
    assert value.snapshot.state_hash == baseline
    assert value.environment_state["co2_ppm"] == 280


def test_latitude_season_lapse_and_bounded_vegetation_rainfall_feedback() -> None:
    blank = np.zeros(12)
    base = context(arrays={"elevation": blank, "plant_biomass": blank})
    no_trees = single(base, ClimateStage())
    trees = single(
        context(arrays={"elevation": blank, "plant_biomass": np.full(12, 1e6)}), ClimateStage()
    )
    assert np.all(field(trees, "rainfall") > field(no_trees, "rainfall"))
    assert np.all(field(trees, "rainfall") < 1.25 * field(no_trees, "rainfall"))
    assert field(no_trees, "rainfall")[4] > field(no_trees, "rainfall")[0]
    mountain = single(context(arrays={"elevation": np.full(12, 1000.0)}), ClimateStage())
    np.testing.assert_allclose(field(no_trees, "temperature") - field(mountain, "temperature"), 6.5)
    summer = single(replace(base, turn_id=4), ClimateStage())
    winter = single(replace(base, turn_id=10), ClimateStage())
    assert field(summer, "temperature")[0] > field(winter, "temperature")[0]
    np.testing.assert_array_equal(
        field(single(replace(base, turn_id=13), ClimateStage()), "temperature"),
        field(no_trees, "temperature"),
    )


def test_plate_centers_persist_move_wrap_and_resume_without_hidden_history() -> None:
    value = context(
        environment={"plates": ({"id": 0, "x": 3.5, "y": 1.5, "vx": 0.001, "vy": 0.002},)}
    )
    first = single(value, GeologyStage())
    plates = cast(tuple[Mapping[str, JsonValue], ...], first.environment_state["plates"])
    assert plates[0]["x"] == 0.5
    assert plates[0]["y"] == 2.0
    restored = WorldSnapshot(
        first.snapshot.version,
        first.snapshot.turn_id,
        state=first.snapshot.state,
        arrays=first.snapshot.arrays,
        manifest=first.snapshot.manifest,
    )
    continuation = replace(first, turn_id=2, stage_results=())
    replay = replace(continuation, snapshot=restored)
    assert (
        single(continuation, GeologyStage()).snapshot.state_hash
        == single(replay, GeologyStage()).snapshot.state_hash
    )
    original_plates = cast(tuple[Mapping[str, JsonValue], ...], value.environment_state["plates"])
    assert original_plates[0]["x"] == 3.5


def test_plate_voronoi_periodic_seam_and_zero_year_erosion_conservation() -> None:
    value = context(
        6,
        1,
        environment={
            "geological_years_per_turn": 0.0,
            "plates": (
                {"id": 0, "x": 5.75, "y": 0.0, "vx": 0.0, "vy": 0.0},
                {"id": 1, "x": 2.0, "y": 0.0, "vx": 0.0, "vy": 0.0},
            ),
        },
    )
    output = single(value, GeologyStage())
    assert field(output, "plate_id")[0] == 0
    assert field(output, "plate_id")[2] == 1
    assert np.sum(field(output, "elevation")) == pytest.approx(
        np.sum(field(value, "elevation")), abs=1e-10
    )
    assert output.stage_results[0].metrics["volcanic_elevation_delta_m"] == 0
    assert not np.array_equal(field(value, "elevation"), field(output, "elevation"))


def test_convergence_and_divergence_have_opposite_terrain_effects() -> None:
    def moving(direction: int) -> TurnContext:
        return context(
            8,
            1,
            environment={
                "geological_years_per_turn": 100.0,
                "plates": (
                    {"id": 0, "x": 1.0, "y": 0.0, "vx": direction * 0.0001, "vy": 0.0},
                    {"id": 1, "x": 5.0, "y": 0.0, "vx": direction * -0.0001, "vy": 0.0},
                ),
            },
            arrays={"elevation": np.full(8, 100.0)},
        )

    converging = single(moving(1), GeologyStage())
    diverging = single(moving(-1), GeologyStage())
    assert field(converging, "elevation")[3] > 100
    assert field(diverging, "elevation")[3] < 100
    assert np.max(np.abs(field(converging, "elevation") - 100)) <= 100


@pytest.mark.parametrize("seed", [7, 91])
def test_volcano_events_and_stress_replay_for_two_seeds(seed: int) -> None:
    value = context(
        20,
        10,
        seed=seed,
        environment={
            "geological_years_per_turn": 100000.0,
            "plates": (
                {"id": 0, "x": 0.0, "y": 4.0, "vx": 0.0, "vy": 0.0},
                {"id": 1, "x": 10.0, "y": 4.0, "vx": 0.0, "vy": 0.0},
            ),
        },
    )
    one, two = single(value, GeologyStage()), single(value, GeologyStage())
    assert one.snapshot.state_hash == two.snapshot.state_hash
    assert one.stage_results[0].events == two.stage_results[0].events
    assert one.stage_results[0].events
    for event in one.stage_results[0].events:
        index = cast(int, event.payload["tile_id"])
        assert event.type == "Volcano"
        assert field(one, "volcanic_stress")[index] == 1
        assert event.target == f"tile:{index}"


@settings(max_examples=50, deadline=None)
@given(half_width=st.integers(1, 4), height=st.integers(1, 6), seed=st.integers(0, 100000))
def test_hypothesis_small_cylinders_conserve_all_water(
    half_width: int,
    height: int,
    seed: int,
) -> None:
    width = 2 * half_width
    size = width * height
    rng = np.random.default_rng(seed)
    value = context(
        width,
        height,
        arrays={
            "elevation": rng.integers(-2, 20, size).astype(np.float64) * 10,
            "temperature": rng.uniform(-20, 40, size),
            "rainfall": rng.uniform(0, 80, size),
            "soil_water": rng.uniform(0, 300, size),
            "surface_water": rng.uniform(0, 30, size),
            "soil_quality": rng.uniform(0, 1, size),
        },
    )
    output = single(value, HydrologyStage())
    ledger = output.stage_results[0].metrics
    before = math.fsum(field(value, "soil_water")) + math.fsum(field(value, "surface_water"))
    incoming = math.fsum(field(value, "rainfall"))
    after = math.fsum(field(output, "soil_water")) + math.fsum(field(output, "surface_water"))
    assert (
        abs(
            before
            + incoming
            - cast(float, ledger["evaporation_mm"])
            - cast(float, ledger["ocean_export_mm"])
            - after
        )
        < 1e-8
    )
    assert abs(cast(float, ledger["water_balance_residual_mm"])) < 1e-8
    assert np.all(field(output, "soil_water") >= 0)
    assert np.all(field(output, "surface_water") >= 0)
    assert np.all(field(output, "soil_water") <= 150 + 100 * field(value, "soil_quality"))
    assert np.all((field(output, "humidity") >= 0) & (field(output, "humidity") <= 1))


def test_subthreshold_rivers_accumulate_through_dry_tiles_and_retain_lakes() -> None:
    value = context(
        6,
        1,
        arrays={
            "elevation": np.array([6.0, 5.0, 4.0, 3.0, 2.0, 1.0]),
            "temperature": np.full(6, -10.0),
            "soil_water": np.array([0.0, 200.0, 200.0, 0.0, 0.0, 0.0]),
            "humidity": np.zeros(6),
            "rainfall": np.array([0.0, 1.0, 0.5, 0.0, 0.0, 0.0]),
        },
    )
    output = single(value, HydrologyStage())
    np.testing.assert_allclose(field(output, "river_flux"), [0, 1, 1.5, 1.5, 1.5, 1.5])
    assert field(output, "humidity")[3] == 0
    assert field(output, "surface_water")[5] == 1.5
    flooded = context(
        6,
        1,
        arrays={
            "elevation": np.array([6.0, 5.0, 4.0, 3.0, 2.0, 1.0]),
            "temperature": np.full(6, -10.0),
            "soil_water": np.full(6, 200.0),
            "rainfall": np.full(6, 30.0),
        },
    )
    lake = single(single(flooded, HydrologyStage()), BiomeStage())
    assert field(lake, "surface_water")[5] == 180
    assert field(lake, "biome")[5] == 1


def test_ocean_receives_existing_stores_rainfall_and_river_export() -> None:
    value = context(
        2,
        1,
        arrays={
            "elevation": np.array([-1.0, 1.0]),
            "temperature": np.full(2, -10.0),
            "soil_water": np.array([5.0, 200.0]),
            "surface_water": np.array([2.0, 0.0]),
            "rainfall": np.array([3.0, 1.0]),
        },
    )
    output = single(value, HydrologyStage())
    assert output.stage_results[0].metrics["ocean_export_mm"] == 11
    np.testing.assert_allclose(field(output, "surface_water"), [0, 0])
    np.testing.assert_allclose(field(output, "soil_water"), [0, 200])
    assert field(output, "river_flux")[0] == 1
    assert field(output, "humidity")[0] == 1


def test_biome_rules_and_vegetation_stock_feedback_are_explainable() -> None:
    value = context(
        8,
        1,
        arrays={
            "elevation": np.array([-1.0, 100.0, 100.0, 100.0, 100.0, 100.0, 3000.0, 100.0]),
            "temperature": np.array([15.0, 15.0, -5.0, 15.0, 15.0, 15.0, 15.0, 15.0]),
            "surface_water": np.array([0.0, 20.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
            "humidity": np.array([1.0, 1.0, 0.5, 0.1, 0.5, 0.8, 0.8, 0.8]),
            "plant_biomass": np.array([80.0, 80.0, 80.0, 80.0, 80.0, 80.0, 80.0, 0.0]),
        },
    )
    output = single(value, BiomeStage())
    np.testing.assert_array_equal(field(output, "biome"), [0, 1, 2, 3, 4, 5, 6, 4])
    assert output.stage_results[0].events
    for event in output.stage_results[0].events:
        assert event.type == "BiomeChanged"
        assert isinstance(event.payload["reason"], str)
        assert event.target == f"tile:{event.payload['tile_id']}"
    assert output.snapshot.state_hash == single(value, BiomeStage()).snapshot.state_hash


@pytest.mark.parametrize("seed", [8, 19])
def test_full_pipeline_replays_is_candidate_only_and_sea_change_changes_biome(seed: int) -> None:
    value = context(
        seed=seed,
        command={"co2_ppm": 560.0},
        environment={
            "geological_years_per_turn": 0.0,
        },
        arrays={"elevation": np.full(12, 0.5)},
    )
    before_hash = value.snapshot.state_hash
    before_command, before_rng = digest(value.command), random.getstate()
    pipeline = DeterministicPipeline(reversed(stages()))
    first, second = pipeline.execute(value), pipeline.execute(value)
    assert first.snapshot.state_hash == second.snapshot.state_hash
    assert [r.output_hash for r in first.stage_results] == [
        r.output_hash for r in second.stage_results
    ]
    assert [r.events for r in first.stage_results] == [r.events for r in second.stage_results]
    np.testing.assert_array_equal(field(first, "biome"), np.zeros(12))
    assert first.environment_state["sea_level"] == pytest.approx(0.75)
    assert value.snapshot.state_hash == before_hash
    assert digest(value.command) == before_command
    assert random.getstate() == before_rng
    assert not first.ai_jobs
    for stage, outcome in zip(stages(), first.stage_results, strict=True):
        assert not outcome.ai_jobs
        for patch in outcome.state_delta.arrays:
            assert f"arrays.{patch.name}" in stage.contract.reads
            assert patch.expected_hash is not None


@pytest.mark.parametrize("co2", [9.999, 5000.01, True, "560", None])
def test_invalid_co2_command_is_fail_closed(co2: JsonValue) -> None:
    value = context(command={"co2_ppm": co2})
    before = value.snapshot.state_hash
    with pytest.raises((StageExecutionError, ValueError)):
        DeterministicPipeline(stages()).execute(value)
    assert value.snapshot.state_hash == before


@pytest.mark.parametrize(
    "name,bad",
    [
        ("soil_water", np.full(12, -1.0)),
        ("surface_water", np.full(12, -1.0)),
        ("plant_biomass", np.full(12, -1.0)),
        ("humidity", np.full(12, 1.1)),
        ("soil_quality", np.full(12, 1.1)),
        ("rainfall", np.zeros(11)),
        ("temperature", np.zeros(12, dtype=np.float32)),
        ("plate_id", np.full(12, 99, dtype=np.int64)),
        ("biome", np.full(12, 9, dtype=np.int64)),
    ],
)
def test_invalid_array_boundary_is_fail_closed(name: str, bad: NDArray[np.generic]) -> None:
    value = context(arrays={name: bad})
    before = value.snapshot.state_hash
    with pytest.raises((StageExecutionError, ValueError)):
        DeterministicPipeline(stages()).execute(value)
    assert value.snapshot.state_hash == before


@pytest.mark.parametrize(
    "name",
    [
        "elevation",
        "temperature",
        "soil_water",
        "surface_water",
        "soil_quality",
        "humidity",
        "plant_biomass",
        "rainfall",
        "river_flux",
        "volcanic_stress",
        "plate_id",
        "biome",
    ],
)
def test_all_required_arrays_must_exist(name: str) -> None:
    value = context()
    arrays = dict(value.snapshot.arrays)
    del arrays[name]
    value = replace(value, snapshot=replace(value.snapshot, arrays=arrays))
    before = value.snapshot.state_hash
    with pytest.raises((StageExecutionError, ValueError)):
        DeterministicPipeline(stages()).execute(value)
    assert value.snapshot.state_hash == before


def test_import_and_execute_reference_environment_does_not_load_gpu_or_ai() -> None:
    program = """
import sys
import runpy
from app.simulation.v2.pipeline import DeterministicPipeline
suite = runpy.run_path("tests/v2/test_reference_environment.py")
output = DeterministicPipeline(suite["stages"]()).execute(suite["context"]())
assert len(output.stage_results) == 4 and not output.ai_jobs
assert not any(name.startswith(('taichi', 'torch', 'app.services.ai')) for name in sys.modules)
"""
    completed = subprocess.run(
        [sys.executable, "-c", program], check=False, capture_output=True, text=True
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    "name",
    [
        "baseline_temperature",
        "global_temperature",
        "co2_ppm",
        "warming_offset",
        "sea_level",
        "ecological_years_per_turn",
        "geological_years_per_turn",
        "plates",
    ],
)
def test_required_environment_state_never_uses_hidden_defaults(name: str) -> None:
    value = context()
    environment = dict(value.environment_state)
    del environment[name]
    value = replace(
        value,
        snapshot=replace(
            value.snapshot,
            state={
                "geometry": value.snapshot.state["geometry"],
                "environment": environment,
            },
        ),
    )
    before = value.snapshot.state_hash
    with pytest.raises(StageExecutionError):
        DeterministicPipeline(stages()).execute(value)
    assert value.snapshot.state_hash == before


@pytest.mark.parametrize(
    "bad_environment",
    [
        {"ecological_years_per_turn": 0},
        {"geological_years_per_turn": -1},
        {"plates": ()},
        {"plates": ({"id": 0, "x": -1.0, "y": 0.0, "vx": 0.0, "vy": 0.0},)},
        {"plates": ({"id": 0, "x": 0.0, "y": 3.0, "vx": 0.0, "vy": 0.0},)},
        {"plates": ({"id": True, "x": 0.0, "y": 0.0, "vx": 0.0, "vy": 0.0},)},
        {"plates": ({"id": 0, "x": 0.0, "y": 0.0, "vx": 1e308, "vy": 0.0},)},
    ],
)
def test_invalid_state_boundaries_are_fail_closed(bad_environment: Mapping[str, JsonValue]) -> None:
    value = context(environment=bad_environment)
    before = value.snapshot.state_hash
    with pytest.raises(StageExecutionError):
        DeterministicPipeline(stages()).execute(value)
    assert value.snapshot.state_hash == before


@pytest.mark.parametrize("co2", [10, 5000])
def test_co2_range_endpoints_are_supported(co2: int) -> None:
    value = single(context(command={"co2_ppm": co2}), ClimateStage())
    assert value.environment_state["co2_ppm"] == co2
    assert np.isfinite(field(value, "temperature")).all()


@pytest.mark.parametrize("invalid", [math.nan, math.inf, -math.inf])
def test_nonfinite_fields_are_rejected_before_snapshot_admission(invalid: float) -> None:
    value = context()
    before = value.snapshot.snapshot_id
    with pytest.raises(ValueError, match="NaN or infinity"):
        context(arrays={"temperature": np.full(12, invalid)})
    assert value.snapshot.snapshot_id == before


def test_temperature_evaporation_is_bounded_and_has_correct_water_ledger_sign() -> None:
    value = context(
        2,
        1,
        arrays={
            "elevation": np.ones(2),
            "soil_water": np.full(2, 200.0),
            "rainfall": np.zeros(2),
            "temperature": np.array([-20.0, 1000.0]),
        },
    )
    output = single(value, HydrologyStage())
    np.testing.assert_allclose(field(output, "soil_water"), [200, 192])
    assert output.stage_results[0].metrics["evaporation_mm"] == 8
    assert output.stage_results[0].metrics["water_balance_residual_mm"] == 0

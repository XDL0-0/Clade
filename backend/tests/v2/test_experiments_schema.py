"""Strict JSON scenario constraints and forcing time semantics."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.simulation.v2.experiments import ExperimentPlan, Forcing, Scenario


def scenario(**changes: object) -> str:
    return json.dumps({"version": 1, "id": "dry-season", "name": "Dry season", **changes})


def test_json_schema_and_persistent_vs_ephemeral_forcing() -> None:
    value = Scenario.parse_json(
        scenario(
            forcing=[
                {"turn": 3, "co2_ppm": 560},
                {"turn": 1, "warming_offset": 2, "disaster_severity": 0.2, "disease_pressure": 0.4},
            ]
        )
    )
    assert [point.turn for point in value.forcing] == [1, 3]
    assert value.parameters(1) == {
        "warming_offset": 2.0,
        "disaster_severity": 0.2,
        "disease_pressure": 0.4,
    }
    assert value.parameters(2) == {"disaster_severity": 0.0, "disease_pressure": 0.0}
    assert value.parameters(3) == {
        "co2_ppm": 560.0,
        "disaster_severity": 0.0,
        "disease_pressure": 0.0,
    }
    assert Scenario.parse_json(value.model_dump_json()) == value
    assert Scenario.model_json_schema()["additionalProperties"] is False
    with pytest.raises(ValidationError):
        value.id = "changed"


@pytest.mark.parametrize(
    "change",
    [
        {"version": True},
        {"version": 1.0},
        {"version": "1"},
        {"version": 2},
        {"id": "../outside"},
        {"id": "a" * 65},
        {"name": "  "},
        {"name": 7},
        {"description": "a" * 2001},
        {"population": 50},
        {"forcing": [{"turn": 0, "co2_ppm": 280}]},
        {"forcing": [{"turn": 1001, "co2_ppm": 280}]},
        {"forcing": [{"turn": True, "co2_ppm": 280}]},
        {"forcing": [{"turn": "2", "co2_ppm": 280}]},
        {"forcing": [{"turn": 2}]},
        {"forcing": [{"turn": 2, "population": 2}]},
        {"forcing": [{"turn": 2, "rng_namespace": "surprise"}]},
        {"forcing": [{"turn": 2, "co2_ppm": "280"}]},
        {"forcing": [{"turn": 2, "co2_ppm": True}]},
        {"forcing": [{"turn": 2, "co2_ppm": 5001}]},
        {"forcing": [{"turn": 2, "warming_offset": -101}]},
        {"forcing": [{"turn": 2, "disease_pressure": -0.1}]},
        {"forcing": [{"turn": 2, "disaster_severity": 1.1}]},
        {"forcing": [{"turn": 2, "co2_ppm": float("nan")}]},
        {"forcing": [{"turn": 2, "warming_offset": float("inf")}]},
        {"forcing": [{"turn": 2, "co2_ppm": 280}, {"turn": 2, "warming_offset": 1}]},
        {"forcing": [{"turn": 2, "co2_ppm": 280}] * 1001},
    ],
)
def test_invalid_scenario_rejected(change: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        Scenario.parse_json(scenario(**change))


def test_duplicate_json_keys_rejected_at_any_depth() -> None:
    for raw in (
        '{"version":1,"id":"a","id":"b","name":"x"}',
        '{"version":1,"id":"a","name":"x","forcing":[{"turn":1,"co2_ppm":280,"co2_ppm":560}]}',
    ):
        with pytest.raises(ValueError, match="Duplicate JSON key"):
            Scenario.parse_json(raw)


@pytest.mark.parametrize(
    "change",
    [
        {"version": True},
        {"turns": 0},
        {"turns": 1001},
        {"turns": True},
        {"rng_namespace": " "},
        {"rng_namespace": "x" * 129},
        {"workers": 5},
        {"source": {"world_id": "w", "timeline_id": "main", "revision": 0}},
        {"source": {"world_id": "w", "timeline_id": "../path", "revision": 0, "generation": 0}},
        {"control": "absent"},
        {"branches": []},
    ],
)
def test_invalid_experiment_limits(change: dict[str, object]) -> None:
    control = {"id": "control", "name": "Control", "scenario": json.loads(scenario())}
    twin = {**control, "id": "twin"}
    request = {
        "version": 1,
        "id": "experiment",
        "name": "Experiment",
        "source": {"world_id": "w", "timeline_id": "main", "generation": 0, "revision": 0},
        "turns": 3,
        "rng_namespace": "paired",
        "control": "control",
        "branches": [control, twin],
        **change,
    }
    with pytest.raises(ValueError):
        ExperimentPlan.parse_json(json.dumps(request))


def test_conflicting_branches_and_out_of_horizon_forcing() -> None:
    base = {
        "version": 1,
        "id": "experiment",
        "name": "Experiment",
        "source": {"world_id": "w", "timeline_id": "main", "generation": 0, "revision": 0},
        "turns": 3,
        "rng_namespace": "paired",
        "control": "control",
    }
    branch = {"id": "control", "name": "Control", "scenario": json.loads(scenario())}
    for branches in (
        [branch, branch],
        [branch, {**branch, "id": "second"}] * 5,
        [
            branch,
            {
                **branch,
                "id": "second",
                "scenario": json.loads(scenario(forcing=[{"turn": 4, "co2_ppm": 300}])),
            },
        ],
        [
            {**branch, "scenario": json.loads(scenario(forcing=[{"turn": 1, "co2_ppm": 300}]))},
            {**branch, "id": "second"},
        ],
    ):
        with pytest.raises(ValueError):
            ExperimentPlan.parse_json(json.dumps({**base, "branches": branches}))


def test_boundary_values_and_empty_forcing_semantics() -> None:
    for value in (Forcing(turn=1, warming_offset=-100.0), Forcing(turn=1000, co2_ppm=5000.0)):
        assert value.turn in (1, 1000)
    with pytest.raises(ValueError):
        Scenario.parse_json(scenario()).parameters(0)
    with pytest.raises(ValueError, match="input limit"):
        Scenario.parse_json(" " * 4_000_001)

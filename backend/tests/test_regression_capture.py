"""Regression capture must observe each turn, never repeat the final state."""

from types import SimpleNamespace

import pytest

from app.schemas.requests import TurnCommand
from app.simulation.regression_test import RegressionTestRunner, SpeciesSnapshot, TurnSnapshot


@pytest.mark.asyncio
async def test_capture_is_at_turn_boundary_and_does_not_mutate_command():
    species = SimpleNamespace(
        lineage_code="A1", common_name="plant", status="alive", trophic_level=1.0,
        habitat_type="terrestrial", morphology_stats={"population": 10, "body_weight_g": 2},
    )
    commands = []

    class Engine:
        async def run_turns_async(self, command):
            commands.append(command)
            species.morphology_stats["population"] += 10
            return [SimpleNamespace(turn_index=len(commands), species=[], branching_events=[])]

    callback_turns = []

    async def capture(snapshot):
        callback_turns.append(snapshot.turn_index)
        snapshot.species_data.clear()

    command = TurnCommand(rounds=3)
    snapshots = await RegressionTestRunner().run_engine_with_snapshots(
        Engine(), command, capture, species_reader=lambda: [species],
    )
    assert [s.total_population for s in snapshots] == [20, 30, 40]
    assert [s.species_data["A1"].population for s in snapshots] == [20, 30, 40]
    assert callback_turns == [1, 2, 3]
    assert command.rounds == 3
    assert all(c.rounds == 1 for c in commands)
    assert len({id(c) for c in commands}) == 3


@pytest.mark.asyncio
async def test_missing_report_cannot_silently_pass_regression():
    class Engine:
        async def run_turns_async(self, command):
            return []

    with pytest.raises(RuntimeError, match="exactly one report"):
        await RegressionTestRunner().run_engine_with_snapshots(
            Engine(), TurnCommand(rounds=1), species_reader=lambda: [],
        )


@pytest.mark.parametrize("difference", ["species", "turn", "status", "trophic", "biomass"])
def test_structural_differences_fail_even_with_equal_total_population(difference):
    import copy

    original = TurnSnapshot(
        turn_index=1,
        species_data={"A1": SpeciesSnapshot("A1", "plant", 0, "alive", 1.0)},
    )
    changed = copy.deepcopy(original)
    if difference == "species":
        changed.species_data.clear()
    elif difference == "turn":
        changed.turn_index = 2
    elif difference == "status":
        changed.species_data["A1"].status = "extinct"
    elif difference == "trophic":
        changed.species_data["A1"].trophic_level = 2
    else:
        changed.total_biomass = 1
    assert not RegressionTestRunner().compare_snapshots([original], [changed]).passed

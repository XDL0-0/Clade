"""Deterministic scheduler, real kernel reduction and exact ecological ledgers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import cast

import numpy as np
import pytest
from numpy.typing import NDArray

from app.simulation.v2.context import TurnContext
from app.simulation.v2.contracts import SimulationStage, StageContract, StageResult
from app.simulation.v2.pipeline import DeterministicPipeline
from app.simulation.v2.reducer import apply_delta, restrict
from app.simulation.v2.reference.adaptation import AdaptationStage
from app.simulation.v2.reference.genetics import GeneFlowStage, GeneticDriftStage, MutationStage
from app.simulation.v2.reference.model import feedback_pipeline
from app.simulation.v2.reference.resolution import ResolutionEvolutionStage, ResolutionPolicyStage
from app.simulation.v2.reference.resolution_policy import Decision
from app.simulation.v2.reference.world import (
    MODEL_ID,
    TRAITS,
    SpeciesSeed,
    create_reference_snapshot,
)
from app.simulation.v2.seed import SeedStream
from app.simulation.v2.values import FrozenArray, JsonValue, digest, thaw
from app.simulation.v2.version import WorldVersion
from app.storage.store import WorldStore

OLD = (MutationStage(), GeneticDriftStage(), GeneFlowStage(), AdaptationStage())
NEW = (ResolutionPolicyStage(), ResolutionEvolutionStage())


def fixture(*, dt: float = 0.1, turn: int = 20) -> TurnContext:
    snapshot = create_reference_snapshot(
        WorldVersion("resolution", "main"),
        seed=37,
        manifest={
            "model": MODEL_ID,
            "stages": {"reference_mutation": "1", "reference_selection": "1"},
        },
        width=8,
        height=1,
        max_species=3,
        species=tuple(
            SpeciesSeed(
                name, "herbivore", 2, 50, habitat="amphibious", traits=dict.fromkeys(TRAITS, 0.2)
            )
            for name in ("animal.a", "animal.b")
        ),
    )
    labels = np.full((3, 8), -1, dtype=np.int64)
    labels[:2] = 0
    snapshot = replace(
        snapshot,
        version=WorldVersion("resolution", "main", 0, turn - 1),
        turn_id=turn - 1,
        state={
            **snapshot.state,
            "environment": {**snapshot.domain("environment"), "ecological_years_per_turn": dt},
        },
    )
    return patch(
        TurnContext(turn, snapshot, 37),
        connectivity=labels,
        gene_connectivity=labels,
        gene_population=snapshot.arrays["population"].numpy().copy(),
    )


def patch(context: TurnContext, **arrays: NDArray[np.generic]) -> TurnContext:
    return context.with_snapshot(
        replace(
            context.snapshot,
            arrays={
                **context.snapshot.arrays,
                **{name: FrozenArray.from_numpy(value) for name, value in arrays.items()},
            },
        )
    )


def array(context: TurnContext, name: str) -> NDArray[np.generic]:
    return context.snapshot.arrays[name].numpy().copy()


def domain(context: TurnContext, name: str, value: Mapping[str, JsonValue]) -> TurnContext:
    return context.with_snapshot(
        replace(context.snapshot, state={**context.snapshot.state, name: value})
    )


def control(context: TurnContext, **values: JsonValue) -> TurnContext:
    return domain(
        context, "evolution", {**context.snapshot.domain("evolution"), "resolution_control": values}
    )


def history(
    context: TurnContext, tiers: tuple[str, str] = ("Background", "Background")
) -> TurnContext:
    records: dict[str, JsonValue] = {}
    for row, (name, tier) in enumerate(zip(("animal.a", "animal.b"), tiers, strict=True)):
        records[name] = Decision(
            tier,
            0,
            context.turn_id - 1,
            0,
            0,
            (0.2,) * 7,
            int(array(context, "population")[row].sum()),
            True,
            0.1,
            ("fixture",),
        ).to_json()
    return domain(
        context,
        "evolution",
        {
            **context.snapshot.domain("evolution"),
            "resolution": {
                "schema": 1,
                "turn": context.turn_id - 1,
                "complete": True,
                "records": records,
                "plan_hash": digest(records),
            },
        },
    )


def one(context: TurnContext, stage: SimulationStage) -> tuple[TurnContext, StageResult]:
    view = restrict(context, stage.contract.reads)
    output = stage.execute(view)
    stage.validate_outputs(view, output)
    return context.with_snapshot(
        apply_delta(context.snapshot, output.state_delta, writes=stage.contract.writes)
    ), output


def run(
    context: TurnContext, stages: tuple[SimulationStage, ...] = NEW
) -> tuple[TurnContext, StageResult]:
    for stage in stages:
        context, output = one(context, stage)
    return context, output


def advance(context: TurnContext) -> TurnContext:
    snapshot = replace(
        context.snapshot, version=context.world_version.advance(), turn_id=context.turn_id
    )
    return TurnContext(context.turn_id + 1, snapshot, context.seed)


def record(context: TurnContext, name: str = "animal.a") -> Decision:
    schedule = context.snapshot.domain("evolution")["resolution"]
    assert isinstance(schedule, Mapping)
    records = schedule["records"]
    assert isinstance(records, Mapping)
    return Decision.parse(records[name], context.turn_id)


def test_all_critical_matches_original_four_stages_bit_for_bit() -> None:
    context = control(fixture(), mode="critical")
    initial = context.snapshot.state_hash
    original, old = run(context, OLD)
    observed, new = run(context)
    for name in context.snapshot.arrays:
        assert observed.snapshot.arrays[name] == original.snapshot.arrays[name], name
    assert observed.species_state == original.species_state
    assert new.events == old.events and new.evolution_proposals == old.evolution_proposals
    assert new.metrics["all_critical_exact_path"] is True
    assert context.snapshot.state_hash == initial
    assert ResolutionEvolutionStage.contract.version == "resolution-1"


def test_missing_state_bootstraps_critical_and_cooldown_downgrades_one_tier() -> None:
    context = fixture()
    observed = []
    for _ in range(8):
        after, _ = run(context)
        observed.append(record(after).tier)
        context = advance(after)
    assert observed == ["Critical"] * 3 + ["Focus"] * 3 + ["Background"] * 2


@pytest.mark.parametrize(
    "trigger",
    [
        "founder",
        "bottleneck",
        "crisis",
        "decline",
        "critical",
        "extinct",
        "small",
        "fragmentation",
        "blocked",
    ],
)
def test_immediate_upgrade_cannot_skip_urgent_lineages(trigger: str) -> None:
    context = history(fixture())
    if trigger in ("founder", "bottleneck"):
        prior = array(context, "gene_population")
        prior[0, 0] = 0 if trigger == "founder" else 1000
        context = patch(context, gene_population=prior)
    elif trigger == "crisis":
        pressure = array(context, "selection_pressure")
        pressure[0, 0] = 0.6
        context = patch(context, selection_pressure=pressure)
    elif trigger in ("fragmentation", "blocked"):
        labels = array(context, "connectivity")
        labels[0, :4] = 4 if trigger == "fragmentation" else -1
        context = patch(context, connectivity=labels)
    elif trigger in ("small", "decline", "extinct"):
        pop = array(context, "population")
        pop[0] = 0 if trigger == "extinct" else 1 if trigger == "small" else 40
        context = patch(context, population=pop)
    else:
        species = cast(dict[str, JsonValue], thaw(context.species_state))
        cast(dict[str, JsonValue], species["animal.a"])["status"] = "Critical"
        context = domain(context, "species", species)
    after, output = run(context)
    assert record(after).tier == "Critical" and record(after).due
    np.testing.assert_array_equal(array(after, "gene_population"), array(context, "population"))
    assert output.metrics["skipped_demes"] == 8  # The other stable lineage can skip.


@pytest.mark.parametrize("trigger", ["player", "food_web", "population", "recent"])
def test_focus_priority_is_explainable_and_updates_immediately(trigger: str) -> None:
    context = history(fixture())
    if trigger == "player":
        context = control(context, focus=("animal.a",))
    elif trigger == "food_web":
        context = domain(
            context,
            "food_web",
            {"edges": ({"predator": "animal.b", "prey": "animal.a", "preference": 1},)},
        )
    elif trigger == "population":
        pop = array(context, "population")
        pop[0] = 125
        context = patch(context, population=pop)
    else:
        species = cast(dict[str, JsonValue], thaw(context.species_state))
        cast(dict[str, JsonValue], species["animal.a"])["traits"] = dict.fromkeys(TRAITS, 0.22)
        context = domain(context, "species", species)
    planned, _ = one(context, NEW[0])
    decision = record(planned)
    assert decision.tier == "Focus" and decision.due and decision.reasons


def test_components_never_mix_and_isolation_ages_advance_on_skipped_turns() -> None:
    context = history(control(fixture(), focus=("animal.a",)))
    labels = array(context, "connectivity")
    labels[:2, 4:] = 4
    traits = array(context, "deme_traits")
    traits[:2, :4] = 0.1
    traits[:2, 4:] = 0.4
    context = patch(
        context,
        connectivity=labels,
        gene_connectivity=labels,
        deme_traits=traits,
        trait_proposals=traits,
    )
    after, output = run(context)
    assert output.metrics["aggregate_components"] == 2
    assert output.metrics["skipped_demes"] == 8
    observed = np.asarray(array(after, "deme_traits"), dtype=np.float64)
    np.testing.assert_array_equal(observed[0, :4], np.broadcast_to(observed[0, 0], (4, 7)))
    np.testing.assert_array_equal(observed[0, 4:], np.broadcast_to(observed[0, 4], (4, 7)))
    assert float(np.min(observed[0, 4:] - observed[0, :4])) > 0.25
    np.testing.assert_array_equal(observed[1], traits[1])
    np.testing.assert_array_equal(array(after, "gene_connectivity"), labels)
    np.testing.assert_array_equal(array(after, "isolation_age")[:2], 1)


def test_background_is_bounded_and_species_time_steps_do_not_pollute_other_streams() -> None:
    context = history(control(fixture(), focus=("animal.a",)))
    steps = []
    for _ in range(5):
        after, output = run(context)
        steps.append((record(after, "animal.b").due, record(after, "animal.b").dt))
        context = advance(after)
    assert steps == [(False, 0), (False, 0), (False, 0), (True, 0.4), (False, 0)]
    # Forcing the other lineage fine never advances a shared mutable RNG cursor.
    base = history(fixture(), ("Critical", "Background"))
    species = cast(dict[str, JsonValue], thaw(base.species_state))
    cast(dict[str, JsonValue], species["animal.a"])["status"] = "Critical"
    base = domain(base, "species", species)
    fine, _ = run(control(base, mode="critical"))
    mixed, _ = run(base)
    np.testing.assert_array_equal(array(fine, "deme_traits")[0], array(mixed, "deme_traits")[0])


def test_kernel_work_reduction_counts_real_random_draw_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    normal = SeedStream.normal

    def counted(self: SeedStream, counter: int) -> float:
        nonlocal calls
        calls += 1
        return normal(self, counter)

    monkeypatch.setattr(SeedStream, "normal", counted)
    context = history(fixture(), ("Focus", "Focus"))
    _, fine = run(control(context, mode="critical"))
    fine_calls = calls
    calls = 0
    _, coarse = run(control(context, focus=("animal.a", "animal.b")))
    assert calls < fine_calls / 2
    assert fine.metrics["fine_demes"] == 16
    assert coarse.metrics["aggregate_components"] == 2
    assert coarse.metrics["aggregate_kernel_evaluations"] == 6
    assert coarse.metrics["fine_kernel_evaluations"] == 0
    assert coarse.metrics["gene_flow_tile_updates"] == 16


def test_dt_change_has_explicit_cap_and_records_discarded_approximation_time() -> None:
    context = history(fixture(dt=0.4))
    after, _ = run(context)
    context = advance(after)
    context = domain(
        context, "environment", {**context.environment_state, "ecological_years_per_turn": 1.0}
    )
    after, output = run(context)
    assert record(after).dt == 1 and record(after).elapsed == 0
    assert output.metrics["discarded_elapsed_years"] == pytest.approx(0.8)


def test_critical_promotion_uses_current_dt_and_records_discarded_backlog() -> None:
    background, _ = run(history(fixture()))
    context = control(advance(background), mode="critical")
    exact, _ = run(context, OLD)
    promoted, output = run(context)
    assert output.metrics["all_critical_exact_path"] is True
    assert output.metrics["discarded_elapsed_years"] == pytest.approx(0.2)
    for name in exact.snapshot.arrays:
        assert promoted.snapshot.arrays[name] == exact.snapshot.arrays[name]


def test_empty_fossil_and_new_lineage_history_are_retained() -> None:
    context = history(fixture())
    population = array(context, "population")
    population[0] = 0
    species = cast(dict[str, JsonValue], thaw(context.species_state))
    fossil = cast(dict[str, JsonValue], species["animal.a"])
    fossil.update(status="Extinct", descendants=("animal.b",))
    child = cast(dict[str, JsonValue], species["animal.b"])
    child.update(ancestor="animal.a", created_turn=context.turn_id)
    context = domain(patch(context, population=population), "species", species)
    evolution = cast(dict[str, JsonValue], thaw(context.snapshot.domain("evolution")))
    schedule = cast(dict[str, JsonValue], evolution["resolution"])
    records = cast(dict[str, JsonValue], schedule["records"])
    records.pop("animal.b")
    schedule["plan_hash"] = digest(records)
    after, _ = run(domain(context, "evolution", evolution))
    assert record(after, "animal.b").tier == "Critical"
    assert after.species_state["animal.a"] == context.species_state["animal.a"]
    assert set(after.species_state) == {"animal.a", "animal.b"}
    np.testing.assert_array_equal(array(after, "gene_population"), population)


def test_significant_local_adaptation_keeps_focus_for_four_turns() -> None:
    context = history(fixture(dt=1), ("Focus", "Focus"))
    gradients = np.zeros((3, 7))
    gradients[:2] = 1
    after, output = run(patch(context, fitness_gradients=gradients))
    assert output.events
    assert record(after).recent_until == context.turn_id + 4
    planned, _ = one(advance(after), NEW[0])
    assert record(planned).tier == "Focus"
    assert "recent_evolution" in record(planned).reasons


def test_all_critical_integration_matches_original_feedback_numerics() -> None:
    original = feedback_pipeline()
    stages: list[SimulationStage] = []
    for stage in original.stages:
        if stage.contract.name == "reference_mutation":
            stages.extend(NEW)
        elif stage.contract.name not in {
            "reference_drift",
            "reference_gene_flow",
            "reference_adaptation",
        }:
            stages.append(stage)
    adaptive = DeterministicPipeline(stages)
    manifest: dict[str, JsonValue] = {
        "model": MODEL_ID,
        "stages": {stage.contract.name: stage.contract.version for stage in original.stages},
    }
    snapshot = create_reference_snapshot(
        WorldVersion("integration", "main"),
        seed=37,
        manifest=manifest,
        width=4,
        height=2,
        max_species=8,
    )
    full = control(TurnContext(1, snapshot, 37), mode="critical")
    scheduled = full
    for _ in range(12):
        full = original.execute(full)
        scheduled = adaptive.execute(scheduled)
        assert full.species_state == scheduled.species_state
        for name in full.snapshot.arrays:
            assert full.snapshot.arrays[name] == scheduled.snapshot.arrays[name], name
        full, scheduled = advance(full), advance(scheduled)


@pytest.mark.parametrize("case", ["plan", "stale", "completed", "bad_control", "bad_history"])
def test_invalid_or_repeated_scheduler_inputs_fail_closed(case: str) -> None:
    context = history(fixture())
    if case == "bad_control":
        with pytest.raises(ValueError):
            run(control(context, focus=("missing",)))
        return
    if case == "bad_history":
        with pytest.raises(ValueError):
            run(replace(context, turn_id=context.turn_id + 1))
        return
    planned, _ = one(context, NEW[0])
    if case == "stale":
        pop = array(planned, "population")
        pop[0, 0] += 1
        planned = patch(planned, population=pop)
    elif case == "completed":
        planned, _ = one(planned, NEW[1])
    else:
        evolution = cast(dict[str, JsonValue], thaw(planned.snapshot.domain("evolution")))
        schedule = cast(dict[str, JsonValue], evolution["resolution"])
        schedule["plan_hash"] = "tampered"
        planned = domain(planned, "evolution", evolution)
    with pytest.raises(ValueError):
        one(planned, NEW[1])


class FitnessReady(SimulationStage):
    contract = StageContract("reference_fitness", "test")

    def execute(self, context: TurnContext) -> StageResult:
        return StageResult(self.contract.name)


def test_checkpoint_replay_budget_and_ecological_conservation(tmp_path: Path) -> None:
    context = fixture(turn=1)
    pipeline = DeterministicPipeline([FitnessReady(), *NEW])
    store = WorldStore(tmp_path, checkpoint_interval=2)
    snapshot = store.create(context.snapshot, seed=context.seed)
    original = {
        name: value
        for name, value in snapshot.arrays.items()
        if name
        not in {
            "trait_proposals",
            "deme_traits",
            "gene_population",
            "gene_connectivity",
            "isolation_age",
        }
    }
    outputs = []
    for turn in range(1, 13):
        candidate = pipeline.execute(TurnContext(turn, snapshot, 37))
        repeated = pipeline.execute(TurnContext(turn, snapshot, 37))
        assert candidate.snapshot.state_hash == repeated.snapshot.state_hash
        assert candidate.stage_results[-1].events == repeated.stage_results[-1].events
        snapshot = store.commit(candidate, command_key=f"turn-{turn}")
        for name, value in original.items():
            assert snapshot.arrays[name] == value, name
        traits = np.asarray(snapshot.arrays["deme_traits"].numpy(), dtype=np.float64)
        assert np.isfinite(traits).all() and np.all((traits >= 0) & (traits <= 1))
        assert np.all(traits.sum(axis=-1) <= 3 + 1e-12)
        outputs.append(snapshot)
        store = WorldStore(tmp_path, checkpoint_interval=2)
        assert store.history.replay(snapshot.version).snapshot_id == snapshot.snapshot_id
    for saved in outputs:
        assert store.history.replay(saved.version).state_hash == saved.state_hash

"""Numerical evidence, atomic lineage splits, and bounded fossil capacity."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import cast

import numpy as np
import pytest
from numpy.typing import NDArray

from app.simulation.v2.context import TurnContext
from app.simulation.v2.contracts import SimulationStage, StageContract, StageResult
from app.simulation.v2.pipeline import DeterministicPipeline, StageExecutionError
from app.simulation.v2.reducer import DeltaConflict, apply_delta
from app.simulation.v2.reference.extinction import ExtinctionStage
from app.simulation.v2.reference.speciation import (
    GENES,
    MIN_SCORE,
    SCRATCH,
    SpeciationCommitStage,
    SpeciationProposalStage,
)
from app.simulation.v2.reference.world import (
    MODEL_ID,
    TRAITS,
    SpeciesSeed,
    create_reference_snapshot,
)
from app.simulation.v2.values import FrozenArray, JsonValue
from app.simulation.v2.version import WorldVersion


class Adapted(SimulationStage):
    contract = StageContract("reference_adaptation", "fixture")

    def execute(self, context: TurnContext) -> StageResult:
        return StageResult(self.contract.name)


def context(*, capacity: int = 3, left: int = 20, right: int = 20, age: int = 24) -> TurnContext:
    seed = SpeciesSeed("parent.id", "herbivore", 2, 0, habitat="amphibious")
    snapshot = create_reference_snapshot(
        WorldVersion("speciation", "control"),
        seed=31,
        manifest={"model": MODEL_ID},
        width=8,
        height=1,
        max_species=capacity,
        species=[seed],
    )
    shape = (capacity, 8)
    population = np.zeros(shape, dtype=np.int64)
    population[0, [0, 4]] = [left, right]
    labels = np.full(shape, -1, dtype=np.int64)
    labels[0, [0, 1]] = 0
    labels[0, [4, 5]] = 4
    gene_labels = np.where(population > 0, labels, -1)
    traits = np.zeros((*shape, len(TRAITS)), dtype=np.float64)
    traits[0, :4, :3] = 1
    traits[0, 4:, 3:6] = 1
    arrays = dict(snapshot.arrays)
    arrays.update(
        {
            name: FrozenArray.from_numpy(value)
            for name, value in {
                "population": population,
                "energy_reserve": population.astype(np.float64) * 0.25,
                "gene_population": population,
                "connectivity": labels,
                "gene_connectivity": gene_labels,
                "isolation_age": np.where(population > 0, age, 0).astype(np.int64),
                "deme_traits": traits,
                "trait_proposals": traits,
                "temperature": np.array([0.0] * 4 + [18.0] * 4),
                "humidity": np.array([0.0] * 4 + [1.0] * 4),
                "selection_pressure": np.zeros((capacity, len(TRAITS)), dtype=np.float64),
                "fitness_gradients": np.zeros((capacity, len(TRAITS)), dtype=np.float64),
            }.items()
        }
    )
    metadata = {**seed.metadata(0, left + right), "status": "Healthy"}
    return TurnContext(
        25,
        replace(
            snapshot, arrays=arrays, state={**snapshot.state, "species": {"parent.id": metadata}}
        ),
        31,
        command={"command_id": "split-turn-25"},
    )


def set_array(value: TurnContext, name: str, data: NDArray[np.generic]) -> TurnContext:
    return value.with_snapshot(
        replace(
            value.snapshot,
            arrays={
                **value.snapshot.arrays,
                name: FrozenArray.from_numpy(data),
            },
        )
    )


def set_domain(value: TurnContext, name: str, data: Mapping[str, JsonValue]) -> TurnContext:
    return value.with_snapshot(replace(value.snapshot, state={**value.snapshot.state, name: data}))


def apply(value: TurnContext, stage: SimulationStage) -> tuple[TurnContext, StageResult]:
    output = stage.execute(value)
    return value.with_snapshot(
        apply_delta(value.snapshot, output.state_delta, writes=stage.contract.writes)
    ), output


def proposed(value: TurnContext) -> tuple[TurnContext, StageResult]:
    return apply(value, SpeciationProposalStage())


def committed(value: TurnContext) -> tuple[TurnContext, StageResult]:
    return apply(value, SpeciationCommitStage())


def proposal(value: TurnContext) -> Mapping[str, JsonValue]:
    pending = cast(tuple[JsonValue, ...], value.snapshot.domain("evolution")["pending_speciation"])
    return cast(Mapping[str, JsonValue], pending[0])


def child(value: TurnContext) -> Mapping[str, JsonValue]:
    return cast(
        Mapping[str, JsonValue],
        next(item for key, item in value.species_state.items() if key.startswith("species-")),
    )


def same_traits(value: TurnContext) -> TurnContext:
    traits = value.snapshot.arrays["deme_traits"].numpy().copy()
    traits[0] = 0.1
    return set_array(set_array(value, "deme_traits", traits), "trait_proposals", traits)


def same_climate(value: TurnContext) -> TurnContext:
    return set_array(set_array(value, "temperature", np.zeros(8)), "humidity", np.zeros(8))


def test_geographic_break_alone_never_generates_a_proposal() -> None:
    value = same_climate(same_traits(context()))
    output = SpeciationProposalStage().execute(value)
    assert not output.evolution_proposals and not output.events
    assert not output.state_delta.arrays
    assert len(output.state_delta.state) == 1
    assert output.state_delta.state[0].value == ()


@pytest.mark.parametrize(("age", "expected"), [(11, 0), (12, 1), (24, 1)])
def test_minimum_isolation_age(age: int, expected: int) -> None:
    output = SpeciationProposalStage().execute(context(age=age))
    assert len(output.evolution_proposals) == expected


@pytest.mark.parametrize(
    ("left", "right", "expected"), [(19, 20, 0), (20, 19, 0), (20, 20, 1), (0, 40, 0)]
)
def test_both_descendant_groups_need_twenty_individuals(
    left: int, right: int, expected: int
) -> None:
    assert (
        len(SpeciationProposalStage().execute(context(left=left, right=right)).evolution_proposals)
        == expected
    )


@pytest.mark.parametrize(("difference", "expected"), [(3.6, 1), (3.59, 0)])
def test_score_threshold_is_inclusive(difference: float, expected: int) -> None:
    value = same_traits(context())
    value = set_array(value, "temperature", np.array([0.0] * 4 + [difference] * 4))
    output = SpeciationProposalStage().execute(value)
    assert len(output.evolution_proposals) == expected
    if expected:
        assert cast(Mapping[str, JsonValue], output.evolution_proposals[0])[
            "score"
        ] == pytest.approx(MIN_SCORE)


def test_empty_habitat_bridge_prevents_isolation_despite_distant_occupied_tiles() -> None:
    value = context()
    labels = value.snapshot.arrays["connectivity"].numpy().copy()
    labels[0] = 0
    gene_labels = np.where(np.greater(value.snapshot.arrays["population"].numpy(), 0), labels, -1)
    value = set_array(set_array(value, "connectivity", labels), "gene_connectivity", gene_labels)
    assert not SpeciationProposalStage().execute(value).evolution_proposals


def test_proposal_has_full_bound_evidence_and_stable_component_tie_break() -> None:
    value, output = proposed(context())
    candidate = proposal(value)
    assert candidate["parent"] == "parent.id"
    assert candidate["component"] == 0
    assert candidate["tile_runs"] == ((0, 1),)
    assert candidate["population"] == candidate["remaining_population"] == 20
    assert candidate["score"] == pytest.approx(1)
    assert candidate["expected"] == value.world_version.to_dict()
    assert candidate["turn"] == 25 and candidate["isolation_turns"] == 24
    assert cast(Mapping[str, JsonValue], candidate["traits"])["armor"] == 1
    assert output.evolution_proposals == (candidate,)
    assert len(output.state_delta.state) == 1
    assert output.state_delta.state[0].path == ("evolution", "pending_speciation")
    assert not output.state_delta.arrays and not output.events and not output.ai_jobs
    again, repeated = proposed(value)
    assert again.snapshot == value.snapshot and not repeated.state_delta.state


def test_split_preserves_integer_population_carbon_and_all_unrelated_scratch() -> None:
    initial = context()
    before_hash = initial.snapshot.snapshot_id
    ready, _ = proposed(initial)
    after, output = committed(ready)
    new = child(after)
    assert new["slot"] == 1 and new["ancestor"] == "parent.id"
    assert new["created_turn"] == 25 and new["declining_turns"] == 0
    assert new["last_population"] == new["last_nonzero_population"] == 20
    assert new["current_habitat_runs"] == ((0, 0),)
    assert new["extinction_cause"] is None and "extinction_turn" not in new
    assert new["body_mass"] == 2
    np.testing.assert_array_equal(
        after.snapshot.arrays["population"].numpy()[0], [0, 0, 0, 0, 20, 0, 0, 0]
    )
    np.testing.assert_array_equal(
        after.snapshot.arrays["population"].numpy()[1], [20, 0, 0, 0, 0, 0, 0, 0]
    )
    np.testing.assert_array_equal(
        after.snapshot.arrays["population"].numpy().sum(axis=0),
        initial.snapshot.arrays["population"].numpy().sum(axis=0),
    )
    assert (
        after.snapshot.arrays["energy_reserve"].numpy().sum()
        == initial.snapshot.arrays["energy_reserve"].numpy().sum()
    )
    assert output.metrics["population_before"] == output.metrics["population_after"] == 40
    assert output.metrics["carbon_before"] == output.metrics["carbon_after"] == 90
    for name in (*SCRATCH, "connectivity"):
        assert after.snapshot.arrays[name] == initial.snapshot.arrays[name]
    assert after.snapshot.arrays["gene_connectivity"].numpy()[1, 0] == 0
    assert after.snapshot.arrays["gene_connectivity"].numpy()[0, 0] == -1
    assert after.snapshot.arrays["isolation_age"].numpy()[1].sum() == 0
    assert after.snapshot.arrays["deme_traits"].numpy()[0, :2].sum() == 0
    np.testing.assert_array_equal(
        after.snapshot.arrays["gene_population"].numpy(),
        after.snapshot.arrays["population"].numpy(),
    )
    assert set(patch.name for patch in output.state_delta.arrays) == {
        "population",
        "energy_reserve",
        *GENES,
    }
    assert initial.snapshot.snapshot_id == before_hash
    assert after.snapshot.domain("evolution")["pending_speciation"] == ()
    assert (
        after.snapshot.domain("evolution")["isolation"]
        == initial.snapshot.domain("evolution")["isolation"]
    )
    assert [event.type for event in output.events] == ["SpeciesCreated", "SpeciationOccurred"]
    assert len({event.event_id for event in output.events}) == 2
    assert all(
        event.payload["cause_terms"] == proposal(ready)["cause_terms"] for event in output.events
    )
    repeated, duplicate = committed(after)
    reproposed, retry = proposed(after)
    assert repeated.snapshot == reproposed.snapshot == after.snapshot
    assert not duplicate.events and not duplicate.state_delta.state
    assert not retry.evolution_proposals
    with pytest.raises(DeltaConflict):
        apply_delta(
            after.snapshot, output.state_delta, writes=SpeciationCommitStage.contract.writes
        )


def test_fossil_slots_are_not_reused_and_capacity_deferral_is_once_per_turn() -> None:
    value = context(capacity=2)
    fossil = {
        **SpeciesSeed("fossil", "producer", 1, 0).metadata(1, 0),
        "last_habitat": ((7, 7),),
        "descendants": ("parent.id",),
    }
    value = set_domain(value, "species", {**value.species_state, "fossil": fossil})
    ready, _ = proposed(value)
    after, output = committed(ready)
    assert after.species_state == value.species_state
    assert after.snapshot.arrays == value.snapshot.arrays
    assert output.metrics["deferred"] == 1 and output.metrics["species_created"] == 0
    assert [event.type for event in output.events] == ["SpeciationDeferred"]
    assert output.events[0].payload["reason"] == "species_capacity"
    assert not output.ai_jobs
    assert after.snapshot.domain("evolution")["pending_speciation"] == ()
    again, _ = proposed(after)
    _, repeated = committed(again)
    assert not repeated.events and not repeated.state_delta.state


@pytest.mark.parametrize(
    ("key", "bad"),
    [
        ("proposal_id", "forged"),
        ("child_id", "llm-child"),
        ("parent", "missing"),
        ("parent_slot", 1),
        ("component", 4),
        ("population", 1000),
        ("remaining_population", 1000),
        ("score", 1.1),
        ("cause_terms", {}),
        ("isolation_turns", 1000),
        ("traits", {"armor": 0.5}),
        ("tile_runs", ((0, 7),)),
        ("expected", {}),
        ("turn", 26),
        ("schema_version", 2),
        ("evidence_hash", "stale"),
    ],
)
def test_tampered_proposals_fail_before_mutating_any_state(key: str, bad: JsonValue) -> None:
    ready, _ = proposed(context())
    altered = {**proposal(ready), key: bad}
    value = set_domain(
        ready, "evolution", {**ready.snapshot.domain("evolution"), "pending_speciation": (altered,)}
    )
    before = value.snapshot.snapshot_id
    with pytest.raises(ValueError, match="tampered"):
        SpeciationCommitStage().execute(value)
    assert value.snapshot.snapshot_id == before


def test_duplicate_and_replayed_proposals_are_rejected() -> None:
    ready, _ = proposed(context())
    candidate = proposal(ready)
    duplicate = set_domain(
        ready,
        "evolution",
        {**ready.snapshot.domain("evolution"), "pending_speciation": (candidate, candidate)},
    )
    with pytest.raises(ValueError, match="duplicate"):
        SpeciationCommitStage().execute(duplicate)
    after, _ = committed(ready)
    replayed = set_domain(
        after,
        "evolution",
        {**after.snapshot.domain("evolution"), "pending_speciation": (candidate,)},
    )
    with pytest.raises(ValueError, match="already committed"):
        SpeciationCommitStage().execute(replayed)
    with pytest.raises(ValueError, match="already committed"):
        SpeciationProposalStage().execute(replayed)


@pytest.mark.parametrize(
    "change", ["revision", "generation", "timeline", "turn", "reserve", "parent"]
)
def test_stale_versions_or_evidence_are_rejected(change: str) -> None:
    ready, _ = proposed(context())
    if change in ("revision", "generation", "timeline"):
        version = ready.world_version
        version = replace(
            version, **({"timeline_id": "fork"} if change == "timeline" else {change: 1})
        )
        value = replace(ready, snapshot=replace(ready.snapshot, version=version))
    elif change == "turn":
        value = replace(ready, turn_id=26)
    elif change == "reserve":
        reserve = ready.snapshot.arrays["energy_reserve"].numpy().copy()
        reserve[0, 0] += 1
        value = set_array(ready, "energy_reserve", reserve)
    else:
        parent = cast(Mapping[str, JsonValue], ready.species_state["parent.id"])
        value = set_domain(ready, "species", {"parent.id": {**parent, "body_mass": 3}})
    with pytest.raises(ValueError, match="Stale"):
        SpeciationCommitStage().execute(value)


def test_food_web_copies_incoming_and_outgoing_edges_preserving_fossils_and_ancestors() -> None:
    value = context(capacity=4)
    parent = cast(Mapping[str, JsonValue], value.species_state["parent.id"])
    plant = {
        **SpeciesSeed("plant", "producer", 1, 0).metadata(1, 0),
        "descendants": ("parent.id",),
        "last_habitat": ((7, 7),),
    }
    hunter = SpeciesSeed("hunter", "carnivore", 10, 0).metadata(2, 0)
    value = set_domain(
        value,
        "species",
        {
            "parent.id": {**parent, "ancestor": "plant", "descendants": ("older-child",)},
            "plant": plant,
            "hunter": hunter,
        },
    )
    value = set_domain(
        value,
        "food_web",
        {
            "edges": (
                {"predator": "parent.id", "prey": "plant", "preference": 0.7},
                {"predator": "hunter", "prey": "parent.id", "preference": 0.3},
            ),
            "custom": "preserved",
        },
    )
    ready, _ = proposed(value)
    after, _ = committed(ready)
    new_id = cast(str, proposal(ready)["child_id"])
    retained = cast(Mapping[str, JsonValue], after.species_state["parent.id"])
    assert retained["ancestor"] == "plant" and retained["descendants"] == ("older-child", new_id)
    assert after.species_state["plant"] == value.species_state["plant"]
    assert after.species_state["hunter"] == value.species_state["hunter"]
    assert child(after)["slot"] == 3
    edges = cast(tuple[Mapping[str, JsonValue], ...], after.food_web_state["edges"])
    assert {(edge["predator"], edge["prey"], edge["preference"]) for edge in edges} == {
        ("parent.id", "plant", 0.7),
        ("hunter", "parent.id", 0.3),
        (new_id, "plant", 0.7),
        ("hunter", new_id, 0.3),
    }
    assert all(
        edge["predator"] in after.species_state and edge["prey"] in after.species_state
        for edge in edges
    )
    assert after.food_web_state["custom"] == "preserved"


def test_invalid_food_edge_and_extinct_parent_fail_atomically() -> None:
    value = set_domain(
        context(), "food_web", {"edges": ({"predator": "parent.id", "prey": "missing"},)}
    )
    ready, _ = proposed(value)
    with pytest.raises(ValueError, match="unknown species"):
        SpeciationCommitStage().execute(ready)
    parent = cast(Mapping[str, JsonValue], ready.species_state["parent.id"])
    invalid = set_domain(ready, "species", {"parent.id": {**parent, "status": "Extinct"}})
    with pytest.raises(ValueError, match="Extinct"):
        SpeciationCommitStage().execute(invalid)


def test_pipeline_contract_determinism_branch_identity_and_no_llm_numerical_inputs() -> None:
    pipeline = DeterministicPipeline(
        [SpeciationCommitStage(), SpeciationProposalStage(), Adapted()]
    )
    value = context()
    left, right = pipeline.execute(value), pipeline.execute(value)
    assert left.snapshot == right.snapshot
    assert left.stage_results[-1].events == right.stage_results[-1].events
    other = replace(
        value,
        command={
            **value.command,
            "counts": 999999,
            "traits": {"armor": 999},
            "ai_speciation_score": 1,
        },
    )
    assert pipeline.execute(other).snapshot == left.snapshot
    branch = replace(
        value,
        snapshot=replace(value.snapshot, version=WorldVersion("speciation", "fork")),
        rng_namespace="fork",
    )
    branch_output = pipeline.execute(branch)
    assert set(branch_output.species_state) != set(left.species_state)
    assert (
        branch_output.stage_results[-1].events[0].event_id
        != left.stage_results[-1].events[0].event_id
    )
    paired = replace(branch, rng_namespace="control")
    paired_output = pipeline.execute(paired)
    assert paired_output.snapshot.state_hash == left.snapshot.state_hash
    assert (
        paired_output.stage_results[-1].events[0].event_id
        != left.stage_results[-1].events[0].event_id
    )
    assert all(len(identity) < 128 for identity in left.species_state)


@pytest.mark.parametrize(
    "name",
    ["population", "energy_reserve", *GENES, "connectivity", "temperature", "humidity", "biome"],
)
def test_invalid_array_axes_rejected(name: str) -> None:
    value = context()
    bad = value.snapshot.arrays[name].numpy().copy()[..., :-1]
    with pytest.raises(ValueError):
        SpeciationProposalStage().execute(set_array(value, name, bad))


@pytest.mark.parametrize("name", ["deme_traits", "trait_proposals"])
def test_invalid_trait_budget_rejected(name: str) -> None:
    value = context()
    traits = value.snapshot.arrays[name].numpy().copy()
    traits[0, 0] = 1
    with pytest.raises(ValueError, match="budget"):
        SpeciationProposalStage().execute(set_array(value, name, traits))


@pytest.mark.parametrize("name", SCRATCH)
def test_nonempty_reserved_scratch_cannot_be_claimed_by_a_new_species(name: str) -> None:
    ready, _ = proposed(context())
    scratch = ready.snapshot.arrays[name].numpy().copy()
    if name == "mortality":
        scratch[0, 1, 0] = 1
    else:
        scratch[1, 0] = 1
    with pytest.raises(ValueError, match="zero ecological scratch"):
        SpeciationCommitStage().execute(set_array(ready, name, scratch))


def test_pipeline_input_failure_leaves_the_original_snapshot_untouched() -> None:
    value = context()
    scratch = value.snapshot.arrays["births"].numpy().copy()
    scratch[1, 0] = 1
    value = set_array(value, "births", scratch)
    before = value.snapshot.snapshot_id
    pipeline = DeterministicPipeline(
        [Adapted(), SpeciationProposalStage(), SpeciationCommitStage()]
    )
    with pytest.raises(StageExecutionError, match="zero ecological scratch"):
        pipeline.execute(value)
    assert value.snapshot.snapshot_id == before


def test_highest_score_wins_over_lower_component_identity() -> None:
    value = context()
    population = value.snapshot.arrays["population"].numpy().copy()
    population[0, 2] = 20
    labels = value.snapshot.arrays["connectivity"].numpy().copy()
    labels[0, 2:4] = 2
    for name, data in {
        "population": population,
        "gene_population": population,
        "energy_reserve": population.astype(np.float64) * 0.25,
        "connectivity": labels,
        "gene_connectivity": np.where(np.greater(population, 0), labels, -1),
        "isolation_age": np.where(np.greater(population, 0), 24, 0).astype(np.int64),
    }.items():
        value = set_array(value, name, data)
    ready, output = proposed(value)
    assert len(output.evolution_proposals) == 1
    assert proposal(ready)["component"] == 4 and proposal(ready)["score"] == pytest.approx(1)


@pytest.mark.parametrize("capacity", [3, 4])
def test_multiple_parents_have_stable_slot_allocation_and_unique_events(capacity: int) -> None:
    value = context(capacity=capacity)
    seed = SpeciesSeed("a-parent", "herbivore", 2, 0, habitat="amphibious")
    value = set_domain(value, "species", {**value.species_state, "a-parent": seed.metadata(1, 40)})
    for name in ("population", "energy_reserve", "connectivity", *GENES):
        data = value.snapshot.arrays[name].numpy().copy()
        data[1] = data[0]
        value = set_array(value, name, data)
    ready, output = proposed(value)
    assert len(output.evolution_proposals) == 2
    after, output = committed(ready)
    assert output.metrics["species_created"] == capacity - 2
    assert output.metrics["deferred"] == 4 - capacity
    assert output.metrics["population_before"] == output.metrics["population_after"] == 80
    assert len({event.event_id for event in output.events}) == len(output.events)
    children = [
        cast(Mapping[str, JsonValue], item)
        for key, item in after.species_state.items()
        if key.startswith("species-")
    ]
    assert next(item for item in children if item["ancestor"] == "a-parent")["slot"] == 2
    if capacity == 3:
        assert output.events[-1].type == "SpeciationDeferred"
        assert output.events[-1].target == "parent.id"


def test_local_reserve_and_demes_transfer_bit_exactly_even_beside_a_large_carbon_pool() -> None:
    value = context()
    reserve = value.snapshot.arrays["energy_reserve"].numpy().copy()
    reserve[0, 0], reserve[0, 4] = 1e-200, 1e200
    value = set_array(value, "energy_reserve", reserve)
    ready, _ = proposed(value)
    after, _ = committed(ready)
    assert after.snapshot.arrays["energy_reserve"].numpy()[1, 0] == 1e-200
    assert after.snapshot.arrays["energy_reserve"].numpy()[0, 0] == 0
    for name in ("deme_traits", "trait_proposals"):
        np.testing.assert_array_equal(
            after.snapshot.arrays[name].numpy()[1, :2], value.snapshot.arrays[name].numpy()[0, :2]
        )
        assert not np.any(after.snapshot.arrays[name].numpy()[0, :2])


@pytest.mark.parametrize(("previous_population", "status"), [(40, "Healthy"), (60, "Declining")])
def test_lifecycle_baseline_excludes_identity_transfer_but_retains_real_decline(
    previous_population: int,
    status: str,
) -> None:
    value = context()
    parent = cast(Mapping[str, JsonValue], value.species_state["parent.id"])
    value = set_domain(
        value,
        "species",
        {
            "parent.id": {
                **parent,
                "last_population": previous_population,
                "declining_turns": 1,
                "lifecycle_turn": 24,
                "current_habitat_runs": ((0, 0), (4, 4)),
            }
        },
    )
    ready, _ = proposed(value)
    split, output = committed(ready)
    parent = cast(Mapping[str, JsonValue], split.species_state["parent.id"])
    assert parent["last_population"] == previous_population - 20
    assert parent["traits"] == dict(zip(TRAITS, (0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 0.0), strict=True))
    assert output.events[0].payload["parent_baseline_before"] == previous_population
    assert output.events[0].payload["parent_baseline_after"] == previous_population - 20
    after, _ = apply(split, ExtinctionStage())
    parent = cast(Mapping[str, JsonValue], after.species_state["parent.id"])
    assert parent["status"] == status
    assert parent["current_habitat_runs"] == ((4, 4),)
    assert parent["declining_turns"] == (0 if status == "Healthy" else 2)


def test_commit_requires_an_explicit_proposal_record() -> None:
    with pytest.raises(ValueError, match="pending record"):
        SpeciationCommitStage().execute(context())

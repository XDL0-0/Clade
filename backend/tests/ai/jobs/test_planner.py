"""Reference facts become atomic, bounded, presentation-only durable work."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import cast

import numpy as np
import pytest

from app.ai.jobs.models import AIJob, JobSpec, JobStatus
from app.ai.jobs.planner import PROMPT_VERSION, ReferenceNarrativePlanner
from app.ai.jobs.schemas import fallback_result, validate_result
from app.ai.jobs.worker import NarrativeWorker
from app.simulation.v2.context import TurnContext, WorldSnapshot
from app.simulation.v2.contracts import StageResult
from app.simulation.v2.engine import SimulationEngineV2, TurnCommand
from app.simulation.v2.events import WorldEvent
from app.simulation.v2.reference.evolution_contracts import EvolutionTrace
from app.simulation.v2.reference.model import evolution_pipeline
from app.simulation.v2.reference.world import (
    MODEL_ID,
    SpeciesSeed,
    create_reference_snapshot,
)
from app.simulation.v2.values import FrozenArray, JsonValue, canonical_bytes
from app.simulation.v2.version import WorldVersion
from app.storage.jobs import SQLiteJobRepository
from app.storage.store import WorldStore
from scripts.run_narrative_jobs import TemplateNarrativeProvider, main, run_jobs


def initial() -> WorldSnapshot:
    # A compact imported numerical fixture, after 24 turns of local isolation.
    names = ("parent.id", *(f"adapt.{index}" for index in range(6)))
    metadata: dict[str, JsonValue] = {}
    pop = np.zeros((8, 2), dtype=np.int64)
    for index, name in enumerate(names):
        slot = 0 if index == 0 else index + 1
        count = 40 if index == 0 else 20
        metadata[name] = SpeciesSeed(name, "herbivore", 2, 0).metadata(slot, count)
        pop[slot, 0] = count
    return WorldSnapshot(
        WorldVersion("narrative", "main"),
        24,
        {"species": metadata},
        {
            "population": FrozenArray.from_numpy(pop),
        },
    )


def candidate(
    before: WorldSnapshot | None = None,
    *,
    split: bool = False,
    adaptations: tuple[tuple[str, float], ...] = (),
) -> TurnContext:
    before = before or initial()
    context = TurnContext(before.turn_id + 1, before, 31, command={"command_id": "facts"})
    species = dict(before.domain("species"))
    events: list[WorldEvent] = []
    for ordinal, (identity, change) in enumerate(adaptations):
        payload = EvolutionTrace(
            identity,
            context.turn_id,
            (change, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            (0.0,) * 7,
            change * 0.5,
            (("speed_change", 0.0),),
            (("mutation", 0.001),),
            max(0.03, abs(change)),
        ).to_json()
        events.append(
            WorldEvent.create(
                version=before.version.advance(),
                turn=context.turn_id,
                command_id="facts",
                stage="reference_adaptation",
                ordinal=ordinal,
                event_type="SpeciesAdapted",
                actor=identity,
                payload=payload,
            )
        )
        metadata = cast(Mapping[str, JsonValue], species[identity])
        species[identity] = {
            **metadata,
            "traits": {**cast(Mapping[str, JsonValue], metadata["traits"]), "armor": 0.1 + change},
        }
    stages = [StageResult("reference_adaptation", events=tuple(events))]
    pop = before.arrays["population"].numpy().copy()
    if split:
        parent = cast(Mapping[str, JsonValue], species["parent.id"])
        species["parent.id"] = {**parent, "descendants": ("child.id",), "last_population": 20}
        species["child.id"] = {
            **SpeciesSeed("child.id", "herbivore", 2, 0).metadata(1, 20),
            "ancestor": "parent.id",
            "created_turn": context.turn_id,
        }
        pop[0, 0], pop[1, 0] = 20, 20
        event = WorldEvent.create(
            version=before.version.advance(),
            turn=context.turn_id,
            command_id="facts",
            stage="reference_speciation",
            ordinal=0,
            event_type="SpeciationOccurred",
            actor="parent.id",
            target="child.id",
            payload={
                "parent": "parent.id",
                "child_id": "child.id",
                "proposal_id": "speciation:fact",
                "population": 20,
                "remaining_population": 20,
                "score": 1.0,
                "isolation_turns": 24,
                "component": 0,
                "genetic_rms_scale": float(np.sqrt(6 / 7)),
                "cause_terms": {
                    name: 1.0
                    for name in (
                        "geographic_isolation",
                        "ecological_divergence",
                        "genetic_distance",
                        "isolation_duration",
                        "low_gene_flow",
                    )
                },
                "parent_baseline_before": 40,
                "parent_baseline_after": 20,
            },
        )
        stages.append(StageResult("reference_speciation", events=(event,)))
    return replace(
        context.with_snapshot(
            replace(
                before,
                state={"species": species},
                arrays={"population": FrozenArray.from_numpy(pop)},
            )
        ),
        stage_results=tuple(stages),
    )


def setup(
    root: Path, *, split: bool = False
) -> tuple[WorldStore, TurnContext, tuple[JobSpec, ...], WorldSnapshot]:
    store = WorldStore(root)
    before = store.create(initial(), seed=31)
    request = candidate(before, split=split, adaptations=(("adapt.0", 0.03),))
    jobs = ReferenceNarrativePlanner()(request)
    return store, request, jobs, store.commit(request, command_key="facts", jobs=jobs)


def test_only_significant_successful_current_facts_create_jobs() -> None:
    planner = ReferenceNarrativePlanner()
    assert planner(candidate()) == ()
    assert planner(candidate(adaptations=(("adapt.0", 0.019),))) == ()
    request = candidate(adaptations=(("adapt.0", 0.02),))
    event = request.stage_results[0].events[0]
    ignored = replace(
        request, stage_results=(), active_events=(event,), evolution_proposals=(event.payload,)
    )
    assert planner(ignored) == ()
    for kind in ("SpeciationDeferred", "SpeciationProposal", "SpeciesCreated"):
        ignored = replace(
            request,
            stage_results=(
                StageResult("reference_speciation", events=(replace(event, type=kind),)),
            ),
        )
        assert planner(ignored) == ()
    jobs = planner(request)
    assert len(jobs) == 1 and jobs[0].job_type == "adaptation"
    future = replace(
        request.snapshot, version=request.world_version.advance(), turn_id=request.turn_id
    )
    assert jobs[0].snapshot_id == future.snapshot_id
    assert jobs[0].expected_world_version == future.version and jobs[0].turn_id == future.turn_id
    assert jobs[0].source_event_ids == (event.event_id,)
    assert jobs[0].target_ids == ("adapt.0",) and jobs[0].proposal_id
    assert jobs[0].prompt_version == PROMPT_VERSION
    assert len(canonical_bytes(jobs[0].payload)) <= 65_536


def test_priority_budget_lineage_roles_and_stable_order() -> None:
    request = candidate(
        split=True,
        adaptations=(
            ("parent.id", 0.8),
            *((f"adapt.{index}", 0.1 + 0.05 * index) for index in range(6)),
        ),
    )
    planner = ReferenceNarrativePlanner(provider_config_hash="tested-config")
    jobs = planner(request)
    assert len(jobs) == 4
    assert jobs[0].job_type == "speciation" and jobs[0].target_ids == ("parent.id", "child.id")
    assert tuple(job.target_ids[0] for job in jobs[1:]) == ("adapt.5", "adapt.4", "adapt.3")
    lineages = cast(Mapping[str, JsonValue], jobs[0].payload["facts"])["lineages"]
    assert (
        cast(tuple[Mapping[str, JsonValue], ...], lineages)[0]["lineage_role"] == "retained_parent"
    )
    assert cast(tuple[Mapping[str, JsonValue], ...], lineages)[1]["lineage_role"] == "new_child"
    assert all(job.provider_config_hash == "tested-config" for job in jobs)
    assert validate_result(jobs[0], fallback_result(jobs[0]))["proposal_id"] == "speciation:fact"
    reordered = replace(
        request,
        stage_results=tuple(
            replace(stage, events=tuple(reversed(stage.events)), duration_ms=17.0)
            for stage in reversed(request.stage_results)
        ),
    )
    assert planner(reordered) == jobs


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("adaptation_threshold", 0.009),
        ("adaptation_threshold", 1.01),
        ("adaptation_threshold", float("nan")),
        ("adaptation_threshold", True),
        ("max_jobs_per_turn", 5),
        ("max_jobs_per_turn", -1),
        ("max_jobs_per_turn", True),
        ("provider_config_hash", ""),
    ],
)
def test_invalid_planner_configuration(field: str, value: JsonValue) -> None:
    with pytest.raises(ValueError):
        if field == "adaptation_threshold":
            ReferenceNarrativePlanner(adaptation_threshold=cast(float, value))
        elif field == "max_jobs_per_turn":
            ReferenceNarrativePlanner(max_jobs_per_turn=cast(int, value))
        else:
            ReferenceNarrativePlanner(provider_config_hash=cast(str, value))


def test_explicit_disable_and_higher_threshold() -> None:
    request = candidate(adaptations=(("adapt.0", 0.03),))
    assert ReferenceNarrativePlanner(max_jobs_per_turn=0)(request) == ()
    assert ReferenceNarrativePlanner(adaptation_threshold=0.04)(request) == ()


def test_complete_generation_revision_and_tied_importance_are_frozen() -> None:
    before = replace(initial(), version=WorldVersion("narrative", "fork", 2, 9))
    request = candidate(before, adaptations=(("adapt.1", 0.03), ("adapt.0", 0.03)))
    jobs = ReferenceNarrativePlanner()(request)
    assert [job.target_ids for job in jobs] == [("adapt.0",), ("adapt.1",)]
    assert all(
        job.expected_world_version == WorldVersion("narrative", "fork", 2, 10) for job in jobs
    )


@pytest.mark.parametrize(
    "bad",
    ["duplicate", "duplicate-outcome", "stage", "turn", "version", "failed", "target", "axes"],
)
def test_invalid_fact_sources_are_rejected(bad: str) -> None:
    request = candidate(adaptations=(("adapt.0", 0.03),))
    stage = request.stage_results[0]
    event = stage.events[0]
    if bad == "duplicate":
        stage = replace(stage, events=(event, event))
    elif bad == "duplicate-outcome":
        stage = replace(stage, events=(event, replace(event, event_id="other-id")))
    elif bad == "stage":
        stage = replace(stage, stage_name="untrusted")
    elif bad == "turn":
        stage = replace(stage, events=(replace(event, turn=event.turn - 1),))
    elif bad == "version":
        stage = replace(stage, events=(replace(event, version=event.version.advance()),))
    elif bad == "failed":
        stage = replace(stage, errors=("failed",))
    else:
        payload = {
            **event.payload,
            **({"species": "missing"} if bad == "target" else {"trait_changes": {"armor": 0.03}}),
        }
        stage = replace(stage, events=(replace(event, payload=payload),))
    with pytest.raises(ValueError):
        ReferenceNarrativePlanner()(replace(request, stage_results=(stage,)))


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("population", 21),
        ("child_id", "missing"),
        ("remaining_population", 0),
        ("score", 0.5),
        ("isolation_turns", 100),
        ("cause_terms", {}),
    ],
)
def test_speciation_facts_require_real_targets_counts_and_evidence(
    field: str, bad: JsonValue
) -> None:
    request = candidate(split=True)
    stage = request.stage_results[-1]
    event = stage.events[0]
    stage = replace(stage, events=(replace(event, payload={**event.payload, field: bad}),))
    with pytest.raises(ValueError):
        ReferenceNarrativePlanner()(replace(request, stage_results=(stage,)))


def test_unbounded_metadata_is_not_copied_into_payload() -> None:
    request = candidate(adaptations=(("adapt.0", 0.03),))
    state = dict(request.species_state)
    state["adapt.0"] = {
        **cast(Mapping[str, JsonValue], state["adapt.0"]),
        "narrative": "x" * 100_000,
    }
    request = request.with_snapshot(replace(request.snapshot, state={"species": state}))
    job = ReferenceNarrativePlanner()(request)[0]
    assert len(canonical_bytes(job.payload)) < 4096
    assert "narrative" not in cast(Mapping[str, JsonValue], job.payload["facts"])


def test_jobs_commit_atomically_and_replay_with_identical_input_hashes(tmp_path: Path) -> None:
    store, request, jobs, head = setup(tmp_path, split=True)
    planned_again = ReferenceNarrativePlanner()(request)
    assert [job.input_hash for job in planned_again] == [job.input_hash for job in jobs]
    assert store.commit(request, command_key="facts", jobs=planned_again) == head
    repo = SQLiteJobRepository(store.db)
    assert all(repo.get(job.job_id) is not None for job in jobs)
    assert head.snapshot_id == jobs[0].snapshot_id
    assert (
        len([item for item in store.messages("narrative", "main") if item["kind"] == "AIJobQueued"])
        == 2
    )


def test_bad_job_rolls_back_world_facts_and_queue_together(tmp_path: Path) -> None:
    store = WorldStore(tmp_path)
    before = store.create(initial(), seed=31)
    request = candidate(before, adaptations=(("adapt.0", 0.03),))
    job = ReferenceNarrativePlanner()(request)[0]
    with pytest.raises(ValueError, match="source event"):
        store.commit(
            request, command_key="facts", jobs=(replace(job, source_event_ids=("missing",)),)
        )
    assert store.head("narrative", "main") == before
    assert SQLiteJobRepository(store.db).get(job.job_id) is None
    with store.db.connection() as connection:
        assert connection.execute("SELECT count(*) FROM events").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_template_worker_applies_only_annotations_and_cli_batch_is_bounded(
    tmp_path: Path,
) -> None:
    store, _, jobs, head = setup(tmp_path, split=True)
    first = await run_jobs(tmp_path, limit=1)
    assert len(first) == 1 and first[0].status == JobStatus.APPLIED
    repo = SQLiteJobRepository(store.db)
    assert len(repo.annotations(head.version)) == 1
    assert len(await run_jobs(tmp_path, limit=4)) == 1
    assert await run_jobs(tmp_path, limit=4) == ()
    assert len(repo.annotations(head.version)) == len(jobs)
    current = store.head("narrative", "main")
    assert current.snapshot_id == head.snapshot_id and current.state_hash == head.state_hash
    assert current.arrays == head.arrays and current.state == head.state


class GatedTemplate(TemplateNarrativeProvider):
    def __init__(self) -> None:
        self.started, self.release = asyncio.Event(), asyncio.Event()

    async def generate(
        self,
        *,
        schema: Mapping[str, object],
        payload: Mapping[str, JsonValue],
        repair_error: str | None = None,
    ) -> str:
        self.started.set()
        await self.release.wait()
        return await super().generate(schema=schema, payload=payload, repair_error=repair_error)


@pytest.mark.asyncio
async def test_old_version_finishes_stale_and_cancellation_is_fenced(tmp_path: Path) -> None:
    store, _, jobs, head = setup(tmp_path)
    repo = SQLiteJobRepository(store.db)
    provider = GatedTemplate()
    task = asyncio.create_task(
        NarrativeWorker(repo, provider, clock=lambda: 100.0).run_once("test")
    )
    await provider.started.wait()
    latest = store.commit(TurnContext(head.turn_id + 1, head, 31), command_key="next")
    provider.release.set()
    completed = await task
    assert completed is not None and completed.status == JobStatus.STALE
    assert repo.annotations(head.version) == repo.annotations(latest.version) == ()
    assert store.head("narrative", "main") == latest
    assert repo.get(jobs[0].job_id) == completed
    next_request = candidate(latest, adaptations=(("adapt.1", 0.03),))
    next_jobs = ReferenceNarrativePlanner()(next_request)
    newest = store.commit(next_request, command_key="cancel", jobs=next_jobs)
    provider = GatedTemplate()
    task = asyncio.create_task(
        NarrativeWorker(repo, provider, clock=lambda: 100.0).run_once("cancel")
    )
    await provider.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    saved = repo.get(next_jobs[0].job_id)
    assert saved is not None and saved.status == JobStatus.CANCELLED
    assert await run_jobs(tmp_path) == ()
    assert repo.annotations(newest.version) == ()


def test_cli_no_work_and_invalid_limits_or_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = WorldStore(tmp_path)
    store.create(initial(), seed=31)
    assert main(["--root", str(tmp_path), "--limit", "1"]) == 0
    assert '"processed": 0' in capsys.readouterr().out
    for args in (
        [],
        ["--root", str(tmp_path), "--limit", "101"],
        ["--root", str(tmp_path / "missing")],
    ):
        with pytest.raises(SystemExit) as error:
            main(args)
        assert error.value.code == 2
    assert not (tmp_path / "missing").exists()


def test_cli_cancellation_exits_normally(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def cancel(root: Path, *, limit: int = 4) -> tuple[AIJob, ...]:
        raise asyncio.CancelledError

    monkeypatch.setattr("scripts.run_narrative_jobs.run_jobs", cancel)
    assert main(["--root", str(tmp_path)]) == 0


def test_real_evolution_pipeline_accepts_planner_without_changing_numerical_output(
    tmp_path: Path,
) -> None:
    store = WorldStore(tmp_path)
    pipeline = evolution_pipeline()
    engine = SimulationEngineV2(store, pipeline, MODEL_ID, ReferenceNarrativePlanner())
    before = store.create(
        create_reference_snapshot(
            WorldVersion("evolution", "main"),
            seed=37,
            manifest=engine.manifest,
            width=4,
            height=2,
            max_species=8,
        ),
        seed=37,
    )
    direct = pipeline.execute(TurnContext(1, before, 37, command={"command_id": "one"}))
    current = engine.run_turn(TurnCommand(before.version, "one"))
    assert current.state_hash == direct.snapshot.state_hash
    assert engine.run_turn(TurnCommand(before.version, "one")) == current
    assert len(ReferenceNarrativePlanner()(direct)) <= 4
    assert len(pipeline.stages) == 26

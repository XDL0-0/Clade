"""Real CPU branches, durable retries, CoW, provenance and bounded concurrency."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from typing import cast

import pytest

from app.ai.jobs.models import JobSpec
from app.simulation.v2.context import TurnContext, WorldSnapshot
from app.simulation.v2.contracts import SimulationStage, StageResult
from app.simulation.v2.engine import SimulationEngineV2, TurnCommand
from app.simulation.v2.experiments import (
    Branch,
    ExperimentConflict,
    ExperimentPlan,
    ExperimentResult,
    ExperimentRunner,
    Forcing,
    Scenario,
    Version,
    timeline_id,
)
from app.simulation.v2.pipeline import DeterministicPipeline
from app.simulation.v2.reference.model import ecological_pipeline, feedback_pipeline
from app.simulation.v2.reference.world import MODEL_ID, create_reference_snapshot
from app.simulation.v2.values import JsonValue, digest, thaw
from app.simulation.v2.version import WorldVersion
from app.storage.codec import decode, encode
from app.storage.database import StorageCorruption
from app.storage.history import commit_id
from app.storage.store import WorldStore


def setup(
    root: Path, factory: Callable[[], DeterministicPipeline] = ecological_pipeline
) -> tuple[WorldStore, ExperimentPlan]:
    store = WorldStore(root)
    engine = SimulationEngineV2(store, factory(), MODEL_ID)
    parent = store.create(
        create_reference_snapshot(
            WorldVersion("world", "main"),
            seed=8,
            manifest=engine.manifest,
            width=4,
            height=2,
            max_species=8,
        ),
        seed=8,
    )
    control = Scenario(version=1, id="control", name="Unforced control")
    treatment = Scenario(
        version=1,
        id="warming",
        name="Warm pulse",
        forcing=(Forcing(turn=1, warming_offset=8.0, co2_ppm=560.0, disaster_severity=0.2),),
    )
    plan = ExperimentPlan(
        version=1,
        id="paired",
        name="Paired forcing",
        source=Version.model_validate(parent.version.to_dict()),
        turns=3,
        rng_namespace="paired-comparison-v1",
        control="control",
        branches=(
            Branch(id="control", name="Control", scenario=control),
            Branch(id="twin", name="Repeat control", scenario=control),
            Branch(id="treatment", name="Treatment", scenario=treatment),
        ),
    )
    return store, plan


class FaultStage(SimulationStage):
    def __init__(self, original: SimulationStage, fail: Callable[[TurnContext], bool]) -> None:
        self.contract, self.original, self.fail = original.contract, original, fail

    def execute(self, context: TurnContext) -> StageResult:
        if self.fail(context):
            raise RuntimeError("injected failure")
        return self.original.execute(context)


def faulty(fail: Callable[[TurnContext], bool]) -> Callable[[], DeterministicPipeline]:
    def factory() -> DeterministicPipeline:
        return DeterministicPipeline(
            [
                FaultStage(stage, fail) if stage.contract.name == "reference_climate" else stage
                for stage in ecological_pipeline().stages
            ]
        )

    return factory


def branch_id(context: TurnContext) -> str:
    marker = context.command["experiment"]
    assert isinstance(marker, Mapping) and isinstance(marker["branch_id"], str)
    return marker["branch_id"]


def test_serial_parallel_every_turn_hash_and_control_twin(tmp_path: Path) -> None:
    serial_store, plan = setup(tmp_path / "serial")
    parallel_store, _ = setup(tmp_path / "parallel")
    first = ExperimentRunner(serial_store, pipeline_factory=ecological_pipeline).run(
        plan, workers=1
    )
    second = ExperimentRunner(parallel_store, pipeline_factory=ecological_pipeline).run(
        plan, workers=3
    )
    assert first.completed and second.completed
    assert first.to_dict() == second.to_dict()
    by_id = {branch.branch_id: branch for branch in first.branches}
    control, twin, treatment = (by_id[key] for key in ("control", "twin", "treatment"))
    assert [p["state_hash"] for p in control.observations] == [
        p["state_hash"] for p in twin.observations
    ]
    assert control.state_hash != treatment.state_hash
    comparison = first.comparisons["twin"]
    assert isinstance(comparison, Mapping)
    deltas = comparison["delta"]
    assert isinstance(deltas, Mapping) and deltas and all(value == 0 for value in deltas.values())
    assert "metrics.trait_distribution.armor.mean" in deltas
    assert "population_by_role.producer.structural_biomass" in deltas
    assert "metrics.mean_trophic_level" in deltas
    assert serial_store.head("world", "main").version == plan.source.value()
    repeated = ExperimentRunner(serial_store, pipeline_factory=ecological_pipeline).run(
        plan, workers=2
    )
    assert repeated.to_dict() == first.to_dict()
    with serial_store.db.connection() as connection:
        assert connection.execute("SELECT count(*) FROM commands").fetchone()[0] == 9
        assert connection.execute("SELECT count(*) FROM ai_jobs").fetchone()[0] == 0


def test_persistent_environment_and_turn_local_pressure_defaults(tmp_path: Path) -> None:
    store, plan = setup(tmp_path)
    result = ExperimentRunner(store, pipeline_factory=ecological_pipeline).run(plan)
    assert result.completed
    branch = next(b for b in result.branches if b.branch_id == "treatment")
    assert branch.version is not None
    final = store.history.replay(branch.version)
    assert final.domain("environment")["co2_ppm"] == 560
    assert final.domain("environment")["warming_offset"] == 8
    request = store.command_input(branch.version)
    command = request["command"]
    assert isinstance(command, Mapping)
    assert command["disaster_severity"] == command["disease_pressure"] == 0
    assert "warming_offset" not in command and "co2_ppm" not in command
    assert request["rng"] == plan.rng_namespace


def test_partial_failure_recovers_exact_keys_without_rerunning_prefix(tmp_path: Path) -> None:
    store, plan = setup(tmp_path)
    calls: list[tuple[str, int]] = []
    lock = Lock()
    armed = True

    def fail(context: TurnContext) -> bool:
        with lock:
            calls.append((branch_id(context), context.turn_id))
            return armed and branch_id(context) == "treatment" and context.turn_id == 2

    runner = ExperimentRunner(store, pipeline_factory=faulty(fail))
    first = runner.run(plan, workers=2)
    failed = next(b for b in first.branches if b.branch_id == "treatment")
    assert not first.completed and failed.completed_turns == 1 and failed.status == "failed"
    assert failed.error is not None and failed.error["type"] == "StageExecutionError"
    assert first.comparisons["treatment"] == {
        "available": False,
        "reason": "both branches must complete",
    }
    prior_keys: list[str]
    with store.db.connection() as connection:
        prior_keys = [row[0] for row in connection.execute("SELECT command_key FROM commands")]
    calls.clear()
    armed = False
    second = runner.run(plan, workers=2)
    assert second.completed and calls == [("treatment", 2), ("treatment", 3)]
    with store.db.connection() as connection:
        keys = [row[0] for row in connection.execute("SELECT command_key FROM commands")]
    assert len(keys) == 9 and set(prior_keys) <= set(keys)
    clean_store, _ = setup(tmp_path / "fresh")
    clean = ExperimentRunner(clean_store, pipeline_factory=ecological_pipeline).run(plan)
    assert second.to_dict() == clean.to_dict()


def test_lost_acknowledgement_reports_durable_partial_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, plan = setup(tmp_path)
    original = store.commit
    armed = True

    def interrupted(
        candidate: TurnContext, *, command_key: str, jobs: tuple[JobSpec, ...] = ()
    ) -> WorldSnapshot:
        result = original(candidate, command_key=command_key, jobs=jobs)
        if armed and branch_id(candidate) == "treatment" and candidate.turn_id == 2:
            raise RuntimeError("lost acknowledgement after commit")
        return result

    monkeypatch.setattr(store, "commit", interrupted)
    runner = ExperimentRunner(store, pipeline_factory=ecological_pipeline)
    first = runner.run(plan)
    failed = next(b for b in first.branches if b.branch_id == "treatment")
    assert failed.status == "failed" and failed.completed_turns == 2
    assert failed.version == failed.observed_head_version
    armed = False
    second = runner.run(plan)
    assert second.completed
    with store.db.connection() as connection:
        assert connection.execute("SELECT count(*) FROM commands").fetchone()[0] == 9


def test_forks_are_cow_and_manifest_survives_failure_before_any_turn(tmp_path: Path) -> None:
    store, plan = setup(tmp_path)
    before = tuple(sorted(tmp_path.rglob("*.npz")))
    failed = ExperimentRunner(store, pipeline_factory=faulty(lambda _: True)).run(plan, workers=3)
    assert not failed.completed and all(b.completed_turns == 0 for b in failed.branches)
    assert tuple(sorted(tmp_path.rglob("*.npz"))) == before
    with store.db.connection() as connection:
        rows = connection.execute("SELECT * FROM commits WHERE timeline_id!='main'").fetchall()
        assert len(rows) == 3
        for row in rows:
            assert row["is_checkpoint"] == 0 and row["parent_id"] == commit_id(plan.source.value())
            assert decode(row["payload"])["arrays"] == {}
        assert connection.execute("SELECT count(*) FROM simulation_experiments").fetchone()[0] == 1
    changed = plan.model_copy(update={"rng_namespace": "different"})
    with pytest.raises(ExperimentConflict):
        ExperimentRunner(store, pipeline_factory=ecological_pipeline).run(changed)


def test_rewind_and_external_advance_are_not_overwritten(tmp_path: Path) -> None:
    store, plan = setup(tmp_path)
    runner = ExperimentRunner(store, pipeline_factory=ecological_pipeline)
    result = runner.run(plan)
    control = next(b for b in result.branches if b.branch_id == "control")
    twin = next(b for b in result.branches if b.branch_id == "twin")
    assert control.version is not None and twin.version is not None
    rewound = store.replace_head(
        control.version, plan.source.value(), command_key="external-rewind"
    )
    advanced = SimulationEngineV2(store, ecological_pipeline(), MODEL_ID).run_turn(
        TurnCommand(twin.version, "outside")
    )
    retried = runner.run(plan)
    assert {b.branch_id: b.status for b in retried.branches} == {
        "control": "conflict",
        "treatment": "completed",
        "twin": "conflict",
    }
    assert store.head("world", control.timeline_id).version == rewound.version
    assert store.head("world", twin.timeline_id).version == advanced.version


@pytest.mark.parametrize("tamper", ["foreign_turn", "command_checksum", "command_content"])
def test_invalid_committed_prefix_is_rejected(tmp_path: Path, tamper: str) -> None:
    store, plan = setup(tmp_path)
    first = ExperimentRunner(
        store,
        pipeline_factory=faulty(lambda ctx: branch_id(ctx) == "treatment" and ctx.turn_id == 2),
    ).run(plan)
    branch = next(b for b in first.branches if b.branch_id == "treatment")
    assert branch.version is not None
    if tamper == "foreign_turn":
        SimulationEngineV2(store, ecological_pipeline(), MODEL_ID).run_turn(
            TurnCommand(branch.version, "outsider")
        )
    else:
        payload = cast(dict[str, JsonValue], thaw(store.command_input(branch.version)))
        payload["rng"] = "tampered"
        with store.db.transaction() as connection:
            connection.execute(
                "UPDATE commands SET input_payload=? WHERE commit_id=?",
                (encode(payload), commit_id(branch.version)),
            )
            if tamper == "command_content":
                connection.execute(
                    "UPDATE commands SET input_hash=? WHERE commit_id=?",
                    (digest(payload), commit_id(branch.version)),
                )
    observed = store.head("world", branch.timeline_id).version
    retry = ExperimentRunner(store, pipeline_factory=ecological_pipeline).run(plan)
    rejected = next(b for b in retry.branches if b.branch_id == "treatment")
    assert rejected.status != "completed" and rejected.error is not None
    assert store.head("world", branch.timeline_id).version == observed
    assert rejected.error["type"] == (
        "StorageCorruption" if tamper == "command_checksum" else "ExperimentConflict"
    )


def test_concurrent_same_experiment_does_not_double_advance(tmp_path: Path) -> None:
    store, plan = setup(tmp_path)

    def launch(_: int) -> ExperimentResult:
        return ExperimentRunner(store, pipeline_factory=ecological_pipeline).run(plan, workers=2)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(launch, (0, 1)))
    assert all(result.completed for result in results)
    with store.db.connection() as connection:
        assert connection.execute("SELECT count(*) FROM commands").fetchone()[0] == 9
        assert connection.execute("SELECT max(revision) FROM commits").fetchone()[0] == 3


@pytest.mark.parametrize("workers", [0, 5, True, 1.5])
def test_worker_limit_and_runtime_plan_revalidation(tmp_path: Path, workers: int) -> None:
    store, plan = setup(tmp_path)
    runner = ExperimentRunner(store, pipeline_factory=ecological_pipeline)
    with pytest.raises(ValueError):
        runner.run(plan, workers=workers)
    with pytest.raises(ValueError):
        runner.run(plan.model_copy(update={"turns": 1001}))


def test_old_recipe_is_not_upgraded_and_feedback_recipe_is_supported(tmp_path: Path) -> None:
    store, plan = setup(tmp_path / "old")
    with pytest.raises(ValueError, match="source recipe"):
        ExperimentRunner(store, pipeline_factory=feedback_pipeline).run(plan)
    with store.db.connection() as connection:
        assert connection.execute("SELECT count(*) FROM timelines").fetchone()[0] == 1
    feedback_store, feedback_plan = setup(tmp_path / "feedback", feedback_pipeline)
    result = ExperimentRunner(feedback_store, pipeline_factory=feedback_pipeline).run(
        feedback_plan, workers=2
    )
    assert result.completed
    traits = next(
        b.summary["population_weighted_traits"] for b in result.branches if b.branch_id == "control"
    )
    assert isinstance(traits, Mapping) and "engineering" in traits


def test_manifest_checksum_and_factory_instance_reuse_rejected(tmp_path: Path) -> None:
    store, plan = setup(tmp_path)
    reused = ecological_pipeline()
    with pytest.raises(ValueError, match="independent stage"):
        ExperimentRunner(store, pipeline_factory=lambda: reused).run(plan)
    runner = ExperimentRunner(store, pipeline_factory=ecological_pipeline)
    assert runner.run(plan).completed
    with store.db.transaction() as connection:
        connection.execute("UPDATE simulation_experiments SET payload='{}'")
    with pytest.raises(StorageCorruption, match="manifest checksum"):
        runner.run(plan)


def test_fork_from_historical_nonzero_turn_and_wrong_existing_origin(tmp_path: Path) -> None:
    store, plan = setup(tmp_path)
    engine = SimulationEngineV2(store, ecological_pipeline(), MODEL_ID)
    source = engine.run_turn(TurnCommand(plan.source.value(), "first"))
    later = engine.run_turn(TurnCommand(source.version, "second"))
    historical = plan.model_copy(
        update={"source": Version.model_validate(source.version.to_dict())}
    )
    wrong = next(b for b in historical.branches if b.id == "treatment")
    existing = store.fork(later.version, timeline_id(historical, wrong))
    result = ExperimentRunner(store, pipeline_factory=ecological_pipeline).run(historical)
    assert {b.branch_id: b.status for b in result.branches} == {
        "control": "completed",
        "treatment": "conflict",
        "twin": "completed",
    }
    assert store.head("world", existing.version.timeline_id).version == existing.version
    control = next(b for b in result.branches if b.branch_id == "control")
    assert control.turn == source.turn_id + 3
    assert control.observations[0]["relative_turn"] == 1 and control.observations[0]["turn"] == 2


def test_changed_fork_parent_metadata_cannot_be_adopted(tmp_path: Path) -> None:
    store, plan = setup(tmp_path)
    runner = ExperimentRunner(store, pipeline_factory=ecological_pipeline)
    first = runner.run(plan)
    control = next(b for b in first.branches if b.branch_id == "control")
    with store.db.transaction() as connection:
        connection.execute(
            "UPDATE timelines SET fork_id=NULL WHERE timeline_id=?", (control.timeline_id,)
        )
    retried = runner.run(plan)
    rejected = next(b for b in retried.branches if b.branch_id == "control")
    assert rejected.status == "conflict" and rejected.completed_turns == 0
    assert store.head("world", control.timeline_id).version == control.version

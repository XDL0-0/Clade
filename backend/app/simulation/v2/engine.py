"""Opt-in durable turn coordinator; the legacy runtime remains the production default."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace

from app.ai.jobs.models import JobSpec
from app.storage.store import WorldStore

from .context import TurnContext, WorldSnapshot
from .events import WorldEvent
from .pipeline import DeterministicPipeline
from .values import JsonValue, freeze, freeze_mapping
from .version import WorldVersion

RNG_ALGORITHM = "clade-blake2b-counter-v1"


@dataclass(frozen=True, slots=True)
class TurnCommand:
    expected_version: WorldVersion
    idempotency_key: str
    payload: Mapping[str, JsonValue] = field(default_factory=dict)
    rng_namespace: str | None = None
    active_events: tuple[WorldEvent, ...] = ()
    external_pressures: tuple[JsonValue, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.expected_version, WorldVersion):
            raise TypeError("Turn command requires a complete WorldVersion")
        if not isinstance(self.idempotency_key, str) or not self.idempotency_key.strip():
            raise ValueError("Turn command requires a nonempty idempotency key")
        if self.rng_namespace is not None and (
            not isinstance(self.rng_namespace, str) or not self.rng_namespace.strip()
        ):
            raise ValueError("An explicit RNG namespace must be nonempty text")
        object.__setattr__(self, "payload", freeze_mapping(self.payload))
        events = tuple(self.active_events)
        if any(not isinstance(event, WorldEvent) for event in events):
            raise TypeError("Active events must be immutable WorldEvent values")
        object.__setattr__(self, "active_events", events)
        object.__setattr__(
            self, "external_pressures", tuple(freeze(value) for value in self.external_pressures)
        )


class SimulationEngineV2:
    """Run pure stages and publish state, facts, observations and jobs in one commit.

    A narrative planner is a pure function of the frozen candidate context. It must
    only construct JobSpec values, never invoke providers. The candidate snapshot
    retains its input version/turn until commit; planners obtain the future frozen
    snapshot with ``replace(context.snapshot, version=context.world_version.advance(),
    turn_id=context.turn_id)``. Stage timings are zeroed in the planner's view so
    wall-clock measurements cannot change durable job inputs on a successful retry.
    """

    def __init__(
        self,
        store: WorldStore,
        pipeline: DeterministicPipeline,
        model_id: str,
        narrative_planner: Callable[[TurnContext], tuple[JobSpec, ...]] | None = None,
    ) -> None:
        if not isinstance(model_id, str) or not model_id.strip():
            raise ValueError("A versioned model ID is required")
        self.store = store
        self.pipeline = pipeline
        self.model_id = model_id
        self.narrative_planner = narrative_planner

    @property
    def manifest(self) -> Mapping[str, JsonValue]:
        """Definition to persist when explicitly creating a world for this engine."""
        return freeze_mapping(
            {
                "model": self.model_id,
                "stages": {
                    stage.contract.name: stage.contract.version for stage in self.pipeline.stages
                },
                "rng": RNG_ALGORITHM,
            }
        )

    def run_turn(self, command: TurnCommand) -> WorldSnapshot:
        if not isinstance(command, TurnCommand):
            raise TypeError("run_turn requires an immutable TurnCommand")
        # Read the specified historical input, not the moving head: storage checks
        # successful duplicate commands before CAS, even after later turns commit.
        snapshot = self.store.history.replay(command.expected_version)
        command.expected_version.require(snapshot.version)
        for field_name, expected in self.manifest.items():
            if snapshot.manifest.get(field_name) != expected:
                raise ValueError(
                    f"Unsupported world manifest {field_name}; explicit migration required"
                )
        context = TurnContext(
            turn_id=snapshot.turn_id + 1,
            snapshot=snapshot,
            seed=self.store.world_seed(snapshot.version.world_id),
            command={**command.payload, "command_id": command.idempotency_key},
            rng_namespace=command.rng_namespace or snapshot.version.timeline_id,
            active_events=command.active_events,
            external_pressures=command.external_pressures,
        )
        candidate = self.pipeline.execute(context)
        jobs: tuple[JobSpec, ...] = ()
        if self.narrative_planner is not None:
            planning_context = replace(
                candidate,
                stage_results=tuple(
                    replace(result, duration_ms=0.0) for result in candidate.stage_results
                ),
            )
            jobs = self.narrative_planner(planning_context)
            if not isinstance(jobs, tuple) or any(not isinstance(job, JobSpec) for job in jobs):
                raise TypeError("Narrative planner must return a tuple of immutable JobSpec values")
        return self.store.commit(candidate, command_key=command.idempotency_key, jobs=jobs)

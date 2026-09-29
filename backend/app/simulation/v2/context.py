"""Frozen world inputs and per-stage context views."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import TYPE_CHECKING

from .events import WorldEvent
from .seed import SeedManager
from .values import FrozenArray, JsonValue, digest, freeze, freeze_mapping, natural
from .version import WorldVersion

if TYPE_CHECKING:
    from .contracts import StageResult


@dataclass(frozen=True, slots=True)
class WorldSnapshot:
    version: WorldVersion
    turn_id: int
    state: Mapping[str, JsonValue] = field(default_factory=dict)
    arrays: Mapping[str, FrozenArray] = field(default_factory=dict)
    manifest: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.version, WorldVersion):
            raise TypeError("Snapshot requires WorldVersion")
        natural(self.turn_id, "turn_id")
        object.__setattr__(self, "state", freeze_mapping(self.state))
        object.__setattr__(self, "manifest", freeze_mapping(self.manifest))
        if any(
            not isinstance(k, str) or not isinstance(v, FrozenArray) for k, v in self.arrays.items()
        ):
            raise TypeError("Snapshot arrays must be named immutable buffers")
        object.__setattr__(self, "arrays", MappingProxyType(dict(sorted(self.arrays.items()))))

    @property
    def state_hash(self) -> str:
        """Numerical hash excludes versions, clocks, narratives and profiling data."""
        return digest(
            {
                "manifest": self.manifest,
                "state": self.state,
                "arrays": {key: value.content_hash for key, value in self.arrays.items()},
            }
        )

    @property
    def snapshot_id(self) -> str:
        return digest([self.version.to_dict(), self.turn_id, self.state_hash])

    def domain(self, name: str) -> Mapping[str, JsonValue]:
        value = self.state.get(name, MappingProxyType({}))
        if not isinstance(value, Mapping):
            raise TypeError(f"State domain {name!r} must be a mapping")
        return value


@dataclass(frozen=True, slots=True)
class TurnContext:
    turn_id: int
    snapshot: WorldSnapshot
    seed: int
    command: Mapping[str, JsonValue] = field(default_factory=dict)
    rng_namespace: str = ""
    start_snapshot_id: str = ""
    active_events: tuple[WorldEvent, ...] = ()
    external_pressures: tuple[JsonValue, ...] = ()
    stage_results: tuple[StageResult, ...] = ()
    evolution_proposals: tuple[JsonValue, ...] = ()
    ai_jobs: tuple[JsonValue, ...] = ()
    metrics: Mapping[str, JsonValue] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        natural(self.turn_id, "turn_id")
        natural(self.seed, "seed")
        if self.turn_id <= self.snapshot.turn_id:
            raise ValueError("A turn must follow its input snapshot")
        object.__setattr__(self, "command", freeze_mapping(self.command))
        object.__setattr__(self, "metrics", freeze_mapping(self.metrics))
        for name in ("external_pressures", "evolution_proposals", "ai_jobs"):
            object.__setattr__(self, name, tuple(freeze(v) for v in getattr(self, name)))
        for name in ("active_events", "stage_results", "warnings", "errors"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if any(not isinstance(v, str) for v in (*self.warnings, *self.errors)):
            raise TypeError("Context diagnostics must be serializable strings")
        if not self.rng_namespace:
            object.__setattr__(self, "rng_namespace", self.timeline_id)
        if not self.start_snapshot_id:
            object.__setattr__(self, "start_snapshot_id", self.snapshot.snapshot_id)

    @property
    def world_version(self) -> WorldVersion:
        return self.snapshot.version

    @property
    def world_id(self) -> str:
        return self.world_version.world_id

    @property
    def timeline_id(self) -> str:
        return self.world_version.timeline_id

    @property
    def seeds(self) -> SeedManager:
        return SeedManager(self.seed, self.rng_namespace, self.turn_id)

    @property
    def input_hash(self) -> str:
        return digest(
            {
                "snapshot": self.snapshot.snapshot_id,
                "turn": self.turn_id,
                "command": self.command,
                "seed": self.seed,
                "rng_namespace": self.rng_namespace,
                "pressures": self.external_pressures,
                "active_events": [event.to_dict() for event in self.active_events],
            }
        )

    @property
    def environment_state(self) -> Mapping[str, JsonValue]:
        return self.snapshot.domain("environment")

    @property
    def species_state(self) -> Mapping[str, JsonValue]:
        return self.snapshot.domain("species")

    @property
    def population_state(self) -> FrozenArray:
        return self.snapshot.arrays["population"]

    @property
    def habitat_state(self) -> Mapping[str, JsonValue]:
        return self.snapshot.domain("habitat")

    @property
    def food_web_state(self) -> Mapping[str, JsonValue]:
        return self.snapshot.domain("food_web")

    @property
    def gene_state(self) -> Mapping[str, JsonValue]:
        return self.snapshot.domain("gene")

    def with_snapshot(self, snapshot: WorldSnapshot) -> TurnContext:
        self.world_version.require(snapshot.version)
        return replace(self, snapshot=snapshot)

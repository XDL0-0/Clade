"""Explicit stage IO and typed deltas. Stages never receive repositories or callbacks."""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from .events import WorldEvent
from .values import FrozenArray, JsonValue, freeze, freeze_mapping, natural

if TYPE_CHECKING:
    from .context import TurnContext


@dataclass(frozen=True, slots=True)
class StatePatch:
    path: tuple[str, ...]
    operation: Literal["create", "replace", "delete"]
    value: JsonValue = None
    expected_hash: str | None = None

    def __post_init__(self) -> None:
        if not self.path or any(not isinstance(p, str) or not p or "." in p for p in self.path):
            raise ValueError("A state patch needs a non-empty string path")
        if self.operation not in ("create", "replace", "delete"):
            raise ValueError("Unknown patch operation")
        if (self.operation == "create") != (self.expected_hash is None):
            raise ValueError("Create expects absence; replace/delete require an old value hash")
        object.__setattr__(self, "path", tuple(self.path))
        object.__setattr__(self, "value", freeze(self.value))


@dataclass(frozen=True, slots=True)
class ArrayPatch:
    name: str
    value: FrozenArray | None
    expected_hash: str | None = None

    def __post_init__(self) -> None:
        if not self.name or "." in self.name or (self.value is None and self.expected_hash is None):
            raise ValueError("An array change requires a name and a new value or prior hash")
        if not isinstance(self.name, str) or (
            self.value is not None and not isinstance(self.value, FrozenArray)
        ):
            raise TypeError("Array patches require a string name and immutable buffer")


@dataclass(frozen=True, slots=True)
class StateDelta:
    state: tuple[StatePatch, ...] = ()
    arrays: tuple[ArrayPatch, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "state", tuple(self.state))
        object.__setattr__(self, "arrays", tuple(self.arrays))
        if any(not isinstance(p, StatePatch) for p in self.state) or any(
            not isinstance(p, ArrayPatch) for p in self.arrays
        ):
            raise TypeError("Deltas must contain typed patches")
        paths = [p.path for p in self.state]
        for index, path in enumerate(paths):
            if any(
                path[: len(other)] == other or other[: len(path)] == path
                for other in paths[index + 1 :]
            ):
                raise ValueError("Overlapping state patches are not allowed")
        if len({p.name for p in self.arrays}) != len(self.arrays):
            raise ValueError("An array may be patched only once per result")


@dataclass(frozen=True, slots=True)
class StageContract:
    name: str
    version: str
    dependencies: tuple[str, ...] = ()
    reads: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()
    deterministic: bool = True
    parallelizable: bool = False
    side_effects: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if any(not isinstance(v, str) or not v.strip() for v in (self.name, self.version)):
            raise ValueError("A stage requires a stable name and version")
        for name in ("dependencies", "reads", "writes", "side_effects"):
            values = tuple(getattr(self, name))
            if any(not isinstance(v, str) or not v.strip() for v in values):
                raise ValueError(f"Invalid stage {name}")
            if len(set(values)) != len(values):
                raise ValueError(f"Duplicate stage {name}")
            object.__setattr__(self, name, values)
        for path in (*self.reads, *self.writes):
            if not (path.startswith("state.") or path.startswith("arrays.")):
                raise ValueError("IO paths must start with state. or arrays.")
            if any(not part for part in path.split(".")):
                raise ValueError("IO paths cannot contain empty segments")


@dataclass(frozen=True, slots=True)
class StageResult:
    stage_name: str
    state_delta: StateDelta = field(default_factory=StateDelta)
    events: tuple[WorldEvent, ...] = ()
    metrics: Mapping[str, JsonValue] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    random_seed: int = 0
    duration_ms: float = 0.0
    stage_version: str = "1"
    input_hash: str = ""
    output_hash: str = ""
    evolution_proposals: tuple[JsonValue, ...] = ()
    ai_jobs: tuple[JsonValue, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.stage_name, str) or not self.stage_name.strip():
            raise ValueError("Stage result name is required")
        if not isinstance(self.state_delta, StateDelta):
            raise TypeError("Stage result delta must be typed")
        natural(self.random_seed, "random_seed")
        if not math.isfinite(self.duration_ms) or self.duration_ms < 0:
            raise ValueError("Invalid stage duration")
        object.__setattr__(self, "metrics", freeze_mapping(self.metrics))
        for name in ("events", "warnings", "errors"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if any(not isinstance(v, str) for v in (*self.warnings, *self.errors)):
            raise TypeError("Diagnostics must be serializable strings")
        if any(not isinstance(event, WorldEvent) for event in self.events):
            raise TypeError("Events must be WorldEvent values")
        for name in ("evolution_proposals", "ai_jobs"):
            object.__setattr__(self, name, tuple(freeze(v) for v in getattr(self, name)))


class SimulationStage(ABC):
    contract: StageContract

    def validate_inputs(self, context: TurnContext) -> None:
        """Stages may add domain-specific validation; the runner enforces declared IO."""
        if context.errors:
            raise ValueError("Cannot execute a context containing errors")

    @abstractmethod
    def execute(self, context: TurnContext) -> StageResult:
        """Return a proposal without changing any published state."""

    def validate_outputs(self, context: TurnContext, result: StageResult) -> None:
        if result.stage_name != self.contract.name or result.errors:
            raise ValueError(f"Invalid/failed result for {self.contract.name}: {result.errors}")

"""World identity is independent from the visible turn counter."""

from __future__ import annotations

from dataclasses import dataclass, replace

from .values import natural


class VersionConflict(ValueError):
    """The expected immutable world head is no longer current."""


@dataclass(frozen=True, slots=True)
class WorldVersion:
    world_id: str
    timeline_id: str
    generation: int = 0
    revision: int = 0

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (self.world_id, self.timeline_id)
        ):
            raise ValueError("World and timeline IDs are required")
        natural(self.generation, "generation")
        natural(self.revision, "revision")

    def advance(self, *, replace_generation: bool = False) -> WorldVersion:
        return replace(
            self,
            revision=self.revision + 1,
            generation=self.generation + int(replace_generation),
        )

    def require(self, expected: WorldVersion) -> None:
        if self != expected:
            raise VersionConflict(f"Expected {expected}, current {self}")

    def to_dict(self) -> dict[str, str | int]:
        return {
            "world_id": self.world_id,
            "timeline_id": self.timeline_id,
            "generation": self.generation,
            "revision": self.revision,
        }

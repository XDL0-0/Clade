"""Immutable facts produced by simulation, separate from transient UI progress."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from .values import JsonValue, digest, freeze_mapping, natural
from .version import WorldVersion


@dataclass(frozen=True, slots=True)
class WorldEvent:
    event_id: str
    version: WorldVersion
    turn: int
    type: str
    ordinal: int
    actor: str | None = None
    target: str | None = None
    location: tuple[int, ...] = ()
    cause: tuple[str, ...] = ()
    payload: Mapping[str, JsonValue] = field(default_factory=dict)
    schema_version: int = 1

    def __post_init__(self) -> None:
        natural(self.turn, "turn")
        natural(self.ordinal, "event ordinal")
        if (
            any(not isinstance(v, str) or not v.strip() for v in (self.event_id, self.type))
            or self.schema_version != 1
            or not isinstance(self.version, WorldVersion)
        ):
            raise ValueError("Invalid event identity or schema")
        if any(not isinstance(v, str) or not v for v in self.cause):
            raise TypeError("Event causes must be stable string IDs")
        if any(isinstance(v, bool) or not isinstance(v, int) for v in self.location):
            raise TypeError("Event locations must be integer coordinates")
        object.__setattr__(self, "payload", freeze_mapping(self.payload))
        object.__setattr__(self, "cause", tuple(self.cause))
        object.__setattr__(self, "location", tuple(self.location))

    @classmethod
    def create(
        cls,
        *,
        version: WorldVersion,
        turn: int,
        command_id: str,
        stage: str,
        ordinal: int,
        event_type: str,
        actor: str | None = None,
        target: str | None = None,
        location: tuple[int, ...] = (),
        cause: tuple[str, ...] = (),
        payload: Mapping[str, JsonValue] | None = None,
    ) -> WorldEvent:
        if not command_id or not stage:
            raise ValueError("Event source command and stage are required")
        identity = digest([version.to_dict(), command_id, stage, ordinal])
        return cls(
            identity,
            version,
            turn,
            event_type,
            ordinal,
            actor,
            target,
            location,
            cause,
            {} if payload is None else payload,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "version": self.version.to_dict(),
            "turn": self.turn,
            "type": self.type,
            "ordinal": self.ordinal,
            "actor": self.actor,
            "target": self.target,
            "location": self.location,
            "cause": self.cause,
            "payload": self.payload,
            "schema_version": self.schema_version,
        }

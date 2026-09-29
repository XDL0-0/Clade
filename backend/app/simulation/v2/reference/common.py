"""Small typed helpers shared by the versioned CPU reference stages."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
from numpy.typing import NDArray

from ..context import TurnContext
from ..contracts import ArrayPatch, StageResult, StateDelta, StatePatch
from ..events import WorldEvent
from ..values import FrozenArray, JsonValue, digest
from .topology import TOPOLOGY_VERSION

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]


def number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not np.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def field(context: TurnContext, name: str) -> FloatArray:
    """Copy inputs before numerical work; no write-through views are exposed."""
    return np.array(context.snapshot.arrays[name].numpy(), dtype=np.float64, copy=True)


def counts(context: TurnContext) -> IntArray:
    return np.array(context.snapshot.arrays["population"].numpy(), dtype=np.int64, copy=True)


def state_patch(context: TurnContext, path: tuple[str, ...], value: JsonValue) -> StatePatch:
    parent: Mapping[str, JsonValue] = context.snapshot.state
    for segment in path[:-1]:
        child = parent[segment]
        if not isinstance(child, Mapping):
            raise ValueError(f"Not a state domain: {segment}")
        parent = child
    return StatePatch(
        path,
        "replace" if path[-1] in parent else "create",
        value,
        digest(parent[path[-1]]) if path[-1] in parent else None,
    )


def result(
    context: TurnContext,
    name: str,
    *,
    arrays: Mapping[str, NDArray[np.generic]] | None = None,
    state: tuple[StatePatch, ...] = (),
    events: tuple[WorldEvent, ...] = (),
    metrics: Mapping[str, JsonValue] | None = None,
    warnings: tuple[str, ...] = (),
) -> StageResult:
    return StageResult(
        name,
        state_delta=StateDelta(
            state,
            tuple(
                ArrayPatch(
                    key,
                    FrozenArray.from_numpy(value),
                    context.snapshot.arrays[key].content_hash
                    if key in context.snapshot.arrays
                    else None,
                )
                for key, value in (arrays or {}).items()
            ),
        ),
        events=events,
        metrics=metrics or {},
        warnings=warnings,
    )


def event(
    context: TurnContext,
    stage: str,
    kind: str,
    *,
    ordinal: int = 0,
    payload: Mapping[str, JsonValue] | None = None,
    actor: str | None = None,
    target: str | None = None,
    location: tuple[int, ...] = (),
    cause: tuple[str, ...] = (),
) -> WorldEvent:
    command = context.command.get("command_id")
    if not isinstance(command, str):
        command = digest(context.command)
    return WorldEvent.create(
        version=context.world_version.advance(),
        turn=context.turn_id,
        command_id=command,
        stage=stage,
        ordinal=ordinal,
        event_type=kind,
        payload=payload,
        actor=actor,
        target=target,
        location=location,
        cause=cause or (command,),
    )


def domain(context: TurnContext, name: str) -> Mapping[str, JsonValue]:
    return context.snapshot.domain(name)


def geometry(context: TurnContext) -> tuple[int, int]:
    value = domain(context, "geometry")
    if value.get("topology", TOPOLOGY_VERSION) != TOPOLOGY_VERSION:
        raise ValueError("Unknown reference topology; explicit migration required")
    width, height = value["width"], value["height"]
    if type(width) is not int or type(height) is not int or width < 2 or height < 1:
        raise ValueError("Reference geometry needs positive integer dimensions")
    return width, height

"""Apply validated proposals to a private candidate; never publish external state."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import cast

import numpy as np

from .context import TurnContext, WorldSnapshot
from .contracts import StateDelta
from .values import JsonValue, digest, freeze_mapping, thaw


class DeltaConflict(ValueError):
    """A patch targets stale, absent or unauthorized state."""


def covers(declared: str, target: str) -> bool:
    return target == declared or target.startswith(declared + ".")


def read_path(snapshot: WorldSnapshot, path: str) -> object:
    prefix, *parts = path.split(".")
    if prefix == "arrays":
        if len(parts) != 1:
            raise ValueError("Array IO declares a complete named buffer")
        return snapshot.arrays[parts[0]]
    if prefix != "state" or not parts:
        raise ValueError(f"Invalid read path {path}")
    value: object = snapshot.state
    for part in parts:
        if not isinstance(value, Mapping) or part not in value:
            raise KeyError(path)
        value = value[part]
    return value


def restrict(context: TurnContext, reads: tuple[str, ...]) -> TurnContext:
    """Deliver only declared state/array inputs, copied into an immutable view."""
    state: dict[str, object] = {}
    arrays = {}
    for path in sorted(reads, key=lambda p: (len(p.split(".")), p)):
        value = read_path(context.snapshot, path)
        if path.startswith("arrays."):
            name = path.split(".")[1]
            arrays[name] = context.snapshot.arrays[name]
            continue
        parts = path.split(".")[1:]
        target = state
        for part in parts[:-1]:
            target = cast(dict[str, object], target.setdefault(part, {}))
        target[parts[-1]] = thaw(cast(JsonValue, value))
    return replace(
        context,
        stage_results=(),
        metrics={},
        warnings=(),
        errors=(),
        evolution_proposals=(),
        ai_jobs=(),
        snapshot=replace(
            context.snapshot,
            state=freeze_mapping(state),
            arrays=arrays,
        ),
    )


def apply_delta(
    snapshot: WorldSnapshot,
    delta: StateDelta,
    *,
    writes: tuple[str, ...],
) -> WorldSnapshot:
    state = cast(dict[str, object], thaw(snapshot.state))
    arrays = dict(snapshot.arrays)
    for patch in delta.state:
        path = "state." + ".".join(patch.path)
        if not any(covers(allowed, path) for allowed in writes):
            raise DeltaConflict(f"Undeclared write: {path}")
        target = state
        for part in patch.path[:-1]:
            next_value = target.get(part)
            if not isinstance(next_value, dict):
                raise DeltaConflict(f"Missing mapping ancestor in {path}")
            target = next_value
        key = patch.path[-1]
        if patch.operation == "create":
            if key in target:
                raise DeltaConflict(f"Entity already exists: {path}")
        elif key not in target or digest(target[key]) != patch.expected_hash:
            raise DeltaConflict(f"Stale patch: {path}")
        if patch.operation == "delete":
            del target[key]
        else:
            target[key] = thaw(patch.value)
    for array_patch in delta.arrays:
        path = "arrays." + array_patch.name
        if path not in writes:
            raise DeltaConflict(f"Undeclared array write: {path}")
        previous = arrays.get(array_patch.name)
        if (None if previous is None else previous.content_hash) != array_patch.expected_hash:
            raise DeltaConflict(f"Stale array patch: {path}")
        if array_patch.value is None:
            del arrays[array_patch.name]
        else:
            if previous is not None and (
                previous.shape != array_patch.value.shape
                or previous.dtype != array_patch.value.dtype
            ):
                raise DeltaConflict("Array replacement must preserve shape and dtype")
            arrays[array_patch.name] = array_patch.value
    candidate = replace(snapshot, state=freeze_mapping(state), arrays=arrays)
    validate_snapshot(candidate)
    return candidate


def validate_snapshot(snapshot: WorldSnapshot) -> None:
    for key, array in snapshot.arrays.items():
        values = array.numpy()
        if not np.isfinite(values).all():
            raise ValueError(f"Non-finite values in {key}")
        if key in {"population", "biomass", "plant_biomass", "detritus", "nutrients", "npp"}:
            if np.any(np.less(values, 0)):
                raise ValueError(f"Negative values in {key}")
        if key == "population" and values.dtype.kind not in "iu":
            raise ValueError("Reference population must use integer counts")

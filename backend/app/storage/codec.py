"""Compact entity patches and array manifests; tensors never become JSON lists."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import cast

from app.simulation.v2.context import WorldSnapshot
from app.simulation.v2.numerics import isolated_numerics
from app.simulation.v2.values import JsonValue, canonical_bytes, digest, freeze_mapping, thaw
from app.simulation.v2.version import WorldVersion

from .database import StorageCorruption
from .objects import ArrayObjectStore


def encode(value: object) -> str:
    return canonical_bytes(value).decode("utf-8")


@isolated_numerics
def decode(value: str) -> Mapping[str, JsonValue]:
    try:
        return freeze_mapping(json.loads(value))
    except (TypeError, ValueError) as exc:
        raise StorageCorruption("Invalid canonical record") from exc


def version_from(value: Mapping[str, object]) -> WorldVersion:
    return WorldVersion(
        cast(str, value["world_id"]),
        cast(str, value["timeline_id"]),
        cast(int, value["generation"]),
        cast(int, value["revision"]),
    )


def checkpoint(snapshot: WorldSnapshot, objects: ArrayObjectStore) -> Mapping[str, JsonValue]:
    return freeze_mapping(
        {
            "state": snapshot.state,
            "manifest": snapshot.manifest,
            "arrays": {name: objects.put(array) for name, array in snapshot.arrays.items()},
        }
    )


def diff_state(
    old: Mapping[str, JsonValue],
    new: Mapping[str, JsonValue],
    path: tuple[str, ...] = (),
) -> list[dict[str, object]]:
    changes: list[dict[str, object]] = []
    for key in sorted(old.keys() | new.keys()):
        here = (*path, key)
        if key not in new:
            changes.append({"path": here, "operation": "delete", "expected_hash": digest(old[key])})
        elif key not in old:
            changes.append({"path": here, "operation": "create", "value": new[key]})
        elif digest(old[key]) != digest(new[key]):
            left, right = old[key], new[key]
            if isinstance(left, Mapping) and isinstance(right, Mapping):
                changes.extend(diff_state(left, right, here))
            else:
                changes.append(
                    {
                        "path": here,
                        "operation": "replace",
                        "value": right,
                        "expected_hash": digest(left),
                    }
                )
    return changes


def delta(
    before: WorldSnapshot,
    after: WorldSnapshot,
    objects: ArrayObjectStore,
) -> Mapping[str, JsonValue]:
    changed: dict[str, object] = {}
    for name in sorted(before.arrays.keys() | after.arrays.keys()):
        old, new = before.arrays.get(name), after.arrays.get(name)
        old_hash = old.content_hash if old else None
        if old_hash != (new.content_hash if new else None):
            changed[name] = {
                "expected_hash": old_hash,
                "value": objects.put(new) if new else None,
            }
    return freeze_mapping(
        {
            "state": diff_state(before.state, after.state),
            "arrays": changed,
            "manifest": after.manifest if before.manifest != after.manifest else None,
        }
    )


def restore(
    payload: Mapping[str, JsonValue],
    version: WorldVersion,
    turn: int,
    objects: ArrayObjectStore,
) -> WorldSnapshot:
    state, manifest, arrays = payload["state"], payload["manifest"], payload["arrays"]
    if not all(isinstance(value, Mapping) for value in (state, manifest, arrays)):
        raise StorageCorruption("Invalid checkpoint fields")
    return WorldSnapshot(
        version,
        turn,
        freeze_mapping(state),
        {
            name: objects.get(freeze_mapping(value))
            for name, value in cast(Mapping[str, JsonValue], arrays).items()
        },
        freeze_mapping(manifest),
    )


def replay_delta(
    before: WorldSnapshot,
    payload: Mapping[str, JsonValue],
    version: WorldVersion,
    turn: int,
    objects: ArrayObjectStore,
) -> WorldSnapshot:
    state = cast(dict[str, object], thaw(before.state))
    patches = payload["state"]
    if not isinstance(patches, tuple):
        raise StorageCorruption("Invalid entity delta")
    for raw in patches:
        patch = freeze_mapping(raw)
        path = patch["path"]
        if not isinstance(path, tuple) or not path or not all(isinstance(p, str) for p in path):
            raise StorageCorruption("Invalid delta path")
        target = state
        for part in cast(tuple[str, ...], path[:-1]):
            child = target.get(part)
            if not isinstance(child, dict):
                raise StorageCorruption("Missing delta ancestor")
            target = child
        key = cast(str, path[-1])
        operation = patch["operation"]
        if operation == "create":
            if key in target:
                raise StorageCorruption("Delta creates an existing key")
        elif operation in ("replace", "delete"):
            if key not in target or digest(target[key]) != patch["expected_hash"]:
                raise StorageCorruption("Entity delta base mismatch")
        else:
            raise StorageCorruption("Unknown delta operation")
        if operation == "delete":
            del target[key]
        else:
            target[key] = thaw(patch["value"])
    arrays = dict(before.arrays)
    for name, raw in freeze_mapping(payload["arrays"]).items():
        patch = freeze_mapping(raw)
        previous = arrays.get(name)
        if (previous.content_hash if previous else None) != patch["expected_hash"]:
            raise StorageCorruption("Array delta base mismatch")
        if patch["value"] is None:
            if name not in arrays:
                raise StorageCorruption("Cannot delete missing array")
            del arrays[name]
        else:
            arrays[name] = objects.get(freeze_mapping(patch["value"]))
    return WorldSnapshot(
        version,
        turn,
        freeze_mapping(state),
        arrays,
        freeze_mapping(payload["manifest"]) if payload["manifest"] is not None else before.manifest,
    )

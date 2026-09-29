"""Canonical values and immutable numeric buffers used by snapshots and deltas."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TypeAlias

import numpy as np
from numpy.typing import NDArray

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | tuple["JsonValue", ...] | Mapping[str, "JsonValue"]


def freeze(value: object) -> JsonValue:
    """Copy recursively, rejecting opaque objects, non-string keys and non-finite numbers."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON numbers must be finite")
        return 0.0 if value == 0 else value
    if isinstance(value, Mapping):
        if any(not isinstance(k, str) for k in value):
            raise TypeError("State keys must be strings")
        return MappingProxyType({k: freeze(v) for k, v in sorted(value.items())})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(v) for v in value)
    raise TypeError(f"Unsupported state value: {type(value).__name__}")


def freeze_mapping(value: object) -> Mapping[str, JsonValue]:
    result = freeze(value)
    if not isinstance(result, Mapping):
        raise TypeError("Expected a mapping")
    return result


def thaw(value: JsonValue) -> object:
    if isinstance(value, Mapping):
        return {k: thaw(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [thaw(v) for v in value]
    return value


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        thaw(freeze(value)),
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def digest(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def natural(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class FrozenArray:
    """Bytes-backed NumPy view: even setflags(write=True) cannot mutate it."""

    dtype: str
    shape: tuple[int, ...]
    data: bytes

    def __post_init__(self) -> None:
        dtype = np.dtype(self.dtype)
        if dtype.kind not in "biuf" or dtype.fields is not None:
            raise ValueError("Only boolean, integer and real floating arrays are supported")
        shape = tuple(self.shape)
        for n in shape:
            natural(n, "array dimension")
        data = bytes(self.data)
        if len(data) != math.prod(shape) * dtype.itemsize:
            raise ValueError("Array shape/dtype does not match buffer size")
        array = np.frombuffer(data, dtype=dtype).reshape(shape)
        if not np.isfinite(array).all():
            raise ValueError("Array contains NaN or infinity")
        canonical = np.array(array, dtype=dtype.newbyteorder("<"), order="C", copy=True)
        if dtype.kind == "f":
            canonical[canonical == 0] = 0  # Normalize negative zero.
        object.__setattr__(self, "dtype", canonical.dtype.str)
        object.__setattr__(self, "shape", shape)
        object.__setattr__(self, "data", canonical.tobytes())

    @classmethod
    def from_numpy(cls, value: NDArray[np.generic]) -> FrozenArray:
        return cls(value.dtype.str, tuple(value.shape), value.tobytes(order="C"))

    def numpy(self) -> NDArray[np.generic]:
        return np.frombuffer(self.data, dtype=self.dtype).reshape(self.shape)

    @property
    def content_hash(self) -> str:
        header = canonical_bytes({"dtype": self.dtype, "shape": self.shape})
        return hashlib.sha256(len(header).to_bytes(8, "big") + header + self.data).hexdigest()

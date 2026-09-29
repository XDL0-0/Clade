"""Legacy native FP modes cannot alter V2 arithmetic, canonical values or replay."""

from __future__ import annotations

import ctypes
import struct
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pytest

from app.simulation.v2.context import TurnContext, WorldSnapshot
from app.simulation.v2.contracts import (
    ArrayPatch,
    SimulationStage,
    StageContract,
    StageResult,
    StateDelta,
)
from app.simulation.v2.numerics import numeric_environment
from app.simulation.v2.pipeline import DeterministicPipeline, StageExecutionError
from app.simulation.v2.values import FrozenArray, canonical_bytes, freeze
from app.simulation.v2.version import WorldVersion
from app.storage.codec import decode
from app.storage.store import WorldStore


def libm() -> ctypes.CDLL:
    library = ctypes.CDLL("libm.so.6")
    for name in ("fegetenv", "fesetenv"):
        getattr(library, name).argtypes = [ctypes.c_void_p]
        getattr(library, name).restype = ctypes.c_int
    library.fesetround.argtypes = [ctypes.c_int]
    library.fesetround.restype = ctypes.c_int
    return library


def controls() -> tuple[int, int]:
    state = (ctypes.c_longdouble * 16)()
    assert libm().fegetenv(ctypes.byref(state)) == 0
    return (
        ctypes.c_uint16.from_address(ctypes.addressof(state)).value,
        ctypes.c_uint32.from_address(ctypes.addressof(state) + 28).value & 0xFFC0,
    )


@contextmanager
def host_mode(flags: int, rounding: int = 0) -> Iterator[None]:
    library = libm()
    saved, modified = (ctypes.c_longdouble * 16)(), (ctypes.c_longdouble * 16)()
    assert library.fegetenv(ctypes.byref(saved)) == 0
    assert library.fegetenv(ctypes.byref(modified)) == 0
    mxcsr = ctypes.c_uint32.from_address(ctypes.addressof(modified) + 28)
    mxcsr.value = (mxcsr.value & ~0x8040) | flags
    try:
        assert library.fesetenv(ctypes.byref(modified)) == 0
        assert library.fesetround(rounding) == 0
        yield
    finally:
        assert library.fesetenv(ctypes.byref(saved)) == 0


class UnderflowStage(SimulationStage):
    contract = StageContract(
        "underflow", "1", reads=("arrays.reserve",), writes=("arrays.reserve",)
    )

    def execute(self, context: TurnContext) -> StageResult:
        before = context.snapshot.arrays["reserve"]
        array = np.asarray(before.numpy(), dtype=np.float64) / 8
        return StageResult(
            "underflow",
            StateDelta(
                arrays=(ArrayPatch("reserve", FrozenArray.from_numpy(array), before.content_hash),)
            ),
        )


def initial() -> WorldSnapshot:
    return WorldSnapshot(
        WorldVersion("numeric", "main"),
        0,
        {},
        {"reserve": FrozenArray.from_numpy(np.array([np.finfo(np.float64).tiny]))},
    )


@pytest.mark.parametrize("flags,rounding", [(0, 0), (0x8000, 0x800), (0x8040, 0x400)])
def test_pipeline_restores_host_and_preserves_subnormal_arithmetic(
    flags: int, rounding: int
) -> None:
    pipeline = DeterministicPipeline([UnderflowStage()])
    snapshot = initial()
    with numeric_environment():
        baseline = pipeline.execute(TurnContext(1, snapshot, 4)).snapshot
    with host_mode(flags, rounding):
        before = controls()
        actual = pipeline.execute(TurnContext(1, snapshot, 4)).snapshot
        assert controls() == before
        assert actual.snapshot_id == baseline.snapshot_id
        assert actual.arrays["reserve"].data == baseline.arrays["reserve"].data
        assert actual.arrays["reserve"].data != bytes(8)


def test_nested_scope_and_stage_failure_restore_caller_environment() -> None:
    class Failed(UnderflowStage):
        def execute(self, context: TurnContext) -> StageResult:
            with numeric_environment():
                assert controls()[1] & 0xE040 == 0
            raise ValueError("expected fixture failure")

    with host_mode(0x8040, 0xC00):
        before = controls()
        with pytest.raises(StageExecutionError):
            DeterministicPipeline([Failed()]).execute(TurnContext(1, initial(), 4))
        assert controls() == before


@pytest.mark.parametrize("dtype", ["<f2", "<f4", "<f8", ">f4", ">f8"])
def test_canonical_arrays_and_json_preserve_denormals_under_daz(dtype: str) -> None:
    with numeric_environment():
        dt = np.dtype(dtype)
        source = np.array([np.nextafter(dt.type(0), dt.type(1)), -0.0], dtype=dt)
        expected = FrozenArray.from_numpy(source)
        scalar = struct.unpack("<d", struct.pack("<Q", 1))[0]
    with host_mode(0x8040, 0x800):
        before = controls()
        actual = FrozenArray.from_numpy(source)
        assert actual.content_hash == expected.content_hash
        assert struct.pack("<d", freeze(scalar)) == struct.pack("<Q", 1)
        assert canonical_bytes({"tiny": scalar}) == b'{"tiny":5e-324}'
        assert struct.pack("<d", decode('{"tiny":5e-324}')["tiny"]) == struct.pack("<Q", 1)
        assert controls() == before


def test_durable_subnormal_values_replay_under_changed_host_mode(tmp_path: Path) -> None:
    store = WorldStore(tmp_path)
    with numeric_environment():
        scalar = struct.unpack("<d", struct.pack("<Q", 1))[0]
        array = FrozenArray.from_numpy(np.array([scalar]))
        source = store.create(
            WorldSnapshot(WorldVersion("tiny", "main"), 0, {"tiny": scalar}, {"x": array}), seed=4
        )
    with host_mode(0x8040):
        loaded = store.history.replay(source.version)
        assert loaded.snapshot_id == source.snapshot_id
        assert loaded.arrays["x"].data == array.data


def test_independent_simulation_threads_keep_their_own_host_controls() -> None:
    def calculate(flags: int) -> str:
        with host_mode(flags, 0x800 if flags else 0):
            before = controls()
            outcome = DeterministicPipeline([UnderflowStage()]).execute(
                TurnContext(1, initial(), 4)
            )
            assert controls() == before
            return outcome.snapshot.state_hash

    with ThreadPoolExecutor(max_workers=3) as pool:
        assert len(set(pool.map(calculate, [0, 0x8000, 0x8040] * 3))) == 1

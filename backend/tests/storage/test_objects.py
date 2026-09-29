"""Integrity, deduplication and publication tests for immutable array objects."""

from __future__ import annotations

import io
import multiprocessing
import os
import stat
import zipfile
from collections.abc import Mapping
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path
from typing import cast

import numpy as np
import pytest
from numpy.typing import NDArray

from app.simulation.v2.values import FrozenArray, JsonValue
from app.storage.objects import ArrayObjectStore


def _objects(root: Path) -> list[Path]:
    return sorted((root / "objects").rglob("*.npz"))


def _chunk_path(root: Path, manifest: Mapping[str, JsonValue], index: int = 0) -> Path:
    chunks = cast(tuple[str, ...], manifest["chunks"])
    content_hash = chunks[index]
    return root / "objects" / content_hash[:2] / f"{content_hash}.npz"


def _put_in_process(root: str) -> str:
    value = FrozenArray.from_numpy(np.arange(257, dtype=np.int64))
    manifest = ArrayObjectStore(Path(root), chunk_elements=64).put(value)
    return str(manifest["content_hash"])


@pytest.mark.parametrize("dtype", ["?", "i1", "u8", "<i4", ">i4", "f4", ">f8"])
@pytest.mark.parametrize("shape", [(), (10,), (3, 5), (2, 3, 4)])
def test_round_trip_and_readonly(tmp_path: Path, dtype: str, shape: tuple[int, ...]) -> None:
    size = int(np.prod(shape))
    array = np.arange(size).reshape(shape).astype(dtype)
    value = FrozenArray.from_numpy(array)
    store = ArrayObjectStore(tmp_path, chunk_elements=4)
    manifest = store.put(value)
    assert manifest["codec"] == "npz-v1"
    assert manifest["content_hash"] == value.content_hash
    # Reader settings can change without changing the meaning of persisted chunks.
    restored = ArrayObjectStore(tmp_path).get(manifest)
    assert restored == value
    np.testing.assert_array_equal(restored.numpy(), array)
    with pytest.raises(ValueError):
        restored.numpy().setflags(write=True)


def test_dedup_and_one_changed_chunk(tmp_path: Path) -> None:
    store = ArrayObjectStore(tmp_path, chunk_elements=4)
    array = np.arange(24, dtype=np.float64).reshape(4, 6)
    value = FrozenArray.from_numpy(array)
    first = store.put(value)
    paths = _objects(tmp_path)
    stats = [(path.stat().st_ino, path.stat().st_mtime_ns) for path in paths]
    assert store.put(value) == first
    assert stats == [(path.stat().st_ino, path.stat().st_mtime_ns) for path in paths]
    array[1, 1] = 777
    second = store.put(FrozenArray.from_numpy(array))
    assert len(_objects(tmp_path)) == len(paths) + 1
    assert store.get(first) == value
    np.testing.assert_array_equal(store.get(second).numpy(), array)


def test_repeated_chunks_and_endian_share_objects(tmp_path: Path) -> None:
    store = ArrayObjectStore(tmp_path, chunk_elements=3)
    array = np.tile(np.array([1, 2, 3], dtype="<i4"), 4)
    first = store.put(FrozenArray.from_numpy(array))
    assert len(_objects(tmp_path)) == 1
    assert len(set(cast(tuple[str, ...], first["chunks"]))) == 1
    second = store.put(FrozenArray.from_numpy(array.astype(">i4")))
    assert first == second
    assert len(_objects(tmp_path)) == 1
    assert store.get(second).dtype == "<i4"


@pytest.mark.parametrize("shape", [(0,), (2, 0, 9), (0, 0)])
def test_empty_array(tmp_path: Path, shape: tuple[int, ...]) -> None:
    value = FrozenArray.from_numpy(np.empty(shape, dtype=np.float64))
    store = ArrayObjectStore(tmp_path)
    manifest = store.put(value)
    assert manifest["chunks"] == ()
    assert _objects(tmp_path) == []
    assert store.get(manifest) == value


def test_negative_zero_is_canonical(tmp_path: Path) -> None:
    store = ArrayObjectStore(tmp_path)
    zero = store.put(FrozenArray.from_numpy(np.array([0.0])))
    assert store.put(FrozenArray.from_numpy(np.array([-0.0]))) == zero
    assert len(_objects(tmp_path)) == 1


@pytest.mark.parametrize("damage", ["truncate", "bytes", "missing"])
def test_corrupted_or_missing_object_rejected(tmp_path: Path, damage: str) -> None:
    store = ArrayObjectStore(tmp_path)
    manifest = store.put(FrozenArray.from_numpy(np.arange(16, dtype=np.int64)))
    path = _chunk_path(tmp_path, manifest)
    if damage == "truncate":
        path.write_bytes(path.read_bytes()[:30])
    elif damage == "bytes":
        path.write_bytes(b"not an NPZ")
    else:
        path.unlink()
    with pytest.raises(ValueError, match="Invalid or missing array object"):
        store.get(manifest)


def test_put_does_not_overwrite_corruption(tmp_path: Path) -> None:
    store = ArrayObjectStore(tmp_path)
    value = FrozenArray.from_numpy(np.arange(8, dtype=np.int32))
    manifest = store.put(value)
    path = _chunk_path(tmp_path, manifest)
    path.write_bytes(b"damaged immutable object")
    with pytest.raises(ValueError, match="Invalid or missing array object"):
        store.put(value)
    assert path.read_bytes() == b"damaged immutable object"
    assert list((tmp_path / "staging").iterdir()) == []


@pytest.mark.parametrize(
    "replacement",
    [
        np.zeros(4, dtype=np.float64),
        np.arange(4, dtype=np.int64),
        np.arange(4, dtype=np.float64).reshape(2, 2),
        np.arange(3, dtype=np.float64),
        np.array([1, 2, 3, float("nan")]),
        np.array([1, 2, 3, float("inf")]),
        np.array([1, 2, 3, {}], dtype=object),
        np.arange(4, dtype=">f8"),
    ],
)
def test_valid_npz_with_wrong_payload_rejected(
    tmp_path: Path, replacement: NDArray[np.generic]
) -> None:
    store = ArrayObjectStore(tmp_path)
    manifest = store.put(FrozenArray.from_numpy(np.arange(4, dtype=np.float64)))
    np.savez_compressed(_chunk_path(tmp_path, manifest), value=replacement)
    with pytest.raises(ValueError):
        store.get(manifest)


@pytest.mark.parametrize("kind", ["wrong_key", "extra_key", "truncated_npy", "extra_bytes"])
def test_npz_structure_rejected(tmp_path: Path, kind: str) -> None:
    store = ArrayObjectStore(tmp_path)
    array = np.arange(4, dtype=np.float64)
    manifest = store.put(FrozenArray.from_numpy(array))
    path = _chunk_path(tmp_path, manifest)
    if kind == "wrong_key":
        np.savez_compressed(path, wrong=array)
    elif kind == "extra_key":
        np.savez_compressed(path, value=array, another=array)
    else:
        buffer = io.BytesIO()
        np.save(buffer, array, allow_pickle=False)
        data = buffer.getvalue()[:-1] if kind == "truncated_npy" else buffer.getvalue() + b"x"
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("value.npy", data)
    with pytest.raises(ValueError):
        store.get(manifest)


@pytest.mark.parametrize(
    ("key", "bad_value"),
    [
        ("codec", "future-codec"),
        ("dtype", "O"),
        ("dtype", "float64"),
        ("dtype", ">f8"),
        ("dtype", "unknown"),
        ("dtype", None),
        ("shape", (-1,)),
        ("shape", (True,)),
        ("shape", (1.0,)),
        ("shape", ("4",)),
        ("shape", (2**64,)),
        ("shape", (2**32, 2**32)),
        ("shape", (1,) * 65),
        ("shape", "4"),
        ("shape", (2, 2)),
        ("chunk_elements", True),
        ("chunk_elements", 0),
        ("chunk_elements", -1),
        ("chunk_elements", 1.5),
        ("chunks", ()),
        ("chunks", ("../outside",)),
        ("chunks", ("A" * 64,)),
        ("chunks", (False,)),
        ("chunks", "a" * 64),
        ("content_hash", "a" * 64),
        ("content_hash", "../outside"),
    ],
)
def test_malicious_manifest_rejected(tmp_path: Path, key: str, bad_value: JsonValue) -> None:
    store = ArrayObjectStore(tmp_path)
    manifest = dict(store.put(FrozenArray.from_numpy(np.arange(4, dtype=np.float64))))
    manifest[key] = bad_value
    with pytest.raises(ValueError):
        store.get(manifest)


def test_manifest_missing_and_extra_fields_rejected(tmp_path: Path) -> None:
    store = ArrayObjectStore(tmp_path)
    manifest = dict(store.put(FrozenArray.from_numpy(np.arange(4, dtype=np.int64))))
    del manifest["chunk_elements"]
    with pytest.raises(ValueError, match="manifest fields"):
        store.get(manifest)
    manifest["chunk_elements"] = 8192
    manifest["path"] = "../../outside.npz"
    with pytest.raises(ValueError, match="manifest fields"):
        store.get(manifest)


def test_threaded_put_deduplicates(tmp_path: Path) -> None:
    store = ArrayObjectStore(tmp_path, chunk_elements=64)
    value = FrozenArray.from_numpy(np.arange(257, dtype=np.int64))
    with ThreadPoolExecutor(max_workers=8) as executor:
        manifests = list(executor.map(store.put, [value] * 16))
    assert all(manifest == manifests[0] for manifest in manifests)
    assert len(_objects(tmp_path)) == 5
    assert store.get(manifests[0]) == value
    assert list((tmp_path / "staging").iterdir()) == []


def test_multiprocess_put_deduplicates(tmp_path: Path) -> None:
    with ProcessPoolExecutor(
        max_workers=4, mp_context=multiprocessing.get_context("spawn")
    ) as executor:
        hashes = list(executor.map(_put_in_process, [str(tmp_path)] * 8))
    assert len(set(hashes)) == 1
    assert len(_objects(tmp_path)) == 5
    store = ArrayObjectStore(tmp_path, chunk_elements=64)
    value = FrozenArray.from_numpy(np.arange(257, dtype=np.int64))
    assert store.get(store.put(value)) == value


def test_staging_is_never_read_or_cleaned_by_another_writer(tmp_path: Path) -> None:
    store = ArrayObjectStore(tmp_path)
    value = FrozenArray.from_numpy(np.arange(4, dtype=np.int64))
    unfinished = store.staging / "another-process.npz"
    unfinished.write_bytes(b"partially written data")
    manifest = store.put(value)
    path = _chunk_path(tmp_path, manifest)
    path.rename(store.staging / path.name)
    with pytest.raises(ValueError):
        ArrayObjectStore(tmp_path).get(manifest)
    assert unfinished.read_bytes() == b"partially written data"
    assert (store.staging / path.name).exists()


def test_failed_publish_cleans_only_own_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ArrayObjectStore(tmp_path)
    unfinished = store.staging / "another-process.npz"
    unfinished.write_bytes(b"still writing")

    def fail_rename(source: Path, destination: Path) -> None:
        raise OSError("simulated failed atomic publication")

    monkeypatch.setattr(os, "rename", fail_rename)
    with pytest.raises(OSError, match="simulated failed"):
        store.put(FrozenArray.from_numpy(np.arange(4, dtype=np.int64)))
    assert _objects(tmp_path) == []
    assert list(store.staging.iterdir()) == [unfinished]


@pytest.mark.parametrize("after_rename", [False, True])
def test_fsync_failure_prevents_manifest_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, after_rename: bool
) -> None:
    store = ArrayObjectStore(tmp_path)
    value = FrozenArray.from_numpy(np.arange(4, dtype=np.int64))
    path = store.objects / value.content_hash[:2] / f"{value.content_hash}.npz"
    original_fsync = os.fsync

    def fail_at_boundary(descriptor: int) -> None:
        is_directory = stat.S_ISDIR(os.fstat(descriptor).st_mode)
        if (after_rename and is_directory and path.exists()) or (
            not after_rename and not is_directory
        ):
            raise OSError("simulated durability failure")
        original_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_at_boundary)
    with pytest.raises(OSError, match="durability failure"):
        store.put(value)
    assert path.exists() == after_rename
    assert list(store.staging.iterdir()) == []
    monkeypatch.setattr(os, "fsync", original_fsync)
    # An orphan from after rename is validated and synchronized by a later writer.
    assert store.get(store.put(value)) == value
    assert len(_objects(tmp_path)) == 1


def test_deduplication_synchronizes_existing_objects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ArrayObjectStore(tmp_path)
    value = FrozenArray.from_numpy(np.arange(4, dtype=np.int64))
    manifest = store.put(value)
    object_inode = _chunk_path(tmp_path, manifest).stat().st_ino
    original_fsync = os.fsync
    synced_inodes = []

    def record_fsync(descriptor: int) -> None:
        synced_inodes.append(os.fstat(descriptor).st_ino)
        original_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", record_fsync)
    assert store.put(value) == manifest
    assert object_inode in synced_inodes
    assert _chunk_path(tmp_path, manifest).parent.stat().st_ino in synced_inodes


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_invalid_chunk_size(tmp_path: Path, value: int) -> None:
    with pytest.raises(ValueError):
        ArrayObjectStore(tmp_path, chunk_elements=value)


def test_object_symlink_rejected(tmp_path: Path) -> None:
    store = ArrayObjectStore(tmp_path)
    value = FrozenArray.from_numpy(np.arange(4, dtype=np.int64))
    manifest = store.put(value)
    path = _chunk_path(tmp_path, manifest)
    moved = tmp_path / "outside.npz"
    path.rename(moved)
    path.symlink_to(moved)
    with pytest.raises(ValueError):
        store.get(manifest)
    with pytest.raises(ValueError):
        store.put(value)
    assert path.is_symlink()

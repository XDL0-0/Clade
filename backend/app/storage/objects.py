"""Durable immutable array chunks, published before SQLite references them.

Publication uses an advisory directory lock and atomic rename on POSIX. All
writers must use this store; externally modified objects are detected, never
repaired implicitly. Unreferenced objects and abandoned staging files require
separate, coordinated garbage collection.
"""

from __future__ import annotations

import fcntl
import math
import os
import re
import stat
import tempfile
import zipfile
import zlib
from collections.abc import Mapping
from pathlib import Path
from typing import BinaryIO

import numpy as np

from app.simulation.v2.values import FrozenArray, JsonValue, freeze_mapping

_HASH = re.compile(r"[0-9a-f]{64}\Z")
_MANIFEST_KEYS = {"codec", "dtype", "shape", "content_hash", "chunks", "chunk_elements"}


def _hash(value: object) -> str:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        raise ValueError("Invalid array content hash")
    return value


def _positive_integer(value: object, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _dimension(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("Array shape must contain non-negative integers")
    return value


def _directory_fd(path: Path) -> int:
    return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def _ensure_directory(path: Path) -> None:
    """Persist newly created directory entries, including a new store root."""
    if not path.exists():
        _ensure_directory(path.parent)
        try:
            path.mkdir()
        except FileExistsError:
            pass  # A concurrent writer may have created the same directory.
    if not stat.S_ISDIR(path.lstat().st_mode):
        raise ValueError(f"Array store directory is not a regular directory: {path}")
    # Sync even an existing entry: its creator may have crashed after mkdir.
    parent_fd = _directory_fd(path.parent)
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def _check_container(source: BinaryIO, dtype: str, elements: int) -> None:
    """Reject unexpected members and oversized/truncated NPY data before loading."""
    with zipfile.ZipFile(source) as archive:
        entries = archive.infolist()
        if len(entries) != 1 or entries[0].filename != "value.npy":
            raise ValueError("Array object must contain exactly value.npy")
        entry = entries[0]
        if entry.compress_type != zipfile.ZIP_DEFLATED or entry.flag_bits & 1:
            raise ValueError("Unsupported array object compression or encryption")
        with archive.open(entry) as member:
            version = np.lib.format.read_magic(member)
            if version == (1, 0):
                shape, fortran, actual_dtype = np.lib.format.read_array_header_1_0(member)
            elif version == (2, 0):
                shape, fortran, actual_dtype = np.lib.format.read_array_header_2_0(member)
            else:
                raise ValueError("Unsupported NPY header version")
            if shape != (elements,) or fortran or actual_dtype.str != dtype:
                raise ValueError("Array chunk shape/dtype does not match manifest")
            if actual_dtype.hasobject or actual_dtype.fields is not None:
                raise ValueError("Object and structured arrays are forbidden")
            if entry.file_size != member.tell() + elements * actual_dtype.itemsize:
                raise ValueError("Array object size does not match header")
    source.seek(0)


def _read_chunk(
    path: Path, content_hash: str, dtype: str, elements: int, *, sync: bool = False
) -> FrozenArray:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise ValueError("Array object is not a regular file")
            _check_container(source, dtype, elements)
            with np.load(source, allow_pickle=False) as archive:
                value = FrozenArray.from_numpy(archive["value"])
            if value.dtype != dtype or value.shape != (elements,):
                raise ValueError("Array chunk shape/dtype does not match manifest")
            if value.content_hash != content_hash:
                raise ValueError("Array chunk hash does not match manifest")
            if sync:
                os.fsync(source.fileno())
        return value
    except (OSError, ValueError, EOFError, zipfile.BadZipFile, OverflowError, zlib.error) as exc:
        raise ValueError(f"Invalid or missing array object {content_hash}: {exc}") from exc


class ArrayObjectStore:
    """Store canonical, flattened FrozenArray chunks under their logical hashes."""

    def __init__(self, root: Path, *, chunk_elements: int = 8192) -> None:
        self.chunk_elements = _positive_integer(chunk_elements, "chunk_elements")
        self.root = Path(root).absolute()
        self.objects = self.root / "objects"
        self.staging = self.root / "staging"
        _ensure_directory(self.root)
        _ensure_directory(self.objects)
        _ensure_directory(self.staging)

    def _put_chunk(self, chunk: FrozenArray) -> str:
        content_hash = chunk.content_hash
        directory = self.objects / content_hash[:2]
        _ensure_directory(directory)
        target = directory / f"{content_hash}.npz"
        directory_fd = _directory_fd(directory)
        temporary: Path | None = None
        try:
            # Independent opens also serialize threads in one process. Locking
            # the directory avoids persistent lock-file objects and rename races.
            fcntl.flock(directory_fd, fcntl.LOCK_EX)
            if os.path.lexists(target):
                _read_chunk(target, content_hash, chunk.dtype, chunk.shape[0], sync=True)
                os.fsync(directory_fd)
                return content_hash
            descriptor, temporary_name = tempfile.mkstemp(suffix=".npz", dir=self.staging)
            temporary = Path(temporary_name)
            with os.fdopen(descriptor, "wb") as destination:
                np.savez_compressed(destination, value=chunk.numpy())
                destination.flush()
                os.fsync(destination.fileno())
            _read_chunk(temporary, content_hash, chunk.dtype, chunk.shape[0])
            os.rename(temporary, target)
            os.fsync(directory_fd)
            # The target is durable first; syncing staging makes removal of our
            # temporary name durable, but is not needed to make the object readable.
            staging_fd = _directory_fd(self.staging)
            try:
                os.fsync(staging_fd)
            finally:
                os.close(staging_fd)
            return content_hash
        finally:
            try:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            finally:
                os.close(directory_fd)  # Closing releases the advisory publication lock.

    def put(self, value: FrozenArray) -> Mapping[str, JsonValue]:
        """Persist all chunks before returning a manifest safe for DB publication."""
        if not isinstance(value, FrozenArray):
            raise TypeError("ArrayObjectStore.put requires FrozenArray")
        flat = value.numpy().reshape(-1)
        chunks = tuple(
            self._put_chunk(FrozenArray.from_numpy(flat[start : start + self.chunk_elements]))
            for start in range(0, flat.size, self.chunk_elements)
        )
        return freeze_mapping(
            {
                "codec": "npz-v1",
                "dtype": value.dtype,
                "shape": value.shape,
                "content_hash": value.content_hash,
                "chunk_elements": self.chunk_elements,
                "chunks": chunks,
            }
        )

    def get(self, manifest: Mapping[str, JsonValue]) -> FrozenArray:
        """Validate every reference and its complete logical array before returning."""
        if not isinstance(manifest, Mapping) or set(manifest) != _MANIFEST_KEYS:
            raise ValueError("Invalid array manifest fields")
        if manifest["codec"] != "npz-v1":
            raise ValueError("Unsupported array codec")
        dtype_string = manifest["dtype"]
        if not isinstance(dtype_string, str):
            raise ValueError("Array dtype must be a canonical dtype string")
        try:
            dtype = np.dtype(dtype_string)
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid array dtype") from exc
        if (
            dtype.kind not in "biuf"
            or dtype.fields is not None
            or dtype.str != dtype_string
            or dtype.newbyteorder("<").str != dtype_string
        ):
            raise ValueError("Array dtype must be canonical numeric little endian")
        shape_value = manifest["shape"]
        if not isinstance(shape_value, (tuple, list)):
            raise ValueError("Array shape must contain non-negative integers")
        shape = tuple(_dimension(dimension) for dimension in shape_value)
        if len(shape) > 64 or any(n > np.iinfo(np.intp).max for n in shape):
            raise ValueError("Array shape exceeds NumPy limits")
        elements = math.prod(shape)
        if elements * dtype.itemsize > np.iinfo(np.intp).max:
            raise ValueError("Array byte size exceeds NumPy limits")
        chunk_elements = _positive_integer(manifest["chunk_elements"], "chunk_elements")
        content_hash = _hash(manifest["content_hash"])
        chunks = manifest["chunks"]
        if (
            not isinstance(chunks, (tuple, list))
            or len(chunks) != (elements + chunk_elements - 1) // chunk_elements
        ):
            raise ValueError("Array chunk count does not match manifest shape")
        hashes = tuple(_hash(value) for value in chunks)
        buffers = []
        for ordinal, chunk_hash in enumerate(hashes):
            size = min(chunk_elements, elements - ordinal * chunk_elements)
            directory = self.objects / chunk_hash[:2]
            # Do not allow a replaced shard directory to redirect object reads.
            try:
                directory_fd = _directory_fd(directory)
            except OSError as exc:
                raise ValueError(f"Invalid array object directory: {chunk_hash[:2]}") from exc
            try:
                chunk = _read_chunk(directory / f"{chunk_hash}.npz", chunk_hash, dtype.str, size)
            finally:
                os.close(directory_fd)
            buffers.append(chunk.data)
        value = FrozenArray(dtype_string, shape, b"".join(buffers))
        if value.content_hash != content_hash:
            raise ValueError("Complete array hash does not match manifest")
        return value

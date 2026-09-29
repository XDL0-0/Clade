"""Thread-scoped IEEE environment, isolated from legacy native-library FP modes.

Linux/glibc x86-64 is the currently verified runtime. A scope is synchronous:
never await or yield execution to another task while holding it. Nested scopes
reuse the outer boundary; independent simulation threads have independent state.
"""

from __future__ import annotations

import ctypes
import platform
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import lru_cache, wraps
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
T = TypeVar("T")
_thread = threading.local()


@lru_cache(maxsize=1)
def _libm() -> ctypes.CDLL:
    if (
        sys.platform != "linux"
        or platform.machine().lower() not in ("x86_64", "amd64")
        or platform.libc_ver()[0] != "glibc"
    ):
        raise RuntimeError("V2 floating-point isolation currently requires Linux/glibc x86-64")
    library = ctypes.CDLL("libm.so.6")
    for name in ("fegetenv", "fesetenv"):
        operation = getattr(library, name)
        operation.argtypes = [ctypes.c_void_p]
        operation.restype = ctypes.c_int
    return library


@contextmanager
def numeric_environment() -> Iterator[None]:
    """Use round-to-nearest with gradual underflow, then restore caller state.

    glibc bits/fenv.h defines FE_DFL_ENV as (fenv_t*) -1. The aligned oversized
    buffer holds the verified x86-64 fenv_t, including x87 and SSE/MXCSR state.
    No process-wide FP policy or NumPy seterr configuration is changed.
    """
    if getattr(_thread, "active", False):
        yield
        return
    library = _libm()
    saved = (ctypes.c_longdouble * 16)()
    if library.fegetenv(ctypes.byref(saved)) != 0:
        raise RuntimeError("Cannot capture the caller floating-point environment")
    if library.fesetenv(ctypes.c_void_p(-1)) != 0:
        library.fesetenv(ctypes.byref(saved))
        raise RuntimeError("Cannot establish the deterministic floating-point environment")
    _thread.active = True
    try:
        yield
    finally:
        _thread.active = False
        if library.fesetenv(ctypes.byref(saved)) != 0:
            raise RuntimeError("Cannot restore the caller floating-point environment")


def isolated_numerics(function: Callable[P, T]) -> Callable[P, T]:
    @wraps(function)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        with numeric_environment():
            return function(*args, **kwargs)

    return wrapped

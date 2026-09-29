# Backend test baseline

The dependency lock targets CPython 3.12 on Linux x86_64. Use that interpreter
for reproducing the baseline; other Python/platform combinations need their own
resolved lock and GPU validation. Production still declares Python >=3.11.

From `backend/`, create a virtual environment, then install the complete runtime
and development dependencies without re-resolving transitive versions:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-dev.lock
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .
.venv/bin/python -m pip check
```

Regenerate the lock with the locked pip-tools version after changing
`pyproject.toml`, and review the dependency diff:

```sh
.venv/bin/python -m piptools compile --extra dev --generate-hashes --allow-unsafe --strip-extras --output-file requirements-dev.lock pyproject.toml
```

The top-level `conftest.py` sets an isolated temporary database and data paths
before collection. It overrides an inherited `DATABASE_URL`; tests that need
their own database should use pytest's `tmp_path` and `monkeypatch`. Network
sockets are disabled by pytest-socket (Unix sockets remain available to async
event loops). Provider interactions must use local mocks, never real AI calls.

```sh
.venv/bin/python -m pytest --collect-only -q
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest -q -m 'not gpu'
.venv/bin/python -m pytest -q -m gpu
.venv/bin/python scripts/check_v2.py
```

GPU markers describe tests that initialize Taichi; they do not automatically
skip failures or certify a physical GPU. Full-suite runs include them. A GPU
runner must record the actual device/backend and driver, as Vulkan may be a
software device. NumPy/state/metrics tests remain unmarked and execute on the
reference test lane. The legacy `app.tensor` package still initializes Taichi
while importing `competition`, so selecting `-m 'not gpu'` alone does not yet
make legacy collection safe on a machine without a GPU. New pure v2 tests should
be collected by their own path until those legacy imports are decoupled.
Existing failures are reported in migration evidence until
their own repair batch; this setup does not mark them xfail.

Async tests use pytest-asyncio's auto mode and function-scoped event loops, so
module-level asyncio marks must not be applied to mixed sync/async modules.

The strict Ruff (including formatting) and mypy gate covers existing Python
files under `app/simulation/v2`, `app/ai/jobs`, `app/storage`, and matching
`tests/v2`, `tests/simulation/v2`, `tests/ai/jobs`, `tests/storage` directories. Tests inside
those application packages are covered too. Missing scopes are not treated as
checked code. Legacy code is not covered by this strict migration gate.

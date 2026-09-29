"""Lint and strictly type-check the migrated modules that exist so far."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
SCOPES = (
    "app/simulation/v2",
    "app/ai/jobs",
    "app/storage",
    "tests/v2",
    "tests/simulation/v2",
    "tests/ai/jobs",
    "tests/storage",
)


def main() -> int:
    targets = [scope for scope in SCOPES if any((BACKEND / scope).rglob("*.py"))]
    if not targets:
        print("No migrated v2/AI jobs/storage modules exist yet; gate has no targets.")
        return 0
    print("Strict migration gate: " + ", ".join(targets), flush=True)
    for command in (("ruff", "check"), ("ruff", "format", "--check"), ("mypy",)):
        result = subprocess.run([sys.executable, "-m", *command, *targets], cwd=BACKEND)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

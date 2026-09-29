"""Keep collection and test execution away from a user's live world.

Environment values must be installed before app modules are imported during
collection. Individual tests can still override settings using monkeypatch.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_workspace = tempfile.TemporaryDirectory(prefix="clade-pytest-")
_root = Path(_workspace.name)

os.environ["DATABASE_URL"] = f"sqlite:///{_root / 'tests.db'}"
os.environ["LOG_TO_FILE"] = "false"
for _variable, _directory in {
    "DATA_DIR": "data",
    "REPORTS_DIR": "reports",
    "EXPORTS_DIR": "exports",
    "SAVES_DIR": "saves",
    "CACHE_DIR": "cache",
    "LOG_DIR": "logs",
}.items():
    _path = _root / _directory
    _path.mkdir()
    os.environ[_variable] = str(_path)
os.environ["UI_CONFIG_PATH"] = str(_root / "settings.json")
os.environ["AI_BASE_URL"] = "http://test-ai.invalid"
os.environ["AI_API_KEY"] = "test-only"

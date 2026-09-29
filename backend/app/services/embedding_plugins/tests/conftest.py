"""Isolate plugin registry tests from the built-in plugin tests."""

from collections.abc import Iterator

import pytest

from .. import load_all_plugins
from ..registry import PluginRegistry


@pytest.fixture(autouse=True)
def isolated_plugin_registry(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # Module imports register built-ins once. A test calling clear() must not
    # erase those registrations for later tests whose imports are cached.
    load_all_plugins()
    monkeypatch.setattr(PluginRegistry, "_plugins", dict(PluginRegistry._plugins))
    monkeypatch.setattr(PluginRegistry, "_instances", {})
    monkeypatch.setattr(PluginRegistry, "_configs", {})
    yield

"""Safety contracts for running the backend suite without a live world."""

import socket
import tempfile
from pathlib import Path

import pytest
from pytest_socket import SocketBlockedError

from app.core.config import PROJECT_ROOT, get_settings
from app.core.database import engine


def test_database_and_data_directories_are_outside_the_repository() -> None:
    settings = get_settings()
    paths = [
        Path(str(engine.url.database)),
        Path(settings.data_dir),
        Path(settings.saves_dir),
        Path(settings.reports_dir),
        Path(settings.exports_dir),
        Path(settings.cache_dir),
        Path(settings.log_dir),
        Path(settings.ui_config_path),
    ]
    for path in paths:
        assert path.is_relative_to(tempfile.gettempdir())
        assert not path.is_relative_to(PROJECT_ROOT)
    assert not settings.log_to_file


def test_external_network_sockets_are_blocked() -> None:
    with pytest.raises(SocketBlockedError):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)

"""Opt-in, explicitly constructed v2 HTTP boundary."""

from .app import create_app
from .service import SimulationService

__all__ = ["SimulationService", "create_app"]

"""Explicitly versioned CPU reference models, independent of legacy algorithms.

These models define new behavior. The legacy tensor implementations remain
available as historical oracles; numerical or topology parity is not implied.
"""

from .topology import TOPOLOGY_VERSION, connected_components, neighbor_graph

__all__ = ["TOPOLOGY_VERSION", "connected_components", "neighbor_graph"]

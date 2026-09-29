"""Pure hex topology for the explicitly versioned CPU reference model.

Tiles use the renderer's odd-q layout: odd x columns sit half a row lower than
even x columns. The flat index is ``y * width + x``. North and south are closed;
east and west wrap by default. This is new model behavior, not a parity claim
with the legacy tensor model's four-neighbor grid.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

TOPOLOGY_VERSION: Final = "odd-q-cylinder-v1"


def neighbor_graph(width: int, height: int, *, wrap_x: bool = True) -> tuple[tuple[int, ...], ...]:
    """Return immutable, sorted, distinct neighbors with no self links.

    A wrapped odd-q grid needs an even width of at least two: otherwise the
    column parity does not match across the seam. An unwrapped grid permits
    any positive width. Height is always positive. Dimensions must be Python
    integers (not bools), and ``wrap_x`` must be a Python bool.
    """
    if type(width) is not int or type(height) is not int:
        raise TypeError("width and height must be integers, not bools")
    if type(wrap_x) is not bool:
        raise TypeError("wrap_x must be a bool")
    if width < 1 or height < 1:
        raise ValueError("width and height must be positive")
    if wrap_x and (width < 2 or width % 2):
        raise ValueError("wrapped odd-q topology requires an even width of at least 2")

    graph: list[tuple[int, ...]] = []
    for y in range(height):
        for x in range(width):
            # An odd column's diagonals descend; an even column's ascend.
            diagonal_y = 1 if x % 2 else -1
            offsets = (
                (0, -1),
                (0, 1),
                (-1, 0),
                (1, 0),
                (-1, diagonal_y),
                (1, diagonal_y),
            )
            neighbors: set[int] = set()
            for dx, dy in offsets:
                nx, ny = x + dx, y + dy
                if ny < 0 or ny >= height:
                    continue
                if wrap_x:
                    nx %= width
                elif nx < 0 or nx >= width:
                    continue
                target = ny * width + nx
                if target != y * width + x:
                    neighbors.add(target)
            graph.append(tuple(sorted(neighbors)))
    return tuple(graph)


def connected_components(
    active: Sequence[bool], neighbors: Sequence[Sequence[int]]
) -> tuple[tuple[int, ...], ...]:
    """Return the components induced by a strict bool mask on an undirected graph.

    Every input vertex and neighbor index is validated, including inactive
    vertices. Neighbor indices must be Python integers, excluding bools.
    Nonreciprocal edges are rejected. Duplicate entries and self links are
    harmless and permitted. Components and their members are sorted by index,
    independent of adjacency ordering. Inputs are neither mutated nor retained.
    Empty matching inputs produce no components.
    """
    size = len(active)
    if len(neighbors) != size:
        raise ValueError("active and neighbors must have the same length")
    if any(type(value) is not bool for value in active):
        raise TypeError("active values must be bools")

    graph: list[frozenset[int]] = []
    for adjacency in neighbors:
        for index in adjacency:
            if type(index) is not int:
                raise TypeError("neighbor indices must be integers, not bools")
            if index < 0 or index >= size:
                raise ValueError("neighbor index is outside the graph")
        graph.append(frozenset(adjacency))
    for source, adjacent_indices in enumerate(graph):
        if any(source not in graph[target] for target in adjacent_indices):
            raise ValueError("neighbors must describe an undirected, reciprocal graph")

    visited: set[int] = set()
    components: list[tuple[int, ...]] = []
    for start in range(size):
        if not active[start] or start in visited:
            continue
        visited.add(start)
        pending = [start]
        component: list[int] = []
        while pending:
            current = pending.pop()
            component.append(current)
            for target in graph[current]:
                if active[target] and target not in visited:
                    visited.add(target)
                    pending.append(target)
        components.append(tuple(sorted(component)))
    return tuple(components)

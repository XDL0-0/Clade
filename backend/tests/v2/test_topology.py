"""Hand-derived geometry and independent property checks for the new topology."""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

import pytest
from hypothesis import given
from hypothesis import strategies as st
from hypothesis.strategies import DrawFn

from app.simulation.v2.reference import (
    TOPOLOGY_VERSION,
    connected_components,
    neighbor_graph,
)


@st.composite
def _grids(draw: DrawFn) -> tuple[int, int, bool]:
    wrap_x = draw(st.booleans())
    width = draw(st.integers(1, 8)) * 2 if wrap_x else draw(st.integers(1, 16))
    return width, draw(st.integers(1, 12)), wrap_x


@st.composite
def _masked_grids(draw: DrawFn) -> tuple[int, int, bool, list[bool]]:
    width, height, wrap_x = draw(_grids())
    active = draw(st.lists(st.booleans(), min_size=width * height, max_size=width * height))
    return width, height, wrap_x, active


def _path_exists(
    start: int, end: int, active: Sequence[bool], graph: Sequence[Sequence[int]]
) -> bool:
    """Independent set-expansion oracle for induced-graph reachability."""
    reachable = {start}
    while True:
        expanded = reachable | {
            target for source in reachable for target in graph[source] if active[target]
        }
        if expanded == reachable:
            return end in reachable
        reachable = expanded


def test_version_identifies_new_hex_model() -> None:
    assert TOPOLOGY_VERSION == "odd-q-cylinder-v1"


def test_hand_derived_even_and_odd_columns_match_downward_odd_offset() -> None:
    graph = neighbor_graph(6, 4, wrap_x=False)
    # (2, 1): N, S, W, E, NW, NE. (3, 1): N, S, W, E, SW, SE.
    assert graph[8] == (1, 2, 3, 7, 9, 14)
    assert graph[9] == (3, 8, 10, 14, 15, 16)
    assert len(graph[8]) == len(graph[9]) == 6


def test_east_west_seam_preserves_parity_and_reciprocity() -> None:
    graph = neighbor_graph(6, 3)
    assert graph[6] == (0, 1, 5, 7, 11, 12)
    assert graph[11] == (5, 6, 10, 12, 16, 17)
    assert 11 in graph[6] and 6 in graph[11]
    assert 5 in graph[6] and 6 in graph[5]
    assert 12 in graph[11] and 11 in graph[12]
    unwrapped = neighbor_graph(6, 3, wrap_x=False)
    assert 11 not in unwrapped[6] and 5 not in unwrapped[6]
    assert 6 not in unwrapped[11] and 12 not in unwrapped[11]


def test_polar_boundaries_are_closed() -> None:
    graph = neighbor_graph(6, 3)
    assert graph[0] == (1, 5, 6)
    assert graph[1] == (0, 2, 6, 7, 8)
    assert graph[12] == (6, 7, 11, 13, 17)
    assert graph[13] == (7, 12, 14)
    assert all(target < 12 for target in graph[0])
    assert all(target >= 6 for target in graph[13])


def test_two_column_cylinder_deduplicates_collapsed_east_and_west() -> None:
    assert neighbor_graph(2, 3) == (
        (1, 2),
        (0, 2, 3),
        (0, 1, 3, 4),
        (1, 2, 4, 5),
        (2, 3, 5),
        (3, 4),
    )
    assert neighbor_graph(2, 1) == ((1,), (0,))


def test_unwrapped_single_column_and_single_tile_are_supported() -> None:
    assert neighbor_graph(1, 1, wrap_x=False) == ((),)
    assert neighbor_graph(1, 3, wrap_x=False) == ((1,), (0, 2), (1,))


@pytest.mark.parametrize("width", [1, 3, 5, 11])
def test_wrapping_rejects_odd_width(width: int) -> None:
    with pytest.raises(ValueError, match="even width"):
        neighbor_graph(width, 3)


@pytest.mark.parametrize("dimension", [True, False, 2.0, "2", float("nan"), None])
def test_dimensions_reject_non_integer_types(dimension: object) -> None:
    with pytest.raises(TypeError):
        neighbor_graph(cast(int, dimension), 3)
    with pytest.raises(TypeError):
        neighbor_graph(2, cast(int, dimension))


@pytest.mark.parametrize("dimension", [0, -1, -100])
def test_dimensions_reject_nonpositive_values(dimension: int) -> None:
    with pytest.raises(ValueError):
        neighbor_graph(dimension, 3, wrap_x=False)
    with pytest.raises(ValueError):
        neighbor_graph(2, dimension)


@pytest.mark.parametrize("wrap_x", [0, 1, None, "true", float("nan")])
def test_wrap_flag_requires_bool(wrap_x: object) -> None:
    with pytest.raises(TypeError):
        neighbor_graph(2, 3, wrap_x=cast(bool, wrap_x))


def test_components_respect_inactive_barriers_and_isolated_vertices() -> None:
    graph = neighbor_graph(1, 5, wrap_x=False)
    assert connected_components([True, True, False, True, True], graph) == ((0, 1), (3, 4))
    assert connected_components([True, False, True, False, True], graph) == ((0,), (2,), (4,))
    assert connected_components([False] * 5, graph) == ()
    assert connected_components([], []) == ()


def test_components_allow_repeated_neighbors_and_self_links() -> None:
    assert connected_components([True, True, True], [[1, 0, 1], [0], [2]]) == ((0, 1), (2,))


@pytest.mark.parametrize("active, graph", [([True], []), ([], [()]), ([True], [(), ()])])
def test_components_reject_length_mismatch(
    active: list[bool], graph: list[tuple[int, ...]]
) -> None:
    with pytest.raises(ValueError, match="same length"):
        connected_components(active, graph)


@pytest.mark.parametrize("invalid", [0, 1, -1, None, "", float("nan"), float("inf")])
def test_components_reject_non_bool_mask(invalid: object) -> None:
    with pytest.raises(TypeError, match="bools"):
        connected_components([False, cast(bool, invalid)], [(1,), (0,)])


@pytest.mark.parametrize("invalid", [True, False, 1.0, "1", None, float("nan"), float("inf")])
def test_components_reject_non_integer_indices_even_in_inactive_rows(invalid: object) -> None:
    with pytest.raises(TypeError, match="integers"):
        connected_components([False], [[cast(int, invalid)]])


@pytest.mark.parametrize("invalid", [-1, 1, 100])
def test_components_reject_out_of_range_indices(invalid: int) -> None:
    with pytest.raises(ValueError, match="outside"):
        connected_components([False], [[invalid]])


def test_components_reject_nonreciprocal_graph() -> None:
    with pytest.raises(ValueError, match="reciprocal"):
        connected_components([True, True], [[], [0]])


@given(_grids())
def test_neighbor_graph_invariants(grid: tuple[int, int, bool]) -> None:
    width, height, wrap_x = grid
    graph = neighbor_graph(width, height, wrap_x=wrap_x)
    assert type(graph) is tuple and len(graph) == width * height
    assert graph == neighbor_graph(width, height, wrap_x=wrap_x)
    for source, adjacency in enumerate(graph):
        assert type(adjacency) is tuple
        assert adjacency == tuple(sorted(set(adjacency)))
        assert source not in adjacency and len(adjacency) <= 6
        for target in adjacency:
            assert 0 <= target < width * height
            assert source in graph[target]
            assert abs(source // width - target // width) <= 1


@given(_masked_grids())
def test_components_partition_active_vertices_and_are_maximal(
    sample: tuple[int, int, bool, list[bool]],
) -> None:
    width, height, wrap_x, active = sample
    graph = neighbor_graph(width, height, wrap_x=wrap_x)
    components = connected_components(active, graph)
    assert type(components) is tuple
    assert components == tuple(sorted(components))
    flattened = [vertex for component in components for vertex in component]
    assert len(flattened) == len(set(flattened))
    assert sorted(flattened) == [vertex for vertex, enabled in enumerate(active) if enabled]
    membership = {vertex: i for i, component in enumerate(components) for vertex in component}
    for component in components:
        assert type(component) is tuple and component == tuple(sorted(component))
        for vertex in component:
            assert _path_exists(component[0], vertex, active, graph)
            for target in graph[vertex]:
                if active[target]:
                    assert membership[vertex] == membership[target]


@given(_masked_grids())
def test_components_do_not_mutate_inputs_and_ignore_neighbor_order(
    sample: tuple[int, int, bool, list[bool]],
) -> None:
    width, height, wrap_x, active = sample
    graph = neighbor_graph(width, height, wrap_x=wrap_x)
    mutable_graph = [list(reversed(adjacency)) * 2 for adjacency in graph]
    original_active = active[:]
    original_graph = [adjacency[:] for adjacency in mutable_graph]
    expected = connected_components(active, graph)
    assert connected_components(active, mutable_graph) == expected
    assert connected_components(active, mutable_graph) == expected
    assert active == original_active and mutable_graph == original_graph
    active[:] = [False] * len(active)
    for adjacency in mutable_graph:
        adjacency.clear()
    assert expected == connected_components(original_active, graph)

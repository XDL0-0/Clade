"""Compact committed observations and explicitly descriptive branch comparisons."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from ..context import WorldSnapshot
from ..values import JsonValue, freeze_mapping
from ..version import WorldVersion
from .distribution import trait_distribution


@dataclass(frozen=True)
class BranchResult:
    branch_id: str
    timeline_id: str
    status: Literal["completed", "failed", "conflict"]
    completed_turns: int
    target_turns: int
    version: WorldVersion | None
    observed_head_version: WorldVersion | None
    turn: int | None
    state_hash: str | None
    observations: tuple[Mapping[str, JsonValue], ...]
    summary: Mapping[str, JsonValue]
    error: Mapping[str, JsonValue] | None = None

    def to_dict(self) -> Mapping[str, JsonValue]:
        return freeze_mapping(
            {
                "branch_id": self.branch_id,
                "timeline_id": self.timeline_id,
                "status": self.status,
                "completed_turns": self.completed_turns,
                "target_turns": self.target_turns,
                "version": self.version.to_dict() if self.version else None,
                "observed_head_version": (
                    self.observed_head_version.to_dict() if self.observed_head_version else None
                ),
                "turn": self.turn,
                "state_hash": self.state_hash,
                "observations": self.observations,
                "summary": self.summary,
                "error": self.error,
            }
        )


@dataclass(frozen=True)
class ExperimentResult:
    manifest_hash: str
    manifest: Mapping[str, JsonValue]
    branches: tuple[BranchResult, ...]
    comparisons: Mapping[str, JsonValue]

    @property
    def completed(self) -> bool:
        return all(branch.status == "completed" for branch in self.branches)

    def to_dict(self) -> Mapping[str, JsonValue]:
        return freeze_mapping(
            {
                "manifest_hash": self.manifest_hash,
                "manifest": self.manifest,
                "completed": self.completed,
                "branches": tuple(b.to_dict() for b in self.branches),
                "comparisons": self.comparisons,
                "interpretation": "descriptive paired outcomes; not proof of emergent diversity",
            }
        )


def summarize(
    snapshot: WorldSnapshot, observations: tuple[Mapping[str, JsonValue], ...]
) -> Mapping[str, JsonValue]:
    summary: dict[str, JsonValue] = {
        "metrics": observations[-1]["metrics"] if observations else {},
        "metrics_version": snapshot.version.to_dict() if observations else None,
    }
    population = snapshot.arrays.get("population")
    if population is None or len(population.shape) != 2:
        return freeze_mapping(summary)
    counts = population.numpy()
    roles: dict[str, dict[str, JsonValue]] = {}
    traits: dict[str, list[tuple[float, int]]] = {}
    alive: set[str] = set()
    for identity, item in snapshot.domain("species").items():
        if not isinstance(item, Mapping):
            continue
        slot, role, mass = item.get("slot"), item.get("role"), item.get("body_mass")
        if type(slot) is not int or not 0 <= slot < counts.shape[0] or not isinstance(role, str):
            continue
        count = int(counts[slot].sum(dtype=object))
        if not count or type(mass) not in (int, float):
            continue
        assert isinstance(mass, (int, float))
        alive.add(identity)
        record = roles.setdefault(role, {"population": 0, "structural_biomass": 0.0, "species": 0})
        for key, value in (
            ("population", count),
            ("structural_biomass", count * mass),
            ("species", 1),
        ):
            old = record[key]
            assert isinstance(old, (int, float))
            record[key] = old + value
        values = item.get("traits", {})
        if isinstance(values, Mapping):
            for key, trait in values.items():
                if type(trait) in (int, float):
                    assert isinstance(trait, (int, float))
                    traits.setdefault(key, []).append((float(trait), count))
    summary["population_by_role"] = freeze_mapping(roles)
    summary["population_weighted_traits"] = {
        key: math.fsum(value * count for value, count in entries) / sum(n for _, n in entries)
        for key, entries in sorted(traits.items())
    }
    summary["trait_distribution"] = trait_distribution(snapshot)
    edges = snapshot.domain("food_web").get("edges", ())
    if isinstance(edges, tuple):
        summary["active_food_web_edges"] = sum(
            isinstance(edge, Mapping)
            and edge.get("predator") in alive
            and edge.get("prey") in alive
            for edge in edges
        )
    return freeze_mapping(summary)


def _numbers(value: JsonValue, path: str = "") -> dict[str, float | int]:
    if type(value) in (float, int):
        assert isinstance(value, (float, int))
        return {path: value}
    if isinstance(value, Mapping):
        result: dict[str, float | int] = {}
        for key, child in value.items():
            if key != "metrics_version":
                result.update(_numbers(child, f"{path}.{key}" if path else key))
        return result
    return {}


def comparisons(branches: tuple[BranchResult, ...], control_id: str) -> Mapping[str, JsonValue]:
    control = next(branch for branch in branches if branch.branch_id == control_id)
    output: dict[str, JsonValue] = {}
    for branch in branches:
        if branch.branch_id == control_id:
            continue
        if control.status != "completed" or branch.status != "completed":
            output[branch.branch_id] = {"available": False, "reason": "both branches must complete"}
            continue
        baseline, changed = _numbers(control.summary), _numbers(branch.summary)
        output[branch.branch_id] = {
            "available": True,
            "control": control_id,
            "relative_turn": branch.completed_turns,
            "control_version": control.version.to_dict() if control.version else None,
            "branch_version": branch.version.to_dict() if branch.version else None,
            "delta": {
                key: changed[key] - baseline[key]
                for key in sorted(changed.keys() & baseline.keys())
            },
        }
    return freeze_mapping(output)

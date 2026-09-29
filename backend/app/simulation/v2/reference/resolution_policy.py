"""Opt-in deterministic evolution scheduling; ecological ledgers remain exact.

State lives in evolution.resolution, not an uncheckpointed cache. The optional
evolution.resolution_control accepts mode=adaptive/critical and focus species IDs.
Missing scheduler state bootstraps every lineage at Critical for one full update.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

import numpy as np

from ..context import TurnContext
from ..contracts import SimulationStage, StageContract, StageResult
from ..values import JsonValue, digest
from .common import number, result, state_patch
from .ecology import Species
from .evolution_contracts import (
    READS,
    EvolutionInputs,
    bottleneck_mask,
    evolution_inputs,
    founder_mask,
)
from .extinction import STATUSES
from .world import TRAITS

TIERS = ("Background", "Focus", "Critical")
HYSTERESIS = 3
BACKGROUND_INTERVAL = 4
MAX_ELAPSED = 1.0
POLICY_READS = (*READS, "state.evolution", "state.food_web")


def integer(value: JsonValue, name: str, high: int) -> int:
    if type(value) is not int or not 0 <= value <= high:
        raise ValueError(f"Invalid resolution {name}")
    return value


@dataclass(frozen=True)
class Decision:
    tier: str
    quiet: int
    last_update: int
    elapsed: float
    recent_until: int
    previous_traits: tuple[float, ...]
    last_population: int
    due: bool
    dt: float
    reasons: tuple[str, ...]
    discarded_years: float = 0.0

    def to_json(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], dict(self.__dict__))

    @classmethod
    def parse(cls, value: JsonValue, turn: int) -> Decision:
        if not isinstance(value, Mapping):
            raise ValueError("Resolution lineage record must be a mapping")
        tier, traits, reasons, due = (
            value.get("tier"),
            value.get("previous_traits"),
            value.get("reasons"),
            value.get("due"),
        )
        if tier not in TIERS or not isinstance(tier, str) or type(due) is not bool:
            raise ValueError("Invalid resolution tier or update flag")
        if not isinstance(traits, tuple) or len(traits) != len(TRAITS):
            raise ValueError("Resolution trait history requires seven coordinates")
        prior = tuple(number(v, "resolution trait history") for v in traits)
        if any(not 0 <= v <= 1 for v in prior) or sum(prior) > 3 + 1e-12:
            raise ValueError("Invalid resolution trait history")
        if not isinstance(reasons, tuple) or any(not isinstance(r, str) for r in reasons):
            raise ValueError("Invalid resolution reason list")
        elapsed = number(value.get("elapsed"), "resolution elapsed time")
        dt = number(value.get("dt"), "resolution effective time")
        discarded = number(value.get("discarded_years", 0), "resolution discarded time")
        if not 0 <= elapsed <= MAX_ELAPSED or not 0 <= dt <= MAX_ELAPSED or discarded < 0:
            raise ValueError("Invalid resolution integration interval")
        if due != (dt > 0):
            raise ValueError("Resolution update and time disagree")
        return cls(
            tier,
            integer(value.get("quiet"), "hysteresis", HYSTERESIS - 1),
            integer(value.get("last_update"), "last update", turn),
            elapsed,
            integer(value.get("recent_until"), "recent evolution", turn + BACKGROUND_INTERVAL),
            prior,
            integer(value.get("last_population"), "population", 2**127),
            due,
            dt,
            cast(tuple[str, ...], reasons),
            discarded,
        )


def source_hash(context: TurnContext) -> str:
    return digest(
        {
            "version": context.world_version.to_dict(),
            "turn": context.turn_id,
            "seed": context.seed,
            "rng": context.rng_namespace,
            "dt": context.environment_state["ecological_years_per_turn"],
            "species": context.species_state,
            "control": context.snapshot.domain("evolution").get("resolution_control", {}),
            "arrays": {
                p: context.snapshot.arrays[p[7:]].content_hash
                for p in READS
                if p.startswith("arrays.")
            },
        }
    )


def read_schedule(context: TurnContext) -> tuple[Mapping[str, JsonValue], dict[str, Decision]]:
    raw = context.snapshot.domain("evolution").get("resolution")
    if (
        not isinstance(raw, Mapping)
        or raw.get("schema") != 1
        or raw.get("turn") != context.turn_id
        or raw.get("complete") is not False
        or raw.get("source_hash") != source_hash(context)
    ):
        raise ValueError("Missing, completed or stale resolution schedule")
    records = raw.get("records")
    if (
        not isinstance(records, Mapping)
        or set(records) != set(context.species_state)
        or raw.get("plan_hash") != digest(records)
    ):
        raise ValueError("Resolution schedule does not cover the current lineages")
    decisions = {
        identity: Decision.parse(value, context.turn_id) for identity, value in records.items()
    }
    if any(
        not d.due
        and (
            d.tier != "Background"
            or d.elapsed >= MAX_ELAPSED
            or context.turn_id - d.last_update >= BACKGROUND_INTERVAL
        )
        for d in decisions.values()
    ):
        raise ValueError("Resolution cannot skip a required update")
    return raw, decisions


def _importance(context: TurnContext, data: EvolutionInputs) -> set[str]:
    alive = {s.identity for s in data.species if np.any(data.population[s.slot])}
    neighbors: dict[str, set[str]] = {s: set() for s in alive}
    diets: dict[str, set[str]] = {}
    edges = context.food_web_state.get("edges", ())
    if not isinstance(edges, tuple):
        raise ValueError("Resolution food web must have edge tuples")
    for raw in edges:
        if not isinstance(raw, Mapping):
            raise ValueError("Invalid resolution food edge")
        pred, prey = raw.get("predator"), raw.get("prey")
        if (
            not isinstance(pred, str)
            or not isinstance(prey, str)
            or pred not in context.species_state
            or prey not in context.species_state
        ):
            raise ValueError("Resolution food edge has an unknown lineage")
        preference = number(raw.get("preference", 1), "food preference")
        if preference < 0:
            raise ValueError("Negative resolution food preference")
        if pred in alive and prey in alive and preference > 0:
            neighbors[pred].add(prey)
            neighbors[prey].add(pred)
            diets.setdefault(pred, set()).add(prey)
    return {s for s, links in neighbors.items() if len(links) >= 2} | {
        prey for diet in diets.values() if len(diet) == 1 for prey in diet
    }


def _decision(
    context: TurnContext,
    data: EvolutionInputs,
    item: Species,
    previous: Decision | None,
    *,
    forced: bool,
    focus: set[str],
    important: set[str],
) -> Decision:
    row = item.slot
    occupied = data.population[row] > 0
    total = int(data.population[row].sum(dtype=object))
    metadata = context.species_state[item.identity]
    assert isinstance(metadata, Mapping)
    if metadata.get("status") not in STATUSES:
        raise ValueError("Invalid lifecycle status in resolution policy")
    traits = tuple(item.traits[name] for name in TRAITS)
    recent = previous.recent_until if previous else 0
    if (
        previous
        and max(abs(a - b) for a, b in zip(traits, previous.previous_traits, strict=True)) >= 0.01
    ):
        recent = context.turn_id + BACKGROUND_INTERVAL
    critical: list[str] = []
    if forced:
        critical.append("all_critical_control")
    if previous is None:
        critical.append("scheduler_initialization")
    if total <= 20 or (np.any(occupied) and not np.any(data.population[row] >= 2)):
        critical.append("small_or_nonbreeding_population")
    if metadata["status"] != "Healthy":
        critical.append("lifecycle_risk")
    if np.any(founder_mask(data, row)):
        critical.append("new_colonization")
    if np.any(bottleneck_mask(data, row)):
        critical.append("local_bottleneck")
    if previous and total * 10 <= previous.last_population * 9 and total < previous.last_population:
        critical.append("population_drop_at_least_ten_percent")
    if np.max(data.pressure[row]) >= 0.6:
        critical.append("current_selection_crisis")
    if np.any(
        occupied
        & (
            (data.connectivity[row] < 0)
            | (data.connectivity[row] != data.previous_connectivity[row])
        )
    ):
        critical.append("changed_or_blocked_connectivity")
    focused = []
    if item.identity in focus:
        focused.append("player_focus")
    if item.identity in important:
        focused.append("food_web_importance")
    if total >= 1000:
        focused.append("large_population")
    if context.turn_id <= recent:
        focused.append("recent_evolution")
    created = integer(metadata.get("created_turn", 0), "creation turn", context.turn_id)
    if context.turn_id - created <= BACKGROUND_INTERVAL:
        focused.append("recent_lineage")
    desired = "Critical" if critical else "Focus" if focused else "Background"
    reasons = critical or focused or ["stable_low_priority"]
    tier, quiet = desired, 0
    if previous and TIERS.index(desired) < TIERS.index(previous.tier):
        quiet = previous.quiet + 1
        tier = previous.tier
        if quiet >= HYSTERESIS:
            tier, quiet = TIERS[TIERS.index(previous.tier) - 1], 0
            reasons.append("three_quiet_turns_downgrade")
        else:
            reasons.append("downgrade_hysteresis")
    elapsed = (previous.elapsed if previous else 0.0) + data.dt
    last = previous.last_update if previous else context.turn_id - 1
    due = (
        tier != "Background"
        or context.turn_id - last >= BACKGROUND_INTERVAL
        or elapsed >= MAX_ELAPSED
    )
    if due and tier == "Background":
        reasons.append("background_interval_due")
    # Critical always uses the current interval and original fine stochastic law.
    # Do not apply old coarse backlog under a newly observed crisis gradient.
    effective = data.dt if tier == "Critical" else min(MAX_ELAPSED, elapsed)
    return Decision(
        tier,
        quiet,
        last,
        min(MAX_ELAPSED, elapsed),
        recent,
        traits,
        total,
        due,
        effective if due else 0.0,
        tuple(reasons),
        max(0.0, elapsed - effective) if due else 0.0,
    )


class ResolutionPolicyStage(SimulationStage):
    contract = StageContract(
        "reference_resolution",
        "1",
        ("reference_fitness",),
        reads=POLICY_READS,
        writes=("state.evolution",),
    )

    def execute(self, context: TurnContext) -> StageResult:
        self.validate_inputs(context)
        data = evolution_inputs(context)
        domain = context.snapshot.domain("evolution")
        control = domain.get("resolution_control", {})
        if not isinstance(control, Mapping) or set(control) - {"mode", "focus"}:
            raise ValueError("Invalid resolution control")
        mode, focus = control.get("mode", "adaptive"), control.get("focus", ())
        if (
            mode not in ("adaptive", "critical")
            or not isinstance(focus, tuple)
            or any(not isinstance(s, str) or s not in context.species_state for s in focus)
        ):
            raise ValueError("Invalid resolution mode or player focus")
        prior = domain.get("resolution")
        history: Mapping[str, JsonValue] = {}
        if prior is not None:
            if (
                not isinstance(prior, Mapping)
                or prior.get("schema") != 1
                or prior.get("complete") is not True
                or prior.get("turn") != context.turn_id - 1
            ):
                raise ValueError("Resolution history must be from the completed previous turn")
            raw = prior.get("records")
            if (
                not isinstance(raw, Mapping)
                or set(raw) - set(context.species_state)
                or prior.get("plan_hash") != digest(raw)
            ):
                raise ValueError("Invalid resolution lineage history")
            history = raw
        important = _importance(context, data)
        records: dict[str, JsonValue] = {}
        totals = dict.fromkeys(TIERS, 0)
        for item in data.species:
            previous = (
                Decision.parse(history[item.identity], context.turn_id - 1)
                if item.identity in history
                else None
            )
            decision = _decision(
                context,
                data,
                item,
                previous,
                forced=mode == "critical",
                focus=set(cast(tuple[str, ...], focus)),
                important=important,
            )
            totals[decision.tier] += 1
            records[item.identity] = decision.to_json()
        schedule: dict[str, JsonValue] = {
            "schema": 1,
            "turn": context.turn_id,
            "complete": False,
            "source_hash": source_hash(context),
            "records": records,
            "plan_hash": digest(records),
        }
        return result(
            context,
            self.contract.name,
            state=(state_patch(context, ("evolution",), {**domain, "resolution": schedule}),),
            metrics={
                **totals,
                "background_interval": BACKGROUND_INTERVAL,
                "downgrade_quiet_turns": HYSTERESIS,
                "maximum_effective_years": MAX_ELAPSED,
            },
        )

"""Pure, bounded narrative planning from successful reference evolution events.

At most four jobs per turn: committed splits first (score, child population),
then adaptations (largest global trait change, absolute proxy gain). Stable IDs
break ties. Adaptations already covered by a selected split are suppressed.
Identical or conflicting duplicate event IDs/lineage facts are rejected, not
silently queued twice. Active events and uncommitted proposals are never inputs.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import cast

import numpy as np

from app.simulation.v2.context import TurnContext
from app.simulation.v2.events import WorldEvent
from app.simulation.v2.reference.ecology import MODEL_VERSION
from app.simulation.v2.reference.evolution_contracts import EvolutionTrace
from app.simulation.v2.reference.selection import PRESSURE_AXES
from app.simulation.v2.reference.world import TRAITS
from app.simulation.v2.values import JsonValue, digest

from .models import JobKind, JobSpec

PROMPT_VERSION = "reference-narrative-v1"
_CAUSES = (
    "geographic_isolation",
    "ecological_divergence",
    "genetic_distance",
    "isolation_duration",
    "low_gene_flow",
)


def _number(value: JsonValue, label: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or not low <= result <= high:
        raise ValueError(f"{label} must be within its declared range")
    return result


def _count(value: JsonValue, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} must be a nonnegative integer")
    return value


def _identifier(value: JsonValue, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 128:
        raise ValueError(f"{label} must be a bounded, nonempty identifier")
    return value


def _numbers(
    value: JsonValue,
    label: str,
    *,
    axes: tuple[str, ...] | None = None,
    low: float = -math.inf,
    high: float = math.inf,
) -> dict[str, float]:
    if not isinstance(value, Mapping) or len(value) > 16:
        raise ValueError(f"{label} must be a bounded numerical record")
    if axes is not None and set(value) != set(axes):
        raise ValueError(f"{label} has invalid numerical axes")
    return {_identifier(key, label): _number(item, label, low, high) for key, item in value.items()}


def _species(context: TurnContext, identity: str) -> Mapping[str, JsonValue]:
    value = context.species_state.get(identity)
    if not isinstance(value, Mapping):
        raise ValueError("Narrative target must exist in the candidate species state")
    return value


def _population(context: TurnContext, identity: str) -> int:
    metadata = _species(context, identity)
    slot = _count(metadata.get("slot"), "species slot")
    array = context.snapshot.arrays["population"].numpy()
    if array.dtype != np.dtype("int64") or array.ndim != 2 or slot >= array.shape[0]:
        raise ValueError("Narrative target needs a valid integer population row")
    row = np.asarray(array[slot], dtype=np.int64)
    if np.any(row < 0):
        raise ValueError("Narrative population cannot be negative")
    return int(row.sum(dtype=object))


@dataclass(frozen=True)
class _Fact:
    event: WorldEvent
    kind: JobKind
    targets: tuple[str, ...]
    proposal: str
    importance: tuple[float, float]
    payload: Mapping[str, JsonValue]


def _split(context: TurnContext, event: WorldEvent) -> _Fact:
    payload = event.payload
    parent = _identifier(payload.get("parent"), "parent")
    child = _identifier(payload.get("child_id"), "child")
    proposal = _identifier(payload.get("proposal_id"), "proposal")
    parent_state, child_state = _species(context, parent), _species(context, child)
    descendants = parent_state.get("descendants", ())
    if (
        parent == child
        or event.actor != parent
        or event.target != child
        or child_state.get("ancestor") != parent
        or child_state.get("created_turn") != context.turn_id
        or not isinstance(descendants, tuple)
        or child not in descendants
        or parent_state.get("status") == "Extinct"
        or child_state.get("status") == "Extinct"
    ):
        raise ValueError("Speciation event must describe the retained parent and actual new child")
    counts = (
        _count(payload.get("remaining_population"), "remaining population"),
        _count(payload.get("population"), "child population"),
    )
    if min(counts) < 20 or counts != (_population(context, parent), _population(context, child)):
        raise ValueError("Speciation fact counts must match both living lineages")
    score = _number(payload.get("score"), "speciation score", 0.70, 1)
    causes = _numbers(payload.get("cause_terms"), "speciation causes", axes=_CAUSES, low=0, high=1)
    age = _count(payload.get("isolation_turns"), "isolation age")
    if age < 12 or age > context.turn_id:
        raise ValueError("Speciation isolation age must match the numerical evidence")
    facts: dict[str, JsonValue] = {
        "score": score,
        "cause_terms": cast(dict[str, JsonValue], causes),
        "isolation_turns": age,
        "component": _count(payload.get("component"), "component"),
        "genetic_rms_scale": _number(payload.get("genetic_rms_scale"), "genetic scale", 1e-12, 1),
        "lineages": (
            {"target_id": parent, "lineage_role": "retained_parent", "population": counts[0]},
            {"target_id": child, "lineage_role": "new_child", "population": counts[1]},
        ),
        "interpretation": "The existing parent identity survives; only the child is newly created.",
    }
    for key in ("parent_baseline_before", "parent_baseline_after"):
        if key in payload:
            facts[key] = _count(payload[key], key)
    return _Fact(event, "speciation", (parent, child), proposal, (score, float(counts[1])), facts)


def _adaptation(context: TurnContext, event: WorldEvent) -> _Fact:
    payload = event.payload
    target = _identifier(payload.get("species"), "adapted species")
    _species(context, target)
    if (
        event.actor != target
        or payload.get("turn") != context.turn_id
        or payload.get("model_version") != MODEL_VERSION
    ):
        raise ValueError(
            "Adaptation trace must identify its actual actor, turn and reference model"
        )
    changes = _numbers(payload.get("trait_changes"), "trait changes", axes=TRAITS, low=-1, high=1)
    pressure = _numbers(payload.get("pressure"), "pressure", axes=PRESSURE_AXES, low=0, high=1)
    gain = _number(payload.get("fitness_gain"), "fitness gain", -math.inf, math.inf)
    tradeoffs = _numbers(payload.get("tradeoffs"), "tradeoffs")
    reasons = _numbers(payload.get("reasons"), "reasons")
    trace = EvolutionTrace(
        target,
        context.turn_id,
        tuple(changes[name] for name in TRAITS),
        tuple(pressure[name] for name in PRESSURE_AXES),
        gain,
        tuple(sorted(tradeoffs.items())),
        tuple(sorted(reasons.items())),
        _number(payload.get("max_deme_change"), "deme change", 0, 1),
    )
    return _Fact(
        event,
        "adaptation",
        (target,),
        f"adaptation:{digest([event.event_id, event.version.to_dict()])}",
        (max(abs(value) for value in changes.values()), abs(gain)),
        trace.to_json(),
    )


@dataclass(frozen=True, slots=True)
class ReferenceNarrativePlanner:
    """No storage, provider, clock or global services; safe to replay before commit.

    ``adaptation_threshold`` is a global trait-mean change in [.01,1], default .02.
    ``max_jobs_per_turn`` is an integer in [0,4]; zero explicitly disables jobs.
    The injected provider configuration identity is frozen with every job.
    """

    adaptation_threshold: float = 0.02
    max_jobs_per_turn: int = 4
    provider_config_hash: str = "offline-template-v1"

    def __post_init__(self) -> None:
        _number(self.adaptation_threshold, "adaptation threshold", 0.01, 1)
        if type(self.max_jobs_per_turn) is not int or not 0 <= self.max_jobs_per_turn <= 4:
            raise ValueError("Narrative budget must be an integer from 0 to 4")
        _identifier(self.provider_config_hash, "provider configuration hash")

    def __call__(self, context: TurnContext) -> tuple[JobSpec, ...]:
        if context.errors or any(stage.errors for stage in context.stage_results):
            raise ValueError("Cannot plan narratives from a failed numerical candidate")
        if self.max_jobs_per_turn == 0:
            return ()
        facts: list[_Fact] = []
        identities: set[str] = set()
        outcomes: set[tuple[str, str]] = set()
        for stage in context.stage_results:
            for event in stage.events:
                if event.type not in ("SpeciesAdapted", "SpeciationOccurred"):
                    continue
                expected_stage = (
                    "reference_adaptation"
                    if event.type == "SpeciesAdapted"
                    else "reference_speciation"
                )
                if (
                    stage.stage_name != expected_stage
                    or event.turn != context.turn_id
                    or event.version != context.world_version.advance()
                ):
                    raise ValueError(
                        "Narrative facts must come from this turn's successful reference stages"
                    )
                if event.event_id in identities:
                    raise ValueError("Duplicate narrative source event identity")
                identities.add(event.event_id)
                fact = (
                    _adaptation(context, event)
                    if event.type == "SpeciesAdapted"
                    else _split(context, event)
                )
                outcome = fact.kind, fact.targets[0]
                if outcome in outcomes:
                    raise ValueError("Duplicate narrative outcome for one lineage in this turn")
                outcomes.add(outcome)
                if fact.kind == "speciation" or fact.importance[0] >= self.adaptation_threshold:
                    facts.append(fact)
        facts.sort(
            key=lambda fact: (
                fact.kind != "speciation",
                -fact.importance[0],
                -fact.importance[1],
                fact.targets,
                fact.event.event_id,
            )
        )
        chosen: list[_Fact] = []
        covered: set[str] = set()
        for fact in facts:
            if fact.kind == "adaptation" and fact.targets[0] in covered:
                continue
            chosen.append(fact)
            if fact.kind == "speciation":
                covered.update(fact.targets)
            if len(chosen) == self.max_jobs_per_turn:
                break
        if not chosen:
            return ()
        future = replace(
            context.snapshot, version=context.world_version.advance(), turn_id=context.turn_id
        )
        snapshot_id = future.snapshot_id
        policy: JsonValue = {
            "prompt_version": PROMPT_VERSION,
            "max_jobs_per_turn": self.max_jobs_per_turn,
            "adaptation_min_global_trait_change": self.adaptation_threshold,
            "priority": "successful speciation, then global trait change and absolute proxy gain",
        }
        return tuple(
            JobSpec(
                future.version,
                context.turn_id,
                fact.kind,
                fact.targets,
                (fact.event.event_id,),
                proposal_id=fact.proposal,
                snapshot_id=snapshot_id,
                prompt_version=PROMPT_VERSION,
                provider_config_hash=self.provider_config_hash,
                payload={
                    "policy": policy,
                    "event_type": fact.event.type,
                    "event_turn": fact.event.turn,
                    "facts": fact.payload,
                    "interpretation_only": (
                        "Describe frozen facts; do not create numerical effects "
                        "or change lineage identities."
                    ),
                },
            )
            for fact in chosen
        )

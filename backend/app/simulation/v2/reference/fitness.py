"""Counterfactual annual fitness *proxy* from real Holling parameters and costs.

This is not a derivative of true survival probability. Sparse diet edges are
summarized once using predator-weighted current tile overlap; finite differences
reuse the actual feeding functional response on these representative densities.
No trait is assigned a hand-authored pressure-to-benefit sign. In particular the
current movement equation has no speed term, so mobility contributes no speed
benefit here. Engineering has only its measured maintenance cost in this proxy.
Environmental and competition fields stay fixed: their trait-mediated changes
are deliberately excluded. All scores have units year^-1 after normalizing
assimilated structural carbon by current focal structural biomass; gradients
have units year^-1 per unit trait. They are unconstrained coordinate derivatives,
not trait-budget-projected adaptation directions.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, replace

import numpy as np

from ..context import TurnContext
from ..contracts import SimulationStage, StageContract, StageResult
from .common import FloatArray, result
from .ecology import MODEL_VERSION, Species, habitat_overlap
from .feeding import _functional_response
from .selection import READS, SelectionInputs, selection_inputs
from .world import TRAITS

EPSILON = 0.01
BASE_MAINTENANCE = 0.25
TRAIT_MAINTENANCE = 0.05


@dataclass(frozen=True)
class EdgeExposure:
    predator: Species
    prey: Species
    predator_density: int
    prey_density: int
    exposed_predators: float
    exposed_prey: float
    habitat: float
    preference: float


@dataclass(frozen=True)
class FitnessInputs:
    selection: SelectionInputs
    totals: FloatArray
    incident: Mapping[int, tuple[EdgeExposure, ...]]
    interaction_count: int
    defensive_groups: bool = False


def prepare_fitness(
    context: TurnContext, *, refuge: FloatArray | None = None, defensive_groups: bool = False
) -> FitnessInputs:
    """Build O(E*T) exposure summaries, with no all-species-pairs scan."""
    data = selection_inputs(context)
    if refuge is not None and (
        refuge.shape != (data.population.shape[1],)
        or not np.isfinite(refuge).all()
        or np.any((refuge < 0) | (refuge > 1))
    ):
        raise ValueError("Habitat refuge must match finite tile values in [0,1]")
    totals = data.population.sum(axis=1, dtype=np.float64)
    by_slot = {item.slot: item for item in data.species}
    incident: dict[int, list[EdgeExposure]] = {item.slot: [] for item in data.species}
    count = 0
    for predator_slot, prey_slot, preference in data.edges:
        predator, prey = by_slot[predator_slot], by_slot[prey_slot]
        if predator.role != "carnivore" or preference == 0:
            continue
        overlap = habitat_overlap(predator, prey) * np.minimum(
            data.arrays["suitability"][predator_slot],
            data.arrays["suitability"][prey_slot],
        )
        if refuge is not None:
            overlap *= 1 - 0.5 * refuge
        active = (
            (data.population[predator_slot] > 0) & (data.population[prey_slot] > 0) & (overlap > 0)
        )
        if not np.any(active):
            continue
        predators = data.population[predator_slot, active].astype(np.float64)
        prey_counts = data.population[prey_slot, active].astype(np.float64)
        exposed = float(predators.sum())
        edge = EdgeExposure(
            predator,
            prey,
            max(1, round(float((predators * predators).sum()) / exposed)),
            max(1, round(float((predators * prey_counts).sum()) / exposed)),
            exposed,
            float(prey_counts.sum()),
            float((predators * overlap[active]).sum()) / exposed,
            preference,
        )
        incident[predator_slot].append(edge)
        incident[prey_slot].append(edge)
        count += 1
    return FitnessInputs(
        data,
        totals,
        {slot: tuple(edges) for slot, edges in incident.items()},
        count,
        defensive_groups,
    )


def _validate_focal(data: FitnessInputs, focal: Species) -> None:
    if focal.slot not in data.incident or set(focal.traits) != set(TRAITS):
        raise ValueError("counterfactual species/trait axes mismatch")
    if any(not math.isfinite(value) or not 0 <= value <= 1 for value in focal.traits.values()):
        raise ValueError("counterfactual traits must be finite and lie in [0,1]")


def _trophic_terms(data: FitnessInputs, focal: Species) -> list[float]:
    """One annual per-capita contribution per incident, canonically ordered edge."""
    population = float(data.totals[focal.slot])
    terms: list[float] = []
    for edge in data.incident[focal.slot]:
        predator = focal if edge.predator.slot == focal.slot else edge.predator
        prey = focal if edge.prey.slot == focal.slot else edge.prey
        rate = edge.exposed_predators * _functional_response(
            predator,
            prey,
            edge.predator_density,
            edge.prey_density,
            edge.habitat,
            edge.preference,
            defensive_groups=data.defensive_groups,
        )
        if not math.isfinite(rate) or rate < 0:
            raise ValueError("counterfactual predation rate is not finite")
        # Respect each edge's current prey pool over the observed timestep. Shared
        # prey competition and integer rounding are intentionally outside this proxy.
        dt = data.selection.dt
        if dt > 0 and rate * dt > edge.exposed_prey:
            rate = edge.exposed_prey / dt
        if predator.slot == focal.slot:
            terms.append(0.7 * (rate / population) * (prey.mass / focal.mass))
        else:
            terms.append(-rate / population)
    if any(not math.isfinite(term) for term in terms):
        raise ValueError("counterfactual trophic contribution is not finite")
    return terms


def fitness_proxy(data: FitnessInputs, focal: Species) -> float:
    """Annual structural-carbon return minus predation hazard and maintenance.

    Perturbing a trait may temporarily exceed the total trait budget; projection
    belongs to adaptation. Every individual trait must still lie in [0,1].
    Other species, densities, geography and suitability remain fixed. Reserve
    energy, appetite allocation, starvation and birth rounding are not simulated.
    Observed selection pressure is diagnostic and adds no invented trait reward.
    A positive timestep caps each edge by its current prey pool; a zero timestep
    evaluates the instantaneous annual rate without a finite-horizon pool cap.
    """
    _validate_focal(data, focal)
    if data.totals[focal.slot] == 0:
        return 0.0
    score = (
        math.fsum(_trophic_terms(data, focal))
        - BASE_MAINTENANCE
        - TRAIT_MAINTENANCE * math.fsum(focal.traits[key] for key in TRAITS)
    )
    if not math.isfinite(score):
        raise ValueError("counterfactual fitness proxy is not finite")
    return float(score)


def finite_difference(data: FitnessInputs, focal: Species) -> FloatArray:
    """Clipped +/- .01 secants plus the exact linear maintenance derivative.

    Difference each trophic contribution before summation, so constant offsets
    cannot numerically erase a small derivative. At trait bounds this is a
    one-sided secant, and near bounds the clipped interval may be asymmetric.
    """
    _validate_focal(data, focal)
    gradient = np.zeros(len(TRAITS), dtype=np.float64)
    if data.totals[focal.slot] == 0:
        return gradient
    for index, name in enumerate(TRAITS):
        low = max(0.0, focal.traits[name] - EPSILON)
        high = min(1.0, focal.traits[name] + EPSILON)
        minus = replace(focal, traits={**focal.traits, name: low})
        plus = replace(focal, traits={**focal.traits, name: high})
        lower = _trophic_terms(data, minus)
        upper = _trophic_terms(data, plus)
        gradient[index] = (
            math.fsum((up - down) / (high - low) for down, up in zip(lower, upper, strict=True))
            - TRAIT_MAINTENANCE
        )
    if not np.isfinite(gradient).all():
        raise ValueError("fitness gradient is not finite")
    return gradient


class TraitFitnessGradientStage(SimulationStage):
    contract = StageContract(
        "reference_fitness",
        MODEL_VERSION,
        dependencies=("reference_selection",),
        reads=READS,
        writes=("arrays.fitness_gradients",),
    )

    def execute(self, context: TurnContext) -> StageResult:
        super().validate_inputs(context)
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            data = prepare_fitness(context)
            gradient = np.zeros((data.selection.population.shape[0], len(TRAITS)), dtype=np.float64)
            scores: list[float] = []
            for item in data.selection.species:
                gradient[item.slot] = finite_difference(data, item)
                scores.append(fitness_proxy(data, item))
            return result(
                context,
                self.contract.name,
                arrays={"fitness_gradients": gradient},
                metrics={
                    "model_version": MODEL_VERSION,
                    "proxy": "annual carbon-return/hazard index; not a survival derivative",
                    "gradient_units": "year^-1 per unit trait; not budget-projected",
                    "held_fixed": "population, habitat/suitability, competition and resources",
                    "fitness_proxy_total": math.fsum(scores),
                    "gradient_max_abs": float(np.max(np.abs(gradient), initial=0)),
                    "active_diet_edges": data.interaction_count,
                    "finite_difference_epsilon": EPSILON,
                    "mobility_trait_benefit": "none: movement currently has no speed coefficient",
                },
            )

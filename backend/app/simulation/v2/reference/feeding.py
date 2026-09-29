"""Inventory transfers and integer predation for ecology-reference-v1.

Separated from ecology.py to keep the public stage/schema module small. Feeding
never writes population: its killed structural carbon is represented by the
predation_deaths scratch array until the single demography owner applies deaths.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np

from ..context import TurnContext
from ..contracts import StageResult
from ..values import digest
from .common import FloatArray, IntArray, result
from .world import FEEDING_VERSION

if TYPE_CHECKING:
    from .ecology import EcologyInputs, Species

MODEL_VERSION = "ecology-reference-v1"


def _carbon(
    data: EcologyInputs,
    reserve: FloatArray,
    leaf: FloatArray,
    detritus: FloatArray,
    deaths: IntArray,
) -> FloatArray:
    structural = np.zeros_like(leaf)
    for item in data.species:
        structural += (data.population[item.slot] - deaths[item.slot]) * item.mass
    return structural + reserve.sum(axis=0) + leaf + detritus


def _functional_response(
    predator: Species,
    prey: Species,
    predator_count: int,
    prey_count: int,
    habitat: float,
    preference: float,
) -> float:
    """Holling II kills/predator/year; handling time bounds density response."""
    log_ratio = math.log(prey.mass) - math.log(predator.mass)
    size = math.exp(-0.5 * ((log_ratio - math.log(0.2)) / 2.0) ** 2)
    attack = (1 + predator.traits["attack"]) / (1 + prey.traits["armor"])
    speed = (1 + predator.traits["speed"]) / (1 + prey.traits["speed"])
    toxin = max(0.0, 1 + predator.traits["detox"] - prey.traits["toxin"])
    cooperation = 1 + predator.traits["cooperation"] * math.log1p(predator_count)
    encounter = 0.5 * size * attack * speed * toxin * cooperation * habitat * preference
    # Years per prey: fixed processing latency plus a body-mass-scaled meal.
    # V1 used >=0.5 year/prey, making the default predators' maximum assimilated
    # ration lower than maintenance, even at infinite prey density.
    handling = 0.02 + 0.25 * (prey.mass / predator.mass)
    if not math.isfinite(handling):
        raise ValueError("predation handling time overflow")
    return encounter * prey_count / (1 + handling * encounter * prey_count)


def _predate(
    context: TurnContext,
    data: EcologyInputs,
    reserve: FloatArray,
    detritus: FloatArray,
    appetite: FloatArray,
    intake: FloatArray,
    deaths: IntArray,
) -> tuple[float, float]:
    by_slot = {item.slot: item for item in data.species}
    predators = [item for item in data.species if item.role == "carnivore"]
    transferred_total = 0.0
    assimilated_total = 0.0
    for tile in range(data.population.shape[1]):
        # A seeded race removes permanent ID priority when predators share prey.
        ordered = sorted(
            predators,
            key=lambda item: (
                context.seeds.stream(
                    "reference_feeding",
                    FEEDING_VERSION,
                    entity=item.identity,
                    purpose="predator-rank",
                ).uint64(tile),
                item.identity,
            ),
        )
        for predator in ordered:
            count = int(data.population[predator.slot, tile])
            budget = float(appetite[predator.slot, tile])
            if count == 0 or budget == 0:
                continue
            targets = [
                (by_slot[prey], preference)
                for pred, prey, preference in data.edges
                if pred == predator.slot and preference > 0
            ]
            targets.sort(
                key=lambda target: (
                    context.seeds.stream(
                        "reference_feeding",
                        FEEDING_VERSION,
                        entity=digest((predator.identity, target[0].identity)),
                        purpose="prey-rank",
                    ).uint64(tile),
                    target[0].identity,
                )
            )
            for prey, preference in targets:
                remaining = int(data.population[prey.slot, tile] - deaths[prey.slot, tile])
                if remaining == 0 or budget <= 0:
                    continue
                overlap = (
                    1.0
                    if predator.habitat == prey.habitat
                    else (0.75 if "amphibious" in (predator.habitat, prey.habitat) else 0.0)
                )
                habitat = overlap * min(
                    float(data.arrays["suitability"][predator.slot, tile]),
                    float(data.arrays["suitability"][prey.slot, tile]),
                )
                rate = _functional_response(predator, prey, count, remaining, habitat, preference)
                expected = data.dt * count * rate
                if not math.isfinite(expected):
                    raise ValueError("predation expected kills overflow")
                carbon_per_prey = prey.mass + float(reserve[prey.slot, tile]) / remaining
                expected = min(expected, remaining, budget / carbon_per_prey)
                whole = math.floor(expected)
                stream = context.seeds.stream(
                    "reference_feeding",
                    FEEDING_VERSION,
                    entity=digest((predator.identity, prey.identity)),
                    purpose="kill-rounding",
                )
                killed = min(remaining, whole + int(stream.uniform(tile) < expected - whole))
                if killed == 0:
                    continue
                reserve_taken = float(reserve[prey.slot, tile]) * (killed / remaining)
                transferred = killed * prey.mass + reserve_taken
                assimilated = 0.7 * transferred
                reserve[prey.slot, tile] -= reserve_taken
                reserve[predator.slot, tile] += assimilated
                detritus[tile] += transferred - assimilated
                intake[predator.slot, tile] += assimilated
                deaths[prey.slot, tile] += killed
                budget -= transferred
                transferred_total += transferred
                assimilated_total += assimilated
    return transferred_total, assimilated_total


def run_feeding(context: TurnContext, data: EcologyInputs, name: str) -> StageResult:
    """Proportional leaf sharing followed by non-cascading herbivore predation.

    Fractional expected kills use unbiased Bernoulli rounding, so intake can
    exceed its continuous appetite by less than one whole prey. Randomized
    sequential allocation is an approximation, not a continuous-time food web.
    Decomposers already gained carbon in regeneration; their existing reserve
    measures energy adequacy here, with no second detritus uptake.
    """
    reserve = data.reserve.copy()
    leaf = data.arrays["plant_biomass"].copy()
    detritus = data.arrays["detritus"].copy()
    deaths = np.zeros(data.population.shape, dtype=np.int64)
    before = _carbon(data, reserve, leaf, detritus, deaths)
    demand = np.zeros_like(reserve)
    for item in data.species:
        demand[item.slot] = (
            data.dt * item.mass * data.population[item.slot] * (0.25 + item.fertility)
        )
    appetite = demand * data.arrays["suitability"] / (1 + data.arrays["competition"])
    requested = np.zeros_like(reserve)
    efficiency = np.zeros_like(reserve)
    for item in data.species:
        if item.role in ("producer", "herbivore"):
            requested[item.slot] = appetite[item.slot]
            efficiency[item.slot] = 1.0 if item.role == "producer" else 0.5
    total = requested.sum(axis=0)
    fraction = np.divide(leaf, total, out=np.ones_like(leaf), where=(total > leaf) & (total > 0))
    eaten = requested * np.minimum(1.0, fraction)
    # A physical supply cap handles the last rounding ULP of proportional sums.
    eaten_total = eaten.sum(axis=0)
    correction = np.divide(
        leaf, eaten_total, out=np.ones_like(leaf), where=(eaten_total > leaf) & (eaten_total > 0)
    )
    eaten *= np.minimum(1.0, correction)
    eaten_total = np.minimum(leaf, eaten.sum(axis=0))
    intake = eaten * efficiency
    reserve += intake
    rejected = eaten * (1 - efficiency)
    detritus += rejected.sum(axis=0)
    leaf -= eaten_total
    transferred, carnivore_assimilated = _predate(
        context,
        data,
        reserve,
        detritus,
        appetite,
        intake,
        deaths,
    )
    adequate = intake.copy()
    for item in data.species:
        if item.role == "decomposer":
            adequate[item.slot] = reserve[item.slot]
    satisfaction = np.divide(
        adequate, demand, out=np.ones_like(demand), where=(adequate < demand) & (demand > 0)
    )
    food_pressure = 1 - np.minimum(1, satisfaction)
    predation_pressure = np.divide(
        deaths, data.population, out=np.zeros_like(reserve), where=data.population > 0
    )
    after = _carbon(data, reserve, leaf, detritus, deaths)
    removed_structure = np.zeros_like(leaf)
    for item in data.species:
        removed_structure += deaths[item.slot] * item.mass
    reserve_change = reserve - data.reserve
    leaf_change = leaf - data.arrays["plant_biomass"]
    detritus_change = detritus - data.arrays["detritus"]
    residual = reserve_change.sum(axis=0) + leaf_change + detritus_change - removed_structure
    flow = (
        np.abs(reserve_change).sum(axis=0)
        + np.abs(leaf_change)
        + np.abs(detritus_change)
        + removed_structure
    )
    if np.any(np.abs(residual) > 1e-9 + 1e-12 * flow):
        raise ValueError("Feeding ledger balance lost at the supported numerical precision")
    return result(
        context,
        name,
        arrays={
            "energy_reserve": reserve,
            "plant_biomass": leaf,
            "detritus": detritus,
            "food_pressure": food_pressure,
            "predation_pressure": predation_pressure,
            "predation_deaths": deaths,
        },
        metrics={
            "model_version": MODEL_VERSION,
            "leaf_consumed_carbon": float(eaten_total.sum()),
            "leaf_assimilated_carbon": float((eaten * efficiency).sum()),
            "leaf_rejected_to_detritus_carbon": float(rejected.sum()),
            "predation_transferred_carbon": transferred,
            "predation_assimilated_carbon": carnivore_assimilated,
            "predation_deaths": float(deaths.sum(dtype=np.float64)),
            "unmet_ingestion_carbon": float(np.maximum(0, demand - adequate).sum()),
            "carbon_before": float(before.sum()),
            "carbon_after": float(after.sum()),
            "carbon_max_abs_residual": float(np.max(np.abs(residual), initial=0)),
            "carbon_pool": (
                "effective structure (population - predation_deaths) + reserves + leaf + detritus"
            ),
            "decomposer_food_pressure": (
                "existing reserve adequacy; uptake already occurs in regeneration"
            ),
        },
    )

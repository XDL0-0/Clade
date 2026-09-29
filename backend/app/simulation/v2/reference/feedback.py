"""Explicit opt-in food-web feedback and physiological fitness counterfactuals.

The earlier 17/20/26-stage recipes keep their original numerical definitions.
This recipe adds prey group defense and aquatic structure refuge to actual
feeding, and evaluates the same relations when traits change. Environmental
selection uses the real thermal/water mortality hazards at fixed current tiles;
future public benefits of habitat construction are not invented as fitness.
"""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

from ..context import TurnContext
from ..contracts import StageResult
from .common import FloatArray, result
from .demography_inputs import float_matrix
from .ecology import FeedingStage, Species, _inputs
from .environment import checked_codes, checked_field
from .feeding import run_feeding
from .fitness import EPSILON, TraitFitnessGradientStage, finite_difference, prepare_fitness
from .physiology import thermal_response, water_response
from .world import TRAITS


class FeedbackFeedingStage(FeedingStage):
    contract = replace(
        FeedingStage.contract,
        version="3",
        reads=(*FeedingStage.contract.reads, "arrays.habitat_complexity"),
    )

    def execute(self, context: TurnContext) -> StageResult:
        self.validate_inputs(context)
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            refuge = checked_field(context, "habitat_complexity", low=0, high=1)
            data = _inputs(context)
            refuge = np.where(data.biome <= 1, refuge, 0.0)
            return run_feeding(
                context,
                data,
                self.contract.name,
                version=self.contract.version,
                refuge=refuge,
                defensive_groups=True,
            )


def environmental_cost(
    item: Species,
    *,
    temperature: FloatArray,
    soil: FloatArray,
    biome: np.typing.NDArray[np.int64],
    population: np.typing.NDArray[np.int64],
    reserve: FloatArray,
    dt: float,
) -> float:
    """Annual hazard plus reserve-funded engineering work per structural carbon.

    Hazards match Mortality (temperature=2, water/other=.5). The engineering
    expenditure matches NicheConstruction on fixed current reserves. This is
    an instantaneous proxy, not a multi-turn survival or public-goods forecast.
    """
    total = int(population.sum(dtype=object))
    if total == 0:
        return 0.0
    weights = population.astype(np.float64) / total
    hazard = 2 * (1 - thermal_response(item, temperature))
    hazard += 0.5 * (1 - water_response(item, soil, biome >= 2))
    work = np.minimum(reserve, 0.02 * dt * item.mass * population * item.traits["engineering"])
    structural = item.mass * population
    annual_cost = np.divide(work, structural, out=np.zeros_like(reserve), where=structural > 0) / dt
    return float(((hazard + annual_cost) * weights).sum())


class EnvironmentalFitnessStage(TraitFitnessGradientStage):
    contract = replace(
        TraitFitnessGradientStage.contract,
        version="2",
        reads=(
            *TraitFitnessGradientStage.contract.reads,
            "arrays.temperature",
            "arrays.soil_water",
            "arrays.biome",
            "arrays.energy_reserve",
            "arrays.habitat_complexity",
        ),
    )

    def execute(self, context: TurnContext) -> StageResult:
        self.validate_inputs(context)
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            temperature = checked_field(context, "temperature")
            soil = checked_field(context, "soil_water", low=0)
            biome = checked_codes(context, "biome", high=6)
            refuge = checked_field(context, "habitat_complexity", low=0, high=1)
            refuge = np.where(biome <= 1, refuge, 0.0)
            data = prepare_fitness(context, refuge=refuge, defensive_groups=True)
            if not 0 < data.selection.dt <= 1:
                raise ValueError("Environmental fitness requires time step in (0,1]")
            population = data.selection.population
            reserve = float_matrix(context, "energy_reserve", population.shape)
            gradient = np.zeros((population.shape[0], len(TRAITS)), dtype=np.float64)
            costs: list[float] = []
            for item in data.selection.species:
                if not np.any(population[item.slot]):
                    continue
                gradient[item.slot] = finite_difference(data, item)

                def cost(focal: Species) -> float:
                    return environmental_cost(
                        focal,
                        temperature=temperature,
                        soil=soil,
                        biome=biome,
                        population=population[focal.slot],
                        reserve=reserve[focal.slot],
                        dt=data.selection.dt,
                    )

                costs.append(cost(item))
                # The other five traits do not enter these equations. Difference
                # only the two actual controls; no pressure-to-reward shortcut.
                for name in ("armor", "engineering"):
                    low, high = (
                        max(0.0, item.traits[name] - EPSILON),
                        min(1.0, item.traits[name] + EPSILON),
                    )
                    lower = cost(replace(item, traits={**item.traits, name: low}))
                    upper = cost(replace(item, traits={**item.traits, name: high}))
                    gradient[item.slot, TRAITS.index(name)] -= (upper - lower) / (high - low)
            if not np.isfinite(gradient).all():
                raise ValueError("Environmental fitness gradient must be finite")
            return result(
                context,
                self.contract.name,
                arrays={"fitness_gradients": gradient},
                metrics={
                    "proxy": (
                        "annual trophic return minus actual thermal/water hazards "
                        "and paid trait/work costs"
                    ),
                    "held_fixed": "current populations, resources, competition and environment",
                    "future_construction_benefit": (
                        "excluded: public habitat feedback evaluated by later turns"
                    ),
                    "environmental_cost_per_individual_sum": math.fsum(costs),
                    "gradient_max_abs": float(np.max(np.abs(gradient), initial=0)),
                    "active_diet_edges": data.interaction_count,
                    "finite_difference_epsilon": EPSILON,
                },
            )

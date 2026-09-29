"""Birth proposals consume real stored carbon before population publication."""

from __future__ import annotations

import numpy as np

from ..context import TurnContext
from ..contracts import SimulationStage, StageContract, StageResult
from .common import result
from .demography_inputs import float_matrix, inputs, integer_matrix, rounded, transported


class ReproductionStage(SimulationStage):
    contract = StageContract(
        "reference_reproduction",
        "1",
        ("reference_mortality",),
        reads=(
            "state.geometry",
            "state.species",
            "state.environment.ecological_years_per_turn",
            "arrays.population",
            "arrays.energy_reserve",
            "arrays.predation_deaths",
            "arrays.migration_in",
            "arrays.migration_out",
            "arrays.deaths",
            "arrays.births",
            "arrays.suitability",
            "arrays.carrying_capacity",
        ),
        writes=("arrays.energy_reserve", "arrays.births"),
    )

    def execute(self, context: TurnContext) -> StageResult:
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            return self._execute(context)

    def _execute(self, context: TurnContext) -> StageResult:
        pop, reserve, cohorts, dt = inputs(context)
        available = transported(context, pop)
        deaths = integer_matrix(context, "deaths")
        kills = integer_matrix(context, "predation_deaths")
        old_births = integer_matrix(context, "births")
        if deaths.shape != pop.shape or old_births.shape != pop.shape or np.any(deaths < kills):
            raise ValueError("Death or birth ledger mismatch")
        survivors = available - (deaths - kills)
        if np.any(survivors < 0):
            raise ValueError("Deaths exceed available individuals")
        suitability = float_matrix(context, "suitability", pop.shape)
        capacity = float_matrix(context, "carrying_capacity", pop.shape)
        if np.any(suitability > 1):
            raise ValueError("Suitability must be in [0,1]")
        births = np.zeros(pop.shape, dtype=np.int64)
        old_reserve = reserve.copy()
        structural_gain = np.zeros(pop.shape[1], dtype=np.float64)
        for cohort in cohorts:
            row = cohort.slot
            if cohort.extinct:
                continue
            crowding = np.divide(
                survivors[row],
                capacity[row],
                out=np.ones(pop.shape[1]),
                where=capacity[row] > survivors[row],
            )
            density = np.maximum(0.0, 1 - crowding)
            expected = survivors[row] * cohort.fertility * dt * suitability[row] * density
            room = np.iinfo(np.int64).max - survivors[row]
            energy_limit = np.array(
                [
                    (
                        int(bound)
                        if float(energy) >= int(bound) * cohort.mass
                        else int(float(energy) / cohort.mass)
                    )
                    for energy, bound in zip(reserve[row], room, strict=True)
                ],
                dtype=np.int64,
            )
            # No parent, no birth, including energy left by malformed inputs.
            energy_limit[survivors[row] == 0] = 0
            births[row] = rounded(
                context, self.contract.name, cohort, expected, energy_limit, "births"
            )
            cost = births[row] * cohort.mass
            if np.any(cost > reserve[row]):
                raise ValueError("Births cannot consume more stored carbon than available")
            reserve[row] -= cost
            structural_gain += cost
        residual = (reserve - old_reserve).sum(axis=0) + structural_gain
        if np.any(np.abs(residual) > 1e-9 + 1e-12 * structural_gain):
            raise ValueError("Birth ledger balance lost at the supported numerical precision")
        return result(
            context,
            self.contract.name,
            arrays={
                "births": births,
                "energy_reserve": reserve,
            },
            metrics={
                "births": int(births.sum(dtype=object)),
                "structural_carbon_created": float(structural_gain.sum()),
                "carbon_max_abs_residual": float(np.max(np.abs(residual))),
            },
        )

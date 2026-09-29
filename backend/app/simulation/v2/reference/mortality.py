"""Exclusive cause-specific deaths and mass-conserving maintenance metabolism."""

from __future__ import annotations

import numpy as np

from ..context import TurnContext
from ..contracts import SimulationStage, StageContract, StageResult
from ..values import JsonValue
from .common import number, result
from .demography_inputs import float_matrix, inputs, integer_matrix, rounded, transported
from .world import MORTALITY_CAUSES

_READS = (
    "state.geometry",
    "state.species",
    "state.environment.ecological_years_per_turn",
    "arrays.population",
    "arrays.energy_reserve",
    "arrays.predation_deaths",
    "arrays.migration_in",
    "arrays.migration_out",
    "arrays.temperature_pressure",
    "arrays.water_pressure",
    "arrays.competition",
    "arrays.volcanic_stress",
    "arrays.nutrients",
    "arrays.detritus",
    "arrays.mortality",
    "arrays.deaths",
)


class MortalityStage(SimulationStage):
    contract = StageContract(
        "reference_mortality",
        "1",
        ("reference_connectivity",),
        reads=_READS,
        writes=(
            "arrays.energy_reserve",
            "arrays.detritus",
            "arrays.nutrients",
            "arrays.mortality",
            "arrays.deaths",
        ),
    )

    def execute(self, context: TurnContext) -> StageResult:
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            return self._execute(context)

    def _execute(self, context: TurnContext) -> StageResult:
        pop, reserve, cohorts, dt = inputs(context)
        available = transported(context, pop)
        killed = integer_matrix(context, "predation_deaths")
        shape = pop.shape
        temperature = float_matrix(context, "temperature_pressure", shape)
        water = float_matrix(context, "water_pressure", shape)
        competition = float_matrix(context, "competition", shape)
        if np.any(temperature > 1) or np.any(water > 1):
            raise ValueError("Environmental pressures must be in [0,1]")
        detritus = float_matrix(context, "detritus", (shape[1],))
        nutrients = float_matrix(context, "nutrients", (shape[1],))
        old_reserve, old_detritus, old_nutrients = reserve.copy(), detritus.copy(), nutrients.copy()
        volcanic = float_matrix(context, "volcanic_stress", (shape[1],))
        old = context.snapshot.arrays["mortality"].numpy()
        if old.dtype != np.dtype("int64") or old.shape != (len(MORTALITY_CAUSES), *shape):
            raise ValueError("Mortality cause axes mismatch")
        if integer_matrix(context, "deaths").shape != shape:
            raise ValueError("Deaths axes mismatch")
        disaster = number(context.command.get("disaster_severity", 0.0), "disaster severity")
        disease = number(context.command.get("disease_pressure", 0.0), "disease pressure")
        if not 0 <= disaster <= 1 or not 0 <= disease <= 1 or np.any(volcanic > 1):
            raise ValueError("Disease/disaster/volcanic pressure must be in [0,1]")
        death_ledger = np.zeros((len(MORTALITY_CAUSES), *shape), dtype=np.int64)
        death_ledger[2] = killed
        respired = np.zeros(shape[1], dtype=np.float64)
        for cohort in cohorts:
            row = cohort.slot
            survivors = available[row].copy()
            demand = survivors * cohort.mass * dt * (0.25 + 0.05 * cohort.trait_cost)
            metabolism = np.minimum(reserve[row], demand)
            shortage = np.divide(
                demand - metabolism, demand, out=np.zeros_like(demand), where=demand > 0
            )
            reserve[row] -= metabolism
            respired += metabolism
            hazards = {
                "temperature": 2 * temperature[row],
                "starvation": 8 * shortage,
                "competition": 0.05 * competition[row] / (1 + competition[row]),
                "disease": disease * survivors / (survivors + 20.0),
                "disaster": np.full(shape[1], 12 * disaster) + volcanic * 0.3,
                "old_age": np.full(shape[1], 1 / cohort.lifespan),
                "other": 0.5 * water[row],
            }
            # Causes act on successive survivors: no individual dies twice.
            for cause, hazard in hazards.items():
                expected = survivors * (-np.expm1(-hazard * dt))
                deaths = rounded(context, self.contract.name, cohort, expected, survivors, cause)
                death_ledger[MORTALITY_CAUSES.index(cause), row] = deaths
                fraction = np.divide(deaths, survivors, out=np.zeros(shape[1]), where=survivors > 0)
                dead_reserve = reserve[row] * fraction
                reserve[row] -= dead_reserve
                detritus += deaths * cohort.mass + dead_reserve
                survivors -= deaths
            # Empty cohorts cannot retain metabolically inaccessible energy.
            abandoned = np.where(survivors == 0, reserve[row], 0.0)
            reserve[row] -= abandoned
            detritus += abandoned
        nutrients += respired * 0.02
        wide_deaths = death_ledger.sum(axis=0, dtype=object)
        if np.any(wide_deaths > np.iinfo(np.int64).max):
            raise ValueError("Mortality count exceeds int64")
        deaths = np.asarray(wide_deaths, dtype=np.int64)
        removed_structure = np.zeros(shape[1], dtype=np.float64)
        for cohort in cohorts:
            non_predation = deaths[cohort.slot] - killed[cohort.slot]
            removed_structure += non_predation * cohort.mass
        reserve_change = reserve - old_reserve
        detritus_change = detritus - old_detritus
        residual = reserve_change.sum(axis=0) + detritus_change + respired - removed_structure
        scale = (
            np.abs(reserve_change).sum(axis=0)
            + np.abs(detritus_change)
            + respired
            + removed_structure
        )
        if np.any(np.abs(residual) > 1e-9 + 1e-12 * scale) or np.any(
            np.abs(nutrients - old_nutrients - 0.02 * respired) > 1e-9 + 1e-12 * 0.02 * respired
        ):
            raise ValueError("Mortality ledger balance lost at the supported numerical precision")
        metrics: dict[str, JsonValue] = {
            "carbon_respired": float(respired.sum()),
            "nutrients_returned": float(respired.sum() * 0.02),
            "carbon_max_abs_residual": float(np.max(np.abs(residual))),
            "death_causes": {
                cause: int(death_ledger[index].sum(dtype=object))
                for index, cause in enumerate(MORTALITY_CAUSES)
            },
        }
        return result(
            context,
            self.contract.name,
            arrays={
                "energy_reserve": reserve,
                "detritus": detritus,
                "nutrients": nutrients,
                "mortality": death_ledger,
                "deaths": deaths,
            },
            metrics=metrics,
        )

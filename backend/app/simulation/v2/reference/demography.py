"""Single regular population writer: validate and publish the cohort ledgers."""

from __future__ import annotations

import numpy as np

from ..context import TurnContext
from ..contracts import SimulationStage, StageContract, StageResult
from .common import geometry, result
from .demography_inputs import integer_matrix


class PopulationUpdateStage(SimulationStage):
    contract = StageContract(
        "reference_population",
        "1",
        ("reference_reproduction",),
        reads=(
            "state.geometry",
            "arrays.population",
            "arrays.births",
            "arrays.deaths",
            "arrays.migration_in",
            "arrays.migration_out",
            "arrays.mortality",
        ),
        writes=("arrays.population",),
    )

    def execute(self, context: TurnContext) -> StageResult:
        geometry(context)
        pop = integer_matrix(context, "population")
        births = integer_matrix(context, "births")
        deaths = integer_matrix(context, "deaths")
        incoming = integer_matrix(context, "migration_in")
        outgoing = integer_matrix(context, "migration_out")
        if any(value.shape != pop.shape for value in (births, deaths, incoming, outgoing)):
            raise ValueError("Demographic ledger axes mismatch")
        mortality = context.snapshot.arrays["mortality"].numpy()
        if mortality.dtype != np.dtype("int64") or mortality.shape != (8, *pop.shape):
            raise ValueError("Mortality cause axes mismatch")
        causes = np.asarray(mortality, dtype=np.int64)
        if np.any(causes < 0) or not np.array_equal(causes.sum(axis=0, dtype=object), deaths):
            raise ValueError("Deaths must equal their exclusive cause breakdown")
        if not np.array_equal(
            incoming.sum(axis=1, dtype=object), outgoing.sum(axis=1, dtype=object)
        ):
            raise ValueError("Migration must conserve every species population")
        total = pop.astype(object) + births - deaths + incoming - outgoing
        if np.any(total < 0) or np.any(total > np.iinfo(np.int64).max):
            raise ValueError("Population update is negative or overflows int64")
        updated = np.asarray(total, dtype=np.int64)
        expected = int(births.sum(dtype=object) - deaths.sum(dtype=object))
        actual = int(updated.sum(dtype=object) - pop.sum(dtype=object))
        if expected != actual:
            raise ValueError("Population balance failed")
        return result(
            context,
            self.contract.name,
            arrays={"population": updated},
            metrics={
                "population_before": int(pop.sum(dtype=object)),
                "population_after": int(updated.sum(dtype=object)),
                "births": int(births.sum(dtype=object)),
                "deaths": int(deaths.sum(dtype=object)),
                "migrated": int(incoming.sum(dtype=object)),
                "population_balance_residual": 0,
            },
        )

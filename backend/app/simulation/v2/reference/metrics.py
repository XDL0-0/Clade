"""Compact end-of-turn ecological observations; no reconstructed narrative facts."""

from __future__ import annotations

import math
from collections.abc import Mapping

from ..context import TurnContext
from ..contracts import SimulationStage, StageContract, StageResult
from ..values import JsonValue
from .common import number, result, state_patch
from .demography_inputs import float_matrix, inputs, integer_matrix


class MetricsStage(SimulationStage):
    def __init__(self, *, after: str = "reference_population", genetics: bool = False) -> None:
        self.genetics = genetics
        self.contract = StageContract(
            "reference_metrics",
            "2" if genetics else "1",
            (after,),
            reads=(
                "state.geometry",
                "state.species",
                "state.environment.ecological_years_per_turn",
                "state.food_web",
                "state.observability",
                "arrays.population",
                "arrays.energy_reserve",
                "arrays.plant_biomass",
                "arrays.detritus",
                "arrays.npp",
                "arrays.births",
                "arrays.deaths",
                "arrays.migration_in",
                *(("arrays.deme_traits",) if genetics else ()),
            ),
            writes=("state.observability",),
        )

    def execute(self, context: TurnContext) -> StageResult:
        pop, reserve, cohorts, dt = inputs(context)
        tiles = pop.shape[1]
        leaf = float_matrix(context, "plant_biomass", (tiles,))
        npp = float_matrix(context, "npp", (tiles,))
        detritus = float_matrix(context, "detritus", (tiles,))
        abundance: dict[str, int] = {}
        biomass: dict[str, float] = {}
        levels: list[float] = []
        weighted_levels: list[float] = []
        trait_values: dict[str, list[tuple[float, float]]] = {}
        speciations = 0
        for cohort in cohorts:
            total = int(pop[cohort.slot].sum(dtype=object))
            if total == 0:
                continue
            metadata = context.species_state[cohort.identity]
            assert isinstance(metadata, Mapping)
            abundance[cohort.identity] = total
            biomass[cohort.identity] = total * cohort.mass
            trophic = number(metadata["trophic_level"], "trophic level")
            levels.append(biomass[cohort.identity])
            weighted_levels.append(biomass[cohort.identity] * trophic)
            if metadata.get("created_turn") == context.turn_id and metadata.get("ancestor"):
                speciations += 1
            traits = metadata["traits"]
            assert isinstance(traits, Mapping)
            for name, value in traits.items():
                trait_values.setdefault(name, []).append((number(value, name), total))
        total_population = sum(abundance.values())
        fractions = [value / total_population for value in abundance.values()]
        structural = math.fsum(levels)
        alive = set(abundance)
        edges = context.food_web_state.get("edges", ())
        if not isinstance(edges, tuple):
            raise ValueError("Food web edges must be a sequence")
        active_edges: set[tuple[str, str]] = set()
        for edge in edges:
            if not isinstance(edge, Mapping):
                raise ValueError("Food web edges must be mappings")
            predator, prey = edge["predator"], edge["prey"]
            if (
                isinstance(predator, str)
                and isinstance(prey, str)
                and predator in alive
                and prey in alive
            ):
                active_edges.add((predator, prey))
        observations = context.snapshot.domain("observability")
        previous_turn = observations.get("turn")
        has_previous = previous_turn is not None
        if has_previous and (
            type(previous_turn) is not int or previous_turn != context.turn_id - 1
        ):
            raise ValueError("Observation baseline must be the immediately previous turn")
        if has_previous and "abundance" not in observations:
            raise ValueError("Previous turn observation is missing abundances")
        previous = observations.get("abundance", {})
        if not isinstance(previous, Mapping):
            raise ValueError("Previous abundances must be a mapping")
        old = {key: number(value, "previous abundance") for key, value in previous.items()}
        if any(value < 0 for value in old.values()):
            raise ValueError("Previous abundances must be nonnegative")
        denominator = sum(old.values()) + total_population
        turnover = (
            sum(abs(old.get(key, 0) - abundance.get(key, 0)) for key in alive | set(old))
            / denominator
            if denominator
            else 0.0
        )
        traits_summary: dict[str, JsonValue] = {}
        for name, values in sorted(trait_values.items()):
            weight = sum(count for _, count in values)
            mean = math.fsum(value * count for value, count in values) / weight
            variance = math.fsum((value - mean) ** 2 * count for value, count in values) / weight
            traits_summary[name] = {"mean": mean, "standard_deviation": math.sqrt(variance)}
        totals: dict[str, int] = {}
        unused = sorted(set(range(pop.shape[0])) - {cohort.slot for cohort in cohorts})
        for name in ("migration_in", "births", "deaths"):
            ledger = integer_matrix(context, name)
            if ledger.shape != pop.shape or ledger[unused].any():
                raise ValueError("Observation ledgers must match assigned species rows")
            totals[name] = int(ledger.sum(dtype=object))
        migrations, births, deaths = totals["migration_in"], totals["births"], totals["deaths"]
        extinctions = len({key for key, value in old.items() if value > 0} - alive)
        metrics: dict[str, JsonValue] = {
            "species_richness": len(alive),
            "total_population": total_population,
            "shannon_diversity": -math.fsum(value * math.log(value) for value in fractions),
            "simpson_diversity": 1 - math.fsum(value * value for value in fractions)
            if alive
            else 0.0,
            "structural_biomass": structural,
            "total_biomass": structural + float(leaf.sum()) + float(reserve.sum()),
            "detritus_carbon": float(detritus.sum()),
            "mean_trophic_level": math.fsum(weighted_levels) / structural if structural else 0.0,
            "food_web_connectivity": len(active_edges) / (len(alive) * (len(alive) - 1))
            if len(alive) > 1
            else 0.0,
            "extinctions": extinctions,
            "extinction_rate_per_year": extinctions / dt,
            "speciations": speciations,
            "speciation_rate_per_year": speciations / dt,
            "migration_rate": migrations / (total_population + deaths)
            if total_population + deaths
            else 0.0,
            "migration_rate_definition": (
                "movement steps / (end population + deaths); not unique migrants"
            ),
            "births": births,
            "deaths": deaths,
            "npp": float(npp.sum()),
            "genetic_diversity": None,
            "ecosystem_stability": 1 - turnover if has_previous else None,
            "stability_definition": "1 - Bray-Curtis abundance turnover since previous turn",
            "trait_distribution": traits_summary,
        }
        if self.genetics:
            from .diversity import deme_diversity

            metrics["genetic_diversity"] = deme_diversity(context, pop)
            metrics["genetic_diversity_definition"] = (
                "population-weighted within-species variance of seven deme mean traits; "
                "not individual genetic diversity or allele heterozygosity"
            )
        return result(
            context,
            self.contract.name,
            state=(
                state_patch(
                    context,
                    ("observability",),
                    {"abundance": abundance, "turn": context.turn_id},
                ),
            ),
            metrics=metrics,
        )

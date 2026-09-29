"""Commit the classic GPU demography once, retaining classic extinction rules."""
from __future__ import annotations

from ..repositories.species_repository import species_repository
from ..services.species.extinction_checker import ExtinctionChecker


def commit_tensor_population(ctx, engine) -> None:
    """The tensor stage owns births/deaths; this step owns persistence/lifecycle."""
    results = ctx.combined_results
    for item in results:
        species = item.species
        population = max(0, int(item.final_population))
        ctx.new_populations[species.lineage_code] = population
        species.morphology_stats = {**species.morphology_stats, "population": population}
        species_repository.upsert(species)

    checker = ExtinctionChecker(
        species_repository=species_repository,
        turn_counter=ctx.turn_index,
        event_callback=ctx.emit_event,
        config=getattr(engine.speciation, "_config", None),
    )
    # Restore counters from this world's species rather than a process singleton.
    for item in results:
        code = item.species.lineage_code
        history = item.species.morphology_stats
        checker._mvp_warning_counts[code] = int(history.get("classic_mvp_turns", 0))
        checker._decline_streak_counts[code] = int(history.get("classic_decline_turns", 0))
        checker._previous_populations[code] = int(item.initial_population)
    extinct = set(checker.check_and_apply(results, ctx.new_populations))
    tensor = ctx.tensor_state
    for item in results:
        species = item.species
        code = species.lineage_code
        if code in extinct:
            ctx.new_populations[code] = 0
            item.final_population = 0
            # Recruitment fails once the classic extinction threshold is crossed.
            item.births = 0
            item.survivors = 0
            item.deaths = item.initial_population
            item.death_rate = 1.0 if item.initial_population else 0.0
            item.adjusted_death_rate = item.death_rate
            if tensor is not None and code in tensor.species_map:
                tensor.pop[tensor.species_map[code]] = 0
            ctx.extinct_codes.add(code)
        species.morphology_stats = {
            **species.morphology_stats,
            "classic_mvp_turns": checker._mvp_warning_counts.get(code, 0),
            "classic_decline_turns": checker._decline_streak_counts.get(code, 0),
        }
        species_repository.upsert(species)
        initial = item.initial_population
        engine.migration_advisor.update_decline_streak(
            code, item.death_rate, item.final_population / initial if initial else 1.0,
        )
    ctx.emit_event(
        "info",
        f"种群结算：出生 {sum(i.births for i in results):,}，死亡 {sum(i.deaths for i in results):,}",
        "物种",
    )

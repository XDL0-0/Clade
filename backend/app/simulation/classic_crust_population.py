"""Carry land-bound classic populations with their drifting habitat."""
from collections import defaultdict

from sqlalchemy import delete

from ..core.database import session_scope
from ..models.environment import HabitatPopulation


def carry_populations(species, habitats, destinations, turn_index):
    """Move whole habitat records without births, deaths, or dropped collisions."""
    carried = {
        sp.id for sp in species
        if sp.habitat_type in {"terrestrial", "aquatic", "freshwater", "amphibious", "coastal"}
    }
    affected = {
        row["species_id"] for row in habitats
        if row["species_id"] in carried and destinations.get(row["tile_id"], row["tile_id"]) != row["tile_id"]
    }
    if not affected:
        return 0
    totals = defaultdict(int)
    moved = 0
    for row in habitats:
        species_id, origin = row["species_id"], row["tile_id"]
        if species_id not in affected:
            continue
        target = destinations.get(origin, origin)
        count = max(0, int(row["population"]))
        totals[species_id, target] += count
        if target != origin:
            moved += count
    with session_scope() as session:
        # Replace only these species' current-turn records, including turn zero.
        session.exec(delete(HabitatPopulation).where(
            HabitatPopulation.turn_index == turn_index,
            HabitatPopulation.species_id.in_(affected),
        ))
        session.add_all([
            HabitatPopulation(species_id=species_id, tile_id=tile_id, population=count,
                              suitability=0.0, turn_index=turn_index)
            for (species_id, tile_id), count in totals.items() if count > 0
        ])
    return moved

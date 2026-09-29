"""Versioned, evidence-bound speciation proposals from disconnected demes.

Score = .30 geography + .25 ecology + .20 genetics + .15 age + .10 low flow.
Geography/low flow are binary observations of >=2 occupied passable components.
Ecology averages clipped temperature difference / thermal width and humidity
difference. Genetics is trait RMS distance / sqrt(6/7), the maximum distance
between [0,1]^7 vectors each having sum <=3. All deme means are population
weighted; age is the youngest occupied tile's isolation age, capped at 24 turns.
These are descriptive reference rules, not probabilities or AI judgments.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

import numpy as np

from ..context import TurnContext
from ..contracts import SimulationStage, StageContract, StageResult
from ..values import JsonValue, digest
from .common import FloatArray, IntArray, geometry, result, state_patch
from .ecology import Species, _integer, _species
from .extinction import STATUSES
from .selection import _float
from .world import MORTALITY_CAUSES, TRAITS

VERSION = "1"
MIN_ISOLATION = 12
MIN_POPULATION = 20
MIN_SCORE = 0.70
GENETIC_SCALE = float(np.sqrt(6 / len(TRAITS)))
GENES = ("deme_traits", "trait_proposals", "gene_population", "gene_connectivity", "isolation_age")
ARRAYS = (
    "population",
    "energy_reserve",
    *GENES,
    "connectivity",
    "temperature",
    "humidity",
    "biome",
)
READS = (
    "state.geometry",
    "state.species",
    "state.evolution",
    *(f"arrays.{name}" for name in ARRAYS),
)
SCRATCH = (
    "suitability",
    "carrying_capacity",
    "competition",
    "temperature_pressure",
    "water_pressure",
    "food_pressure",
    "predation_pressure",
    "predation_deaths",
    "migration_in",
    "migration_out",
    "births",
    "deaths",
    "selection_pressure",
    "fitness_gradients",
    "mortality",
)


@dataclass(frozen=True)
class SpeciationInputs:
    population: IntArray
    reserve: FloatArray
    integers: Mapping[str, IntArray]
    floats: Mapping[str, FloatArray]
    species: tuple[Species, ...]
    evidence_hash: str


def tile_runs(mask: np.typing.NDArray[np.bool_]) -> tuple[tuple[int, int], ...]:
    runs: list[tuple[int, int]] = []
    for tile in np.flatnonzero(mask):
        index = int(tile)
        if runs and runs[-1][1] + 1 == index:
            runs[-1] = runs[-1][0], index
        else:
            runs.append((index, index))
    return tuple(runs)


def lifecycle_marker(context: TurnContext) -> int:
    value = context.snapshot.domain("evolution").get("speciation_turn", 0)
    if type(value) is not int or not 0 <= value <= context.turn_id:
        raise ValueError("Invalid speciation turn marker")
    return value


def speciation_inputs(context: TurnContext) -> SpeciationInputs:
    width, height = geometry(context)
    shape = context.snapshot.arrays["population"].shape
    if len(shape) != 2 or shape[1] != width * height:
        raise ValueError("Speciation population must have species-by-tile axes")
    population = _integer(context, "population", shape)
    reserve = _float(context, "energy_reserve", shape)
    if np.any((population == 0) & (reserve != 0)):
        raise ValueError("Absent individuals cannot own reserve carbon")
    integers = {
        name: _integer(context, name, shape) for name in ("gene_population", "isolation_age")
    }
    for name in ("connectivity", "gene_connectivity"):
        source = context.snapshot.arrays[name].numpy()
        if source.dtype != np.dtype("int64") or source.shape != shape:
            raise ValueError(f"{name} must match int64 population axes")
        labels = np.array(source, dtype=np.int64, copy=True)
        if np.any(labels < -1) or np.any(labels >= shape[1]):
            raise ValueError("Invalid component label")
        integers[name] = labels
    biome = _integer(context, "biome", (shape[1],))
    if np.any(biome > 6):
        raise ValueError("Unknown biome code")
    floats = {
        name: _float(context, name, (*shape, len(TRAITS)), bounded=True)
        for name in ("deme_traits", "trait_proposals")
    }
    if any(np.any(values.sum(axis=2) > 3 + 1e-12) for values in floats.values()):
        raise ValueError("Deme traits must respect the shared budget of 3")
    floats["temperature"] = _float(context, "temperature", (shape[1],), signed=True)
    floats["humidity"] = _float(context, "humidity", (shape[1],), bounded=True)
    if not np.array_equal(integers["gene_population"], population):
        raise ValueError("Gene population must describe the current adapted population")
    occupied = population > 0
    if np.any(integers["gene_connectivity"][~occupied] != -1) or not np.array_equal(
        integers["gene_connectivity"][occupied], integers["connectivity"][occupied]
    ):
        raise ValueError("Gene connectivity must describe current occupied components")
    if np.any(integers["isolation_age"] > context.turn_id):
        raise ValueError("Isolation age cannot exceed the world turn")
    if np.any(integers["isolation_age"][~occupied] != 0):
        raise ValueError("Empty demes cannot retain isolation age")
    species = _species(context, shape[0])
    for item in species:
        metadata = context.species_state[item.identity]
        assert isinstance(metadata, Mapping)
        if not item.identity.strip() or metadata.get("status") not in STATUSES:
            raise ValueError("Species require valid identities and lifecycle states")
        if metadata["status"] == "Extinct" and np.any(population[item.slot]):
            raise ValueError("Extinct species cannot speciate or retain population")
        descendants = metadata.get("descendants")
        ancestor = metadata.get("ancestor")
        if (ancestor is not None and (not isinstance(ancestor, str) or not ancestor)) or (
            not isinstance(descendants, tuple)
            or any(not isinstance(child, str) or not child for child in descendants)
            or len(set(descendants)) != len(descendants)
        ):
            raise ValueError("Invalid species lineage")
        labels = integers["connectivity"][item.slot]
        for label in np.unique(labels[labels >= 0]):
            if int(np.flatnonzero(labels == label)[0]) != int(label):
                raise ValueError("A component label must be its minimum passable tile")
    unused = sorted(set(range(shape[0])) - {item.slot for item in species})
    if any(
        np.any(values[unused])
        for values in (
            population,
            reserve,
            integers["gene_population"],
            integers["isolation_age"],
            floats["deme_traits"],
            floats["trait_proposals"],
        )
    ) or any(
        np.any(integers[name][unused] != -1) for name in ("connectivity", "gene_connectivity")
    ):
        raise ValueError("Unused species slots must have empty stocks and genes")
    evidence = digest(
        {
            "species": context.species_state,
            "geometry": context.snapshot.domain("geometry"),
            "arrays": {name: context.snapshot.arrays[name].content_hash for name in ARRAYS},
        }
    )
    return SpeciationInputs(population, reserve, integers, floats, species, evidence)


def _mean(
    values: FloatArray, population: IntArray, mask: np.typing.NDArray[np.bool_]
) -> FloatArray:
    weights = population[mask].astype(np.float64)
    weights /= weights.sum()
    return np.asarray(np.tensordot(weights, values[mask], axes=1), dtype=np.float64)


def trait_mean(
    values: FloatArray, population: IntArray, mask: np.typing.NDArray[np.bool_]
) -> FloatArray:
    """Round a convex trait mean back inside the numerical trait budget."""
    mean = np.clip(_mean(values, population, mask), 0, 1)
    total = float(mean.sum())
    return mean * min(1.0, 3 / total) if total else mean


def proposals(context: TurnContext, data: SpeciationInputs) -> tuple[JsonValue, ...]:
    if lifecycle_marker(context) == context.turn_id:
        return ()
    output: list[JsonValue] = []
    for item in sorted(data.species, key=lambda value: value.identity):
        row = data.population[item.slot]
        occupied = row > 0
        labels = data.integers["connectivity"][item.slot]
        components = np.unique(labels[occupied & (labels >= 0)])
        if len(components) < 2:
            continue
        total = int(row.sum(dtype=object))
        candidates: list[tuple[float, int, dict[str, JsonValue]]] = []
        for raw_label in components:
            label = int(raw_label)
            component = labels == label
            group = occupied & component
            others = occupied & (labels >= 0) & ~component
            count = int(row[group].sum(dtype=object))
            age = int(data.integers["isolation_age"][item.slot, group].min())
            if count < MIN_POPULATION or total - count < MIN_POPULATION or age < MIN_ISOLATION:
                continue
            local = trait_mean(data.floats["deme_traits"][item.slot], row, group)
            other = trait_mean(data.floats["deme_traits"][item.slot], row, others)
            genetic = min(1.0, float(np.sqrt(np.mean((local - other) ** 2))) / GENETIC_SCALE)
            climate = {
                name: abs(
                    float(_mean(data.floats[name], row, group))
                    - float(_mean(data.floats[name], row, others))
                )
                for name in ("temperature", "humidity")
            }
            ecological = (
                min(climate["temperature"] / item.width, 1.0) + min(climate["humidity"], 1.0)
            ) / 2
            terms: dict[str, JsonValue] = {
                "geographic_isolation": 1.0,
                "ecological_divergence": ecological,
                "genetic_distance": genetic,
                "isolation_duration": min(age / 24, 1.0),
                "low_gene_flow": 1.0,
            }
            score = 0.30 + 0.25 * ecological + 0.20 * genetic + 0.15 * min(age / 24, 1.0) + 0.10
            if score < MIN_SCORE:
                continue
            key = context.seeds.stream(
                "reference_speciation_proposal",
                VERSION,
                entity=item.identity,
                purpose=f"component:{label}:lineage",
            ).key.hex()
            proposal: dict[str, JsonValue] = {
                "schema_version": 1,
                "proposal_id": f"speciation:{digest([key, 'proposal'])}",
                "child_id": f"species-{digest([key, 'child'])[:32]}",
                "parent": item.identity,
                "parent_slot": item.slot,
                "component": label,
                "tile_runs": tile_runs(component),
                "population": count,
                "remaining_population": total - count,
                "score": score,
                "cause_terms": terms,
                "isolation_turns": age,
                "traits": {name: float(local[index]) for index, name in enumerate(TRAITS)},
                "genetic_rms_scale": GENETIC_SCALE,
                "expected": context.world_version.to_dict(),
                "turn": context.turn_id,
                "evidence_hash": data.evidence_hash,
            }
            candidates.append((score, -label, proposal))
        if candidates:
            output.append(max(candidates, key=lambda item: (item[0], item[1]))[2])
    return tuple(output)


def pending(context: TurnContext) -> tuple[JsonValue, ...]:
    value = context.snapshot.domain("evolution").get("pending_speciation", ())
    if not isinstance(value, tuple):
        raise ValueError("Pending speciation must be a tuple of proposals")
    return value


def validate_scratch(context: TurnContext, data: SpeciationInputs) -> None:
    shape = data.population.shape
    unused = sorted(set(range(shape[0])) - {item.slot for item in data.species})
    empty: IntArray | FloatArray
    for name in SCRATCH:
        dimensions = (
            (len(MORTALITY_CAUSES), *shape)
            if name == "mortality"
            else (
                (shape[0], len(TRAITS))
                if name in ("selection_pressure", "fitness_gradients")
                else shape
            )
        )
        if name in (
            "predation_deaths",
            "migration_in",
            "migration_out",
            "births",
            "deaths",
            "mortality",
        ):
            integer = _integer(context, name, dimensions)
            empty = integer[:, unused] if name == "mortality" else integer[unused]
        else:
            floating = _float(context, name, dimensions, signed=name == "fitness_gradients")
            empty = floating[unused]
        if np.any(empty != 0):
            raise ValueError("Unused rows must have zero ecological scratch before speciation")


class SpeciationProposalStage(SimulationStage):
    contract = StageContract(
        "reference_speciation_proposal",
        VERSION,
        ("reference_adaptation",),
        reads=READS,
        writes=("state.evolution.pending_speciation",),
    )

    def execute(self, context: TurnContext) -> StageResult:
        super().validate_inputs(context)
        old = pending(context)
        if lifecycle_marker(context) == context.turn_id:
            if old:
                raise ValueError("Speciation already committed for this turn")
            return result(context, self.contract.name, metrics={"candidates": 0})
        proposed = proposals(context, speciation_inputs(context))
        if old and digest(old) != digest(proposed):
            raise ValueError("Stale or tampered pending speciation proposals")
        recorded = "pending_speciation" in context.snapshot.domain("evolution")
        patches = (
            ()
            if recorded and old == proposed
            else (state_patch(context, ("evolution", "pending_speciation"), proposed),)
        )
        return replace(
            result(
                context, self.contract.name, state=patches, metrics={"candidates": len(proposed)}
            ),
            evolution_proposals=proposed,
        )


class SpeciationCommitStage(SimulationStage):
    contract = StageContract(
        "reference_speciation",
        VERSION,
        ("reference_speciation_proposal",),
        reads=(*READS, "state.food_web", *(f"arrays.{name}" for name in SCRATCH)),
        writes=(
            "state.species",
            "state.food_web",
            "state.evolution.pending_speciation",
            "state.evolution.speciation_turn",
            "arrays.population",
            "arrays.energy_reserve",
            *(f"arrays.{name}" for name in GENES),
        ),
    )

    def execute(self, context: TurnContext) -> StageResult:
        from .speciation_commit import commit_speciation

        super().validate_inputs(context)
        return commit_speciation(context, self.contract.name)

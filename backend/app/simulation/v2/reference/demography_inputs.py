"""Validated cohort inputs shared by mortality, reproduction and population commit."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from ..context import TurnContext
from .common import FloatArray, IntArray, geometry, number


@dataclass(frozen=True)
class Cohort:
    identity: str
    slot: int
    mass: float
    fertility: float
    lifespan: float
    trait_cost: float
    extinct: bool


def integer_matrix(context: TurnContext, name: str) -> IntArray:
    value = context.snapshot.arrays[name].numpy()
    if value.dtype != np.dtype("int64") or value.ndim != 2:
        raise ValueError(f"{name} must be a nonnegative int64 species-by-tile matrix")
    width, height = geometry(context)
    if value.shape[1] != width * height:
        raise ValueError(f"{name} tile axis mismatch")
    converted = np.array(value, dtype=np.int64, copy=True)
    if np.any(converted < 0):
        raise ValueError(f"{name} must be nonnegative")
    return converted


def float_matrix(context: TurnContext, name: str, shape: tuple[int, ...]) -> FloatArray:
    value = context.snapshot.arrays[name].numpy()
    if value.dtype != np.dtype("float64") or value.shape != shape:
        raise ValueError(f"{name} must match float64 shape {shape}")
    converted = np.array(value, dtype=np.float64, copy=True)
    if not np.isfinite(converted).all() or np.any(converted < 0):
        raise ValueError(f"{name} must be finite and nonnegative")
    return converted


def inputs(context: TurnContext) -> tuple[IntArray, FloatArray, tuple[Cohort, ...], float]:
    pop = integer_matrix(context, "population")
    reserve = float_matrix(context, "energy_reserve", pop.shape)
    dt = number(context.environment_state["ecological_years_per_turn"], "ecological time step")
    if not 0 < dt <= 1:
        raise ValueError("Demography requires ecological time step in (0,1] years")
    cohorts: list[Cohort] = []
    slots: set[int] = set()
    for identity, value in sorted(context.species_state.items()):
        if not isinstance(value, Mapping):
            raise ValueError("Species must be a phenotype mapping")
        slot = value["slot"]
        if type(slot) is not int or not 0 <= slot < pop.shape[0] or slot in slots:
            raise ValueError("Species slots must be unique and in bounds")
        slots.add(slot)
        mass = number(value["body_mass"], "body_mass")
        fertility = number(value["fertility"], "fertility")
        lifespan = number(value["lifespan"], "lifespan")
        traits = value["traits"]
        if mass <= 0 or lifespan <= 0 or fertility < 0 or not isinstance(traits, Mapping):
            raise ValueError("Invalid life-history phenotype")
        costs = [number(trait, "trait") for trait in traits.values()]
        if any(not 0 <= cost <= 1 for cost in costs):
            raise ValueError("Trait values must be in [0,1]")
        extinct = value["status"] == "Extinct"
        if extinct and np.any(pop[slot]):
            raise ValueError("An extinct species cannot contain living individuals")
        cohorts.append(Cohort(identity, slot, mass, fertility, lifespan, sum(costs), extinct))
    for slot in set(range(pop.shape[0])) - slots:
        if np.any(pop[slot]) or np.any(reserve[slot]):
            raise ValueError("Unassigned rows cannot contain population or reserves")
    return pop, reserve, tuple(cohorts), dt


def transported(context: TurnContext, pop: IntArray) -> IntArray:
    killed = integer_matrix(context, "predation_deaths")
    incoming = integer_matrix(context, "migration_in")
    outgoing = integer_matrix(context, "migration_out")
    if any(value.shape != pop.shape for value in (killed, incoming, outgoing)):
        raise ValueError("Movement and kill ledger axes mismatch")
    if np.any(killed > pop):
        raise ValueError("Predation cannot kill more than the starting population")
    # Object arithmetic is only used to validate overflow before narrowing to int64.
    available = pop.astype(object) - killed.astype(object) + incoming - outgoing
    if np.any(available < 0) or np.any(available > np.iinfo(np.int64).max):
        raise ValueError("Movement produced invalid or overflowing population")
    if not np.array_equal(incoming.sum(axis=1, dtype=object), outgoing.sum(axis=1, dtype=object)):
        raise ValueError("Migration must conserve each species population")
    return np.asarray(available, dtype=np.int64)


def rounded(
    context: TurnContext,
    stage: str,
    cohort: Cohort,
    values: FloatArray,
    limits: IntArray,
    purpose: str,
) -> IntArray:
    if not np.isfinite(values).all() or np.any(values < 0):
        raise ValueError("Expected demographic counts must be finite and nonnegative")
    stream = context.seeds.stream(stage, "1", entity=cohort.identity, purpose=purpose)
    draws = np.fromiter((stream.uniform(tile) for tile in range(values.size)), dtype=np.float64)
    bounded = np.minimum(values, limits.astype(np.float64))
    # Python ints avoid float64 rounding INT64_MAX upward during the cast.
    return np.array(
        [
            min(int(limit), int(np.floor(value)) + int(draw < value % 1))
            for value, limit, draw in zip(bounded, limits, draws, strict=True)
        ],
        dtype=np.int64,
    )

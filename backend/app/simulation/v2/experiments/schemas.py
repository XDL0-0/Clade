"""Versioned, strict scenario inputs with explicit relative-turn semantics."""

from __future__ import annotations

import json
from typing import Annotated, Literal, NoReturn, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..values import JsonValue, freeze_mapping
from ..version import WorldVersion

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")]
Label = Annotated[str, Field(min_length=1, max_length=120, pattern=r"\S")]
Natural = Annotated[int, Field(ge=0, le=2**63 - 1)]
RelativeTurn = Annotated[int, Field(ge=1, le=1000)]


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError(f"Duplicate JSON key: {name}")
        result[name] = value
    return result


def _nonfinite(value: str) -> NoReturn:
    raise ValueError(f"Nonfinite JSON number: {value}")


class StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, allow_inf_nan=False)

    @classmethod
    def parse_json(cls, raw: str | bytes) -> Self:
        """Public JSON entry point also rejects duplicate object keys, not just turns."""
        if len(raw.encode("utf-8") if isinstance(raw, str) else raw) > 4_000_000:
            raise ValueError("Scenario JSON exceeds the 4 MB input limit")
        json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_nonfinite)
        return cls.model_validate_json(raw)


class Version(StrictModel):
    world_id: Identifier
    timeline_id: Identifier
    generation: Natural
    revision: Natural

    def value(self) -> WorldVersion:
        return WorldVersion(self.world_id, self.timeline_id, self.generation, self.revision)


class Forcing(StrictModel):
    turn: RelativeTurn
    warming_offset: Annotated[float, Field(ge=-100, le=100)] | None = None
    co2_ppm: Annotated[float, Field(ge=10, le=5000)] | None = None
    disaster_severity: Annotated[float, Field(ge=0, le=1)] | None = None
    disease_pressure: Annotated[float, Field(ge=0, le=1)] | None = None

    @model_validator(mode="after")
    def nonempty(self) -> Self:
        if all(getattr(self, key) is None for key in type(self).model_fields if key != "turn"):
            raise ValueError("A forcing must set at least one allowed parameter")
        return self


class Scenario(StrictModel):
    version: Literal[1]
    id: Identifier
    name: Label
    description: Annotated[str, Field(max_length=2000)] = ""
    forcing: Annotated[tuple[Forcing, ...], Field(max_length=1000)] = ()

    @field_validator("version", mode="before")
    @classmethod
    def integer_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("Scenario version must be the integer 1")
        return value

    @model_validator(mode="after")
    def unique_turns(self) -> Self:
        if len({point.turn for point in self.forcing}) != len(self.forcing):
            raise ValueError("Only one forcing entry per relative turn is allowed")
        object.__setattr__(self, "forcing", tuple(sorted(self.forcing, key=lambda p: p.turn)))
        return self

    def parameters(self, relative_turn: int) -> dict[str, JsonValue]:
        """Persistent environment settings are sparse; turn-local pressures reset.

        turn=1 is the first simulation turn after the chosen source snapshot.
        An omitted warming/CO2 update inherits the current environment (initially
        the source), while an omitted disaster/disease is explicitly zero.
        """
        if type(relative_turn) is not int or not 1 <= relative_turn <= 1000:
            raise ValueError("Relative turn must lie in [1,1000]")
        values: dict[str, JsonValue] = {"disaster_severity": 0.0, "disease_pressure": 0.0}
        for point in self.forcing:
            if point.turn == relative_turn:
                values.update(freeze_mapping(point.model_dump(exclude={"turn"}, exclude_none=True)))
                break
        return values


class Branch(StrictModel):
    id: Identifier
    name: Label
    scenario: Scenario


class ExperimentPlan(StrictModel):
    version: Literal[1]
    id: Identifier
    name: Label
    description: Annotated[str, Field(max_length=2000)] = ""
    source: Version
    turns: RelativeTurn
    rng_namespace: Annotated[str, Field(min_length=1, max_length=128, pattern=r"\S")]
    control: Identifier
    branches: Annotated[tuple[Branch, ...], Field(min_length=2, max_length=8)]

    @field_validator("version", mode="before")
    @classmethod
    def integer_version(cls, value: object) -> object:
        return Scenario.integer_version(value)

    @model_validator(mode="after")
    def valid_branches(self) -> Self:
        by_id = {branch.id: branch for branch in self.branches}
        if len(by_id) != len(self.branches):
            raise ValueError("Experiment branch IDs must be unique")
        if self.control not in by_id or by_id[self.control].scenario.forcing:
            raise ValueError("Control must name a branch with an empty forcing plan")
        if any(point.turn > self.turns for b in self.branches for point in b.scenario.forcing):
            raise ValueError("Forcing extends past the experiment horizon")
        object.__setattr__(self, "branches", tuple(sorted(self.branches, key=lambda b: b.id)))
        return self

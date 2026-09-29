"""Strict transport commands, with explicit textual parsing for URL queries."""

from __future__ import annotations

import re
from typing import Annotated, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from app.simulation.v2.version import WorldVersion

ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"
MAX_INTEGER = 2**63 - 1
Identifier = Annotated[str, Field(pattern=ID_PATTERN, min_length=1, max_length=64)]
Natural = Annotated[int, Field(ge=0, le=MAX_INTEGER)]
Key = Annotated[str, Field(min_length=1, max_length=128, pattern=r"\S")]


class StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", allow_inf_nan=False)


class Version(StrictModel):
    world_id: Identifier
    timeline_id: Identifier
    generation: Natural
    revision: Natural

    def value(self) -> WorldVersion:
        return WorldVersion(self.world_id, self.timeline_id, self.generation, self.revision)


class CreateWorld(StrictModel):
    world_id: Identifier
    timeline_id: Identifier = "main"
    seed: Natural = 0
    width: Annotated[int, Field(ge=2, le=256)] = 16
    height: Annotated[int, Field(ge=1, le=256)] = 8
    max_species: Annotated[int, Field(ge=7, le=256)] = 32

    @model_validator(mode="after")
    def geometry_budget(self) -> Self:
        if self.width % 2:
            raise ValueError("Cylindrical topology requires even width")
        if self.width * self.height * self.max_species > 500_000:
            raise ValueError("Species-by-tile capacity exceeds 500000")
        return self


class AdvanceTurn(StrictModel):
    expected_version: Version
    idempotency_key: Key
    warming_offset: Annotated[float, Field(ge=-100, le=100)] | None = None
    co2_ppm: Annotated[float, Field(ge=10, le=5000)] | None = None
    disaster_severity: Annotated[float, Field(ge=0, le=1)] | None = None
    disease_pressure: Annotated[float, Field(ge=0, le=1)] | None = None
    rng_namespace: Key | None = None


class ForkTimeline(StrictModel):
    parent: Version
    child_timeline_id: Identifier


class Rewind(StrictModel):
    expected_version: Version
    source_version: Version
    idempotency_key: Key


def query_integer(value: object) -> object:
    if isinstance(value, str):
        if not re.fullmatch(r"-?(0|[1-9][0-9]*)", value) or len(value) > 20:
            raise ValueError("Expected a decimal integer")
        return int(value)
    return value


def query_boolean(value: object) -> object:
    if isinstance(value, str):
        if value not in ("true", "false"):
            raise ValueError("Expected true or false")
        return value == "true"
    return value


QueryNatural = Annotated[int, BeforeValidator(query_integer), Field(ge=0, le=MAX_INTEGER)]
Limit = Annotated[int, BeforeValidator(query_integer), Field(ge=1, le=1000)]


class Page(StrictModel):
    limit: Limit = 100
    offset: Annotated[int, BeforeValidator(query_integer), Field(ge=0, le=1_000_000)] = 0


class AtTurn(StrictModel):
    turn: QueryNatural | None = None


class MetricPage(StrictModel):
    generation: QueryNatural | None = None
    after_revision: Annotated[
        int, BeforeValidator(query_integer), Field(ge=-1, le=MAX_INTEGER)
    ] = -1
    limit: Limit = 100


class EventPage(StrictModel):
    after: QueryNatural = 0
    limit: Limit = 100


class StreamPage(EventPage):
    follow: Annotated[bool, BeforeValidator(query_boolean)] = True


class NarrativePage(AtTurn):
    species_id: Identifier | None = None
    limit: Annotated[int, BeforeValidator(query_integer), Field(ge=1, le=100)] = 20
    offset: Annotated[int, BeforeValidator(query_integer), Field(ge=0, le=1_000_000)] = 0

"""Strict, bounded presentation-only schemas and frozen-target verification."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.simulation.v2.values import JsonValue, canonical_bytes, digest, freeze_mapping

from .models import MAX_INPUT_BYTES, JobKind, JobSpec

Identifier = Annotated[
    str, StringConstraints(strict=True, min_length=1, max_length=128, pattern=r"^\S(?:.*\S)?$")
]
Name = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=120)]
LatinName = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=160)]
Description = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=2000)]
Summary = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=1000)]
EventIDs = Annotated[tuple[Identifier, ...], Field(max_length=64)]


class NarrativeModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class OrganNarrative(NarrativeModel):
    organ_id: Identifier
    description: Description


class ChildNarrative(NarrativeModel):
    child_id: Identifier
    common_name: Name
    latin_name: LatinName
    description: Description


class SpeciesNarrativeResult(NarrativeModel):
    job_type: Literal["species"]
    species_id: Identifier
    common_name: Name
    latin_name: LatinName
    description: Description
    organ_descriptions: Annotated[tuple[OrganNarrative, ...], Field(max_length=32)]
    source_event_ids: EventIDs


class SpeciationNarrativeResult(NarrativeModel):
    job_type: Literal["speciation"]
    proposal_id: Identifier
    children: Annotated[tuple[ChildNarrative, ...], Field(min_length=2, max_length=16)]
    explanation: Description = Field(
        description="Narrative interpretation of frozen events; do not invent numeric causes."
    )
    source_event_ids: EventIDs


class AdaptationNarrativeResult(NarrativeModel):
    job_type: Literal["adaptation"]
    proposal_id: Identifier
    species_id: Identifier
    summary: Summary
    tradeoff_explanation: Description = Field(
        description="Narrative interpretation; numerical effects come only from frozen trace."
    )
    source_event_ids: EventIDs


class HybridizationNarrativeResult(NarrativeModel):
    job_type: Literal["hybridization"]
    proposal_id: Identifier
    child: ChildNarrative
    parent_narrative: Description = Field(
        description="Narrative interpretation of the frozen parents; do not decide parentage."
    )
    source_event_ids: EventIDs


NarrativeResult: TypeAlias = (
    SpeciesNarrativeResult
    | SpeciationNarrativeResult
    | AdaptationNarrativeResult
    | HybridizationNarrativeResult
)
_MODELS: dict[JobKind, type[NarrativeResult]] = {
    "species": SpeciesNarrativeResult,
    "speciation": SpeciationNarrativeResult,
    "adaptation": AdaptationNarrativeResult,
    "hybridization": HybridizationNarrativeResult,
}


def result_schema(job_type: JobKind) -> dict[str, object]:
    """Return the JSON Schema supplied to a structured-output provider."""
    return _MODELS[job_type].model_json_schema()


def _same_ids(actual: tuple[str, ...], expected: tuple[str, ...], label: str) -> None:
    if len(set(actual)) != len(actual) or set(actual) != set(expected):
        raise ValueError(f"{label} must exactly match the frozen targets without duplicates")


def validate_result(spec: JobSpec, result: object) -> Mapping[str, JsonValue]:
    """Validate raw JSON or a JSON object, then return an immutable presentation record.

    JSON arrays are the wire representation of immutable tuple fields. No substring
    extraction, coercion, arbitrary Python parsing, or unvalidated fields are allowed.
    """
    if spec.schema_version != "1":
        raise ValueError("Unsupported narrative schema version")
    encoded = result.encode("utf-8") if isinstance(result, str) else canonical_bytes(result)
    if len(encoded) > MAX_INPUT_BYTES:
        raise ValueError("Narrative result exceeds the maximum encoded size")
    narrative = _MODELS[spec.job_type].model_validate_json(encoded, strict=True)
    events = narrative.source_event_ids
    if len(set(events)) != len(events) or not set(events).issubset(spec.source_event_ids):
        raise ValueError("source_event_ids must be a duplicate-free subset of frozen events")
    if isinstance(narrative, (SpeciesNarrativeResult, AdaptationNarrativeResult)):
        _same_ids((narrative.species_id,), spec.target_ids, "species_id")
    if isinstance(narrative, SpeciesNarrativeResult):
        organ_ids = spec.payload.get("organ_ids", ())
        assert isinstance(organ_ids, tuple)  # JobSpec validates and freezes this field.
        _same_ids(
            tuple(item.organ_id for item in narrative.organ_descriptions),
            tuple(str(item) for item in organ_ids),
            "organ_ids",
        )
    else:
        if narrative.proposal_id != spec.proposal_id:
            raise ValueError("proposal_id must match the frozen proposal")
        if isinstance(narrative, SpeciationNarrativeResult):
            _same_ids(
                tuple(child.child_id for child in narrative.children), spec.target_ids, "children"
            )
        if isinstance(narrative, HybridizationNarrativeResult):
            _same_ids((narrative.child.child_id,), spec.target_ids, "child")
    return freeze_mapping(narrative.model_dump(mode="json"))


def _child(target_id: str) -> dict[str, object]:
    suffix = digest(target_id)[:12]
    return {
        "child_id": target_id,
        "common_name": f"Species {suffix}",
        "latin_name": f"Clade {suffix}",
        "description": f"Recorded species {target_id}. Narrative enrichment is unavailable.",
    }


def fallback_result(spec: JobSpec) -> Mapping[str, JsonValue]:
    """Stable names/templates depend solely on frozen IDs, never clocks or randomness."""
    result: dict[str, object] = {
        "job_type": spec.job_type,
        "source_event_ids": spec.source_event_ids,
    }
    if spec.job_type == "species":
        child = _child(spec.target_ids[0])
        child.pop("child_id")
        organs = spec.payload.get("organ_ids", ())
        assert isinstance(organs, tuple)
        result.update(
            child,
            species_id=spec.target_ids[0],
            organ_descriptions=[
                {"organ_id": organ, "description": f"Recorded organ {organ}."} for organ in organs
            ],
        )
    else:
        result["proposal_id"] = spec.proposal_id
        if spec.job_type == "speciation":
            result.update(
                children=[_child(target) for target in spec.target_ids],
                explanation="Narrative interpretation: the frozen events record speciation.",
            )
        elif spec.job_type == "adaptation":
            result.update(
                species_id=spec.target_ids[0],
                summary="The frozen events record an adaptation.",
                tradeoff_explanation=(
                    "Narrative interpretation unavailable; consult the event trace."
                ),
            )
        else:
            result.update(
                child=_child(spec.target_ids[0]),
                parent_narrative=(
                    "Narrative interpretation: the frozen events record hybridization."
                ),
            )
    return validate_result(spec, result)

"""Acceptance coverage for immutable identities and presentation-only contracts."""

from __future__ import annotations

import json
from collections.abc import Mapping, MutableMapping
from dataclasses import FrozenInstanceError, replace
from typing import cast

import pytest
from pydantic import ValidationError

from app.ai.jobs import AIJob, JobKind, JobSpec, fallback_result, result_schema, validate_result
from app.simulation.v2.values import JsonValue, canonical_bytes, thaw
from app.simulation.v2.version import WorldVersion


def spec(kind: JobKind = "species") -> JobSpec:
    return JobSpec(
        WorldVersion("world", "timeline", 3, 19),
        turn_id=9,
        job_type=kind,
        target_ids=("child-a", "child-b") if kind == "speciation" else ("species-a",),
        source_event_ids=("event-a", "event-b"),
        payload={"organ_ids": ("organ-a",)} if kind == "species" else {},
        proposal_id=None if kind == "species" else "proposal-a",
        snapshot_id="snapshot-a",
    )


def mutable_result(job_spec: JobSpec) -> dict[str, object]:
    return cast(dict[str, object], thaw(fallback_result(job_spec)))


def test_freezes_payload_copies_and_round_trips_plain_json() -> None:
    payload: dict[str, object] = {"trace": {"events": [1, 2]}}
    original = replace(spec(), payload=cast(Mapping[str, JsonValue], payload))
    initial_hash = original.input_hash
    payload.clear()
    assert original.input_hash == initial_hash
    assert original.payload == {"trace": {"events": (1, 2)}}
    with pytest.raises(TypeError):
        cast(MutableMapping[str, JsonValue], original.payload)["new"] = 1
    frozen_attribute = "turn_id"
    with pytest.raises(FrozenInstanceError):
        setattr(original, frozen_attribute, 100)
    job = AIJob(original, result=fallback_result(original))
    restored = AIJob.from_dict(json.loads(json.dumps(job.to_dict())))
    assert restored == job
    assert restored.spec.job_id == original.job_id
    assert restored.spec.input_hash == initial_hash
    with pytest.raises(TypeError):
        cast(MutableMapping[str, JsonValue], restored.result)["population"] = 1


def test_stable_job_identity_retains_input_conflict_and_scopes_every_generation() -> None:
    original = spec()
    assert JobSpec.from_dict(original.to_dict()).job_id == original.job_id
    changed = replace(original, payload={"new": "frozen facts"})
    assert changed.job_id == original.job_id
    assert changed.idempotency_scope == original.idempotency_scope
    assert changed.input_hash != original.input_hash
    for version in (
        WorldVersion("other-world", "timeline", 3, 19),
        WorldVersion("world", "other-timeline", 3, 19),
        WorldVersion("world", "timeline", 4, 19),
    ):
        changed = replace(original, expected_world_version=version)
        assert changed.job_id != original.job_id
        assert changed.input_hash != original.input_hash
    advanced = replace(original, expected_world_version=original.expected_world_version.advance())
    # Explicitly retaining a key retains its scope; hash detects the different revision.
    assert advanced.input_hash != original.input_hash
    derived = replace(advanced, idempotency_key="")
    assert derived.job_id != original.job_id


@pytest.mark.parametrize("kind", ["species", "speciation", "adaptation", "hybridization"])
def test_every_schema_is_strict_bounded_and_its_fallback_is_valid(kind: JobKind) -> None:
    job_spec = spec(kind)
    result = fallback_result(job_spec)
    assert validate_result(job_spec, canonical_bytes(result).decode()) == result
    assert fallback_result(JobSpec.from_dict(job_spec.to_dict())) == result
    schema = result_schema(kind)
    assert schema["additionalProperties"] is False
    for forbidden in ("population", "traits", "status", "fitness", "trait_changes"):
        invalid = mutable_result(job_spec)
        invalid[forbidden] = 1
        with pytest.raises(ValidationError):
            validate_result(job_spec, invalid)


@pytest.mark.parametrize("kind", ["speciation", "hybridization"])
def test_child_identity_count_and_nested_extra_fields_are_rejected(kind: JobKind) -> None:
    job_spec = spec(kind)
    record = mutable_result(job_spec)
    children = cast(list[dict[str, object]], record.get("children", [record.get("child")]))
    children[0]["population"] = 5
    with pytest.raises(ValidationError):
        validate_result(job_spec, record)
    children[0].pop("population")
    children[0]["child_id"] = "foreign-child"
    with pytest.raises(ValueError, match="frozen targets"):
        validate_result(job_spec, record)
    if kind == "speciation":
        record = mutable_result(job_spec)
        children = cast(list[dict[str, object]], record["children"])
        children[1]["child_id"] = children[0]["child_id"]
        with pytest.raises(ValueError, match="duplicates"):
            validate_result(job_spec, record)
        children.append(dict(children[0], child_id="extra-child"))
        with pytest.raises(ValueError, match="frozen targets"):
            validate_result(job_spec, record)


@pytest.mark.parametrize("events", [["unknown"], ["event-a", "event-a"], [1]])
def test_invalid_source_events_are_rejected(events: list[object]) -> None:
    record = mutable_result(spec())
    record["source_event_ids"] = events
    with pytest.raises(ValueError):
        validate_result(spec(), record)


def test_frozen_target_proposal_and_organ_ids_are_checked() -> None:
    species = spec()
    record = mutable_result(species)
    record["species_id"] = "foreign-species"
    with pytest.raises(ValueError, match="species_id"):
        validate_result(species, record)
    record = mutable_result(species)
    record["organ_descriptions"] = [{"organ_id": "unknown", "description": "Text"}]
    with pytest.raises(ValueError, match="organ_ids"):
        validate_result(species, record)
    record["organ_descriptions"] = []
    with pytest.raises(ValueError, match="organ_ids"):
        validate_result(species, record)
    adaptation = spec("adaptation")
    record = mutable_result(adaptation)
    record["proposal_id"] = "foreign-proposal"
    with pytest.raises(ValueError, match="proposal_id"):
        validate_result(adaptation, record)


@pytest.mark.parametrize(
    ("field", "value"),
    [("description", "x" * 2001), ("common_name", 17), ("job_type", "adaptation")],
)
def test_strings_are_bounded_and_values_are_not_coerced(field: str, value: object) -> None:
    record = mutable_result(spec())
    record[field] = value
    with pytest.raises(ValidationError):
        validate_result(spec(), record)


def test_json_must_be_one_complete_document_and_unknown_schema_is_rejected() -> None:
    valid = canonical_bytes(fallback_result(spec())).decode()
    for invalid in (f"```json\n{valid}\n```", f"prefix {valid}", valid + " trailing", "{}"):
        with pytest.raises(ValidationError):
            validate_result(spec(), invalid)
    with pytest.raises(ValueError, match="schema version"):
        validate_result(replace(spec(), schema_version="999"), valid)


def test_event_subset_is_allowed_and_result_cannot_mutate() -> None:
    record = mutable_result(spec())
    record["source_event_ids"] = ["event-b"]
    validated = validate_result(spec(), record)
    record["common_name"] = "changed"
    assert validated["common_name"] != "changed"
    with pytest.raises(TypeError):
        cast(MutableMapping[str, JsonValue], validated)["common_name"] = "changed"


@pytest.mark.parametrize("missing", ["world_id", "timeline_id", "generation", "revision"])
def test_serialized_expected_version_cannot_omit_any_identity_component(missing: str) -> None:
    record = spec().to_dict()
    version = cast(dict[str, object], record["expected_world_version"])
    version.pop(missing)
    with pytest.raises(ValueError, match="complete identity"):
        JobSpec.from_dict(record)


@pytest.mark.parametrize(
    "changes",
    [
        {"turn_id": True},
        {"target_ids": ()},
        {"target_ids": ("a", "b")},
        {"source_event_ids": ("duplicate", "duplicate")},
        {"payload": {"organ_ids": ("duplicate", "duplicate")}},
        {"payload": {"opaque": object()}},
        {"payload": {"number": float("nan")}},
        {"payload": {"huge": "x" * 65_537}},
        {"idempotency_key": False},
    ],
)
def test_invalid_frozen_inputs_are_rejected(changes: dict[str, object]) -> None:
    record = spec().to_dict()
    record.update(changes)
    with pytest.raises((TypeError, ValueError)):
        JobSpec.from_dict(record)

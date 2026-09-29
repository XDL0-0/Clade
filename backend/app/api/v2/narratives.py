"""Verified semantic commit history, including explicit rewind source boundaries.

Ordinary/fork commits follow parent_id. A replacement's parent_id is an audit
edge into the abandoned head; its verified command source is the semantic edge.
Pages contain whole annotated commits, never a partial commit's annotations.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import cast

from app.ai.jobs.models import AIJob, JobStatus
from app.ai.jobs.schemas import validate_result
from app.simulation.v2.context import WorldSnapshot
from app.simulation.v2.values import JsonValue, digest
from app.simulation.v2.version import WorldVersion
from app.storage.codec import decode, version_from
from app.storage.database import StorageCorruption
from app.storage.history import commit_id, record_hash
from app.storage.store import WorldStore


def _bounded(value: int, name: str, low: int, high: int) -> None:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer in [{low}, {high}]")


def _version(value: JsonValue) -> WorldVersion:
    if not isinstance(value, Mapping) or set(value) != {
        "world_id",
        "timeline_id",
        "generation",
        "revision",
    }:
        raise StorageCorruption("A history source requires a complete world version")
    return version_from(value)


def checked_commit(connection: sqlite3.Connection, identity: str, world: str) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM commits WHERE commit_id=?", (identity,)).fetchone()
    if row is None:
        raise StorageCorruption("Missing narrative ancestor commit")
    if row["world_id"] != world or commit_id(version_from(dict(row))) != identity:
        raise StorageCorruption("Narrative ancestry crossed a world or invalid commit identity")
    if record_hash(row) != row["record_hash"]:
        raise StorageCorruption("Narrative ancestor record checksum mismatch")
    if type(row["turn_id"]) is not int or row["turn_id"] < 0 or row["is_checkpoint"] not in (0, 1):
        raise StorageCorruption("Invalid narrative ancestor metadata")
    return cast(sqlite3.Row, row)


def selected_commit(connection: sqlite3.Connection, snapshot: WorldSnapshot) -> sqlite3.Row:
    row = checked_commit(connection, commit_id(snapshot.version), snapshot.version.world_id)
    if row["state_hash"] != snapshot.state_hash or row["turn_id"] != snapshot.turn_id:
        raise StorageCorruption("Selected snapshot does not match its committed identity")
    return row


def _parent(connection: sqlite3.Connection, row: sqlite3.Row) -> str | None:
    version = version_from(dict(row))
    commands = connection.execute(
        "SELECT * FROM commands WHERE commit_id=?", (row["commit_id"],)
    ).fetchall()
    parent_id = row["parent_id"]
    if parent_id is None:
        if (
            commands
            or row["command_key"] is not None
            or row["base_hash"] is not None
            or not row["is_checkpoint"]
            or version.generation
            or version.revision
        ):
            raise StorageCorruption("Malformed narrative genesis")
        return None
    parent = checked_commit(connection, parent_id, version.world_id)
    parent_version = version_from(dict(parent))
    if row["base_hash"] != parent["state_hash"]:
        raise StorageCorruption("Narrative ancestor state hash chain mismatch")
    if row["command_key"] is None:
        if (
            commands
            or version.generation
            or version.revision
            or version.timeline_id == parent_version.timeline_id
            or row["turn_id"] != parent["turn_id"]
            or row["state_hash"] != parent["state_hash"]
        ):
            raise StorageCorruption("Malformed narrative fork boundary")
        return cast(str, parent_id)
    if len(commands) != 1:
        raise StorageCorruption("Missing or ambiguous narrative commit command")
    command = commands[0]
    payload = decode(command["input_payload"])
    if digest(payload) != command["input_hash"] or command["command_key"] != row["command_key"]:
        raise StorageCorruption("Narrative command checksum or association mismatch")
    expected = _version(payload.get("expected"))
    if expected != parent_version or (
        command["world_id"],
        command["timeline_id"],
        command["generation"],
    ) != (expected.world_id, expected.timeline_id, expected.generation):
        raise StorageCorruption(
            "Narrative command expected version disagrees with its audit parent"
        )
    if payload.get("kind") == "replace":
        if (
            set(payload) != {"kind", "expected", "source"}
            or version != expected.advance(replace_generation=True)
            or not row["is_checkpoint"]
        ):
            raise StorageCorruption("Malformed narrative replacement boundary")
        source = _version(payload.get("source"))
        restored = checked_commit(connection, commit_id(source), version.world_id)
        if row["turn_id"] != restored["turn_id"] or row["state_hash"] != restored["state_hash"]:
            raise StorageCorruption("Narrative replacement source does not match restored state")
        return commit_id(source)
    if (
        "kind" in payload
        or version != expected.advance()
        or row["turn_id"] != parent["turn_id"] + 1
        or payload.get("start")
        != digest([parent_version.to_dict(), parent["turn_id"], parent["state_hash"]])
    ):
        raise StorageCorruption("Malformed narrative turn boundary")
    return cast(str, parent_id)


def checked_job(row: sqlite3.Row) -> AIJob:
    job = AIJob.from_dict(decode(row["job_payload"]))
    version = job.spec.expected_world_version
    if (
        job.job_id != row["job_id"]
        or job.spec.input_hash != row["input_hash"]
        or job.status.value != row["status"]
        or (version.world_id, version.timeline_id, version.generation, job.spec.idempotency_key)
        != (row["world_id"], row["timeline_id"], row["generation"], row["idempotency_key"])
    ):
        raise StorageCorruption("Stored narrative job identity or checksum mismatch")
    return job


def _annotations(
    connection: sqlite3.Connection, commit: sqlite3.Row, species_id: str | None
) -> tuple[JsonValue, ...]:
    rows = connection.execute(
        "SELECT a.job_id,a.narrative_revision,a.payload AS annotation_payload,"
        "j.payload AS job_payload,j.world_id,j.timeline_id,j.generation,j.idempotency_key,"
        "j.input_hash,j.status FROM annotations a LEFT JOIN ai_jobs j ON j.job_id=a.job_id "
        "WHERE a.commit_id=? ORDER BY a.narrative_revision DESC,a.job_id",
        (commit["commit_id"],),
    ).fetchall()
    version = version_from(dict(commit))
    source_ids = (
        {
            row["event_id"]
            for row in connection.execute(
                "SELECT event_id FROM events WHERE commit_id=?", (commit["commit_id"],)
            )
        }
        if rows
        else set()
    )
    items: list[JsonValue] = []
    revisions: set[int] = set()
    for row in rows:
        if row["job_payload"] is None:
            raise StorageCorruption("Annotation has no durable job")
        job = checked_job(row)
        if job.status != JobStatus.APPLIED:
            continue
        spec = job.spec
        if (
            spec.expected_world_version != version
            or spec.turn_id != commit["turn_id"]
            or spec.snapshot_id
            != digest([version.to_dict(), commit["turn_id"], commit["state_hash"]])
            or not set(spec.source_event_ids) <= source_ids
        ):
            raise StorageCorruption("Applied annotation does not belong to this immutable commit")
        revision = row["narrative_revision"]
        if type(revision) is not int or revision < 1 or revision in revisions:
            raise StorageCorruption("Invalid annotation revision")
        revisions.add(revision)
        validated = validate_result(spec, decode(row["annotation_payload"]))
        if job.result is None or validated != validate_result(spec, job.result):
            raise StorageCorruption("Annotation differs from the applied job result")
        if species_id is None or species_id in spec.target_ids:
            items.append(
                {
                    "job_id": job.job_id,
                    "version": version.to_dict(),
                    "turn": commit["turn_id"],
                    "narrative_revision": revision,
                    "fallback_used": job.fallback_used,
                    "source": (
                        "offline_template"
                        if job.lease_owner == "reference-offline-template"
                        else "fallback_template"
                        if job.fallback_used
                        else "unspecified_provider"
                    ),
                    "result": validated,
                }
            )
    return tuple(items)


def narrative_history(
    store: WorldStore,
    snapshot: WorldSnapshot,
    *,
    species_id: str | None = None,
    limit: int = 20,
    offset: int = 0,
    max_scan: int = 500,
) -> Mapping[str, JsonValue]:
    """Return complete annotated-commit groups, newest semantic ancestor first.

    Offset counts all semantic commits, including empty ones. Scanned counts only
    this page's window after offset; skipped ancestors are revalidated to resolve
    the cursor. max_scan bounds the window, not this necessary offset traversal.
    Truncated means another semantic commit remains, due to either page limit.
    """
    _bounded(limit, "limit", 1, 100)
    _bounded(offset, "offset", 0, 1_000_000)
    _bounded(max_scan, "max_scan", 1, 1000)
    if species_id is not None and (
        not isinstance(species_id, str) or not species_id.strip() or len(species_id) > 128
    ):
        raise ValueError("species_id must be bounded, nonempty text")
    scanned = skipped = 0
    groups: list[JsonValue] = []
    seen: set[str] = set()
    identity: str | None = commit_id(snapshot.version)
    try:
        with store.db.connection() as connection:
            connection.execute("BEGIN")  # One read snapshot for commit, job and annotation checks.
            selected_commit(connection, snapshot)
            while identity is not None:
                if identity in seen:
                    raise StorageCorruption("Cyclic semantic narrative ancestry")
                seen.add(identity)
                row = checked_commit(connection, identity, snapshot.version.world_id)
                next_identity = _parent(connection, row)
                if next_identity in seen:
                    raise StorageCorruption("Cyclic semantic narrative ancestry")
                if skipped < offset:
                    skipped += 1
                else:
                    annotations = _annotations(connection, row, species_id)
                    scanned += 1
                    if annotations:
                        groups.append(
                            {
                                "version": version_from(dict(row)).to_dict(),
                                "turn": row["turn_id"],
                                "annotations": annotations,
                            }
                        )
                identity = next_identity
                if scanned >= max_scan or len(groups) >= limit:
                    break
    except StorageCorruption:
        raise
    except (KeyError, TypeError, ValueError, sqlite3.DatabaseError) as error:
        raise StorageCorruption("Malformed stored narrative history") from error
    return {
        "version": snapshot.version.to_dict(),
        "turn": snapshot.turn_id,
        "species_id": species_id,
        "page_unit": "annotated_commit",
        "items": tuple(groups),
        "scanned": scanned,
        "next_offset": offset + scanned if identity is not None else None,
        "truncated": identity is not None,
    }

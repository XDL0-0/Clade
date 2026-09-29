"""Read-only presentation diagnostics, separate from numerical state and metrics."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from app.ai.jobs.models import JobStatus
from app.simulation.v2.context import WorldSnapshot
from app.simulation.v2.values import JsonValue
from app.storage.database import StorageCorruption
from app.storage.store import WorldStore

from .narratives import checked_job, selected_commit
from .narratives import narrative_history as narrative_history


def _rss_bytes() -> int | None:
    try:
        resident = int(Path("/proc/self/statm").read_text().split()[1])
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        return resident * page_size if resident >= 0 and page_size > 0 else None
    except (OSError, ValueError, IndexError, AttributeError):
        return None


def _save_bytes(root: Path) -> tuple[int, int]:
    total = skipped = 0
    pending = [root]
    while pending:
        try:
            with os.scandir(pending.pop()) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        skipped += 1
        except OSError:
            skipped += 1
    return total, skipped


def runtime_diagnostics(store: WorldStore, snapshot: WorldSnapshot) -> Mapping[str, JsonValue]:
    """Current process memory, whole-directory disk use, exact-version queue counts."""
    try:
        population = snapshot.arrays["population"].numpy()
        if (
            population.dtype != np.dtype("int64")
            or population.ndim != 2
            or np.any(np.less(population, 0))
        ):
            raise StorageCorruption("Invalid diagnostic population axes or counts")
        metadata = snapshot.domain("species")
        slots: set[int] = set()
        active = 0
        for value in metadata.values():
            if not isinstance(value, Mapping):
                raise StorageCorruption("Invalid diagnostic species metadata")
            slot = value.get("slot")
            if type(slot) is not int or not 0 <= slot < population.shape[0] or slot in slots:
                raise StorageCorruption("Invalid or duplicate diagnostic species slot")
            slots.add(slot)
            living = bool(np.any(population[slot]))
            if living and value.get("status") == "Extinct":
                raise StorageCorruption("Extinct species has living diagnostic population")
            active += int(living)
        if any(np.any(population[row]) for row in set(range(population.shape[0])) - slots):
            raise StorageCorruption("Unassigned diagnostic population row is occupied")
        counts = dict.fromkeys((status.value for status in JobStatus), 0)
        version = snapshot.version
        with store.db.connection() as connection:
            connection.execute("BEGIN")
            selected_commit(connection, snapshot)
            rows = connection.execute(
                "SELECT *,payload AS job_payload FROM ai_jobs "
                "WHERE world_id=? AND timeline_id=? AND generation=?",
                (version.world_id, version.timeline_id, version.generation),
            ).fetchall()
            for row in rows:
                job = checked_job(row)
                if job.spec.expected_world_version == version:
                    if (
                        job.spec.snapshot_id != snapshot.snapshot_id
                        or job.spec.turn_id != snapshot.turn_id
                    ):
                        raise StorageCorruption(
                            "Diagnostic job does not match its selected snapshot"
                        )
                    counts[job.status.value] += 1
    except StorageCorruption:
        raise
    except (KeyError, TypeError, ValueError, sqlite3.DatabaseError) as error:
        raise StorageCorruption("Malformed diagnostic input") from error
    size, skipped = _save_bytes(store.root)
    return {
        "version": version.to_dict(),
        "turn": snapshot.turn_id,
        "snapshot_id": snapshot.snapshot_id,
        "observed_at": datetime.now(UTC).isoformat(),
        "scope": {
            "rss_bytes": "current_process",
            "save_bytes": "entire_store_directory",
            "simulation": "selected_snapshot",
            "ai_jobs": "selected_snapshot_full_version",
        },
        "rss_bytes": _rss_bytes(),
        "gpu_memory_bytes": None,
        "gpu_note": "The reference simulator uses CPU; GPU memory is not measured.",
        "save_bytes": size,
        "save_bytes_approximate": True,
        "save_scan_skipped": skipped,
        "species_count": len(metadata),
        "active_species_count": active,
        "population_habitat_records": int(np.count_nonzero(population)),
        "species_capacity": int(population.shape[0]),
        "ai_jobs": counts,
        "ai_token_usage": None,
        "ai_token_usage_note": "The provider did not report token usage.",
    }

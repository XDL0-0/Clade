"""Drain a bounded batch of existing jobs using validated offline templates.

python -m scripts.run_narrative_jobs --root PATH_TO_EXISTING_SAVE --limit 4
No model provider is configured or called. Only job records and annotations change.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from app.ai.jobs.models import AIJob, JobSpec
from app.ai.jobs.schemas import fallback_result
from app.ai.jobs.worker import NarrativeWorker
from app.simulation.v2.values import JsonValue, canonical_bytes
from app.storage.jobs import SQLiteJobRepository
from app.storage.store import WorldStore


class TemplateNarrativeProvider:
    async def generate(
        self,
        *,
        schema: Mapping[str, object],
        payload: Mapping[str, JsonValue],
        repair_error: str | None = None,
    ) -> str:
        """The existing strict fallback schema is also a zero-cost local provider."""
        return canonical_bytes(fallback_result(JobSpec.from_dict(payload))).decode("utf-8")


async def run_jobs(root: Path, *, limit: int = 4) -> tuple[AIJob, ...]:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer from 1 to 100")
    if not (root / "world.sqlite").is_file() or not (root / "metadata.json").is_file():
        raise ValueError("root must point to an existing versioned world save")
    store = WorldStore(root)
    worker = NarrativeWorker(SQLiteJobRepository(store.db), TemplateNarrativeProvider())
    completed: list[AIJob] = []
    for _ in range(limit):
        job = await worker.run_once("reference-offline-template")
        if job is None:
            break
        completed.append(job)
    return tuple(completed)


def _limit(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 100:
        raise argparse.ArgumentTypeError("limit must be between 1 and 100")
    return number


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--limit", type=_limit, default=4)
    args = parser.parse_args(argv)
    try:
        jobs = asyncio.run(run_jobs(args.root, limit=args.limit))
    except (KeyboardInterrupt, asyncio.CancelledError):
        return 0
    except ValueError as error:
        parser.error(str(error))
    print(
        json.dumps(
            {
                "provider": "offline-template",
                "processed": len(jobs),
                "jobs": [{"job_id": job.job_id, "status": job.status.value} for job in jobs],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

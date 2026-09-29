"""Storage/AI stress gate, not an ecological stability benchmark.

Run from backend: python -m scripts.benchmark_v2_storage --turns 1000 --output report.json
The temporary world is isolated and removed after metrics have been collected.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import statistics
import tempfile
import time
import tracemalloc
from dataclasses import replace
from pathlib import Path

import numpy as np

from app.ai.jobs.models import JobSpec
from app.ai.jobs.schemas import fallback_result
from app.simulation.v2.context import TurnContext, WorldSnapshot
from app.simulation.v2.values import FrozenArray
from app.simulation.v2.version import WorldVersion
from app.storage.jobs import SQLiteJobRepository
from app.storage.store import WorldStore


def current_rss_bytes() -> int | None:
    # Linux peak RSS can include the launcher high-water mark; sample current RSS separately.
    statm = Path("/proc/self/statm")
    if not statm.exists():
        return None
    return int(statm.read_text().split()[1]) * int(os.sysconf("SC_PAGE_SIZE"))


def run(turns: int) -> dict[str, object]:
    windows: list[dict[str, object]] = []
    latencies: list[float] = []
    retained: dict[int, WorldSnapshot] = {}
    population = np.full((16, 512), 100, dtype=np.int64)
    tracemalloc.start()
    with tempfile.TemporaryDirectory(prefix="clade-storage-bench-") as directory:
        store = WorldStore(Path(directory), checkpoint_interval=25)
        current = store.create(
            WorldSnapshot(
                WorldVersion("benchmark", "control"),
                0,
                {"counter": 0},
                {"population": FrozenArray.from_numpy(population)},
            ),
            seed=17,
        )
        initial_total = int(population.sum())
        retained[0] = current
        for turn in range(1, turns + 1):
            started = time.perf_counter()
            left, right = turn % 512, (turn + 1) % 512
            population[0, left] -= 1
            population[0, right] += 1
            context = TurnContext(
                turn, current, 17, command={"kind": "storage-stress"}
            ).with_snapshot(
                replace(
                    current,
                    state={"counter": turn},
                    arrays={"population": FrozenArray.from_numpy(population)},
                )
            )
            job_specs: tuple[JobSpec, ...] = ()
            if turn % 25 == 0:
                after = replace(context.snapshot, version=current.version.advance(), turn_id=turn)
                job_specs = (
                    JobSpec(
                        after.version,
                        turn,
                        "species",
                        ("fixture-species",),
                        (),
                        snapshot_id=after.snapshot_id,
                    ),
                )
            current = store.commit(context, command_key=f"turn-{turn}", jobs=job_specs)
            if job_specs:
                repository = SQLiteJobRepository(store.db, clock=lambda: 0.0)
                job = repository.claim("mock", now=0.0, lease_seconds=30)
                assert job is not None
                applied = repository.finish(
                    job.job_id, job.lease_token, result=fallback_result(job.spec), now=1.0
                )
                assert applied is not None and applied.status == "APPLIED"
                assert store.head("benchmark", "control").state_hash == current.state_hash
            assert int(current.arrays["population"].numpy().sum()) == initial_total
            latencies.append((time.perf_counter() - started) * 1000)
            if turn in {100, 500, 1000, turns}:
                retained[turn] = current
                for saved in retained.values():
                    assert store.history.replay(saved.version).snapshot_id == saved.snapshot_id
                # Reopen after each checkpoint window to exercise process-independent storage.
                store = WorldStore(Path(directory), checkpoint_interval=25)
                current = store.head("benchmark", "control")
                heap, peak = tracemalloc.get_traced_memory()
                files = [path for path in Path(directory).rglob("*") if path.is_file()]
                windows.append(
                    {
                        "turn": turn,
                        "state_hash": current.state_hash,
                        "save_bytes": sum(path.stat().st_size for path in files),
                        "array_objects": len(list((Path(directory) / "objects").rglob("*.npz"))),
                        "python_heap_bytes": heap,
                        "python_peak_bytes": peak,
                        "process_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                        "current_rss_bytes": current_rss_bytes(),
                        "last_100_p50_ms": statistics.median(latencies[-100:]),
                        "last_100_p95_ms": sorted(latencies[-100:])[
                            int(len(latencies[-100:]) * 0.95) - 1
                        ],
                    }
                )
    tracemalloc.stop()
    return {
        "scope": "checkpoint/delta/replay + mock narrative transactions; no ecology model",
        "turns": turns,
        "species": 16,
        "tiles": 512,
        "windows": windows,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--turns", type=int, default=1000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.turns < 1:
        parser.error("--turns must be positive")
    report = run(args.turns)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))

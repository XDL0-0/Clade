"""Opt-in reference ecosystem + durable replay stress, without real AI.

python -m scripts.benchmark_reference_ecology --turns 1000 --output report.json
The world is isolated in a temporary directory; no production saves are changed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import statistics
import tempfile
import time
from collections import deque
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import cast

import numpy as np

from app.ai.jobs.models import JobSpec
from app.ai.jobs.schemas import fallback_result
from app.simulation.v2.context import TurnContext, WorldSnapshot
from app.simulation.v2.engine import SimulationEngineV2, TurnCommand
from app.simulation.v2.reference.model import ecological_pipeline, evolution_pipeline
from app.simulation.v2.reference.world import MODEL_ID, create_reference_snapshot
from app.simulation.v2.values import JsonValue, thaw
from app.simulation.v2.version import WorldVersion
from app.storage.jobs import SQLiteJobRepository
from app.storage.observations import ObservationReader
from app.storage.store import WorldStore
from scripts.benchmark_v2_storage import current_rss_bytes


def organic_carbon(snapshot: WorldSnapshot) -> float:
    total = math.fsum(
        float(snapshot.arrays[name].numpy().sum())
        for name in ("plant_biomass", "detritus", "energy_reserve")
    )
    pop = np.asarray(snapshot.arrays["population"].numpy(), dtype=np.int64)
    for metadata in snapshot.domain("species").values():
        assert isinstance(metadata, Mapping)
        slot, mass = metadata["slot"], metadata["body_mass"]
        assert isinstance(slot, int) and isinstance(mass, (int, float))
        total += int(pop[slot].sum(dtype=object)) * mass
    return total


def mock_plan(context: TurnContext) -> tuple[JobSpec, ...]:
    if context.turn_id % 50:
        return ()
    snapshot = replace(
        context.snapshot, version=context.world_version.advance(), turn_id=context.turn_id
    )
    return (
        JobSpec(
            snapshot.version,
            snapshot.turn_id,
            "species",
            ("grazer",),
            (),
            snapshot_id=snapshot.snapshot_id,
        ),
    )


def run(
    turns: int, *, seed: int = 37, width: int = 8, height: int = 4, evolution: bool = False
) -> dict[str, object]:
    if turns < 1:
        raise ValueError("turns must be positive")
    pipeline = evolution_pipeline() if evolution else ecological_pipeline()
    source_hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((Path(__file__).parents[1] / "app/simulation/v2/reference").glob("*.py"))
    }
    latencies: deque[float] = deque(maxlen=100)
    windows: list[dict[str, object]] = []
    max_carbon_error = max_nutrient_error = 0.0
    max_population = 0
    migrations = kills = 0
    retained: dict[int, WorldSnapshot] = {}
    with tempfile.TemporaryDirectory(prefix="clade-ecology-bench-") as directory:
        root = Path(directory)
        store = WorldStore(root, checkpoint_interval=25)
        engine = SimulationEngineV2(store, pipeline, MODEL_ID, narrative_planner=mock_plan)
        current = store.create(
            create_reference_snapshot(
                WorldVersion("reference-benchmark", "control"),
                seed=seed,
                manifest=engine.manifest,
                width=width,
                height=height,
                max_species=16,
            ),
            seed=seed,
        )
        retained[0] = current
        max_population = int(current.arrays["population"].numpy().sum())
        old_carbon = organic_carbon(current)
        nitrogen_initial = float(current.arrays["nutrients"].numpy().sum()) + 0.02 * old_carbon
        for turn in range(1, turns + 1):
            started = time.perf_counter()
            previous = current
            current = engine.run_turn(TurnCommand(current.version, f"turn-{turn}"))
            profile = ObservationReader(store.db).profile(current.version)
            metrics = {
                str(stage["stage_name"]): cast(Mapping[str, JsonValue], stage["metrics"])
                for stage in profile
            }
            carbon = organic_carbon(current)
            fixed = float(cast(float, metrics["reference_productivity"]["carbon_fixed"]))
            respired = float(cast(float, metrics["reference_regeneration"]["carbon_respired"]))
            respired += float(cast(float, metrics["reference_mortality"]["carbon_respired"]))
            carbon_error = abs(carbon - old_carbon - fixed + respired)
            nitrogen = float(current.arrays["nutrients"].numpy().sum()) + 0.02 * carbon
            max_carbon_error = max(max_carbon_error, carbon_error)
            max_nutrient_error = max(max_nutrient_error, abs(nitrogen - nitrogen_initial))
            assert carbon_error < 1e-6 and abs(nitrogen - nitrogen_initial) < 1e-6
            old_carbon = carbon
            for array in current.arrays.values():
                assert np.isfinite(array.numpy()).all()
            for name in ("population", "plant_biomass", "detritus", "energy_reserve", "nutrients"):
                assert np.all(np.asarray(current.arrays[name].numpy(), dtype=np.float64) >= 0)
            max_population = max(max_population, int(current.arrays["population"].numpy().sum()))
            migrations += int(cast(int, metrics["reference_population"]["migrated"]))
            kills += int(cast(float, metrics["reference_feeding"]["predation_deaths"]))
            if turn % 50 == 0:
                repository = SQLiteJobRepository(store.db, clock=lambda: 0.0)
                job = repository.claim("mock", now=0, lease_seconds=30)
                assert job is not None
                applied = repository.finish(
                    job.job_id, job.lease_token, result=fallback_result(job.spec), now=1
                )
                assert applied is not None and applied.status == "APPLIED"
                assert store.head("reference-benchmark", "control").state_hash == current.state_hash
            latencies.append((time.perf_counter() - started) * 1000)
            if turn in {100, 500, 1000, turns}:
                rerun = pipeline.execute(
                    TurnContext(turn, previous, seed, command={"command_id": f"turn-{turn}"})
                )
                assert rerun.snapshot.state_hash == current.state_hash
                retained[turn] = current
                store = WorldStore(root, checkpoint_interval=25)
                engine = SimulationEngineV2(store, pipeline, MODEL_ID, narrative_planner=mock_plan)
                for saved in retained.values():
                    assert store.history.replay(saved.version).snapshot_id == saved.snapshot_id
                durations = list(latencies)
                windows.append(
                    {
                        "turn": turn,
                        "timing_samples": len(durations),
                        "state_hash": current.state_hash,
                        "save_bytes": sum(
                            path.stat().st_size for path in root.rglob("*") if path.is_file()
                        ),
                        "current_rss_bytes": current_rss_bytes(),
                        "last_100_p50_ms": statistics.median(durations),
                        "last_100_p95_ms": sorted(durations)[
                            max(0, math.ceil(0.95 * len(durations)) - 1)
                        ],
                        "metrics": thaw(metrics["reference_metrics"]),
                        "stage_duration_ms": {
                            str(stage["stage_name"]): stage["duration_ms"] for stage in profile
                        },
                    }
                )
                print(
                    json.dumps(
                        {
                            "seed": seed,
                            "completed_turn": turn,
                            "population": metrics["reference_metrics"]["total_population"],
                        }
                    ),
                    flush=True,
                )
    return {
        "scope": (
            "26-stage ecosystem with deme evolution, speciation and extinction lifecycle"
            if evolution
            else "17-stage environment/resources/ecology/movement/demography; no evolution"
        ),
        "seed": seed,
        "turns": turns,
        "width": width,
        "height": height,
        "max_population": max_population,
        "migration_steps": migrations,
        "predation_deaths": kills,
        "max_carbon_error": max_carbon_error,
        "max_nutrient_error": max_nutrient_error,
        "mock_ai": "template-only immutable annotations every 50 turns",
        "windows": windows,
        "engine_manifest": thaw(engine.manifest),
        "runtime": {"python": platform.python_version(), "numpy": np.__version__},
        "reference_source_hashes_at_start": source_hashes,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--turns", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=37)
    parser.add_argument("--width", type=int, default=8)
    parser.add_argument("--height", type=int, default=4)
    parser.add_argument("--evolution", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(
        args.turns, seed=args.seed, width=args.width, height=args.height, evolution=args.evolution
    )
    args.output.write_text(json.dumps(report, indent=2) + "\n")

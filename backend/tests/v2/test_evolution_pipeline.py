"""Actual 26-stage evolution with storage, paired universes and carbon budgets."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from app.simulation.v2.context import TurnContext
from app.simulation.v2.engine import SimulationEngineV2, TurnCommand
from app.simulation.v2.reference.common import number
from app.simulation.v2.reference.diversity import deme_diversity
from app.simulation.v2.reference.model import evolution_pipeline, feedback_pipeline
from app.simulation.v2.reference.world import MODEL_ID, TRAITS, create_reference_snapshot
from app.simulation.v2.values import FrozenArray
from app.simulation.v2.version import WorldVersion
from app.storage.observations import ObservationReader
from app.storage.store import WorldStore
from scripts.benchmark_reference_ecology import organic_carbon


@pytest.mark.parametrize("feedback", [False, True])
def test_durable_evolution_replay_and_paired_fork(tmp_path: Path, feedback: bool) -> None:
    store = WorldStore(tmp_path / "evolution", checkpoint_interval=3)
    pipeline = feedback_pipeline() if feedback else evolution_pipeline()
    engine = SimulationEngineV2(store, pipeline, MODEL_ID)
    current = store.create(
        create_reference_snapshot(
            WorldVersion("evolution", "control"),
            seed=37,
            manifest=engine.manifest,
            width=4,
            height=3,
            max_species=8,
        ),
        seed=37,
    )
    initial = current
    assert current.arrays["deme_traits"].shape == (8, 12, 7)
    np.testing.assert_array_equal(
        current.arrays["gene_population"].numpy(), current.arrays["population"].numpy()
    )
    histories = [current]
    nitrogen = float(current.arrays["nutrients"].numpy().sum()) + 0.02 * organic_carbon(current)
    for turn in range(1, 21):
        previous = current
        command = TurnCommand(previous.version, f"turn-{turn}")
        current = engine.run_turn(command)
        profile = ObservationReader(store.db).profile(current.version)
        assert len(profile) == (28 if feedback else 26)
        flux = 0.0
        for stage in profile:
            metrics = stage["metrics"]
            assert isinstance(metrics, Mapping)
            for name, sign in (("carbon_fixed", 1), ("carbon_respired", -1)):
                value = metrics.get(name, 0)
                assert isinstance(value, (int, float))
                flux += sign * value
        carbon = organic_carbon(current)
        assert carbon - organic_carbon(previous) == pytest.approx(flux, abs=1e-8)
        assert float(current.arrays["nutrients"].numpy().sum()) + 0.02 * carbon == pytest.approx(
            nitrogen, abs=1e-8
        )
        assert engine.run_turn(command).snapshot_id == current.snapshot_id
        for array in current.arrays.values():
            assert np.isfinite(array.numpy()).all()
        deme = np.asarray(current.arrays["deme_traits"].numpy(), dtype=np.float64)
        assert np.all(deme >= 0) and np.all(deme <= 1)
        assert np.all(deme.sum(axis=2) <= 3 + 1e-12)
        for phenotype in current.domain("species").values():
            assert isinstance(phenotype, Mapping)
            slot = phenotype["slot"]
            traits = phenotype["traits"]
            assert isinstance(slot, int) and isinstance(traits, Mapping)
            row = current.arrays["population"].numpy()[slot]
            if row.sum():
                np.testing.assert_allclose(
                    [number(traits[name], name) for name in TRAITS],
                    np.average(deme[slot], axis=0, weights=row),
                    atol=1e-12,
                )
        histories.append(current)
    assert initial.domain("species") != current.domain("species")
    reopened = WorldStore(tmp_path / "evolution")
    for saved in histories:
        assert (
            reopened.history.at_turn("evolution", "control", saved.turn_id).snapshot_id
            == saved.snapshot_id
        )
    branch = store.fork(histories[9].version, "experiment")
    paired = engine.run_turn(TurnCommand(branch.version, "paired", rng_namespace="control"))
    # Event IDs/versions are scoped to the branch; numerical and species state agree.
    for name in paired.arrays:
        np.testing.assert_array_equal(
            paired.arrays[name].numpy(), histories[10].arrays[name].numpy()
        )
    assert paired.domain("species") == histories[10].domain("species")
    warmed = engine.run_turn(
        TurnCommand(
            paired.version, "warm", rng_namespace="control", payload={"warming_offset": 4.0}
        )
    )
    assert (
        warmed.arrays["temperature"].content_hash
        != histories[11].arrays["temperature"].content_hash
    )
    assert store.head("evolution", "control").snapshot_id == histories[-1].snapshot_id


def test_deme_diversity_measures_within_species_variance(tmp_path: Path) -> None:
    engine = SimulationEngineV2(WorldStore(tmp_path), evolution_pipeline(), MODEL_ID)
    snapshot = create_reference_snapshot(
        WorldVersion("diversity", "main"),
        seed=1,
        manifest=engine.manifest,
        width=2,
        height=1,
        max_species=8,
    )
    pop = np.zeros((8, 2), dtype=np.int64)
    pop[0] = [10, 30]
    genes = np.zeros((8, 2, 7), dtype=np.float64)
    genes[0, 1, 0] = 1
    snapshot = replace(
        snapshot, arrays={**snapshot.arrays, "deme_traits": FrozenArray.from_numpy(genes)}
    )
    context = TurnContext(1, snapshot, 1)
    assert deme_diversity(context, pop) == pytest.approx(0.1875 / 7)
    pop[:] = 0
    assert deme_diversity(context, pop) == 0
    genes[7, 0, 0] = 0.1
    context = replace(
        context,
        snapshot=replace(
            snapshot, arrays={**snapshot.arrays, "deme_traits": FrozenArray.from_numpy(genes)}
        ),
    )
    with pytest.raises(ValueError, match="Unused"):
        deme_diversity(context, pop)

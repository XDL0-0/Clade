"""Legacy import acceptance: preservation, bounded parsing and isolated publication."""

from __future__ import annotations

import ctypes
import gzip
import hashlib
import json
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import cast

import numpy as np
import pytest

from app.simulation.v2.context import WorldSnapshot
from app.simulation.v2.values import JsonValue, digest, freeze_mapping, thaw
from app.storage import legacy
from app.storage.legacy import import_legacy, legacy_snapshot, read_legacy
from app.storage.store import WorldStore

FIXTURE = Path(__file__).parents[1] / "fixtures" / "legacy_world_v2.json"


def fixture() -> dict[str, object]:
    return cast(dict[str, object], json.loads(FIXTURE.read_text()))


def records(payload: dict[str, object], name: str) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], payload[name])


def write_source(path: Path, payload: object, *, compressed: bool = False) -> Path:
    raw = json.dumps(payload, ensure_ascii=False).encode()
    path.write_bytes(gzip.compress(raw) if compressed else raw)
    return path


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("compressed", [False, True])
@pytest.mark.parametrize("directory", [False, True])
def test_fixture_import_and_replay(tmp_path: Path, compressed: bool, directory: bool) -> None:
    source_dir = tmp_path / "legacy"
    source_dir.mkdir()
    filename = "game_state.json.gz" if compressed else "game_state.json"
    source = write_source(source_dir / filename, fixture(), compressed=compressed)
    before = file_hash(source)
    target = tmp_path / "imported"
    imported = import_legacy(source_dir if directory else source, target, "world", 42, "import")
    snapshot = imported.snapshot
    replayed = WorldStore(target).head("world", "import")
    assert replayed.snapshot_id == snapshot.snapshot_id
    assert file_hash(source) == before
    assert snapshot.version.revision == snapshot.version.generation == 0
    assert snapshot.turn_id == 3
    assert snapshot.manifest["model"] == "legacy-import-v1"
    assert snapshot.manifest["earliest_replayable_turn"] == 3
    assert snapshot.manifest["species_axis"] == (1, 2)
    assert snapshot.manifest["tile_axis"] == (1, 2)
    np.testing.assert_array_equal(snapshot.arrays["population"].numpy(), [[700, 500], [90, 0]])
    np.testing.assert_array_equal(snapshot.arrays["suitability"].numpy(), [[0.8, 0.7], [0.8, 0]])
    for name, expected in {
        "temperature": [20, 18],
        "humidity": [0.6, 0.5],
        "elevation": [20, 25],
        "resources": [100, 80],
    }.items():
        np.testing.assert_array_equal(snapshot.arrays[name].numpy(), expected)
    assert snapshot.domain("species")["1"] == freeze_mapping(
        {
            "id": 1,
            "lineage_code": "A1",
            "status": "alive",
            "trophic_level": 1.0,
            "morphology_stats": {"body_weight_g": 10},
            "abstract_traits": {"photosynthesis": 2},
            "hidden_traits": {"speed": 1},
            "ecological_vector": [0.1, 0.2],
            "diet_type": "autotroph",
            "habitat_type": "terrestrial",
        }
    )
    web = cast(Mapping[str, JsonValue], snapshot.domain("food_web")["2"])
    assert web["prey_species"] == ("A1",)
    assert web["prey_preferences"] == {"A1": 1.0}
    environment = snapshot.domain("environment")
    assert environment["map_state"] == read_legacy(source)["map_state"]
    tiles = cast(Mapping[str, JsonValue], environment["tiles"])
    assert cast(Mapping[str, JsonValue], tiles["2"])["x"] == 1
    with gzip.open(target / "legacy-source.json.gz", "rt") as stream:
        assert json.load(stream) == fixture()
    assert imported.source_hash == digest(read_legacy(source))
    assert snapshot.manifest["migration_warnings"] == imported.warnings
    for token in ("RNG", "isolation", "resource", "history"):
        assert any(token in warning for warning in imported.warnings)


def test_magic_detection_and_semantic_hash(tmp_path: Path) -> None:
    json_source = write_source(tmp_path / "plain.gz", fixture())
    gzip_source = write_source(tmp_path / "compressed.json", fixture(), compressed=True)
    plain = read_legacy(json_source)
    compressed = read_legacy(gzip_source)
    assert plain == compressed
    assert (
        legacy_snapshot(plain, "world").source_hash
        == legacy_snapshot(compressed, "world").source_hash
    )


def test_axes_are_stable_and_input_is_unchanged() -> None:
    payload = fixture()
    records(payload, "species").reverse()
    records(payload, "map_tiles").reverse()
    records(payload, "habitats").reverse()
    before = json.dumps(payload)
    snapshot = legacy_snapshot(freeze_mapping(payload), "world").snapshot
    assert snapshot.manifest["species_axis"] == snapshot.manifest["tile_axis"] == (1, 2)
    np.testing.assert_array_equal(snapshot.arrays["population"].numpy(), [[700, 500], [90, 0]])
    assert json.dumps(payload) == before


def test_archive_preserves_names_history_and_unknown_fields_once(tmp_path: Path) -> None:
    payload = fixture()
    payload["future_optional_notes"] = {"memo": "名称与历史都不能遗失"}
    records(payload, "species")[0]["user_notes"] = "自定义名 / Uncommon name"
    source = write_source(tmp_path / "legacy.json", payload)
    target = tmp_path / "new"
    result = import_legacy(source, target, "world", 7)
    with gzip.open(target / "legacy-source.json.gz", "rt") as stream:
        archived = json.load(stream)
    assert archived == payload
    state_text = json.dumps(thaw(result.snapshot.state), ensure_ascii=False)
    for token in ("初生草", "固定模板", "future_optional_notes", "user_notes", "history_logs"):
        assert token not in state_text
    with WorldStore(target).db.connection() as connection:
        saved = connection.execute("SELECT payload FROM commits").fetchone()[0]
        assert "history_logs" not in saved and "latin_name" not in saved


def corrupt(payload: dict[str, object], problem: str) -> None:
    species = records(payload, "species")
    tiles = records(payload, "map_tiles")
    habitats = records(payload, "habitats")
    if problem == "duplicate_species":
        species.append(species[0])
    elif problem == "duplicate_tile":
        tiles.append(tiles[0])
    elif problem == "duplicate_coords":
        tiles[1].update(x=0, y=0)
    elif problem == "duplicate_habitat":
        habitats.append(habitats[0])
    elif problem == "duplicate_lineage":
        species[1]["lineage_code"] = "A1"
    elif problem == "dangling_species":
        habitats[0]["species_id"] = 999
    elif problem == "dangling_tile":
        habitats[0]["tile_id"] = 999
    elif problem == "dangling_prey":
        species[1]["prey_species"] = ["missing"]
    elif problem == "dangling_preference":
        species[1]["prey_preferences"] = {"missing": 1.0}
    elif problem == "negative":
        habitats[0]["population"] = -1
    elif problem == "fractional":
        habitats[0]["population"] = 2.5
    elif problem == "boolean":
        habitats[0]["population"] = True
    elif problem == "overflow":
        habitats[0]["population"] = 2**63
    elif problem == "total_mismatch":
        habitats[0]["population"] = 699
    elif problem == "nonfinite":
        tiles[0]["temperature"] = float("nan")
    elif problem == "nonfinite_extra":
        payload["extra"] = float("inf")
    elif problem == "future_version":
        payload["version"] = "3.0"
    elif problem == "v2_identity":
        payload["format"] = "clade.checkpoint-delta"
    else:
        raise AssertionError(problem)


@pytest.mark.parametrize(
    "problem",
    [
        "duplicate_species",
        "duplicate_tile",
        "duplicate_coords",
        "duplicate_habitat",
        "duplicate_lineage",
        "dangling_species",
        "dangling_tile",
        "dangling_prey",
        "dangling_preference",
        "negative",
        "fractional",
        "boolean",
        "overflow",
        "total_mismatch",
        "nonfinite",
        "nonfinite_extra",
        "future_version",
        "v2_identity",
    ],
)
def test_invalid_save_never_publishes_or_changes_live_world(tmp_path: Path, problem: str) -> None:
    live = WorldStore(tmp_path / "active")
    active = legacy_snapshot(read_legacy(FIXTURE), "live").snapshot
    live.create(active, seed=17)
    live_bytes = live.db.path.read_bytes()
    payload = fixture()
    corrupt(payload, problem)
    source = write_source(tmp_path / "invalid.json", payload)
    before = file_hash(source)
    target = tmp_path / "new"
    with pytest.raises(ValueError):
        import_legacy(source, target, "new", 7)
    assert not target.exists()
    assert not tuple(tmp_path.glob(".new-import-*"))
    assert live.db.path.read_bytes() == live_bytes
    assert live.head("live", "main").snapshot_id == active.snapshot_id
    assert file_hash(source) == before


@pytest.mark.parametrize("compressed", [False, True])
def test_bounded_read_before_json_allocation(tmp_path: Path, compressed: bool) -> None:
    source = write_source(tmp_path / "big", {"padding": "a" * 100_000}, compressed=compressed)
    with pytest.raises(ValueError, match="max_bytes"):
        read_legacy(source, max_bytes=2000)


def test_reject_duplicate_json_field_and_truncated_gzip(tmp_path: Path) -> None:
    source = tmp_path / "bad"
    source.write_bytes(b'{"version":"2.0","version":"2.0"}')
    with pytest.raises(ValueError, match="Duplicate JSON"):
        read_legacy(source)
    source.write_bytes(gzip.compress(FIXTURE.read_bytes())[:-5])
    with pytest.raises(ValueError, match="Cannot read"):
        read_legacy(source)


def test_missing_distribution_defaults_are_explicit_and_conserve_totals() -> None:
    payload = fixture()
    payload.pop("habitats")
    records(payload, "map_tiles").reverse()
    records(payload, "map_tiles")[0].pop("resources")
    imported = legacy_snapshot(freeze_mapping(payload), "world")
    np.testing.assert_array_equal(
        imported.snapshot.arrays["population"].numpy(), [[1200, 0], [90, 0]]
    )
    assert sum(int(value) for value in imported.snapshot.arrays["population"].numpy().flat) == 1290
    assert any("not a recovered distribution" in warning for warning in imported.warnings)
    assert any("resources missing" in warning for warning in imported.warnings)


def test_dense_allocation_limit_is_checked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(legacy, "MAX_BYTES", 1)
    with pytest.raises(ValueError, match="allocation limit"):
        import_legacy(FIXTURE, tmp_path / "new", "world", 7)
    assert not (tmp_path / "new").exists()


@pytest.mark.parametrize("existing", ["empty", "world", "symlink"])
def test_existing_destination_is_never_overwritten(tmp_path: Path, existing: str) -> None:
    target = tmp_path / "new"
    if existing == "empty":
        target.mkdir()
    elif existing == "world":
        WorldStore(target).create(legacy_snapshot(read_legacy(FIXTURE), "old").snapshot, seed=3)
    else:
        target.symlink_to(tmp_path / "absent")
    with pytest.raises(FileExistsError):
        import_legacy(FIXTURE, target, "world", 7)
    if existing == "world":
        assert WorldStore(target).head("old", "main").version.world_id == "old"
    if existing == "symlink":
        assert target.is_symlink() and not target.exists()


def test_concurrent_imports_publish_exactly_one_world(tmp_path: Path) -> None:
    target = tmp_path / "new"

    def attempt(number: int) -> str:
        try:
            import_legacy(FIXTURE, target, f"world-{number}", number)
            return "created"
        except FileExistsError:
            return "exists"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(attempt, (1, 2)))
    assert sorted(outcomes) == ["created", "exists"]
    with WorldStore(target).db.connection() as connection:
        assert connection.execute("SELECT count(*) FROM worlds").fetchone()[0] == 1
    assert not tuple(tmp_path.glob(".new-import-*"))


def test_uncooperative_destination_race_does_not_replace_empty_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = legacy._publish
    target = tmp_path / "new"
    inode = 0

    def race(source: Path, destination: Path) -> None:
        nonlocal inode
        destination.mkdir()
        inode = destination.stat().st_ino
        original(source, destination)

    monkeypatch.setattr(legacy, "_publish", race)
    with pytest.raises(FileExistsError):
        import_legacy(FIXTURE, target, "world", 7)
    assert target.stat().st_ino == inode
    assert not tuple(target.iterdir())
    assert not tuple(tmp_path.glob(".new-import-*"))


def test_replay_failure_cleans_only_its_temporary_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = WorldStore.head
    other = tmp_path / ".new-import-other"
    other.mkdir()

    def wrong_head(self: WorldStore, world: str, timeline: str) -> WorldSnapshot:
        return replace(original(self, world, timeline), turn_id=999)

    monkeypatch.setattr(WorldStore, "head", wrong_head)
    with pytest.raises(ValueError, match="replay"):
        import_legacy(FIXTURE, tmp_path / "new", "world", 7)
    assert not (tmp_path / "new").exists()
    assert tuple(tmp_path.glob(".new-import-*")) == (other,)


def test_no_unsafe_fallback_when_no_clobber_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ctypes, "CDLL", lambda *args, **kwargs: object())
    with pytest.raises(OSError, match="requires renameat2"):
        import_legacy(FIXTURE, tmp_path / "new", "world", 7)
    assert not (tmp_path / "new").exists()
    assert not tuple(tmp_path.glob(".new-import-*"))


@pytest.mark.parametrize("count", [-1, 1.5, True, 2**63])
def test_invalid_species_total_is_rejected(count: JsonValue) -> None:
    payload = fixture()
    morph = cast(dict[str, object], records(payload, "species")[0]["morphology_stats"])
    morph["population"] = count
    with pytest.raises(ValueError, match="population"):
        legacy_snapshot(freeze_mapping(payload), "world")


def test_integral_legacy_float_totals_are_preserved() -> None:
    payload = fixture()
    morph = cast(dict[str, object], records(payload, "species")[0]["morphology_stats"])
    morph["population"] = 1200.0
    snapshot = legacy_snapshot(freeze_mapping(payload), "world").snapshot
    assert snapshot.arrays["population"].dtype == "<i8"
    assert int(snapshot.arrays["population"].numpy()[0].sum()) == 1200


def test_invalid_map_state_is_rejected() -> None:
    payload = fixture()
    payload["map_state"] = []
    with pytest.raises(ValueError, match="map_state must be an object"):
        legacy_snapshot(freeze_mapping(payload), "world")


def test_missing_tiles_cannot_hold_nonzero_population() -> None:
    payload = fixture()
    payload["map_tiles"] = []
    payload["habitats"] = []
    with pytest.raises(ValueError, match="at least one tile"):
        legacy_snapshot(freeze_mapping(payload), "world")

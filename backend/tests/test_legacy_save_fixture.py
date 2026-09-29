"""Keep both historical JSON formats readable while new storage is introduced."""

import gzip
import hashlib
import json
from pathlib import Path

import pytest
from sqlmodel import create_engine

from app.core import database
from app.repositories.environment_repository import environment_repository
from app.repositories.species_repository import species_repository
from app.services.system.save_manager import SaveManager
from app.services.system.species_cache import get_species_cache


@pytest.mark.parametrize("compressed", [False, True])
def test_old_save_load_export_reload_preserves_numeric_world(tmp_path, monkeypatch, compressed):
    isolated_engine = create_engine(f"sqlite:///{tmp_path / 'fixture.sqlite'}")
    monkeypatch.setattr(database, "engine", isolated_engine)
    database.init_db()
    source = Path(__file__).parent / "fixtures" / "legacy_world_v2.json"
    raw = source.read_bytes()
    source_hash = hashlib.sha256(raw).hexdigest()
    save_dir = tmp_path / "saves" / "save_fixture"
    save_dir.mkdir(parents=True)
    (save_dir / "metadata.json").write_text(json.dumps({"save_name": "fixture", "turn_index": 3}))
    name = "game_state.json.gz" if compressed else "game_state.json"
    (save_dir / name).write_bytes(gzip.compress(raw, mtime=0) if compressed else raw)
    manager = SaveManager(tmp_path / "saves")
    try:
        loaded = manager.load_game("fixture")
        assert loaded["turn_index"] == 3
        expected = {"A1": 1200, "B1": 90}

        def assert_world():
            species = species_repository.list_species()
            assert {s.lineage_code: s.morphology_stats["population"] for s in species} == expected
            assert len(environment_repository.list_tiles()) == 2
            assert sum(h.population for h in environment_repository.list_latest_habitats()) == 1290
            assert next(s for s in species if s.lineage_code == "B1").prey_species == ["A1"]
            assert environment_repository.get_state().turn_index == 3

        assert_world()
        manager.save_game("roundtrip", 3)
        manager.load_game("roundtrip")
        assert_world()
        assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
    finally:
        get_species_cache().clear()
        isolated_engine.dispose()

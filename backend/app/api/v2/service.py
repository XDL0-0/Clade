"""Explicit per-app CPU service; reads never advance simulation or invoke AI."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from pathlib import Path

from app.ai.jobs.models import JobSpec
from app.simulation.v2.context import TurnContext, WorldSnapshot
from app.simulation.v2.engine import SimulationEngineV2, TurnCommand
from app.simulation.v2.pipeline import DeterministicPipeline
from app.simulation.v2.reference.model import explainable_pipeline
from app.simulation.v2.reference.world import MODEL_ID, create_reference_snapshot
from app.simulation.v2.values import JsonValue
from app.simulation.v2.version import VersionConflict, WorldVersion
from app.storage.codec import decode, version_from
from app.storage.database import IdempotencyConflict, StorageCorruption
from app.storage.history import commit_id, head_row
from app.storage.observations import ObservationReader
from app.storage.store import WorldStore

from .presentation import narrative_history, runtime_diagnostics
from .schemas import AdvanceTurn, CreateWorld, ForkTimeline, Rewind
from .views import identity, snapshot_view, species

PipelineFactory = Callable[[], DeterministicPipeline]
NarrativePlanner = Callable[[TurnContext], tuple[JobSpec, ...]]


class NotFound(LookupError):
    """A requested public resource is absent, not a corrupt committed resource."""


class SimulationService:
    def __init__(
        self,
        root: Path,
        pipeline_factory: PipelineFactory = explainable_pipeline,
        narrative_planner: NarrativePlanner | None = None,
        *,
        compatible_factories: tuple[PipelineFactory, ...] = (),
    ) -> None:
        self.store = WorldStore(root)
        self.pipeline_factories = (pipeline_factory, *compatible_factories)
        self.engine = SimulationEngineV2(
            self.store, pipeline_factory(), MODEL_ID, narrative_planner=narrative_planner
        )
        self.compatible_engines = tuple(
            SimulationEngineV2(self.store, factory(), MODEL_ID, narrative_planner=narrative_planner)
            for factory in compatible_factories
        )
        self.observations = ObservationReader(self.store.db)

    def engine_for(self, world: str) -> SimulationEngineV2:
        with self.store.db.connection() as connection:
            row = connection.execute(
                "SELECT manifest FROM worlds WHERE world_id=?", (world,)
            ).fetchone()
        if row is None:
            raise NotFound("World not found")
        manifest = decode(row["manifest"])
        for engine in (self.engine, *self.compatible_engines):
            if all(manifest.get(name) == value for name, value in engine.manifest.items()):
                return engine
        raise ValueError("Unsupported world manifest; explicit migration required")

    def pipeline_factory_for(self, world: str) -> PipelineFactory:
        selected = self.engine_for(world)
        for engine, factory in zip(
            (self.engine, *self.compatible_engines), self.pipeline_factories, strict=True
        ):
            if engine is selected:
                return factory
        raise ValueError("Saved recipe has no independent pipeline factory")

    def require_world(self, world: str) -> None:
        try:
            self.store.world_seed(world)
        except KeyError as exc:
            raise NotFound("World not found") from exc

    def head_version(self, world: str, timeline: str) -> WorldVersion:
        with self.store.db.connection() as connection:
            try:
                return version_from(dict(head_row(connection, world, timeline)))
            except KeyError as exc:
                raise NotFound("World or timeline not found") from exc

    def require_version(self, version: WorldVersion, *, expected: bool = False) -> None:
        self.head_version(version.world_id, version.timeline_id)
        with self.store.db.connection() as connection:
            row = connection.execute(
                "SELECT commit_id FROM commits WHERE commit_id=?", (commit_id(version),)
            ).fetchone()
        if row is None:
            if expected:
                raise VersionConflict("Expected version does not exist")
            raise NotFound("Source version not found")

    @staticmethod
    def match_route(version: WorldVersion, world: str, timeline: str) -> None:
        if (version.world_id, version.timeline_id) != (world, timeline):
            raise ValueError("Version world/timeline must match the route")

    def create(self, request: CreateWorld) -> dict[str, JsonValue]:
        snapshot = create_reference_snapshot(
            WorldVersion(request.world_id, request.timeline_id),
            seed=request.seed,
            manifest=self.engine.manifest,
            width=request.width,
            height=request.height,
            max_species=request.max_species,
        )
        return snapshot_view(self.store.create(snapshot, seed=request.seed))

    def snapshot(self, world: str, timeline: str, turn: int | None = None) -> WorldSnapshot:
        self.head_version(world, timeline)
        try:
            return (
                self.store.head(world, timeline)
                if turn is None
                else self.store.history.at_turn(world, timeline, turn)
            )
        except KeyError as exc:
            raise NotFound("Committed turn not found") from exc

    def advance(self, world: str, timeline: str, request: AdvanceTurn) -> dict[str, JsonValue]:
        version = request.expected_version.value()
        self.match_route(version, world, timeline)
        self.require_version(version, expected=True)
        parameters: dict[str, JsonValue] = {}
        for name in ("warming_offset", "co2_ppm", "disaster_severity", "disease_pressure"):
            value = getattr(request, name)
            if value is not None:
                parameters[name] = value
        result = self.engine_for(world).run_turn(
            TurnCommand(
                version,
                request.idempotency_key,
                payload=parameters,
                rng_namespace=request.rng_namespace,
            )
        )
        return snapshot_view(result)

    def fork(self, world: str, timeline: str, request: ForkTimeline) -> dict[str, JsonValue]:
        parent = request.parent.value()
        self.match_route(parent, world, timeline)
        self.require_version(parent)
        try:
            result = self.store.fork(parent, request.child_timeline_id)
        except sqlite3.IntegrityError as exc:
            if exc.sqlite_errorcode not in (
                sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY,
                sqlite3.SQLITE_CONSTRAINT_UNIQUE,
            ):
                raise
            raise IdempotencyConflict("Child timeline already exists") from exc
        return snapshot_view(result)

    def rewind(self, world: str, timeline: str, request: Rewind) -> dict[str, JsonValue]:
        expected, source = request.expected_version.value(), request.source_version.value()
        self.match_route(expected, world, timeline)
        if source.world_id != world:
            raise ValueError("Rewind source must belong to the same world")
        self.require_version(expected, expected=True)
        self.require_version(source)
        return snapshot_view(
            self.store.replace_head(expected, source, command_key=request.idempotency_key)
        )

    def timelines(self, world: str | None, *, limit: int, offset: int) -> dict[str, JsonValue]:
        if world is not None:
            self.require_world(world)
        where = "WHERE t.world_id=?" if world is not None else ""
        parameters: tuple[object, ...] = (
            (world, limit, offset) if world is not None else (limit, offset)
        )
        with self.store.db.connection() as connection:
            rows = connection.execute(
                "SELECT c.world_id,c.timeline_id,c.generation,c.revision,c.turn_id,"
                "c.state_hash,w.manifest FROM timelines t JOIN commits c ON c.commit_id=t.head_id "
                f"JOIN worlds w ON w.world_id=t.world_id {where} "
                "ORDER BY t.world_id,t.timeline_id LIMIT ? OFFSET ?",
                parameters,
            ).fetchall()
        items: list[JsonValue] = []
        for row in rows:
            items.append(
                {
                    "world_id": row["world_id"],
                    "timeline_id": row["timeline_id"],
                    "version": version_from(dict(row)).to_dict(),
                    "turn": row["turn_id"],
                    "model": decode(row["manifest"]).get("model"),
                    "state_hash": row["state_hash"],
                }
            )
        return {
            "items": tuple(items),
            "next_offset": offset + len(items) if len(items) == limit else None,
        }

    def detail(
        self,
        world: str,
        timeline: str,
        species_id: str,
        turn: int | None,
    ) -> dict[str, JsonValue]:
        snapshot = self.snapshot(world, timeline, turn)
        if species_id not in snapshot.domain("species"):
            raise NotFound("Species not found")
        record = species(snapshot, species_id)
        slot = record["slot"]
        assert isinstance(slot, int)
        with self.store.db.connection() as connection:
            rows = connection.execute(
                "SELECT payload FROM events WHERE commit_id=? "
                "AND json_extract(payload,'$.payload.species')=? "
                "AND json_type(payload,'$.payload.trait_changes')='object' "
                "ORDER BY ordinal LIMIT 1001",
                (commit_id(snapshot.version), species_id),
            ).fetchall()
        if len(rows) > 1000:
            raise StorageCorruption("Commit exceeds the transport trace budget")
        traces = tuple(decode(row["payload"]) for row in rows)
        return {
            **identity(snapshot),
            "species": record,
            "distribution": tuple(int(v) for v in snapshot.arrays["population"].numpy()[slot]),
            "fossil": {
                key: record[key]
                for key in (
                    "status",
                    "extinction_turn",
                    "extinction_cause",
                    "last_nonzero_population",
                    "last_habitat",
                    "ancestor",
                    "descendants",
                )
            },
            "evolution_traces": traces,
        }

    def messages(
        self,
        world: str,
        timeline: str,
        *,
        after: int,
        limit: int,
    ) -> tuple[Mapping[str, JsonValue], ...]:
        self.head_version(world, timeline)
        return self.store.messages(world, timeline, after=after, limit=limit)

    def metrics(
        self,
        world: str,
        timeline: str,
        *,
        generation: int | None,
        after_revision: int,
        limit: int,
    ) -> dict[str, JsonValue]:
        head = self.head_version(world, timeline)
        selected = head.generation if generation is None else generation
        if selected > head.generation:
            raise NotFound("Generation not found")
        items = self.observations.metrics(
            world,
            timeline,
            generation=selected,
            after_revision=after_revision,
            limit=limit,
        )
        return {
            "world_id": world,
            "timeline_id": timeline,
            "generation": selected,
            "items": items,
            "next_after_revision": items[-1]["revision"] if items else after_revision,
        }

    def profile(self, world: str, timeline: str, turn: int | None) -> dict[str, JsonValue]:
        snapshot = self.snapshot(world, timeline, turn)
        with self.store.db.connection() as connection:
            rows = connection.execute(
                "SELECT payload FROM stage_runs WHERE commit_id=? ORDER BY rowid LIMIT 1001",
                (commit_id(snapshot.version),),
            ).fetchall()
        if len(rows) > 1000:
            raise StorageCorruption("Commit exceeds the transport stage budget")
        return {**identity(snapshot), "stages": tuple(decode(row["payload"]) for row in rows)}

    def narratives(
        self,
        world: str,
        timeline: str,
        turn: int | None,
        *,
        species_id: str | None,
        limit: int,
        offset: int,
    ) -> Mapping[str, JsonValue]:
        snapshot = self.snapshot(world, timeline, turn)
        if species_id is not None and species_id not in snapshot.domain("species"):
            raise NotFound("Species not found")
        return narrative_history(
            self.store, snapshot, species_id=species_id, limit=limit, offset=offset
        )

    def diagnostics(self, world: str, timeline: str, turn: int | None) -> Mapping[str, JsonValue]:
        return runtime_diagnostics(self.store, self.snapshot(world, timeline, turn))

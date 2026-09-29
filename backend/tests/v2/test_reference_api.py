"""Real isolated HTTP calls over CPU stages, SQLite commits, and immutable history."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator, Mapping
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.types import Message, Scope

from app.ai.jobs.models import JobSpec
from app.api.v2 import SimulationService, create_app
from app.api.v2.events import stream_messages
from app.api.v2.schemas import StreamPage
from app.simulation.v2.context import TurnContext
from app.simulation.v2.contracts import SimulationStage, StageContract, StageResult
from app.simulation.v2.events import WorldEvent
from app.simulation.v2.pipeline import DeterministicPipeline
from app.simulation.v2.values import JsonValue, freeze_mapping, thaw
from app.storage.history import commit_id

BASE = "/api/v2/worlds/demo/main"


def body(response: object) -> Mapping[str, JsonValue]:
    from httpx import Response

    assert isinstance(response, Response)
    assert 200 <= response.status_code < 300, response.text
    return freeze_mapping(response.json())


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(tmp_path / "store")) as connection:
        yield connection


def genesis(client: TestClient, world: str = "demo") -> Mapping[str, JsonValue]:
    return body(
        client.post(
            "/api/v2/worlds",
            json={
                "world_id": world,
                "seed": 31,
                "width": 4,
                "height": 2,
                "max_species": 8,
            },
        )
    )


def advance(
    client: TestClient,
    snapshot: Mapping[str, JsonValue],
    key: str,
    *,
    base: str = BASE,
    **parameters: JsonValue,
) -> Mapping[str, JsonValue]:
    return body(
        client.post(
            base + "/turns",
            json=thaw(
                {
                    "expected_version": snapshot["version"],
                    "idempotency_key": key,
                    **parameters,
                }
            ),
        )
    )


def service(client: TestClient) -> SimulationService:
    return cast(SimulationService, cast(FastAPI, client.app).state.simulation_service)


def test_create_turn_history_and_reads_do_not_change_head(client: TestClient) -> None:
    first = genesis(client)
    second = advance(client, first, "one", warming_offset=4.0)
    assert first["turn"] == 0 and second["turn"] == 1
    assert first["state_hash"] != second["state_hash"]
    assert body(client.get(BASE + "/snapshot?turn=0")) == first
    assert body(client.get(BASE + "/snapshot")) == second
    assert len(cast(tuple[JsonValue, ...], second["species"])) == 7
    tiles = cast(Mapping[str, JsonValue], second["map"])
    assert set(tiles) == {
        "elevation",
        "temperature",
        "biome",
        "plant_biomass",
        "soil_water",
        "surface_water",
    }
    assert all(len(cast(tuple[JsonValue, ...], array)) == 8 for array in tiles.values())
    assert "population" not in tiles and "arrays" not in second
    head = service(client).store.head("demo", "main").snapshot_id
    for path in ("/snapshot", "/species/grazer", "/metrics", "/profile", "/events"):
        assert client.get(BASE + path).status_code == 200
    assert client.get(BASE + "/stream?follow=false").status_code == 200
    assert service(client).store.head("demo", "main").snapshot_id == head
    assert body(client.get(BASE + "/profile?turn=0"))["stages"] == ()
    assert len(cast(tuple[JsonValue, ...], body(client.get(BASE + "/profile"))["stages"])) == 20
    detail = body(client.get(BASE + "/species/grazer?turn=0"))
    assert len(cast(tuple[JsonValue, ...], detail["distribution"])) == 8
    assert detail["evolution_traces"] == ()
    assert list((service(client).store.root / "arrays").glob("**/*.npz")) or list(
        service(client).store.root.rglob("*.npz")
    )
    assert not list(service(client).store.root.rglob("world*.json"))


def test_idempotency_conflicts_and_explicit_version_route_matching(client: TestClient) -> None:
    first = genesis(client)
    second = advance(client, first, "one")
    third = advance(client, second, "two")
    assert advance(client, first, "one") == second
    assert body(client.get(BASE + "/snapshot")) == third
    command = {"expected_version": thaw(first["version"]), "idempotency_key": "one"}
    assert client.post(BASE + "/turns", json={**command, "warming_offset": 1.0}).status_code == 409
    assert (
        client.post(BASE + "/turns", json={**command, "idempotency_key": "stale"}).status_code
        == 409
    )
    version = cast(Mapping[str, JsonValue], first["version"])
    for update, status in (({"world_id": "elsewhere"}, 422), ({"revision": 999}, 409)):
        assert (
            client.post(
                BASE + "/turns",
                json=thaw(
                    {
                        "idempotency_key": "one",
                        "expected_version": {**version, **update},
                    }
                ),
            ).status_code
            == status
        )
    assert client.post("/api/v2/worlds", json={"world_id": "demo"}).status_code == 409


def test_fork_shares_arrays_rewind_generation_and_scoped_profiles(client: TestClient) -> None:
    zero = genesis(client)
    one = advance(client, zero, "one")
    two = advance(client, one, "two")
    objects_before = set(service(client).store.root.rglob("*.npz"))
    fork = body(
        client.post(
            BASE + "/forks",
            json=thaw(
                {
                    "parent": one["version"],
                    "child_timeline_id": "branch",
                }
            ),
        )
    )
    assert set(service(client).store.root.rglob("*.npz")) == objects_before
    assert fork["state_hash"] == one["state_hash"]
    branch = "/api/v2/worlds/demo/branch"
    assert body(client.get(branch + "/snapshot?turn=0")) == zero
    assert client.get(branch + "/snapshot?turn=2").status_code == 404
    assert body(client.get(branch + "/profile"))["stages"] == ()
    child = advance(client, fork, "branch-one", base=branch, warming_offset=6.0)
    assert child["state_hash"] != two["state_hash"]
    assert body(client.get(BASE + "/snapshot")) == two
    assert body(client.get(branch + "/profile"))["version"] == child["version"]
    assert len(cast(tuple[JsonValue, ...], body(client.get(branch + "/metrics"))["items"])) == 1
    page = body(client.get(BASE + "/metrics?limit=1"))
    assert page["next_after_revision"] == 1
    later = body(client.get(BASE + "/metrics?after_revision=1"))
    assert len(cast(tuple[JsonValue, ...], later["items"])) == 1
    rewind_request = {
        "expected_version": two["version"],
        "source_version": zero["version"],
        "idempotency_key": "rewind",
    }
    rewound = body(client.post(BASE + "/rewind", json=thaw(rewind_request)))
    assert (
        rewound["turn"] == 0
        and cast(Mapping[str, JsonValue], rewound["version"])["generation"] == 1
    )
    assert body(client.post(BASE + "/rewind", json=thaw(rewind_request))) == rewound
    assert body(client.get(BASE + "/metrics"))["items"] == ()
    assert (
        len(cast(tuple[JsonValue, ...], body(client.get(BASE + "/metrics?generation=0"))["items"]))
        == 2
    )
    assert client.get(BASE + "/metrics?generation=2").status_code == 404
    replayed = advance(client, rewound, "one")  # Same key, different generation.
    assert cast(Mapping[str, JsonValue], replayed["version"])["generation"] == 1
    assert replayed["state_hash"] == one["state_hash"]
    assert body(client.get(branch + "/snapshot")) == child
    assert (
        client.post(
            BASE + "/forks",
            json=thaw(
                {
                    "parent": one["version"],
                    "child_timeline_id": "branch",
                }
            ),
        ).status_code
        == 409
    )


def test_independent_cursors_and_sse_reconnection_do_not_consume_messages(
    client: TestClient,
) -> None:
    first = genesis(client)
    advance(client, first, "one", warming_offset=4.0)
    all_events = body(client.get(BASE + "/events"))
    assert all_events == body(client.get(BASE + "/events"))
    items = cast(tuple[Mapping[str, JsonValue], ...], all_events["items"])
    first_page = body(client.get(BASE + "/events?limit=1"))
    assert first_page["items"] == items[:1]
    stream = client.get(BASE + "/stream?follow=false&limit=1")
    assert stream.headers["content-type"].startswith("text/event-stream")
    assert stream.headers["x-accel-buffering"] == "no"
    emitted = tuple(
        freeze_mapping(json.loads(line[6:]))
        for line in stream.text.splitlines()
        if line.startswith("data: ")
    )
    assert emitted == items[:1]
    cursor = first_page["next_cursor"]
    resumed = client.get(BASE + "/stream?follow=false", headers={"Last-Event-ID": str(cursor)})
    remaining = tuple(
        freeze_mapping(json.loads(line[6:]))
        for line in resumed.text.splitlines()
        if line.startswith("data: ")
    )
    assert remaining == items[1:]
    assert body(client.get(BASE + "/events")) == all_events
    assert client.get(BASE + f"/stream?follow=false&after={all_events['next_cursor']}").text == ""
    genesis(client, "other")
    other = body(client.get("/api/v2/worlds/other/main/events"))
    assert len(cast(tuple[JsonValue, ...], other["items"])) == 1
    assert body(client.get(BASE + "/events")) == all_events


@pytest.mark.parametrize(
    "update",
    [
        {"seed": True},
        {"seed": -1},
        {"seed": "31"},
        {"seed": 2**63},
        {"world_id": "../escape"},
        {"world_id": "a" * 65},
        {"world_id": ""},
        {"width": 3},
        {"height": 0},
        {"max_species": 257},
        {"width": 256, "height": 256, "max_species": 256},
        {"population": 999},
    ],
)
def test_strict_create_schema(client: TestClient, update: dict[str, object]) -> None:
    assert client.post("/api/v2/worlds", json={"world_id": "demo", **update}).status_code == 422


@pytest.mark.parametrize(
    "update",
    [
        {"traits": {"speed": 1}},
        {"population": 999},
        {"co2_ppm": "400"},
        {"co2_ppm": 5001},
        {"disease_pressure": 2},
        {"disaster_severity": True},
        {"warming_offset": 101},
        {"rng_namespace": ""},
        {"idempotency_key": " "},
        {"expected_version": {"world_id": "demo", "timeline_id": "main", "revision": 0}},
    ],
)
def test_strict_turn_schema(client: TestClient, update: dict[str, object]) -> None:
    value = genesis(client)
    assert (
        client.post(
            BASE + "/turns",
            json={
                "expected_version": thaw(value["version"]),
                "idempotency_key": "one",
                **update,
            },
        ).status_code
        == 422
    )
    assert body(client.get(BASE + "/snapshot")) == value


@pytest.mark.parametrize(
    "query",
    [
        "/snapshot?turn=-1",
        "/snapshot?turn=1.0",
        "/snapshot?turn=false",
        "/snapshot?unknown=1",
        "/events?after=-1",
        "/events?limit=1001",
        "/events?limit=0",
        "/events?after=9223372036854775808",
        "/metrics?generation=-1",
        "/metrics?after_revision=-2",
        "/profile?turn=no",
        "/stream?follow=1",
    ],
)
def test_invalid_queries_fail_closed(client: TestClient, query: str) -> None:
    genesis(client)
    assert client.get(BASE + query).status_code == 422


def test_missing_resources_and_invalid_sse_header(client: TestClient) -> None:
    for path in ("/snapshot", "/events", "/metrics", "/profile", "/stream?follow=false"):
        assert client.get(BASE + path).status_code == 404
    genesis(client)
    assert client.get(BASE + "/snapshot?turn=99").status_code == 404
    assert client.get(BASE + "/species/missing").status_code == 404
    assert client.get("/api/v2/worlds/missing/timelines").status_code == 404
    for cursor in ("nan", "-1", "9223372036854775808"):
        assert (
            client.get(BASE + "/stream?follow=false", headers={"Last-Event-ID": cursor}).status_code
            == 422
        )


def test_all_worlds_share_one_store_and_lists_are_paginated(client: TestClient) -> None:
    genesis(client)
    genesis(client, "second")
    page = body(client.get("/api/v2/worlds?limit=1"))
    assert len(cast(tuple[JsonValue, ...], page["items"])) == 1 and page["next_offset"] == 1
    assert body(client.get("/api/v2/worlds?offset=1"))["next_offset"] is None
    assert (
        len(cast(tuple[JsonValue, ...], body(client.get("/api/v2/worlds/demo/timelines"))["items"]))
        == 1
    )
    assert len(list(service(client).store.root.rglob("world.sqlite"))) == 1


def test_storage_corruption_is_500_not_bad_input_or_not_found(client: TestClient) -> None:
    genesis(client)
    with service(client).store.db.transaction() as connection:
        connection.execute("UPDATE commits SET record_hash='corrupt'")
    assert client.get(BASE + "/snapshot").status_code == 500


def test_pure_cpu_import_has_no_legacy_main_or_gpu_modules() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; from app.api.v2 import create_app, SimulationService; "
                "assert 'app.main' not in sys.modules; "
                "assert not any(k == 'taichi' or k.startswith('taichi.') for k in sys.modules)"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_injected_planner_and_pipeline_are_not_called_on_reads(tmp_path: Path) -> None:
    planned: list[int] = []

    def planner(context: TurnContext) -> tuple[JobSpec, ...]:
        planned.append(context.turn_id)
        return ()

    class TraceStage(SimulationStage):
        contract = StageContract("trace-fixture", "1")

        def execute(self, context: TurnContext) -> StageResult:
            event = WorldEvent.create(
                version=context.world_version.advance(),
                turn=context.turn_id,
                command_id=str(context.command["command_id"]),
                stage=self.contract.name,
                ordinal=0,
                event_type="SpeciesAdapted",
                actor="grazer",
                payload={"species": "grazer", "trait_changes": {"speed": 0.01}},
            )
            return StageResult(self.contract.name, events=(event,))

    app = create_app(
        tmp_path / "injected",
        pipeline_factory=lambda: DeterministicPipeline([TraceStage()]),
        narrative_planner=planner,
    )
    with TestClient(app) as connection:
        initial = genesis(connection)
        connection.get(BASE + "/snapshot")
        assert planned == []
        advanced = advance(connection, initial, "one")
        assert planned == [1]
        detail = body(connection.get(BASE + "/species/grazer"))
        assert len(cast(tuple[JsonValue, ...], detail["evolution_traces"])) == 1
        assert body(connection.get(BASE + "/species/hunter"))["evolution_traces"] == ()
        assert detail["version"] == advanced["version"]
        assert planned == [1]


@pytest.mark.asyncio
async def test_stream_heartbeat_disconnect_and_cancellation(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    genesis(client)
    cursor = cast(int, body(client.get(BASE + "/events"))["next_cursor"])
    disconnected = False

    async def receive() -> Message:
        if disconnected:
            return {"type": "http.disconnect"}
        await asyncio.sleep(3600)
        return {"type": "http.request", "body": b"", "more_body": False}

    scope: Scope = {"type": "http", "method": "GET", "path": "/stream", "headers": []}
    request = Request(scope, receive=receive)
    monkeypatch.setattr("app.api.v2.events.HEARTBEAT_SECONDS", 0.0)
    monkeypatch.setattr("app.api.v2.events.POLL_SECONDS", 0.01)
    messages: AsyncIterator[str] = stream_messages(
        service(client),
        request,
        "demo",
        "main",
        StreamPage(),
        cursor,
    )
    assert await asyncio.wait_for(anext(messages), timeout=1) == ": heartbeat\n\n"
    disconnected = True
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(messages), timeout=1)


def test_fork_is_delta_and_rewind_is_explicit_checkpoint(client: TestClient) -> None:
    initial = genesis(client)
    first = advance(client, initial, "one")
    child = body(
        client.post(
            BASE + "/forks",
            json=thaw(
                {
                    "parent": first["version"],
                    "child_timeline_id": "child",
                }
            ),
        )
    )
    from app.api.v2.schemas import Version

    child_version = Version.model_validate(thaw(child["version"])).value()
    with service(client).store.db.connection() as connection:
        row = connection.execute(
            "SELECT is_checkpoint,parent_id,payload FROM commits WHERE commit_id=?",
            (commit_id(child_version),),
        ).fetchone()
    assert row["is_checkpoint"] == 0 and row["parent_id"] is not None
    payload = json.loads(row["payload"])
    assert not payload.get("arrays")


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_json_numbers_are_rejected_without_a_commit(
    client: TestClient, value: str
) -> None:
    initial = genesis(client)
    encoded = json.dumps({"expected_version": thaw(initial["version"]), "idempotency_key": "bad"})
    raw = encoded[:-1] + ',"warming_offset":' + value + "}"
    rejected = client.post(
        BASE + "/turns", content=raw, headers={"Content-Type": "application/json"}
    )
    assert rejected.status_code == 422
    assert body(client.get(BASE + "/snapshot")) == initial

"""The opt-in HTTP router captures one explicitly injected simulation service."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Path, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.simulation.v2.values import JsonValue, thaw

from .events import resume_cursor, stream_messages
from .schemas import (
    ID_PATTERN,
    AdvanceTurn,
    AtTurn,
    CreateWorld,
    EventPage,
    ForkTimeline,
    MetricPage,
    NarrativePage,
    Page,
    Rewind,
    StreamPage,
)
from .service import SimulationService
from .views import snapshot_view

RouteID = Annotated[str, Path(pattern=ID_PATTERN, min_length=1, max_length=64)]


def response(value: JsonValue, *, status: int = 200) -> JSONResponse:
    return JSONResponse(thaw(value), status_code=status)


def create_router(service: SimulationService, *, prefix: str = "/api/v2") -> APIRouter:
    router = APIRouter(prefix=prefix)
    scope = "/worlds/{world_id}/{timeline_id}"

    @router.post("/worlds", status_code=201)
    def create_world(body: CreateWorld) -> JSONResponse:
        return response(service.create(body), status=201)

    @router.get("/worlds")
    def worlds(query: Annotated[Page, Query()]) -> JSONResponse:
        return response(service.timelines(None, limit=query.limit, offset=query.offset))

    @router.get("/worlds/{world_id}/timelines")
    def timelines(world_id: RouteID, query: Annotated[Page, Query()]) -> JSONResponse:
        return response(service.timelines(world_id, limit=query.limit, offset=query.offset))

    @router.get(scope + "/snapshot")
    def snapshot(
        world_id: RouteID,
        timeline_id: RouteID,
        query: Annotated[AtTurn, Query()],
    ) -> JSONResponse:
        return response(snapshot_view(service.snapshot(world_id, timeline_id, query.turn)))

    @router.get(scope + "/species/{species_id}")
    def detail(
        world_id: RouteID,
        timeline_id: RouteID,
        species_id: RouteID,
        query: Annotated[AtTurn, Query()],
    ) -> JSONResponse:
        return response(service.detail(world_id, timeline_id, species_id, query.turn))

    @router.post(scope + "/turns")
    def advance(world_id: RouteID, timeline_id: RouteID, body: AdvanceTurn) -> JSONResponse:
        return response(service.advance(world_id, timeline_id, body))

    @router.post(scope + "/forks", status_code=201)
    def fork(world_id: RouteID, timeline_id: RouteID, body: ForkTimeline) -> JSONResponse:
        return response(service.fork(world_id, timeline_id, body), status=201)

    @router.post(scope + "/rewind")
    def rewind(world_id: RouteID, timeline_id: RouteID, body: Rewind) -> JSONResponse:
        return response(service.rewind(world_id, timeline_id, body))

    @router.get(scope + "/metrics")
    def metrics(
        world_id: RouteID,
        timeline_id: RouteID,
        query: Annotated[MetricPage, Query()],
    ) -> JSONResponse:
        return response(
            service.metrics(
                world_id,
                timeline_id,
                generation=query.generation,
                after_revision=query.after_revision,
                limit=query.limit,
            )
        )

    @router.get(scope + "/profile")
    def profile(
        world_id: RouteID,
        timeline_id: RouteID,
        query: Annotated[AtTurn, Query()],
    ) -> JSONResponse:
        return response(service.profile(world_id, timeline_id, query.turn))

    @router.get(scope + "/events")
    def events(
        world_id: RouteID,
        timeline_id: RouteID,
        query: Annotated[EventPage, Query()],
    ) -> JSONResponse:
        messages = service.messages(world_id, timeline_id, after=query.after, limit=query.limit)
        return response(
            {
                "items": messages,
                "next_cursor": messages[-1]["cursor"] if messages else query.after,
            }
        )

    @router.get(scope + "/narratives")
    def narratives(
        world_id: RouteID, timeline_id: RouteID, query: Annotated[NarrativePage, Query()]
    ) -> JSONResponse:
        return response(
            service.narratives(
                world_id,
                timeline_id,
                query.turn,
                species_id=query.species_id,
                limit=query.limit,
                offset=query.offset,
            )
        )

    @router.get(scope + "/diagnostics")
    def diagnostics(
        world_id: RouteID, timeline_id: RouteID, query: Annotated[AtTurn, Query()]
    ) -> JSONResponse:
        return response(service.diagnostics(world_id, timeline_id, query.turn))

    @router.get(scope + "/stream")
    async def stream(
        request: Request,
        world_id: RouteID,
        timeline_id: RouteID,
        query: Annotated[StreamPage, Query()],
        last_event_id: Annotated[str | None, Header(max_length=20)] = None,
    ) -> StreamingResponse:
        cursor = resume_cursor(query.after, last_event_id)
        # Verify before sending HTTP headers, so missing scopes still return 404.
        await run_in_threadpool(service.head_version, world_id, timeline_id)
        return StreamingResponse(
            stream_messages(service, request, world_id, timeline_id, query, cursor),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router

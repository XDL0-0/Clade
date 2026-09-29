"""Independent, resumable outbox consumers with bounded asynchronous polling."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from time import monotonic

from fastapi import Request
from starlette.concurrency import run_in_threadpool

from app.simulation.v2.values import canonical_bytes

from .schemas import MAX_INTEGER, StreamPage, query_integer
from .service import SimulationService

POLL_SECONDS = 1.0
HEARTBEAT_SECONDS = 15.0


def resume_cursor(after: int, last_event_id: str | None) -> int:
    if last_event_id is None:
        return after
    value = query_integer(last_event_id)
    if type(value) is not int or not 0 <= value <= MAX_INTEGER:
        raise ValueError("Last-Event-ID must be a nonnegative int64 cursor")
    return max(after, value)


async def stream_messages(
    service: SimulationService,
    request: Request,
    world: str,
    timeline: str,
    query: StreamPage,
    cursor: int,
) -> AsyncIterator[str]:
    last_send = monotonic()
    while not await request.is_disconnected():
        messages = await run_in_threadpool(
            service.messages, world, timeline, after=cursor, limit=query.limit
        )
        for message in messages:
            if await request.is_disconnected():
                return
            next_cursor, kind = message["cursor"], message["kind"]
            assert isinstance(next_cursor, int) and isinstance(kind, str)
            event_name = kind if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,127}", kind) else "message"
            yield (
                f"id: {next_cursor}\nevent: {event_name}\n"
                f"data: {canonical_bytes(message).decode('utf-8')}\n\n"
            )
            cursor = next_cursor
            last_send = monotonic()
        if not query.follow:
            return  # Finite mode is exactly one bounded page, including an empty page.
        if not messages:
            if monotonic() - last_send >= HEARTBEAT_SECONDS:
                yield ": heartbeat\n\n"
                last_send = monotonic()
            await asyncio.sleep(POLL_SECONDS)

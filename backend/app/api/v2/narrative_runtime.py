"""One lifespan-owned local narrative worker; generation never blocks a turn."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI

from app.ai.jobs.local import LocalCapabilityRouter, LocalNarrativeConfig
from app.ai.jobs.models import AIJob
from app.ai.jobs.provider import ModelRouterNarrativeProvider
from app.ai.jobs.worker import NarrativeWorker, WorkerConfig
from app.storage.jobs import SQLiteJobRepository

logger = logging.getLogger(__name__)


class ConfiguredJobRepository(SQLiteJobRepository):
    def __init__(self, application: FastAPI, config: LocalNarrativeConfig) -> None:
        super().__init__(application.state.simulation_service.store.db)
        self.config_identity = config.identity

    def claim(self, worker_id: str, *, now: float, lease_seconds: float) -> AIJob | None:
        return super().claim(worker_id, now=now, lease_seconds=lease_seconds,
                             provider_config_hash=self.config_identity)


async def _run(worker: NarrativeWorker, owner: str) -> None:
    while True:
        try:
            job = await worker.run_once(owner)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Do not log provider payloads or credentials. Keep the game usable on a model outage.
            logger.error("Local narrative worker paused after a storage or provider error")
            await asyncio.sleep(5)
            continue
        if job is None:
            await asyncio.sleep(1)
        else:
            logger.info("Local narrative job %s", job.status.value)
            await asyncio.sleep(0.2)


@asynccontextmanager
async def narrative_lifespan(application: FastAPI) -> AsyncIterator[None]:
    config: LocalNarrativeConfig | None = application.state.narrative_config
    if config is None:
        yield
        return
    headers = {"Authorization": "Bearer " + config.api_key} if config.api_key else {}
    async with httpx.AsyncClient(
        headers=headers, timeout=config.timeout, trust_env=False,
        limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
    ) as client:
        worker = NarrativeWorker(
            ConfiguredJobRepository(application, config),
            ModelRouterNarrativeProvider(LocalCapabilityRouter(client, config)),
            config=WorkerConfig(request_timeout=config.timeout,
                                lease_seconds=2 * config.timeout + 15),
        )
        task = asyncio.create_task(_run(worker, "local-model:" + config.model),
                                   name="clade-local-narratives")
        logger.info("Local narrative worker configured: %s / %s", config.provider_name, config.model)
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

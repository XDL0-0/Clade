"""Running leases protect an active request from unrelated cleanup."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import Barrier
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from fastapi import BackgroundTasks, HTTPException

from app.api import analytics, simulation
from app.core.session import SimulationSessionManager
from app.schemas.requests import TurnCommand
from app.schemas.responses import TurnReport
from app.services.system import divine_energy

if TYPE_CHECKING:
    from app.core.container import ServiceContainer


@dataclass
class LatchEngine:
    """Only explicit event release or cancellation can finish a fake turn."""

    failure: Exception | None = None
    turn_counter: int = 0
    calls: int = 0
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    finish: asyncio.Event = field(default_factory=asyncio.Event)

    def update_watchlist(self, watchlist: set[str]) -> None:
        pass

    async def run_turns_async(self, command: TurnCommand) -> list[TurnReport]:
        self.calls += 1
        self.entered.set()
        await self.finish.wait()
        if self.failure is not None:
            raise self.failure
        self.turn_counter += command.rounds
        return []


@dataclass
class LatchRouter:
    failure: Exception | None = None
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    finish: asyncio.Event = field(default_factory=asyncio.Event)
    completed: bool = False

    async def reset_client(self) -> None:
        self.entered.set()
        await self.finish.wait()
        if self.failure is not None:
            raise self.failure
        self.completed = True


@dataclass
class FakeEnergy:
    enabled: bool = False

    def regenerate(self, turn: int) -> int:
        return 0


@pytest.fixture(autouse=True)
def isolate_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(divine_energy, "energy_service", FakeEnergy())
    monkeypatch.setattr(simulation, "get_watchlist", lambda: set())


def fake_container(engine: LatchEngine, router: LatchRouter | None = None) -> ServiceContainer:
    return cast(
        "ServiceContainer",
        SimpleNamespace(simulation_engine=engine, model_router=router),
    )


def test_leases_reject_stale_tokens_and_preserve_legacy_setters() -> None:
    session = SimulationSessionManager()
    session.set_running(True)
    assert session.is_running
    assert session.acquire_running() is None
    assert not session.release_running(object())
    session.set_running(False)
    assert not session.is_running

    old_token = session.acquire_running()
    assert old_token is not None
    session.set_running(False)
    assert session.is_running
    assert not session.release_running(object())
    assert session.release_running(old_token)
    new_token = session.acquire_running()
    assert new_token is not None and new_token is not old_token
    assert not session.release_running(old_token)
    assert session.is_running
    assert session.release_running(new_token)
    assert not session.is_running


def test_acquisition_is_atomic_across_threads() -> None:
    session = SimulationSessionManager()
    barrier = Barrier(3)

    def acquire() -> object | None:
        barrier.wait(timeout=5)
        return session.acquire_running()

    with ThreadPoolExecutor(max_workers=2) as executor:
        pending = [executor.submit(acquire) for _ in range(2)]
        barrier.wait(timeout=5)
        tokens = [future.result(timeout=5) for future in pending]
    owners = [token for token in tokens if token is not None]
    assert len(owners) == 1
    assert session.is_running
    assert session.release_running(owners[0])


@pytest.mark.asyncio
async def test_simulation_lock_does_not_hold_thread_lock_across_await() -> None:
    session = SimulationSessionManager()
    with pytest.raises(ValueError, match="failed work"):
        with session.simulation_lock():
            # Another thread must read state while this context is suspended.
            assert await asyncio.to_thread(lambda: session.is_running)
            session.set_running(False)
            assert session.is_running
            with pytest.raises(RuntimeError, match="模拟已在运行中"):
                with session.simulation_lock():
                    pytest.fail("a second context acquired the active lease")
            assert session.is_running
            raise ValueError("failed work")
    assert not session.is_running


@pytest.mark.asyncio
async def test_rejected_requests_cannot_clear_running_or_schedule_autosave() -> None:
    session = SimulationSessionManager()
    engine = LatchEngine()
    container = fake_container(engine)
    background = BackgroundTasks()
    first = asyncio.create_task(
        simulation.run_turns(TurnCommand(rounds=1), background, session, container)
    )
    try:
        await engine.entered.wait()
        for _ in range(2):
            rejected_background = BackgroundTasks()
            with pytest.raises(HTTPException) as rejected:
                await simulation.run_turns(
                    TurnCommand(rounds=1), rejected_background, session, container
                )
            assert rejected.value.status_code == 400
            assert session.is_running
            assert not rejected_background.tasks
        assert engine.calls == 1
        assert not background.tasks
    finally:
        engine.finish.set()
        response = await first
    assert response.status_code == 200
    assert not session.is_running
    # Tasks are inspected only; no real autosave is executed.
    assert len(background.tasks) == 1
    assert background.tasks[0].func is simulation._perform_autosave


@pytest.mark.parametrize(
    ("failure", "status"),
    [(RuntimeError("engine failed"), 500), (HTTPException(status_code=409), 409)],
)
@pytest.mark.asyncio
async def test_failed_request_releases_lease(failure: Exception, status: int) -> None:
    session = SimulationSessionManager()
    engine = LatchEngine(failure=failure)
    background = BackgroundTasks()
    pending = asyncio.create_task(
        simulation.run_turns(TurnCommand(rounds=1), background, session, fake_container(engine))
    )
    await engine.entered.wait()
    assert session.is_running
    engine.finish.set()
    with pytest.raises(HTTPException) as failed:
        await pending
    assert failed.value.status_code == status
    assert not session.is_running
    assert not background.tasks
    token = session.acquire_running()
    assert token is not None
    assert session.release_running(token)


@pytest.mark.asyncio
async def test_cancelled_request_releases_lease_without_autosave() -> None:
    session = SimulationSessionManager()
    engine = LatchEngine()
    background = BackgroundTasks()
    pending = asyncio.create_task(
        simulation.run_turns(TurnCommand(rounds=1), background, session, fake_container(engine))
    )
    await engine.entered.wait()
    assert session.is_running
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert not session.is_running
    assert not background.tasks


@pytest.mark.asyncio
async def test_abort_awaits_reset_without_releasing_an_active_turn() -> None:
    session = SimulationSessionManager()
    engine = LatchEngine()
    router = LatchRouter()
    container = fake_container(engine, router)
    pending = asyncio.create_task(
        simulation.run_turns(TurnCommand(rounds=1), BackgroundTasks(), session, container)
    )
    await engine.entered.wait()
    abort = asyncio.create_task(analytics.abort_current_tasks(session, container))
    try:
        await router.entered.wait()
        assert not abort.done()
        assert session.abort_requested
        assert session.is_running
        router.finish.set()
        response = await abort
        assert router.completed
        assert response["success"] is True
        assert "运行中的回合可能继续" in response["message"]
        assert not session.abort_requested
        assert session.is_running
        with pytest.raises(HTTPException) as rejected:
            await simulation.run_turns(TurnCommand(rounds=1), BackgroundTasks(), session, container)
        assert rejected.value.status_code == 400
        assert session.is_running
    finally:
        router.finish.set()
        await abort
        engine.finish.set()
        await pending
    assert not session.is_running


@pytest.mark.asyncio
async def test_failed_reset_preserves_lease_and_clears_abort_request() -> None:
    session = SimulationSessionManager()
    token = session.acquire_running()
    assert token is not None
    router = LatchRouter(failure=RuntimeError("reset failed"))
    router.finish.set()
    with pytest.raises(RuntimeError, match="reset failed"):
        await analytics.abort_current_tasks(session, fake_container(LatchEngine(), router))
    assert not session.abort_requested
    assert session.is_running
    assert session.release_running(token)


@pytest.mark.asyncio
async def test_unsupported_reset_reports_failure() -> None:
    session = SimulationSessionManager()
    response = await analytics.abort_current_tasks(session, fake_container(LatchEngine()))
    assert response["success"] is False
    assert not session.abort_requested

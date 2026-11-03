import asyncio
from uuid import uuid4

import httpx
import pytest

from app import main
from app.agent.orchestrator import AgentOrchestrator
from app.config.settings import Settings
from app.llm.client import get_chat_model
from app.schemas.message import SendMessageRequest
from app.services.session_service import SessionService


class BlockingModel:
    def __init__(self, ignore_cancel=False):
        self.started = asyncio.Event()
        self.calls = 0
        self.cancelled = False
        self.ignore_cancel = ignore_cancel

    async def generate_reply(self, messages):
        self.calls += 1
        self.started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            self.cancelled = True
            if self.ignore_cancel:
                return "late reply after cancellation"
            raise
        return "reply"


@pytest.mark.parametrize("action", ["cancel", "end", "clear"])
@pytest.mark.parametrize("ignore_cancel", [False, True])
def test_interrupts_prevent_late_commits(monkeypatch, action, ignore_cancel):
    monkeypatch.setattr(main, "session_service", SessionService())

    async def run():
        model = BlockingModel(ignore_cancel)
        main.app.dependency_overrides[get_chat_model] = lambda: model
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test"
        ) as client:
            session_id = (await client.post("/sessions")).json()["session_id"]
            base = f"/sessions/{session_id}"
            pending = asyncio.create_task(
                client.post(base + "/messages", json={"content": "in flight"})
            )
            await asyncio.wait_for(model.started.wait(), 1)
            running = (await client.get(base)).json()
            assert running["workflow"]["phase"] == "RUNNING"
            if action == "cancel":
                stopped = await client.post(
                    base + "/workflow/cancel",
                    json={"run_id": running["workflow"]["run_id"]},
                )
                assert stopped.json()["workflow"]["phase"] == "CANCELLED"
            elif action == "end":
                stopped = await client.post(base + "/end")
                assert stopped.json()["status"] == "ENDED"
            else:
                stopped = await client.delete(base + "/messages")
                assert stopped.json()["workflow"]["phase"] == "IDLE"
            assert stopped.status_code == 200
            failed = await asyncio.wait_for(pending, 1)
            assert failed.status_code == 409
            assert failed.json()["detail"]["code"] == "run_cancelled"
            session = (await client.get(base)).json()
            assert session["messages"] == []
            assert model.cancelled

    try:
        asyncio.run(run())
    finally:
        main.app.dependency_overrides.pop(get_chat_model, None)


@pytest.mark.parametrize("action", ["end", "clear"])
def test_lifecycle_barrier_rejects_already_queued_messages(monkeypatch, action):
    monkeypatch.setattr(main, "session_service", SessionService())

    async def run():
        model = BlockingModel()
        main.app.dependency_overrides[get_chat_model] = lambda: model
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test"
        ) as client:
            session_id = (await client.post("/sessions")).json()["session_id"]
            base = f"/sessions/{session_id}"
            first = asyncio.create_task(
                client.post(base + "/messages", json={"content": "first"})
            )
            await asyncio.wait_for(model.started.wait(), 1)
            queued = asyncio.create_task(
                client.post(base + "/messages", json={"content": "queued"})
            )
            await asyncio.sleep(0.02)
            if action == "end":
                stopped = await client.post(base + "/end")
            else:
                stopped = await client.delete(base + "/messages")
            assert stopped.status_code == 200
            results = await asyncio.wait_for(asyncio.gather(first, queued), 1)
            assert [result.status_code for result in results] == [409, 409]
            assert model.calls == 1
            assert (await client.get(base)).json()["messages"] == []

    try:
        asyncio.run(run())
    finally:
        main.app.dependency_overrides.pop(get_chat_model, None)


def test_concurrent_duplicate_requests_call_model_once(monkeypatch):
    monkeypatch.setattr(main, "session_service", SessionService())

    class Model:
        calls = 0

        async def generate_reply(self, messages):
            self.calls += 1
            await asyncio.sleep(0.02)
            return "one result"

    async def run():
        model = Model()
        main.app.dependency_overrides[get_chat_model] = lambda: model
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test"
        ) as client:
            session_id = (await client.post("/sessions")).json()["session_id"]
            path = f"/sessions/{session_id}/messages"
            payload = {"content": "duplicate", "request_id": str(uuid4())}
            results = await asyncio.gather(
                client.post(path, json=payload), client.post(path, json=payload)
            )
            assert [result.status_code for result in results] == [200, 200]
            assert results[0].json() == results[1].json()
            assert model.calls == 1
            assert len((await client.get(f"/sessions/{session_id}")).json()["messages"]) == 2

    try:
        asyncio.run(run())
    finally:
        main.app.dependency_overrides.pop(get_chat_model, None)


def test_runtime_timeout_bounds_an_injected_model(monkeypatch):
    monkeypatch.setattr(main, "session_service", SessionService())
    settings = Settings(_env_file=None, llm_timeout_seconds=0.02)
    monkeypatch.setattr(
        main, "get_orchestrator",
        lambda: AgentOrchestrator(main.session_service, settings),
    )

    async def run():
        model = BlockingModel()
        main.app.dependency_overrides[get_chat_model] = lambda: model
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test"
        ) as client:
            session_id = (await client.post("/sessions")).json()["session_id"]
            response = await client.post(
                f"/sessions/{session_id}/messages", json={"content": "timeout"}
            )
            assert response.status_code == 504
            assert model.cancelled
            session = (await client.get(f"/sessions/{session_id}")).json()
            assert session["messages"] == []
            assert session["workflow"]["phase"] == "FAILED"

    try:
        asyncio.run(run())
    finally:
        main.app.dependency_overrides.pop(get_chat_model, None)


@pytest.mark.parametrize("action", ["end_session", "clear_history"])
def test_cancelled_lifecycle_operation_releases_its_barrier(action):
    service = SessionService()
    runtime = AgentOrchestrator(service, Settings(_env_file=None))
    session = service.create()

    class Model:
        async def generate_reply(self, messages):
            return "after cancelled lifecycle operation"

    async def run():
        lock = service.get_lock(session.session_id)
        await lock.acquire()
        operation = asyncio.create_task(getattr(runtime, action)(session.session_id))
        await asyncio.sleep(0)
        operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)
        lock.release()
        response = await runtime.handle_message(
            session.session_id,
            SendMessageRequest(content="hello"),
            Model(),
        )
        assert response.reply == "after cancelled lifecycle operation"
        assert len(session.messages) == 2

    asyncio.run(run())

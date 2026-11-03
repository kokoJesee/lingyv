import asyncio

import httpx

from app import main
from app.llm.client import get_chat_model
from app.llm.errors import LLMError
from app.services.session_service import SessionService


def test_previous_turn_is_forwarded_to_model(client, recording_model):
    session_id = client.post("/sessions").json()["session_id"]
    path = f"/sessions/{session_id}/messages"
    first = client.post(path, json={"content": "第一条"})
    assert first.status_code == 200
    second = client.post(path, json={"content": "第二条"})
    assert second.status_code == 200
    assert [message.role for message in recording_model.calls[1]] == [
        "user", "assistant", "user"
    ]
    assert recording_model.calls[1][0].content == "第一条"
    assert recording_model.calls[1][1].content == first.json()["reply"]
    assert recording_model.calls[1][2].content == "第二条"


def test_model_failure_preserves_history_and_can_be_retried(
    client, recording_model
):
    session_id = client.post("/sessions").json()["session_id"]
    path = f"/sessions/{session_id}/messages"
    assert client.post(path, json={"content": "成功的第一轮"}).status_code == 200
    before = client.get(f"/sessions/{session_id}").json()

    class FailingModel:
        async def generate_reply(self, messages):
            raise LLMError("model_timeout", "模型回复超时，请稍后重试。", 504)

    main.app.dependency_overrides[get_chat_model] = lambda: FailingModel()
    failed = client.post(path, json={"content": "失败的消息"})
    assert failed.status_code == 504
    assert failed.json()["detail"]["code"] == "model_timeout"
    after = client.get(f"/sessions/{session_id}").json()
    assert after["messages"] == before["messages"]
    assert after["state"] == before["state"]
    assert after["workflow"]["phase"] == "FAILED"
    assert after["workflow"]["stop_reason"] == "model_error"

    main.app.dependency_overrides[get_chat_model] = lambda: recording_model
    retried = client.post(path, json={"content": "再次尝试"})
    assert retried.status_code == 200
    history = client.get(f"/sessions/{session_id}").json()["messages"]
    assert len(history) == 4
    assert "失败的消息" not in [message["content"] for message in history]


def test_same_session_requests_are_serialized(monkeypatch):
    class SlowModel:
        def __init__(self):
            self.context_sizes = []
            self.active = 0
            self.max_active = 0

        async def generate_reply(self, messages):
            self.context_sizes.append(len(messages))
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            return "并发测试回复"

    model = SlowModel()
    monkeypatch.setattr(main, "session_service", SessionService())
    main.app.dependency_overrides[get_chat_model] = lambda: model

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test"
        ) as client:
            session_id = (await client.post("/sessions")).json()["session_id"]
            path = f"/sessions/{session_id}/messages"
            responses = await asyncio.gather(
                client.post(path, json={"content": "第一条"}),
                client.post(path, json={"content": "第二条"}),
            )
            assert [response.status_code for response in responses] == [200, 200]
            history = (await client.get(f"/sessions/{session_id}")).json()["messages"]
            assert len(history) == 4
            assert [message["role"] for message in history] == [
                "user", "assistant", "user", "assistant"
            ]

    try:
        asyncio.run(run())
        assert model.max_active == 1
        assert model.context_sizes == [1, 3]
    finally:
        main.app.dependency_overrides.pop(get_chat_model, None)

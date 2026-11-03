from collections.abc import Sequence

import pytest
from fastapi.testclient import TestClient

from app import main
from app.llm.client import get_chat_model
from app.schemas.session import Message
from app.services.session_service import SessionService


class RecordingModel:
    def __init__(self):
        self.calls: list[list[Message]] = []

    async def generate_reply(self, messages: Sequence[Message]) -> str:
        self.calls.append([message.model_copy() for message in messages])
        return f"测试回复 {len(self.calls)}"


@pytest.fixture
def recording_model():
    return RecordingModel()


@pytest.fixture
def client(monkeypatch, recording_model):
    monkeypatch.setattr(main, "session_service", SessionService())
    main.app.dependency_overrides[get_chat_model] = lambda: recording_model
    try:
        with TestClient(main.app) as test_client:
            yield test_client
    finally:
        main.app.dependency_overrides.pop(get_chat_model, None)

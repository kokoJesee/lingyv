import asyncio
import json
import logging

import httpx
import pytest

from app.config.settings import Settings
from app.llm.client import DeepSeekClient
from app.llm.errors import LLMError
from app.schemas.session import Message


def make_client(handler, key="test-secret-key"):
    settings = Settings(_env_file=None, deepseek_api_key=key)
    return DeepSeekClient(settings, transport=httpx.MockTransport(handler))


def test_deepseek_request_format_and_reply(caplog):
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["authorization"]
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json={
            "choices": [{
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": " 有效回复 "},
            }]
        })

    messages = [
        Message(role="user", content="private-input-sentinel"),
        Message(role="assistant", content="此前回复"),
        Message(role="user", content="继续"),
    ]
    with caplog.at_level(logging.INFO, logger="app"):
        reply = asyncio.run(make_client(handler).generate_reply(messages))
    assert reply == "有效回复"
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["authorization"] == "Bearer test-secret-key"
    payload = captured["payload"]
    assert payload["model"] == "deepseek-flash"
    assert payload["stream"] is False
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][1:] == [message.model_dump() for message in messages]
    assert "test-secret-key" not in caplog.text
    assert "private-input-sentinel" not in caplog.text
    assert "有效回复" not in caplog.text


def test_missing_key_does_not_make_a_request():
    def handler(request):
        pytest.fail("Missing-key requests must not contact the provider")

    with pytest.raises(LLMError) as caught:
        asyncio.run(make_client(handler, key="").generate_reply([]))
    assert caught.value.status_code == 503
    assert caught.value.code == "model_not_configured"


@pytest.mark.parametrize(
    "upstream_status, expected_status, expected_code",
    [
        (401, 502, "model_authentication_failed"),
        (403, 502, "model_authentication_failed"),
        (402, 502, "model_balance_insufficient"),
        (429, 503, "model_unavailable"),
        (500, 503, "model_unavailable"),
        (503, 503, "model_unavailable"),
        (400, 502, "model_request_rejected"),
        (422, 502, "model_request_rejected"),
    ],
)
def test_provider_errors_are_safe(
    upstream_status, expected_status, expected_code, caplog
):
    def handler(request):
        return httpx.Response(
            upstream_status,
            json={"error": "UPSTREAM_BODY private-input test-secret-key"},
        )

    with caplog.at_level(logging.INFO, logger="app"):
        with pytest.raises(LLMError) as caught:
            asyncio.run(make_client(handler).generate_reply([]))
    error = caught.value
    assert error.status_code == expected_status
    assert error.code == expected_code
    assert "UPSTREAM_BODY" not in str(error) + caplog.text
    assert "test-secret-key" not in str(error) + caplog.text


@pytest.mark.parametrize(
    "exception_type, expected_status",
    [(httpx.ReadTimeout, 504), (httpx.ConnectError, 502)],
)
def test_network_failures_are_safe(exception_type, expected_status):
    def handler(request):
        raise exception_type("sensitive upstream details", request=request)

    with pytest.raises(LLMError) as caught:
        asyncio.run(make_client(handler).generate_reply([]))
    assert caught.value.status_code == expected_status
    assert "sensitive upstream details" not in str(caught.value)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"choices": []},
        {"choices": None},
        {"choices": [{"finish_reason": "stop", "message": {"content": None}}]},
        {"choices": [{"finish_reason": "stop", "message": {"content": "  "}}]},
        {"choices": [{"finish_reason": "length", "message": {"content": "截断"}}]},
        {"choices": [{"finish_reason": "tool_calls", "message": {"content": ""}}]},
    ],
)
def test_invalid_or_incomplete_responses_are_rejected(body):
    def handler(request):
        return httpx.Response(200, json=body)

    with pytest.raises(LLMError) as caught:
        asyncio.run(make_client(handler).generate_reply([]))
    assert caught.value.status_code == 502


def test_non_json_response_is_rejected():
    def handler(request):
        return httpx.Response(200, text="<html>upstream error</html>")

    with pytest.raises(LLMError) as caught:
        asyncio.run(make_client(handler).generate_reply([]))
    assert caught.value.code == "invalid_model_response"


def test_config_hides_the_api_key():
    settings = Settings(_env_file=None, deepseek_api_key="test-secret-key")
    assert settings.key_configured
    assert "test-secret-key" not in repr(settings)

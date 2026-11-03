from uuid import uuid4

import pytest

def create_session(client):
    response = client.post("/sessions")
    assert response.status_code == 201
    return response.json()["session_id"]


def test_two_turn_conversation_preserves_ordered_history(client):
    session_id = create_session(client)
    contents = ["最近朋友不怎么理我了。", "她这周都没有主动找我聊天。"]
    replies = []

    for content in contents:
        response = client.post(
            f"/sessions/{session_id}/messages",
            json={"content": content},
        )
        assert response.status_code == 200
        result = response.json()
        assert result["session_id"] == session_id
        assert result["state"] == "INITIAL"
        assert result["reply"]
        replies.append(result["reply"])

    response = client.get(f"/sessions/{session_id}")
    assert response.status_code == 200
    assert response.json()["messages"] == [
        {"role": "user", "content": contents[0]},
        {"role": "assistant", "content": replies[0]},
        {"role": "user", "content": contents[1]},
        {"role": "assistant", "content": replies[1]},
    ]


def test_messages_are_isolated_between_sessions(client):
    first = create_session(client)
    second = create_session(client)
    first_response = client.post(
        f"/sessions/{first}/messages", json={"content": "第一段经历"}
    )
    assert first_response.status_code == 200
    assert client.get(f"/sessions/{second}").json()["messages"] == []

    second_response = client.post(
        f"/sessions/{second}/messages", json={"content": "另一段经历"}
    )
    assert second_response.status_code == 200
    first_messages = client.get(f"/sessions/{first}").json()["messages"]
    second_messages = client.get(f"/sessions/{second}").json()["messages"]
    assert len(first_messages) == len(second_messages) == 2
    assert first_messages[0]["content"] == "第一段经历"
    assert second_messages[0]["content"] == "另一段经历"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"content": ""},
        {"content": "   "},
        {"content": "\n\t"},
        {"content": None},
        {"content": 123},
        {"content": ["message"]},
        {"content": "hello", "role": "assistant"},
        {"content": "hello", "state": "CLARIFICATION"},
    ],
)
def test_invalid_message_does_not_change_history(client, payload):
    session_id = create_session(client)
    response = client.post(
        f"/sessions/{session_id}/messages", json=payload
    )
    assert response.status_code == 422
    assert client.get(f"/sessions/{session_id}").json()["messages"] == []


def test_message_content_is_trimmed(client):
    session_id = create_session(client)
    response = client.post(
        f"/sessions/{session_id}/messages",
        json={"content": "  最近有点失落。\n"},
    )
    assert response.status_code == 200
    messages = client.get(f"/sessions/{session_id}").json()["messages"]
    assert messages[0]["content"] == "最近有点失落。"


def test_missing_session_returns_404(client):
    missing_id = uuid4()
    response = client.post(
        f"/sessions/{missing_id}/messages", json={"content": "hello"}
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Session not found"}
    assert client.get(f"/sessions/{missing_id}").status_code == 404


def test_invalid_session_id_returns_422(client):
    response = client.post(
        "/sessions/not-a-uuid/messages", json={"content": "hello"}
    )
    assert response.status_code == 422


def test_existing_routes_and_api_documentation(client):
    assert client.get("/").status_code == 200
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/docs").status_code == 200
    schema = client.get("/openapi.json").json()
    endpoint = schema["paths"]["/sessions/{session_id}/messages"]["post"]
    assert "requestBody" in endpoint
    assert "200" in endpoint["responses"]

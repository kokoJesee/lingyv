from uuid import UUID, uuid4

import pytest

from app import main
from app.agent.orchestrator import AgentOrchestrator
from app.config.settings import Settings
from app.llm.client import get_chat_model
from app.llm.errors import LLMError


def new_session(client):
    return client.post("/sessions").json()["session_id"]


def test_demo_waits_for_explicit_current_confirmation(client, recording_model):
    session_id = new_session(client)
    base = f"/sessions/{session_id}"
    waiting = client.post(base + "/workflow/demo")
    assert waiting.status_code == 200
    workflow = waiting.json()["workflow"]
    assert workflow["phase"] == "WAITING_CONFIRMATION"
    assert workflow["stop_reason"] == "awaiting_user"
    assert workflow["confirmation_token"]
    before = client.get(base).json()
    assert client.post(base + "/messages", json={"content": "hello"}).status_code == 409
    assert client.get(base).json() == before
    assert recording_model.calls == []

    wrong = {"run_id": workflow["run_id"], "confirmation_token": str(uuid4())}
    assert client.post(base + "/workflow/confirm", json=wrong).status_code == 409
    assert client.get(base).json() == before
    correct = {key: workflow[key] for key in ("run_id", "confirmation_token")}
    completed = client.post(base + "/workflow/confirm", json=correct)
    assert completed.status_code == 200
    assert completed.json()["workflow"]["phase"] == "COMPLETED"
    assert completed.json()["state"] == "INITIAL"
    assert completed.json()["messages"] == []
    assert client.post(base + "/workflow/confirm", json=correct).status_code == 409


def test_cancelled_or_cleared_confirmation_cannot_resume_a_new_run(client):
    session_id = new_session(client)
    base = f"/sessions/{session_id}"
    old = client.post(base + "/workflow/demo").json()["workflow"]
    assert client.post(base + "/workflow/demo").status_code == 409
    cancelled = client.post(
        base + "/workflow/cancel", json={"run_id": old["run_id"]}
    )
    assert cancelled.json()["workflow"]["phase"] == "CANCELLED"
    current = client.post(base + "/workflow/demo").json()["workflow"]
    stale = {key: old[key] for key in ("run_id", "confirmation_token")}
    assert client.post(base + "/workflow/confirm", json=stale).status_code == 409
    assert client.get(base).json()["workflow"] == current
    cleared = client.delete(base + "/messages")
    assert cleared.json()["workflow"]["phase"] == "IDLE"
    current_confirmation = {
        key: current[key] for key in ("run_id", "confirmation_token")
    }
    assert client.post(
        base + "/workflow/confirm", json=current_confirmation
    ).status_code == 409


def test_end_is_idempotent_and_clear_preserves_ended_status(client, recording_model):
    session_id = new_session(client)
    base = f"/sessions/{session_id}"
    assert client.post(base + "/messages", json={"content": "hello"}).status_code == 200
    first = client.post(base + "/end").json()
    assert first["status"] == "ENDED"
    assert client.post(base + "/end").json() == first
    assert client.post(base + "/messages", json={"content": "later"}).status_code == 409
    assert client.post(base + "/workflow/demo").status_code == 409
    cleared = client.delete(base + "/messages").json()
    assert cleared["messages"] == []
    assert cleared["status"] == "ENDED"
    assert client.post(base + "/messages", json={"content": "later"}).status_code == 409
    assert len(recording_model.calls) == 1
    fresh = new_session(client)
    assert client.post(
        f"/sessions/{fresh}/messages", json={"content": "new"}
    ).status_code == 200


def test_clear_discards_cached_content_without_resurrecting_it(client, recording_model):
    session_id = new_session(client)
    base = f"/sessions/{session_id}"
    request_id = str(uuid4())
    payload = {"content": "private-cleared-sentinel", "request_id": request_id}
    assert client.post(base + "/messages", json=payload).status_code == 200
    assert client.delete(base + "/messages").json()["messages"] == []
    replay = client.post(base + "/messages", json=payload)
    assert replay.status_code == 409
    assert replay.json()["detail"]["code"] == "request_cleared"
    assert client.get(base).json()["messages"] == []
    assert len(recording_model.calls) == 1
    assert "private-cleared-sentinel" not in repr(
        main.session_service.require(UUID(session_id)).operations
    )


def test_session_list_contains_metadata_only(client):
    first = new_session(client)
    second = new_session(client)
    client.post(f"/sessions/{first}/messages", json={"content": "private-list-sentinel"})
    summaries = client.get("/sessions").json()
    by_id = {item["session_id"]: item for item in summaries}
    assert by_id[first]["message_count"] == 2
    assert by_id[second]["message_count"] == 0
    assert "messages" not in by_id[first]
    assert "private-list-sentinel" not in str(summaries)


def test_same_request_reuses_result_but_new_request_can_repeat_text(client, recording_model):
    session_id = new_session(client)
    path = f"/sessions/{session_id}/messages"
    payload = {"content": "same text", "request_id": str(uuid4())}
    first = client.post(path, json=payload)
    second = client.post(path, json=payload)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(recording_model.calls) == 1
    conflicting = client.post(path, json={**payload, "content": "different"})
    assert conflicting.status_code == 409
    assert conflicting.json()["detail"]["code"] == "request_id_conflict"
    assert len(recording_model.calls) == 1
    repeated_text = client.post(path, json={"content": "same text"})
    assert repeated_text.status_code == 200
    assert len(recording_model.calls) == 2


def test_request_ids_are_scoped_to_each_session(client, recording_model):
    payload = {"content": "hello", "request_id": str(uuid4())}
    for _ in range(2):
        session_id = new_session(client)
        assert client.post(
            f"/sessions/{session_id}/messages", json=payload
        ).status_code == 200
    assert len(recording_model.calls) == 2


def test_failure_replay_does_not_repeat_provider_call(client, recording_model):
    class Failing:
        calls = 0

        async def generate_reply(self, messages):
            self.calls += 1
            raise LLMError("model_timeout", "timeout", 504)

    model = Failing()
    main.app.dependency_overrides[get_chat_model] = lambda: model
    session_id = new_session(client)
    path = f"/sessions/{session_id}/messages"
    payload = {"content": "hello", "request_id": str(uuid4())}
    first = client.post(path, json=payload)
    replay = client.post(path, json=payload)
    assert first.status_code == replay.status_code == 504
    assert first.json() == replay.json()
    assert model.calls == 1
    main.app.dependency_overrides[get_chat_model] = lambda: recording_model
    assert client.post(path, json={"content": "retry with new id"}).status_code == 200


@pytest.mark.parametrize(
    "settings, expected_code, expected_status, expected_calls",
    [
        ({"runtime_max_context_chars": 1}, "context_limit_exceeded", 413, 0),
        ({"runtime_max_steps": 1}, "step_limit_exceeded", 409, 0),
        ({"runtime_max_steps": 2}, "step_limit_exceeded", 409, 1),
    ],
)
def test_budgets_stop_execution_without_saving_a_partial_turn(
    client, recording_model, monkeypatch, settings, expected_code,
    expected_status, expected_calls,
):
    configured = Settings(_env_file=None, **settings)
    monkeypatch.setattr(
        main, "get_orchestrator",
        lambda: AgentOrchestrator(main.session_service, configured),
    )
    session_id = new_session(client)
    response = client.post(f"/sessions/{session_id}/messages", json={"content": "hello"})
    assert response.status_code == expected_status
    assert response.json()["detail"]["code"] == expected_code
    session = client.get(f"/sessions/{session_id}").json()
    assert session["messages"] == []
    assert session["workflow"]["phase"] == "FAILED"
    assert len(recording_model.calls) == expected_calls


def test_full_request_cache_rejects_new_ids_but_allows_replays(client, monkeypatch):
    configured = Settings(_env_file=None, runtime_max_requests_per_session=1)
    monkeypatch.setattr(
        main, "get_orchestrator",
        lambda: AgentOrchestrator(main.session_service, configured),
    )
    session_id = new_session(client)
    path = f"/sessions/{session_id}/messages"
    payload = {"content": "one", "request_id": str(uuid4())}
    first = client.post(path, json=payload)
    assert first.status_code == 200
    exhausted = client.post(path, json={"content": "two"})
    assert exhausted.status_code == 409
    assert exhausted.json()["detail"]["code"] == "request_limit_exceeded"
    assert client.post(path, json=payload).json() == first.json()


def test_invalid_model_output_fails_without_committing(client):
    class Invalid:
        async def generate_reply(self, messages):
            return {"invalid": "private-model-sentinel"}

    main.app.dependency_overrides[get_chat_model] = lambda: Invalid()
    session_id = new_session(client)
    response = client.post(
        f"/sessions/{session_id}/messages", json={"content": "hello"}
    )
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "output_validation_failed"
    session = client.get(f"/sessions/{session_id}").json()
    assert session["messages"] == []
    assert session["workflow"]["stop_reason"] == "output_invalid"


def test_events_do_not_contain_private_content(client, caplog):
    session_id = new_session(client)
    payload = {"content": "private-event-sentinel", "request_id": str(uuid4())}
    response = client.post(f"/sessions/{session_id}/messages", json=payload)
    assert response.status_code == 200
    events = client.get(f"/sessions/{session_id}/events").json()
    assert [event["event"] for event in events] == [
        "run_started", "step_started", "step_started", "run_completed"
    ]
    assert all(event["request_id"] == payload["request_id"] for event in events)
    assert all(event["run_id"] == response.json()["run_id"] for event in events)
    assert "private-event-sentinel" not in str(events) + caplog.text
    assert events[-1]["elapsed_ms"] >= 0


def test_event_buffer_is_bounded(client, recording_model):
    session_id = new_session(client)
    payload = {"content": "bounded", "request_id": str(uuid4())}
    path = f"/sessions/{session_id}/messages"
    for _ in range(205):
        assert client.post(path, json=payload).status_code == 200
    assert len(client.get(f"/sessions/{session_id}/events").json()) == 200
    assert len(recording_model.calls) == 1


def test_replayed_result_keeps_original_trace_and_does_not_resume_waiting_workflow(client):
    session_id = new_session(client)
    base = f"/sessions/{session_id}"
    payload = {"content": "first", "request_id": str(uuid4())}
    original = client.post(base + "/messages", json=payload).json()
    waiting = client.post(base + "/workflow/demo").json()["workflow"]
    replay = client.post(base + "/messages", json=payload)
    assert replay.status_code == 200
    assert replay.json() == original
    assert client.get(base).json()["workflow"] == waiting
    event = client.get(base + "/events").json()[-1]
    assert event["run_id"] == original["run_id"]
    assert event["phase"] == "COMPLETED"
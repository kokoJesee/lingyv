import asyncio
import os
from pathlib import Path
import subprocess
import sys
from uuid import UUID, uuid4

import httpx
import pytest

from app import main


@pytest.fixture
def base(client):
    session_id = client.post("/sessions").json()["session_id"]
    return f"/sessions/{session_id}"


def create(client, base, **extra):
    response = client.post(base + "/drafts", json={
        "content": {"title": "original", "items": ["one"]}, **extra,
    })
    assert response.status_code == 201
    return response.json()


def confirm_payload(draft, **extra):
    return {
        "expected_revision": draft["revision"],
        "confirmation_token": draft["confirmation_token"], **extra,
    }


def test_edit_and_confirmation_are_bound_to_current_version(client, base):
    first = create(client, base)
    path = base + "/drafts/" + first["draft_id"]
    second = client.put(path, json={
        "expected_revision": 1, "content": {"title": "corrected", "items": ["two"]},
    }).json()
    assert second["revision"] == 2
    assert second["confirmation_token"] != first["confirmation_token"]
    stale = client.post(path + "/confirm", json=confirm_payload(first))
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "stale_revision"
    assert client.get(path).json() == second
    wrong = client.post(path + "/confirm", json={
        "expected_revision": 2, "confirmation_token": str(uuid4()),
    })
    assert wrong.status_code == 409
    assert wrong.json()["detail"]["code"] == "stale_confirmation"
    confirmed = client.post(path + "/confirm", json=confirm_payload(second)).json()
    assert confirmed["status"] == "CONFIRMED"
    assert confirmed["revision"] == confirmed["confirmed_revision"] == 3
    edited = client.put(path, json={
        "expected_revision": 3, "content": {"title": "edited again", "items": ["three"]},
    }).json()
    assert edited["revision"] == 4
    assert edited["status"] == "PROPOSED"
    assert edited["confirmed_revision"] is None
    assert edited["confirmation_token"]


def test_stale_edit_does_not_overwrite_current_content(client, base):
    first = create(client, base)
    path = base + "/drafts/" + first["draft_id"]
    payload = {"expected_revision": 1, "content": {"title": "new", "items": ["new"]}}
    updated = client.put(path, json=payload).json()
    stale = client.put(path, json={**payload, "content": {"title": "old", "items": ["old"]}})
    assert stale.status_code == 409
    assert client.get(path).json() == updated


@pytest.mark.parametrize("confirmed_first", [False, True])
def test_user_can_reject_proposed_or_withdraw_confirmed_draft(client, base, confirmed_first):
    draft = create(client, base)
    path = base + "/drafts/" + draft["draft_id"]
    if confirmed_first:
        draft = client.post(path + "/confirm", json=confirm_payload(draft)).json()
    rejected = client.post(path + "/reject", json={"expected_revision": draft["revision"]}).json()
    assert rejected["status"] == "REJECTED"
    assert rejected["confirmed_revision"] is None
    assert rejected["confirmation_token"] is None
    blocked = client.put(path, json={
        "expected_revision": rejected["revision"],
        "content": {"title": "no", "items": ["no"]},
    })
    assert blocked.status_code == 409
    assert client.get(path).json() == rejected


@pytest.mark.parametrize("action", ["edit", "confirm", "reject", "create"])
def test_ended_session_rejects_new_draft_changes(client, base, action):
    draft = create(client, base)
    path = base + "/drafts/" + draft["draft_id"]
    client.post(base + "/end")
    if action == "edit":
        response = client.put(path, json={
            "expected_revision": 1, "content": {"title": "no", "items": ["no"]},
        })
    elif action == "confirm":
        response = client.post(path + "/confirm", json=confirm_payload(draft))
    elif action == "reject":
        response = client.post(path + "/reject", json={"expected_revision": 1})
    else:
        response = client.post(base + "/drafts", json={"content": draft["content"]})
    assert response.status_code == 409
    assert client.get(path).json() == draft


def test_operation_replay_and_conflicts_do_not_mutate_latest_draft(client, base):
    operation_id = str(uuid4())
    first = create(client, base, operation_id=operation_id)
    assert create(client, base, operation_id=operation_id) == first
    assert len(client.get(base + "/drafts").json()) == 1
    path = base + "/drafts/" + first["draft_id"]
    updated = client.put(path, json={
        "expected_revision": 1, "content": {"title": "latest", "items": ["latest"]},
    }).json()
    assert create(client, base, operation_id=operation_id) == first
    assert client.get(path).json() == updated
    conflict = client.post(base + "/drafts", json={
        "operation_id": operation_id,
        "content": {"title": "conflict", "items": ["one"]},
    })
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "operation_id_conflict"


@pytest.mark.parametrize("action", ["edit", "confirm", "reject"])
def test_mutation_replays_are_idempotent(client, base, action):
    draft = create(client, base)
    path = base + "/drafts/" + draft["draft_id"]
    payload = {"expected_revision": 1, "operation_id": str(uuid4())}
    if action == "edit":
        payload["content"] = {"title": "changed", "items": ["changed"]}
        call = lambda: client.put(path, json=payload)
    elif action == "confirm":
        payload["confirmation_token"] = draft["confirmation_token"]
        call = lambda: client.post(path + "/confirm", json=payload)
    else:
        call = lambda: client.post(path + "/reject", json=payload)
    first, second = call(), call()
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert client.get(path).json() == first.json()


def test_clear_removes_drafts_and_cached_content_without_resurrection(client, base):
    operation_id = str(uuid4())
    draft = create(client, base, operation_id=operation_id)
    client.delete(base + "/messages")
    assert client.get(base + "/drafts").json() == []
    assert client.get(base + "/drafts/" + draft["draft_id"]).status_code == 404
    replay = client.post(base + "/drafts", json={
        "operation_id": operation_id, "content": draft["content"],
    })
    assert replay.status_code == 409
    assert replay.json()["detail"]["code"] == "request_cleared"
    record = main.session_service.require(UUID(draft["session_id"]))
    assert all(op.result is None for op in record.draft_operations.values())


def test_cross_session_draft_ids_are_not_accessible(client, base):
    draft = create(client, base)
    other = "/sessions/" + client.post("/sessions").json()["session_id"]
    assert client.get(other + "/drafts").json() == []
    assert client.get(other + "/drafts/" + draft["draft_id"]).status_code == 404
    assert client.post(other + "/drafts/" + draft["draft_id"] + "/confirm", json=confirm_payload(draft)).status_code == 404


@pytest.mark.parametrize("content", [
    {"title": "", "items": ["one"]},
    {"title": "   ", "items": ["one"]},
    {"title": "title", "items": []},
    {"title": "title", "items": ["  "]},
    {"title": 123, "items": ["one"]},
    {"title": "title", "items": ["one"], "diagnosis": "forbidden"},
])
def test_invalid_content_is_rejected_without_creating_a_draft(client, base, content):
    assert client.post(base + "/drafts", json={"content": content}).status_code == 422
    assert client.get(base + "/drafts").json() == []


def test_concurrent_edits_cannot_both_overwrite_same_version(client, base):
    first = create(client, base)
    path = base + "/drafts/" + first["draft_id"]

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test"
        ) as api:
            return await asyncio.gather(*[
                api.put(path, json={
                    "expected_revision": 1,
                    "content": {"title": str(index), "items": ["one"]},
                }) for index in range(2)
            ])

    results = asyncio.run(run())
    assert sorted(response.status_code for response in results) == [200, 409]
    assert client.get(path).json()["revision"] == 2


def test_draft_logs_omit_content_and_do_not_advance_business_state(client, base, caplog):
    response = client.post(base + "/drafts", json={
        "content": {"title": "private-draft-sentinel", "items": ["private-item"]},
    })
    assert response.status_code == 201
    assert "private-draft-sentinel" not in caplog.text
    assert "private-item" not in caplog.text
    session = client.get(base).json()
    assert session["state"] == "INITIAL"
    assert session["workflow"]["phase"] == "IDLE"
    assert session["messages"] == []


@pytest.mark.parametrize("interactive", [False, True])
def test_terminal_draft_entrypoints_are_offline(interactive):
    arguments = ["--offline"] if interactive else ["--draft-demo"]
    commands = "/draft-new hello\n/draft-edit corrected\n/draft-confirm\n/draft-edit again\n/draft-reject\n/clear\n/draft\n/exit\n"
    result = subprocess.run(
        [sys.executable, "-B", "-m", "app.cli", *arguments],
        cwd=Path(__file__).resolve().parents[1],
        env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"),
        input=commands if interactive else None,
        encoding="utf-8", capture_output=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert "PROPOSED" in result.stdout and "CONFIRMED" in result.stdout
    if interactive:
        assert "REJECTED" in result.stdout
        assert "当前会话没有草稿" in result.stdout
    else:
        assert "stale_revision" in result.stdout
        assert "原确认已失效" in result.stdout


def test_draft_size_budget_rejects_content_and_replays_failure(client, base, monkeypatch):
    from app.config.settings import Settings
    original = main.DraftService
    monkeypatch.setattr(main, "DraftService", lambda sessions: original(
        sessions, Settings(_env_file=None, runtime_max_context_chars=1)
    ))
    payload = {
        "operation_id": str(uuid4()),
        "content": {"title": "oversized", "items": ["content"]},
    }
    first = client.post(base + "/drafts", json=payload)
    second = client.post(base + "/drafts", json=payload)
    assert first.status_code == second.status_code == 413
    assert first.json() == second.json()
    assert client.get(base + "/drafts").json() == []


def test_draft_and_chat_share_operation_budget(client, base, monkeypatch, recording_model):
    from app.agent.orchestrator import AgentOrchestrator
    from app.config.settings import Settings
    settings = Settings(_env_file=None, runtime_max_requests_per_session=1)
    original = main.DraftService
    monkeypatch.setattr(main, "DraftService", lambda sessions: original(sessions, settings))
    monkeypatch.setattr(main, "get_orchestrator", lambda: AgentOrchestrator(
        main.session_service, settings
    ))
    create(client, base)
    blocked = client.post(base + "/messages", json={"content": "over budget"})
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "request_limit_exceeded"
    assert recording_model.calls == []

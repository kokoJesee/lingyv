import asyncio
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import threading
from uuid import UUID, uuid4

import pytest

from app import main
from app.agent.errors import RuntimeFailure
from app.agent.orchestrator import AgentOrchestrator
from app.config.settings import PROJECT_ROOT
from app.schemas.archive import MAX_SNAPSHOT_BYTES, SaveArchiveRequest
from app.schemas.draft import ConfirmDraftRequest, CreateDraftRequest, DraftContent, EditDraftRequest
from app.schemas.message import SendMessageRequest
from app.schemas.workflow import WorkflowPhase
from app.services.archive_service import ArchiveService
from app.services.archive_store import SQLiteArchiveStore
from app.services.draft_service import DraftService
from app.services.session_service import SessionService


def run(coroutine):
    return asyncio.run(coroutine)


def setup_store(tmp_path):
    sessions = SessionService()
    sid = sessions.create().session_id
    store = SQLiteArchiveStore(tmp_path / "archives.sqlite3")
    return sessions, sid, store, ArchiveService(sessions, store)


async def save(archives, sid, version=None):
    return await archives.save(sid, SaveArchiveRequest(
        expected_revision=archives.sessions.get(sid).revision,
        expected_archive_version=version))


def assert_failure(code, action):
    with pytest.raises(RuntimeFailure) as caught:
        run(action)
    assert caught.value.code == code


class EchoModel:
    def __init__(self):
        self.calls = []

    async def generate_reply(self, messages):
        self.calls.append(messages)
        blocks = [m.content for m in messages if "current_user_confirmed_drafts" in m.content]
        return blocks[-1] if blocks else "PRIVATE_ASSISTANT_SENTINEL"


def test_default_chat_and_archive_listing_do_not_create_database(tmp_path):
    sessions, sid, store, archives = setup_store(tmp_path)
    run(AgentOrchestrator(sessions).handle_message(
        sid, SendMessageRequest(content="PRIVATE_MESSAGE_SENTINEL"), EchoModel()))
    assert run(archives.list()) == []
    assert not store.path.exists()


def test_round_trip_has_no_model_replay_and_retains_isolation(tmp_path):
    sessions, sid, store, archives = setup_store(tmp_path)
    other = sessions.create().session_id
    model = EchoModel()
    request = SendMessageRequest(content="PRIVATE_MESSAGE_SENTINEL")
    runtime = AgentOrchestrator(sessions)
    run(runtime.handle_message(sid, request, model))
    run(runtime.handle_message(other, SendMessageRequest(content="OTHER_SESSION"), model))
    info = run(save(archives, sid))
    assert info.message_count == 2
    fresh = SessionService()
    recovered = ArchiveService(fresh, SQLiteArchiveStore(store.path))
    restored = run(recovered.restore(sid))
    assert [m.content for m in restored.messages] == ["PRIVATE_MESSAGE_SENTINEL", "PRIVATE_ASSISTANT_SENTINEL"]
    assert fresh.get(other) is None
    assert restored.revision == sessions.get(sid).revision + 1
    assert restored.workflow.phase == WorkflowPhase.IDLE
    assert fresh.require(sid).last_context is None
    assert len(model.calls) == 2
    assert_failure("request_restored", AgentOrchestrator(fresh).handle_message(sid, request, model))
    assert len(model.calls) == 2
    assert_failure("request_id_conflict", AgentOrchestrator(fresh).handle_message(
        sid, SendMessageRequest(content="DIFFERENT", request_id=request.request_id), model))
    run(AgentOrchestrator(fresh).handle_message(sid, SendMessageRequest(content="new"), model))
    assert len(model.calls) == 3
    assert len(fresh.get(sid).messages) == 4


def test_restored_draft_credentials_rotate_and_old_operation_is_tombstoned(tmp_path):
    sessions, sid, store, archives = setup_store(tmp_path)
    drafts = DraftService(sessions)
    create = CreateDraftRequest(content=DraftContent(title="draft", items=["one"]))
    draft = run(drafts.create(sid, create))
    waiting = run(AgentOrchestrator(sessions).start_demo(sid))
    run(save(archives, sid))
    raw = store.path.read_bytes()
    assert str(draft.confirmation_token).encode() not in raw
    assert str(waiting.workflow.confirmation_token).encode() not in raw
    fresh = SessionService()
    restored = run(ArchiveService(fresh, store).restore(sid))
    current = fresh.require(sid).drafts[draft.draft_id]
    assert current.revision == draft.revision
    assert current.confirmation_token != draft.confirmation_token
    assert restored.workflow.phase == WorkflowPhase.CANCELLED
    assert restored.workflow.stop_reason.value == "restore_interrupted"
    assert restored.workflow.run_id is None and restored.workflow.confirmation_token is None
    assert_failure("invalid_transition", AgentOrchestrator(fresh).confirm(
        sid, waiting.workflow.run_id, waiting.workflow.confirmation_token))
    assert_failure("stale_confirmation", DraftService(fresh).confirm(sid, current.draft_id, ConfirmDraftRequest(
        expected_revision=current.revision, confirmation_token=draft.confirmation_token)))
    assert_failure("request_restored", DraftService(fresh).create(sid, create))
    confirmed = run(DraftService(fresh).confirm(sid, current.draft_id, ConfirmDraftRequest(
        expected_revision=current.revision, confirmation_token=current.confirmation_token)))
    assert confirmed.status.value == "CONFIRMED"


@pytest.mark.parametrize("edit_before_save", [True, False])
def test_dependency_tracking_survives_restore_and_correction(tmp_path, edit_before_save):
    sessions, sid, store, archives = setup_store(tmp_path)
    service, model = DraftService(sessions), EchoModel()
    draft = run(service.create(sid, CreateDraftRequest(
        content=DraftContent(title="VERSION_A_SENTINEL", items=["sample"]))))
    draft = run(service.confirm(sid, draft.draft_id, ConfirmDraftRequest(
        expected_revision=draft.revision, confirmation_token=draft.confirmation_token)))
    run(AgentOrchestrator(sessions).handle_message(sid, SendMessageRequest(content="first"), model))
    if edit_before_save:
        run(service.edit(sid, draft.draft_id, EditDraftRequest(
            expected_revision=draft.revision, content=DraftContent(title="VERSION_B_SENTINEL", items=["new"]))))
    run(save(archives, sid))
    fresh = SessionService()
    run(ArchiveService(fresh, store).restore(sid))
    if not edit_before_save:
        restored_draft = fresh.require(sid).drafts[draft.draft_id]
        assert restored_draft.status.value == "CONFIRMED"
        assert restored_draft.confirmed_revision == restored_draft.revision
        run(DraftService(fresh).edit(sid, draft.draft_id, EditDraftRequest(
            expected_revision=restored_draft.revision,
            content=DraftContent(title="VERSION_B_SENTINEL", items=["new"]))))
    response = run(AgentOrchestrator(fresh).handle_message(sid, SendMessageRequest(content="continue"), model))
    text = "\n".join(m.content for m in model.calls[-1])
    assert "VERSION_A_SENTINEL" not in text and "VERSION_B_SENTINEL" not in text
    assert response.context_manifest.excluded_history_indices == [1]
    assert len(fresh.get(sid).messages) == 4


def test_clear_save_deletion_and_recreation_keep_distinct_semantics(tmp_path):
    sessions, sid, store, archives = setup_store(tmp_path)
    run(AgentOrchestrator(sessions).handle_message(sid, SendMessageRequest(content="before clear"), EchoModel()))
    first = run(save(archives, sid))
    run(AgentOrchestrator(sessions).clear_history(sid))
    assert run(archives.list())[0].message_count == 2
    second = run(save(archives, sid, first.archive_version))
    assert second.message_count == 0
    restored_sessions = SessionService()
    run(ArchiveService(restored_sessions, store).restore(sid))
    assert restored_sessions.get(sid).messages == []
    run(archives.delete(sid, second.archive_version))
    assert sessions.get(sid) is not None
    assert run(archives.list()) == []
    assert_failure("archive_not_found", ArchiveService(SessionService(), store).restore(sid))
    recreated = run(save(archives, sid))
    assert recreated.archive_version > second.archive_version
    assert_failure("archive_version_conflict", archives.delete(sid, second.archive_version))
    assert run(archives.list())[0].archive_version == recreated.archive_version


def test_version_conflicts_and_memory_collision_do_not_overwrite(tmp_path):
    sessions, sid, store, archives = setup_store(tmp_path)
    assert_failure("stale_revision", archives.save(sid, SaveArchiveRequest(expected_revision=99)))
    assert not store.path.exists()
    first = run(save(archives, sid))
    assert_failure("archive_version_conflict", save(archives, sid))
    assert_failure("archive_version_conflict", save(archives, sid, first.archive_version + 1))
    before = sessions.get(sid).model_dump()
    assert_failure("session_already_loaded", archives.restore(sid))
    assert sessions.get(sid).model_dump() == before
    assert run(archives.list())[0].archive_version == first.archive_version


def test_ended_state_survives_restart(tmp_path):
    sessions, sid, store, archives = setup_store(tmp_path)
    run(AgentOrchestrator(sessions).end_session(sid))
    run(save(archives, sid))
    fresh = SessionService()
    restored = run(ArchiveService(fresh, store).restore(sid))
    assert restored.status.value == "ENDED"
    assert_failure("session_ended", AgentOrchestrator(fresh).handle_message(
        sid, SendMessageRequest(content="blocked"), EchoModel()))


def test_save_rejects_inflight_model_without_waiting_or_writing(tmp_path):
    sessions, sid, store, archives = setup_store(tmp_path)

    async def scenario():
        started = asyncio.Event()

        class Blocked:
            async def generate_reply(self, messages):
                started.set()
                await asyncio.Event().wait()
        runtime = AgentOrchestrator(sessions)
        task = asyncio.create_task(runtime.handle_message(sid, SendMessageRequest(content="pending"), Blocked()))
        await started.wait()
        with pytest.raises(RuntimeFailure) as caught:
            await save(archives, sid)
        assert caught.value.code == "archive_session_busy"
        await runtime.cancel(sid, sessions.get(sid).workflow.run_id)
        with pytest.raises(RuntimeFailure):
            await task
    run(scenario())
    assert not store.path.exists()
    assert sessions.get(sid).messages == []


@pytest.mark.parametrize("field", ["operations", "draft_operations"])
def test_cleared_and_failed_ids_never_reexecute_after_restore(tmp_path, field):
    sessions, sid, store, archives = setup_store(tmp_path)
    if field == "operations":
        request = SendMessageRequest(content="cleared")
        run(AgentOrchestrator(sessions).handle_message(sid, request, EchoModel()))
    else:
        request = CreateDraftRequest(content=DraftContent(title="cleared", items=["sample"]))
        run(DraftService(sessions).create(sid, request))
    run(AgentOrchestrator(sessions).clear_history(sid))
    run(save(archives, sid))
    fresh = SessionService()
    run(ArchiveService(fresh, store).restore(sid))
    action = (AgentOrchestrator(fresh).handle_message(sid, request, EchoModel()) if field == "operations"
              else DraftService(fresh).create(sid, request))
    assert_failure("request_restored", action)
    assert fresh.get(sid).messages == [] and not fresh.require(sid).drafts


def read_payload(store, sid):
    with sqlite3.connect(store.path) as connection:
        return json.loads(connection.execute("SELECT payload FROM archives WHERE archive_id=?", (str(sid),)).fetchone()[0])


def replace_payload(store, sid, payload):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE archives SET payload=? WHERE archive_id=?", (text, str(sid)))


@pytest.mark.parametrize("damage", ["missing_dependency", "wrong_session", "future_schema", "duplicate_key",
                                    "partial_turn", "bad_fingerprint", "unknown_field", "noncanonical_index"])
def test_corrupt_snapshot_is_rejected_before_memory_mutation(tmp_path, damage):
    sessions, sid, store, archives = setup_store(tmp_path)
    run(AgentOrchestrator(sessions).handle_message(sid, SendMessageRequest(content="private"), EchoModel()))
    run(save(archives, sid))
    payload = read_payload(store, sid)
    if damage == "missing_dependency":
        payload["turn_sources"] = {}
    elif damage == "wrong_session":
        payload["session"]["session_id"] = str(uuid4())
    elif damage == "future_schema":
        payload["version"] = 99
    elif damage == "duplicate_key":
        payload = '{"version":1,"version":1}'
    elif damage == "partial_turn":
        payload["session"]["messages"].pop()
    elif damage == "bad_fingerprint":
        payload["operations"][next(iter(payload["operations"]))] = "bad"
    elif damage == "noncanonical_index":
        payload["turn_sources"] = {"01": []}
    else:
        payload["session"]["execute"] = "PRIVATE_EXCEPTION_SENTINEL"
    replace_payload(store, sid, payload)
    fresh = SessionService()
    error = "archive_version_unsupported" if damage == "future_schema" else "archive_invalid"
    assert_failure(error, ArchiveService(fresh, store).restore(sid))
    assert fresh.list() == []


def test_unsupported_database_and_invalid_file_are_safe(tmp_path):
    path = tmp_path / "future.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=999")
    archives = ArchiveService(SessionService(), SQLiteArchiveStore(path))
    assert_failure("archive_version_unsupported", archives.list())
    assert_failure("archive_version_unsupported", archives.restore(uuid4()))
    path.write_bytes(b"PRIVATE_INVALID_DATABASE")
    assert_failure("archive_storage_error", archives.list())


def test_save_size_limit_does_not_create_database(tmp_path):
    sessions, sid, store, archives = setup_store(tmp_path)
    request = SendMessageRequest(content="a" * (MAX_SNAPSHOT_BYTES // 2 + 10))
    # Construct valid completed history through runtime with a larger explicit budget.
    from app.config.settings import Settings
    settings = Settings(runtime_max_context_chars=MAX_SNAPSHOT_BYTES * 2)
    class LongReply:
        async def generate_reply(self, messages):
            return "b" * (MAX_SNAPSHOT_BYTES // 2 + 10)
    run(AgentOrchestrator(sessions, settings).handle_message(sid, request, LongReply()))
    assert_failure("archive_too_large", save(archives, sid))
    assert not store.path.exists()


def test_sqlite_failure_rolls_back_previous_snapshot(tmp_path):
    sessions, sid, store, archives = setup_store(tmp_path)
    info = run(save(archives, sid))
    with sqlite3.connect(store.path) as connection:
        connection.execute("""CREATE TRIGGER reject_update BEFORE UPDATE ON archives
                              BEGIN SELECT RAISE(ABORT, 'PRIVATE_DB_ERROR'); END""")
    run(AgentOrchestrator(sessions).handle_message(sid, SendMessageRequest(content="new"), EchoModel()))
    assert_failure("archive_storage_error", save(archives, sid, info.archive_version))
    metadata, snapshot = store.load(sid)
    assert metadata.archive_version == info.archive_version
    assert snapshot.session.messages == []


def test_concurrent_save_cas_has_one_winner(tmp_path):
    sessions, sid, store, archives = setup_store(tmp_path)
    info = run(save(archives, sid))
    # Separate memory stores and SQLite connections exercise database CAS, not just the session lock.
    other_sessions = SessionService()
    other_archives = ArchiveService(other_sessions, SQLiteArchiveStore(store.path))
    run(other_archives.restore(sid))

    async def scenario():
        return await asyncio.gather(
            save(archives, sid, info.archive_version), save(other_archives, sid, info.archive_version),
            return_exceptions=True)
    results = run(scenario())
    assert sum(isinstance(item, RuntimeFailure) for item in results) == 1
    assert next(item for item in results if isinstance(item, RuntimeFailure)).code == "archive_version_conflict"


def test_disk_failure_and_metadata_logs_do_not_expose_content(tmp_path, caplog):
    sessions, sid, store, archives = setup_store(tmp_path)
    caplog.set_level("INFO")
    run(AgentOrchestrator(sessions).handle_message(sid, SendMessageRequest(content="PRIVATE_USER_MARKER"), EchoModel()))
    run(save(archives, sid))
    listed = run(archives.list())[0].model_dump_json()
    assert "PRIVATE_USER_MARKER" not in listed + caplog.text
    assert "PRIVATE_ASSISTANT_SENTINEL" not in listed + caplog.text
    payload = read_payload(store, sid)
    assert "PRIVATE_ASSISTANT_SENTINEL" not in json.dumps(payload["operations"])
    assert "events" not in payload and "last_context" not in payload
    assert "confirmation_token" not in json.dumps(payload)


def test_api_save_restart_restore_and_delete(client, monkeypatch, tmp_path):
    store = SQLiteArchiveStore(tmp_path / "api.sqlite3")
    main.app.dependency_overrides[main.get_archive_service] = lambda: ArchiveService(main.session_service, store)
    try:
        assert client.get("/archives").json() == []
        assert not store.path.exists()
        session = client.post("/sessions").json()
        sid = session["session_id"]
        response = client.post(f"/sessions/{sid}/messages", json={"content": "fictional api message"})
        assert response.status_code == 200
        revision = response.json()["revision"]
        assert client.post(f"/sessions/{sid}/archive", json={"expected_revision": "1"}).status_code == 422
        first = client.post(f"/sessions/{sid}/archive", json={"expected_revision": revision})
        assert first.status_code == 200, first.text
        assert first.json()["message_count"] == 2
        assert client.post(f"/archives/{sid}/restore").status_code == 409
        monkeypatch.setattr(main, "session_service", SessionService())
        restored = client.post(f"/archives/{sid}/restore")
        assert restored.status_code == 200, restored.text
        assert len(restored.json()["messages"]) == 2
        assert restored.json()["workflow"]["phase"] == "IDLE"
        assert client.delete(f"/archives/{sid}?expected_version=99").status_code == 409
        assert client.delete(f"/archives/{sid}?expected_version=1").status_code == 204
        assert client.get(f"/sessions/{sid}").status_code == 200
        monkeypatch.setattr(main, "session_service", SessionService())
        assert client.post(f"/archives/{sid}/restore").status_code == 404
    finally:
        main.app.dependency_overrides.pop(main.get_archive_service, None)


def invoke_cli(path, *args, text=None):
    return subprocess.run(
        [sys.executable, "-B", "-m", "app.cli", "--archive-db", str(path), *args],
        cwd=PROJECT_ROOT, env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"),
        input=text, capture_output=True, encoding="utf-8", timeout=20)


def test_terminal_actual_process_restart_restore_continue_and_delete(tmp_path):
    path = tmp_path / "cli.sqlite3"
    first = invoke_cli(path, "--offline", text="FICTIONAL_CLI_MESSAGE\n/save\n/exit\n")
    assert first.returncode == 0, first.stderr
    sid = re.search(r"已保存：([0-9a-f-]{36})", first.stdout).group(1)
    second = invoke_cli(path, "--offline", text=(
        f"/restore {sid}\n/history\nnew fictional turn\n/save 1\n"
        f"/archives\n/archive-delete {sid} 2\n/history\n/exit\n"))
    assert second.returncode == 0, second.stderr
    assert "已恢复：" in second.stdout and "FICTIONAL_CLI_MESSAGE" in second.stdout
    assert "存档版本 2" in second.stdout and "磁盘存档已删除" in second.stdout
    assert SQLiteArchiveStore(path).list() == []
    third = invoke_cli(path, "--offline", text=f"/restore {sid}\n/exit\n")
    assert third.returncode == 0
    assert "已恢复：" not in third.stdout


def test_terminal_without_save_never_creates_database(tmp_path):
    path = tmp_path / "never-created" / "cli.sqlite3"
    result = invoke_cli(path, "--offline", text="fictional\n/archives\n/exit\n")
    assert result.returncode == 0, result.stderr
    assert not path.parent.exists()


def test_archive_demo_runs_offline_and_leaves_no_saved_content(tmp_path):
    path = tmp_path / "demo.sqlite3"
    result = invoke_cli(path, "--archive-demo")
    assert result.returncode == 0, result.stderr
    assert "旧请求未重放" in result.stdout and "新运行环境无法再次恢复" in result.stdout
    assert SQLiteArchiveStore(path).list() == []


def test_cancelled_save_finishes_transaction_before_releasing_session_lock(tmp_path, monkeypatch):
    sessions, sid, store, archives = setup_store(tmp_path)
    run(AgentOrchestrator(sessions).handle_message(sid, SendMessageRequest(content="fictional"), EchoModel()))
    entered, release = threading.Event(), threading.Event()
    original = store.save

    def slow(*args):
        entered.set()
        assert release.wait(2)
        return original(*args)
    monkeypatch.setattr(store, "save", slow)

    async def scenario():
        pending = asyncio.create_task(save(archives, sid))
        assert await asyncio.to_thread(entered.wait, 1)
        pending.cancel()
        await asyncio.sleep(0)
        pending.cancel()
        clearing = asyncio.create_task(AgentOrchestrator(sessions).clear_history(sid))
        await asyncio.sleep(0.01)
        assert not clearing.done() and sessions.require(sid).lock.locked()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await pending
        await clearing
    run(scenario())
    assert sessions.get(sid).messages == []
    assert store.list()[0].message_count == 2


def test_archive_count_budget_and_sqlite_lock_timeout(tmp_path, monkeypatch):
    import app.services.archive_store as module
    sessions, sid, store, archives = setup_store(tmp_path)
    first = run(save(archives, sid))
    monkeypatch.setattr(module, "MAX_ARCHIVES", 1)
    other = sessions.create().session_id
    assert_failure("archive_limit_exceeded", save(archives, other))
    with sqlite3.connect(store.path, isolation_level=None) as lock:
        lock.execute("BEGIN IMMEDIATE")
        try:
            assert_failure("archive_storage_error", save(archives, sid, first.archive_version))
        finally:
            lock.rollback()
    assert len(store.list()) == 1
    assert store.list()[0].archive_version == first.archive_version


def test_concurrent_restore_cannot_replace_the_first_loaded_record(tmp_path):
    sessions, sid, store, archives = setup_store(tmp_path)
    run(save(archives, sid))
    fresh = SessionService()
    restoring = ArchiveService(fresh, store)

    async def scenario():
        return await asyncio.gather(restoring.restore(sid), restoring.restore(sid), return_exceptions=True)
    results = run(scenario())
    assert sum(isinstance(item, RuntimeFailure) for item in results) == 1
    assert len(fresh.list()) == 1

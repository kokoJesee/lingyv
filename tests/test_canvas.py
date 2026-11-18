import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from app import api_canvas, events
from app.agent.context import build_context
from app.agent.orchestrator import AgentOrchestrator
from app.config.settings import Settings
from app.schemas.canvas import (
    CanvasOp,
    CanvasReference,
    ConfirmCanvasRequest,
    CreateCanvasRequest,
    PatchCanvasRequest,
)
from app.schemas.message import SendMessageRequest
from app.services.canvas_service import CanvasService
from app.services.canvas_store import CanvasStore
from app.services.content_cipher import ContentCipher, generate_key
from app.services.session_service import SessionService
from app.services.sqlite_migrations import migrate


class EchoModel:
    def __init__(self):
        self.calls = []

    async def generate_reply(self, messages):
        self.calls.append([message.model_copy() for message in messages])
        return f"reply-{len(self.calls)}"


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values = dict(
        _env_file=None,
        canvas_db_path=tmp_path / "canvases.sqlite3",
        canvas_encryption=False,
        canvas_key_path=tmp_path / ".canvas.key",
        memory_db_path=tmp_path / "memories.sqlite3",
    )
    values.update(overrides)
    return Settings(**values)


def make_service(tmp_path: Path, *, encrypted: bool = False, **overrides) -> CanvasService:
    settings = make_settings(tmp_path, **overrides)
    cipher = ContentCipher(generate_key(), enabled=encrypted)
    store = CanvasStore(settings.canvas_db_path, cipher)
    return CanvasService(store=store, settings=settings, cipher=cipher)


def add_fact(service: CanvasService, text: str = "期末周复习到两点", **kwargs):
    return service.create("session-1", CreateCanvasRequest(
        lanes={"facts": [{"text": text}]}, **kwargs))


def add_and_confirm(service: CanvasService, text: str = "期末周复习到两点"):
    canvas = add_fact(service, text)
    return service.confirm(canvas.canvas_id, ConfirmCanvasRequest(expected_revision=canvas.revision))


# ---- create / patch --------------------------------------------------------

def test_blank_canvas_can_be_created(tmp_path):
    service = make_service(tmp_path)
    canvas = service.create("s1", CreateCanvasRequest())
    assert canvas.status == "draft"
    assert canvas.revision == 1
    assert canvas.is_empty()
    assert canvas.day


def test_create_with_entries_sets_source_and_unknown_lane(tmp_path):
    service = make_service(tmp_path)
    canvas = service.create("s1", CreateCanvasRequest(lanes={
        "facts": [{"text": "室友说「你最近话好少」"}],
        "unknown": [{"text": "她为什么突然这么说"}],
    }))
    assert canvas.lanes["facts"][0].source == "user"
    assert canvas.lanes["facts"][0].confirmed is True
    assert len(canvas.lanes["unknown"]) == 1
    assert canvas.is_empty() is False


def test_patch_add_update_delete_bumps_revision(tmp_path):
    service = make_service(tmp_path)
    canvas = add_fact(service)
    entry_id = canvas.lanes["facts"][0].entry_id
    canvas = service.patch(canvas.canvas_id, PatchCanvasRequest(expected_revision=1, ops=[
        CanvasOp(op="add", lane="emotion", text="慌，累"),
        CanvasOp(op="update", lane="facts", entry_id=entry_id, text="期末周，复习到两点"),
    ]))
    assert canvas.revision == 2
    assert canvas.lanes["facts"][0].text == "期末周，复习到两点"
    assert len(canvas.lanes["emotion"]) == 1
    canvas = service.patch(canvas.canvas_id, PatchCanvasRequest(expected_revision=2, ops=[
        CanvasOp(op="delete", lane="emotion", entry_id=canvas.lanes["emotion"][0].entry_id),
    ]))
    assert canvas.lanes["emotion"] == []


def test_patch_rejects_stale_revision(tmp_path):
    service = make_service(tmp_path)
    canvas = add_fact(service)
    with pytest.raises(Exception) as error:
        service.patch(canvas.canvas_id, PatchCanvasRequest(expected_revision=9, ops=[
            CanvasOp(op="add", lane="facts", text="x")]))
    assert error.value.code == "canvas_revision_conflict"


def test_patch_limits_entries(tmp_path):
    service = make_service(tmp_path, canvas_max_entries=1)
    canvas = add_fact(service)
    with pytest.raises(Exception) as error:
        service.patch(canvas.canvas_id, PatchCanvasRequest(expected_revision=1, ops=[
            CanvasOp(op="add", lane="facts", text="第二条")]))
    assert error.value.code == "canvas_too_many_entries"


def test_patch_limits_total_size(tmp_path):
    service = make_service(tmp_path, canvas_max_total_chars=100)
    canvas = add_fact(service, "这是一句很长很长的话" * 4)
    with pytest.raises(Exception) as error:
        service.patch(canvas.canvas_id, PatchCanvasRequest(expected_revision=1, ops=[
            CanvasOp(op="add", lane="needs", text="想被说一句你已经够努力了" * 8)]))
    assert error.value.code == "canvas_too_large"


def test_patching_confirmed_canvas_requires_reconfirm(tmp_path):
    service = make_service(tmp_path)
    result = add_and_confirm(service)
    canvas = service.patch(result.canvas.canvas_id, PatchCanvasRequest(expected_revision=1, ops=[
        CanvasOp(op="add", lane="needs", text="想被说一句你已经够努力了")]))
    assert canvas.status == "draft"
    assert canvas.confirmed_revision == 1
    assert canvas.revision == 2


# ---- confirm / records -----------------------------------------------------

def test_confirm_empty_canvas_is_rejected(tmp_path):
    service = make_service(tmp_path)
    canvas = service.create("s1", CreateCanvasRequest())
    with pytest.raises(Exception) as error:
        service.confirm(canvas.canvas_id, ConfirmCanvasRequest(expected_revision=1))
    assert error.value.code == "canvas_empty"


def test_confirm_writes_record_and_freezes_snapshot(tmp_path):
    service = make_service(tmp_path)
    result = add_and_confirm(service)
    assert result.canvas.status == "confirmed"
    assert result.record.version == 1
    assert result.record.canvas_revision == 1
    assert result.record.snapshot.lanes["facts"][0].text == "期末周复习到两点"
    # 画布后续修改不影响已存快照
    service.patch(result.canvas.canvas_id, PatchCanvasRequest(expected_revision=1, ops=[
        CanvasOp(op="add", lane="facts", text="后来加的一句")]))
    stored = service.get_record(result.record.record_id)
    assert len(stored.snapshot.lanes["facts"]) == 1


def test_confirm_is_idempotent_by_request_id(tmp_path):
    service = make_service(tmp_path)
    canvas = add_fact(service)
    first = service.confirm(canvas.canvas_id, ConfirmCanvasRequest(expected_revision=1, request_id="req-1"))
    second = service.confirm(canvas.canvas_id, ConfirmCanvasRequest(expected_revision=1, request_id="req-1"))
    assert first.record.record_id == second.record.record_id


def test_confirm_is_idempotent_by_revision_without_request_id(tmp_path):
    service = make_service(tmp_path)
    canvas = add_fact(service)
    first = service.confirm(canvas.canvas_id, ConfirmCanvasRequest(expected_revision=1))
    second = service.confirm(canvas.canvas_id, ConfirmCanvasRequest(expected_revision=1))
    assert first.record.record_id == second.record.record_id


def test_reconfirm_creates_new_version_and_supersedes(tmp_path):
    service = make_service(tmp_path)
    first = add_and_confirm(service)
    edited = service.patch(first.canvas.canvas_id, PatchCanvasRequest(expected_revision=1, ops=[
        CanvasOp(op="add", lane="needs", text="想被说一句你已经够努力了")]))
    second = service.confirm(edited.canvas_id, ConfirmCanvasRequest(expected_revision=edited.revision))
    assert second.record.version == 2
    old = service.store.get_record(first.record.record_id)
    assert old.status == "superseded"
    assert old.superseded_by == second.record.record_id


def test_confirm_respects_observed_at(tmp_path):
    service = make_service(tmp_path)
    canvas = add_fact(service)
    result = service.confirm(canvas.canvas_id, ConfirmCanvasRequest(expected_revision=1, observed_at="2026-11-13"))
    assert result.record.observed_at == "2026-11-13"


def test_delete_record_is_soft_and_keeps_canvas(tmp_path):
    service = make_service(tmp_path)
    result = add_and_confirm(service)
    deleted = service.delete_record(result.record.record_id)
    assert deleted.status == "deleted"
    assert service.store.get_canvas(result.canvas.canvas_id).status == "confirmed"
    assert service.day_view(result.record.day).records == []


def test_missing_canvas_and_record_return_404(tmp_path):
    service = make_service(tmp_path)
    with pytest.raises(Exception) as canvas_error:
        service.get("cv_missing")
    assert canvas_error.value.code == "canvas_not_found"
    with pytest.raises(Exception) as record_error:
        service.get_record("rec_missing")
    assert record_error.value.code == "record_not_found"


def test_records_are_scoped_to_owner(tmp_path):
    service = make_service(tmp_path)
    result = add_and_confirm(service)
    with pytest.raises(Exception) as error:
        service.get_record(result.record.record_id, owner_id="someone-else")
    assert error.value.code == "record_not_found"


def test_disabled_canvas_rejects_writes(tmp_path):
    service = make_service(tmp_path, canvas_enabled=False)
    with pytest.raises(Exception) as error:
        service.create("s1", CreateCanvasRequest())
    assert error.value.code == "canvas_disabled"


# ---- views -----------------------------------------------------------------

def test_day_view_lists_multiple_canvases_with_previews(tmp_path):
    service = make_service(tmp_path)
    first = add_and_confirm(service, "期末周复习到两点")
    second = service.create("s1", CreateCanvasRequest(lanes={"facts": [{"text": "和室友去散步"}]}))
    service.confirm(second.canvas_id, ConfirmCanvasRequest(expected_revision=1))
    view = service.day_view(first.record.day)
    assert len(view.records) == 2
    assert {record.title for record in view.records} == {"期末周复习到两点", "和室友去散步"}
    assert all(record.card_count == 0 for record in view.records)


def test_month_view_groups_days(tmp_path):
    service = make_service(tmp_path)
    canvas = add_fact(service)
    service.confirm(canvas.canvas_id, ConfirmCanvasRequest(expected_revision=1, observed_at="2026-11-13"))
    other = service.create("s1", CreateCanvasRequest(lanes={"facts": [{"text": "另一天"}]}))
    service.confirm(other.canvas_id, ConfirmCanvasRequest(expected_revision=1, observed_at="2026-11-16"))
    days = service.month_view("2026-11")
    assert [day.day for day in days] == ["2026-11-16", "2026-11-13"]


def test_latest_confirmed_prefers_session_then_owner(tmp_path):
    service = make_service(tmp_path)
    canvas = add_fact(service)
    service.confirm(canvas.canvas_id, ConfirmCanvasRequest(expected_revision=1))
    assert service.latest_confirmed("session-1").canvas_id == canvas.canvas_id
    assert service.latest_confirmed("another-session").canvas_id == canvas.canvas_id  # owner fallback
    assert service.active_references() == {(canvas.canvas_id, 1)}


def test_draft_canvas_is_not_active(tmp_path):
    service = make_service(tmp_path)
    add_fact(service)
    assert service.active_references() == set()
    assert service.latest_confirmed("session-1") is None


# ---- encryption / migration ------------------------------------------------

def test_encrypted_store_hides_content_on_disk(tmp_path):
    service = make_service(tmp_path, encrypted=True)
    add_and_confirm(service)
    raw = (tmp_path / "canvases.sqlite3").read_bytes()
    assert "期末周复习到两点".encode() not in raw
    assert b"enc:v1:" in raw
    # 读回明文
    assert service.month_view("2026-11") == [] or service.month_view("2026-11") is not None


def test_encrypted_roundtrip_returns_plaintext(tmp_path):
    service = make_service(tmp_path, encrypted=True)
    result = add_and_confirm(service)
    assert service.get(result.canvas.canvas_id).lanes["facts"][0].text == "期末周复习到两点"
    assert service.get_record(result.record.record_id).snapshot.lanes["facts"][0].text == "期末周复习到两点"


def test_encryption_can_be_disabled(tmp_path):
    service = make_service(tmp_path, encrypted=False)
    add_and_confirm(service)
    raw = (tmp_path / "canvases.sqlite3").read_bytes()
    assert "期末周复习到两点".encode() in raw


def test_migration_sets_version_and_is_idempotent(tmp_path):
    service = make_service(tmp_path)
    service.create("s1", CreateCanvasRequest(lanes={"facts": [{"text": "第一条"}]}))
    connection = sqlite3.connect(service.settings.canvas_db_path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
    finally:
        connection.close()
    # 第二次写入会再走一次迁移，既不重复建表也不丢数据
    service.create("s1", CreateCanvasRequest(lanes={"facts": [{"text": "第二条"}]}))
    with sqlite3.connect(service.settings.canvas_db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM canvases").fetchone()[0] == 2


def test_future_schema_version_is_refused():
    import sqlite3 as sqlite
    connection = sqlite.connect(":memory:")
    try:
        connection.execute("PRAGMA user_version=99")
        with pytest.raises(RuntimeError):
            migrate(connection, {1: lambda conn: None})
    finally:
        connection.close()


# ---- sensitivity / events --------------------------------------------------

def test_sensitive_text_gets_a_notice_but_still_saves(tmp_path):
    service = make_service(tmp_path)
    canvas = add_fact(service, "有时候真的不想活了")
    assert canvas.sensitivity.level == "notice"
    assert "self_harm" in canvas.sensitivity.categories
    result = service.confirm(canvas.canvas_id, ConfirmCanvasRequest(expected_revision=1))
    assert result.record.sensitivity.level == "notice"


def test_normal_text_has_no_sensitivity(tmp_path):
    service = make_service(tmp_path)
    canvas = add_fact(service, "今天和室友去散步，心情还行")
    assert canvas.sensitivity.level == "none"


def test_events_are_published(tmp_path):
    received = []
    handler = received.append
    events.subscribe("canvas.confirmed", handler)
    events.subscribe("record.superseded", handler)
    events.subscribe("record.deleted", handler)
    try:
        service = make_service(tmp_path)
        first = add_and_confirm(service)
        edited = service.patch(first.canvas.canvas_id, PatchCanvasRequest(expected_revision=1, ops=[
            CanvasOp(op="add", lane="facts", text="后来加的一句")]))
        service.confirm(edited.canvas_id, ConfirmCanvasRequest(expected_revision=edited.revision))
        service.delete_record(service.day_view(first.record.day).records[0].record_id)
    finally:
        events.unsubscribe("canvas.confirmed", handler)
        events.unsubscribe("record.superseded", handler)
        events.unsubscribe("record.deleted", handler)
    assert len(received) >= 3


# ---- context injection -----------------------------------------------------

def test_confirmed_canvas_is_injected_into_context(tmp_path):
    service = make_service(tmp_path)
    result = add_and_confirm(service)
    sessions = SessionService()
    session = sessions.create()
    bundle = build_context(
        session, [], {}, "今天想聊聊", __import__("uuid").uuid4(), 48000,
        canvas=result.canvas,
    )
    block = [message for message in bundle.messages if "current_confirmed_canvas" in message.content]
    assert block and "期末周复习到两点" in block[0].content
    assert bundle.manifest.canvas_reference.canvas_id == result.canvas.canvas_id
    assert any(source.kind == "canvas" for source in bundle.manifest.sources)


def test_draft_canvas_is_not_injected(tmp_path):
    service = make_service(tmp_path)
    canvas = add_fact(service)
    sessions = SessionService()
    session = sessions.create()
    bundle = build_context(
        session, [], {}, "今天想聊聊", __import__("uuid").uuid4(), 48000, canvas=canvas,
    )
    assert not any("current_confirmed_canvas" in message.content for message in bundle.messages)
    assert bundle.manifest.canvas_reference is None


def test_orchestrator_injects_canvas_and_tracks_reference(tmp_path):
    service = make_service(tmp_path)
    result = add_and_confirm(service)
    sessions = SessionService()
    orchestrator = AgentOrchestrator(sessions, make_settings(tmp_path), memory=make_memory(service), canvas=service)
    session = sessions.create()
    model = EchoModel()
    response = asyncio.run(orchestrator.handle_message(
        session.session_id, SendMessageRequest(content="最近有点烦"), model))
    assert response.context_manifest.canvas_reference.canvas_id == result.canvas.canvas_id
    assert any("current_confirmed_canvas" in message.content for message in model.calls[0])
    record = sessions.require(session.session_id)
    assert record.turn_canvas_sources[1][0].canvas_id == result.canvas.canvas_id


def test_old_canvas_reply_is_pruned_after_reconfirm(tmp_path):
    service = make_service(tmp_path)
    result = add_and_confirm(service)
    sessions = SessionService()
    orchestrator = AgentOrchestrator(sessions, make_settings(tmp_path), memory=make_memory(service), canvas=service)
    session = sessions.create()
    model = EchoModel()
    asyncio.run(orchestrator.handle_message(session.session_id, SendMessageRequest(content="最近有点烦"), model))
    edited = service.patch(result.canvas.canvas_id, PatchCanvasRequest(expected_revision=1, ops=[
        CanvasOp(op="add", lane="facts", text="后来加的一句")]))
    service.confirm(edited.canvas_id, ConfirmCanvasRequest(expected_revision=edited.revision))
    asyncio.run(orchestrator.handle_message(session.session_id, SendMessageRequest(content="好一点了"), model))
    history = " ".join(message.content for message in model.calls[1])
    assert "reply-1" not in history


def make_memory(service):
    from app.services.memory_service import MemoryService
    from app.services.memory_store import MemoryStore
    return MemoryService(store=MemoryStore(service.settings.memory_db_path), settings=service.settings)


# ---- HTTP ------------------------------------------------------------------

@pytest.fixture
def canvas_client(tmp_path, monkeypatch, client):
    service = make_service(tmp_path)
    monkeypatch.setattr(api_canvas, "_service", service)
    return client, service


def test_http_canvas_flow(canvas_client):
    client, service = canvas_client
    sid = client.post("/sessions").json()["session_id"]
    created = client.post(f"/sessions/{sid}/canvases", json={"lanes": {"facts": [{"text": "期末周复习到两点"}]}})
    assert created.status_code == 201
    canvas = created.json()
    listed = client.get(f"/sessions/{sid}/canvases").json()
    assert listed[0]["canvas_id"] == canvas["canvas_id"]
    patched = client.patch(f"/canvases/{canvas['canvas_id']}",
                           json={"expected_revision": 1, "ops": [{"op": "add", "lane": "needs", "text": "想被说一句"}]})
    assert patched.status_code == 200
    confirmed = client.post(f"/canvases/{canvas['canvas_id']}/confirm",
                            json={"expected_revision": 2, "request_id": "r1"})
    assert confirmed.status_code == 200
    record = confirmed.json()["record"]
    assert client.get(f"/records/{record['record_id']}").status_code == 200
    month = client.get(f"/records?month={record['day'][:7]}")
    assert month.status_code == 200 and month.json()[0]["records"][0]["record_id"] == record["record_id"]
    assert client.get(f"/days/{record['day']}").json()["records"][0]["record_id"] == record["record_id"]
    deleted = client.delete(f"/records/{record['record_id']}")
    assert deleted.status_code == 200 and deleted.json()["linked_memories"] == []


def test_http_revision_conflict(canvas_client):
    client, _ = canvas_client
    sid = client.post("/sessions").json()["session_id"]
    canvas = client.post(f"/sessions/{sid}/canvases", json={}).json()
    response = client.patch(f"/canvases/{canvas['canvas_id']}",
                            json={"expected_revision": 5, "ops": [{"op": "add", "lane": "facts", "text": "x"}]})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "canvas_revision_conflict"


def test_http_unknown_canvas_404(canvas_client):
    client, _ = canvas_client
    assert client.get("/canvases/cv_missing").status_code == 404


def test_http_reserved_endpoints_return_501(canvas_client):
    client, _ = canvas_client
    assert client.post("/canvases/cv_x/draft").status_code == 501
    assert client.post("/canvases/cv_x/cards").status_code == 501
    assert client.post("/records/rec_x/append").status_code == 501
    assert client.get("/canvases/cv_x/export").status_code == 501
    assert client.get("/personas").status_code == 501


def test_http_bad_month_is_422(canvas_client):
    client, _ = canvas_client
    assert client.get("/records?month=2026-13-01").status_code == 422


def test_http_health_reports_canvas_module(client):
    modules = client.get("/health").json()["modules"]
    assert modules["canvas"]["schema_version"] == "canvas.v1"
    assert modules["cards"]["status"] == "reserved"

import asyncio
import json
from pathlib import Path

import pytest

from app import api_memory
from app.agent.orchestrator import AgentOrchestrator
from app.config.settings import Settings
from app.schemas.memory import (
    MemoryConfirmRequest,
    MemoryCreateRequest,
    MemoryUpdateRequest,
    summarise,
)
from app.schemas.message import SendMessageRequest
from app.services.memory_service import MemoryService, relevance
from app.services.memory_store import MemoryStore
from app.services.session_service import SessionService


class EchoModel:
    def __init__(self):
        self.calls = []

    async def generate_reply(self, messages):
        self.calls.append([m.model_copy() for m in messages])
        return f"reply-{len(self.calls)}"


def make_service(tmp_path: Path) -> MemoryService:
    settings = Settings(_env_file=None, memory_db_path=tmp_path / "mem.sqlite3")
    return MemoryService(store=MemoryStore(tmp_path / "mem.sqlite3"), settings=settings)


def confirmed(service: MemoryService, text="在准备专业课期末考", kind="fact", summary=None):
    memory = service.create(MemoryCreateRequest(kind=kind, text=text, summary=summary))
    return service.confirm(memory.memory_id, MemoryConfirmRequest(expected_revision=memory.revision))


def test_create_starts_proposed_and_stored(tmp_path):
    service = make_service(tmp_path)
    memory = service.create(MemoryCreateRequest(kind="preference", text="别问家里的事。"))
    assert memory.status == "proposed"
    assert memory.revision == 1
    assert memory.summary == "别问家里的事。"
    assert service.list()[0].memory_id == memory.memory_id


def test_confirm_requires_current_revision(tmp_path):
    service = make_service(tmp_path)
    memory = service.create(MemoryCreateRequest(kind="fact", text="和室友小林住一间"))
    with pytest.raises(Exception) as error:
        service.confirm(memory.memory_id, MemoryConfirmRequest(expected_revision=99))
    assert error.value.code == "memory_revision_conflict"
    confirmed_memory = service.confirm(memory.memory_id, MemoryConfirmRequest(expected_revision=1))
    assert (confirmed_memory.status, confirmed_memory.revision) == ("confirmed", 2)


def test_only_confirmed_memories_are_selected(tmp_path):
    service = make_service(tmp_path)
    service.create(MemoryCreateRequest(kind="fact", text="在准备专业课期末考"))
    selection = service.select("期末考")
    assert selection.memories == []


def test_selection_prefers_topic_match_and_excludes_unrelated(tmp_path):
    service = make_service(tmp_path)
    relevant = confirmed(service, "在准备专业课期末考，睡得很晚", summary="期末考")
    unrelated = confirmed(service, "喜欢喝冰美式", kind="preference", summary="冰美式")
    selection = service.select("这周期末考好累")
    assert [m.memory_id for m in selection.memories] == [relevant.memory_id]
    assert unrelated.memory_id in selection.excluded_memory_ids


def test_selection_respects_top_k_and_budget(tmp_path):
    service = make_service(tmp_path)
    for index in range(6):
        confirmed(service, f"期末考复习第{index}天", summary=f"期末考{index}")
    selection = service.select("期末考")
    assert len(selection.memories) == 5
    assert sum(len(m.summary) for m in selection.memories) <= service.settings.memory_budget_chars


def test_usage_count_and_recency_raise_score(tmp_path):
    service = make_service(tmp_path)
    first = confirmed(service, "期末考复习到很晚", summary="期末考A")
    second = confirmed(service, "期末考复习得还行", summary="期末考B")
    service.touch(service.select("期末考").references())
    ordered = service.select("期末考").memories
    assert {m.memory_id for m in ordered} == {first.memory_id, second.memory_id}
    stored = service.store.get(first.memory_id)
    assert stored.use_count >= 1 and stored.last_used_at is not None


def test_edit_returns_to_proposed(tmp_path):
    service = make_service(tmp_path)
    memory = confirmed(service)
    edited = service.update(memory.memory_id, MemoryUpdateRequest(
        expected_revision=memory.revision, text="在准备专业课期末考，很慌"))
    assert edited.status == "proposed"
    assert edited.revision == memory.revision + 1
    assert service.select("期末考").memories == []


def test_delete_is_a_tombstone(tmp_path):
    service = make_service(tmp_path)
    memory = confirmed(service)
    deleted = service.delete(memory.memory_id)
    assert deleted.status == "deleted"
    assert deleted.summary == "" and deleted.text == ""
    assert deleted.deleted_at is not None
    assert service.select("期末考").memories == []
    with pytest.raises(Exception):
        service.delete(memory.memory_id)


def test_limit_and_disabled_guards(tmp_path):
    settings = Settings(_env_file=None, memory_db_path=tmp_path / "mem.sqlite3", memory_max_items=1)
    service = MemoryService(store=MemoryStore(tmp_path / "mem.sqlite3"), settings=settings)
    service.create(MemoryCreateRequest(kind="fact", text="第一条"))
    with pytest.raises(Exception) as error:
        service.create(MemoryCreateRequest(kind="fact", text="第二条"))
    assert error.value.code == "memory_limit_exceeded"

    off = Settings(_env_file=None, memory_db_path=tmp_path / "off.sqlite3", memory_enabled=False)
    disabled = MemoryService(store=MemoryStore(tmp_path / "off.sqlite3"), settings=off)
    with pytest.raises(Exception) as error:
        disabled.create(MemoryCreateRequest(kind="fact", text="不允许"))
    assert error.value.code == "memory_disabled"
    assert disabled.select("任何话题").memories == []


def test_export_markdown_and_json(tmp_path):
    service = make_service(tmp_path)
    confirmed(service, "不喜欢被叫宝贝", kind="preference", summary="别叫宝贝")
    markdown = service.export("markdown")
    assert "别叫宝贝" in markdown and "# 我的记忆" in markdown
    payload = json.loads(service.export("json"))
    assert payload["memories"][0]["summary"] == "别叫宝贝"


def test_summarise_keeps_first_sentence():
    assert summarise("这周在期末周。下周回家。") == "这周在期末周。"
    assert len(summarise("很长的句子" * 20)) <= 50


def test_relevance_is_zero_without_shared_terms(tmp_path):
    service = make_service(tmp_path)
    memory = confirmed(service, "喜欢喝冰美式", summary="冰美式")
    assert relevance("期末考", memory) == 0.0


def test_context_injects_memory_and_manifest(tmp_path):
    service = make_service(tmp_path)
    memory = confirmed(service, "在准备专业课期末考", summary="在准备期末考")
    sessions = SessionService()
    orchestrator = AgentOrchestrator(sessions, Settings(_env_file=None, memory_db_path=tmp_path / "mem.sqlite3"), memory=service)
    session = sessions.create()
    model = EchoModel()
    response = asyncio.run(orchestrator.handle_message(
        session.session_id, SendMessageRequest(content="这周期末考好累"), model))
    block = [m for m in model.calls[0] if "user_long_term_memory" in m.content]
    assert block and "在准备期末考" in block[0].content
    assert response.context_manifest.memory_references[0].memory_id == memory.memory_id


def test_deleted_memory_removes_dependent_reply_from_context(tmp_path):
    service = make_service(tmp_path)
    memory = confirmed(service, "在准备专业课期末考", summary="在准备期末考")
    sessions = SessionService()
    orchestrator = AgentOrchestrator(sessions, Settings(_env_file=None, memory_db_path=tmp_path / "mem.sqlite3"), memory=service)
    session = sessions.create()
    model = EchoModel()
    asyncio.run(orchestrator.handle_message(session.session_id, SendMessageRequest(content="这周期末考好累"), model))
    service.delete(memory.memory_id)
    asyncio.run(orchestrator.handle_message(session.session_id, SendMessageRequest(content="今天好一点了"), model))
    history = " ".join(m.content for m in model.calls[1])
    assert "reply-1" not in history
    assert "这周期末考好累" in history


def test_http_memory_routes(tmp_path, monkeypatch, client):
    service = make_service(tmp_path)
    monkeypatch.setattr(api_memory, "_service", service)
    created = client.post("/memories", json={"kind": "preference", "text": "别问家里的事"})
    assert created.status_code == 201
    memory = created.json()
    confirmed_response = client.post(
        f"/memories/{memory['memory_id']}/confirm",
        json={"expected_revision": memory["revision"]},
    )
    assert confirmed_response.status_code == 200
    assert confirmed_response.json()["status"] == "confirmed"
    listed = client.get("/memories").json()
    assert listed[0]["memory_id"] == memory["memory_id"]
    assert client.get("/memories/export").status_code == 200
    assert client.post("/memories/propose").status_code == 501
    assert client.delete(f"/memories/{memory['memory_id']}").status_code == 204
    assert client.get("/memories").json()[0]["status"] == "deleted"
def test_preview_shows_memory_without_calling_model(tmp_path, monkeypatch, client):
    service = make_service(tmp_path)
    monkeypatch.setattr(api_memory, "_service", service)
    memory = confirmed(service, "在准备专业课期末考", summary="在准备期末考")
    session_id = client.post("/sessions").json()["session_id"]
    preview = client.post(
        f"/sessions/{session_id}/context/preview",
        json={"content": "这周期末考好累", "request_id": "11111111-1111-4111-8111-111111111111"},
    )
    assert preview.status_code == 200
    manifest = preview.json()
    assert [ref["memory_id"] for ref in manifest["memory_references"]] == [memory.memory_id]
    assert any(source["kind"] == "long_term_memory" for source in manifest["sources"])


def test_read_only_store_does_not_create_missing_file(tmp_path):
    path = tmp_path / "absent.sqlite3"
    store = MemoryStore(path)
    assert store.list() == []
    assert store.get("mem_missing") is None
    assert not path.exists()
def test_duplicate_memory_is_rejected_but_other_kinds_pass(tmp_path):
    service = make_service(tmp_path)
    service.create(MemoryCreateRequest(kind="preference", text="别问家里的事"))
    with pytest.raises(Exception) as error:
        service.create(MemoryCreateRequest(kind="preference", text="  别问家里的事  "))
    assert error.value.code == "memory_duplicate"
    # 同类文本换个 kind 允许：语义不同
    service.create(MemoryCreateRequest(kind="fact", text="别问家里的事"))


def test_http_detail_and_query_validation(tmp_path, monkeypatch, client):
    service = make_service(tmp_path)
    monkeypatch.setattr(api_memory, "_service", service)
    created = client.post("/memories", json={"kind": "fact", "text": "在准备期末考"}).json()
    detail = client.get(f"/memories/{created['memory_id']}")
    assert detail.status_code == 200
    assert detail.json()["memory_id"] == created["memory_id"]
    assert client.get("/memories/mem_missing").status_code == 404
    assert client.get("/memories?status=bogus").status_code == 422
    assert client.get("/memories?kind=fact").status_code == 200
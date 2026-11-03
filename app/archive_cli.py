from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from app.agent.errors import RuntimeFailure
from app.agent.orchestrator import AgentOrchestrator
from app.schemas.archive import SaveArchiveRequest
from app.schemas.draft import CreateDraftRequest, DraftContent
from app.schemas.message import SendMessageRequest
from app.services.archive_service import ArchiveService
from app.services.archive_store import SQLiteArchiveStore
from app.services.draft_service import DraftService
from app.services.session_service import SessionService

ARCHIVE_HELP = """
/save [存档版本]          主动保存当前快照；覆盖已有存档需填写其版本
/archives                列出磁盘存档元数据
/restore 存档编号前缀    恢复并切换会话，不覆盖已加载会话
/archive-delete 编号前缀 版本  删除指定磁盘存档；当前内存会话保留
""".strip()


async def handle_archive_command(text, current, archives):
    command, _, argument = text.partition(" ")
    if command not in ("/save", "/archives", "/restore", "/archive-delete"):
        return False, current
    if command == "/save":
        try:
            version = int(argument.strip()) if argument.strip() else None
            if version is not None and version < 1:
                raise ValueError
        except ValueError:
            print("格式：/save 或 /save 正整数存档版本。用 /archives 查看版本。")
            return True, current
        info = await archives.save(current, SaveArchiveRequest(
            expected_revision=archives.sessions.require(current).session.revision,
            expected_archive_version=version))
        print(f"已保存：{info.archive_id} | 存档版本 {info.archive_version} | 消息 {info.message_count} 条")
        print("后续聊天仍只保存在内存；更新存档请再次 /save 并填写存档版本。")
        return True, current
    if command == "/archives":
        for info in await archives.list():
            print(f"{info.archive_id} | 存档版本 {info.archive_version} | {info.status.value} | "
                  f"消息 {info.message_count} 条 | {info.saved_at.isoformat()}")
        return True, current
    parts = argument.split()
    if (command == "/restore" and len(parts) != 1) or (command == "/archive-delete" and len(parts) != 2):
        print("格式：/restore 唯一编号前缀，或 /archive-delete 唯一编号前缀 存档版本。")
        return True, current
    matches = [info for info in await archives.list() if str(info.archive_id).startswith(parts[0])]
    if len(matches) != 1:
        print("请从 /archives 中提供唯一的存档编号或前缀。")
        return True, current
    info = matches[0]
    if command == "/restore":
        restored = await archives.restore(info.archive_id)
        print(f"已恢复：{restored.session_id} | 消息 {len(restored.messages)} 条；未自动调用模型。")
        return True, restored.session_id
    try:
        version = int(parts[1])
        if version < 1:
            raise ValueError
    except ValueError:
        print("存档版本必须是正整数。")
        return True, current
    await archives.delete(info.archive_id, version)
    print("磁盘存档已删除；已加载的内存会话仍保留，可另行 /clear。")
    return True, current


async def archive_demo(path: Path | None = None) -> int:
    if path is None:
        with TemporaryDirectory(prefix="agent-archive-demo-") as directory:
            return await archive_demo(Path(directory) / "demo.sqlite3")
    from app.cli import OfflineModel

    model = OfflineModel()
    first = SessionService()
    sid = first.create().session_id
    runtime = AgentOrchestrator(first)
    request = SendMessageRequest(content="虚构存档演示消息")
    await runtime.handle_message(sid, request, model)
    draft = await DraftService(first).create(sid, CreateDraftRequest(
        content=DraftContent(title="虚构待确认草稿", items=["示例"])))
    await runtime.start_demo(sid)
    archives = ArchiveService(first, SQLiteArchiveStore(path))
    info = await archives.save(sid, SaveArchiveRequest(expected_revision=first.get(sid).revision))
    print("主动保存完成：消息、草稿和来源依赖已写入本地快照。")
    second = SessionService()
    recovered = ArchiveService(second, SQLiteArchiveStore(path))
    restored = await recovered.restore(sid)
    assert len(restored.messages) == 2 and restored.workflow.stop_reason.value == "restore_interrupted"
    assert second.require(sid).drafts[draft.draft_id].confirmation_token != draft.confirmation_token
    print("重新创建运行环境后恢复成功；未完成流程已停止，旧确认凭据失效。")
    try:
        await AgentOrchestrator(second).handle_message(sid, request, model)
    except RuntimeFailure as error:
        assert error.code == "request_restored"
    else:
        raise AssertionError("old request must not execute")
    assert model.calls == 1
    print("旧请求未重放：模型调用次数保持不变。")
    await AgentOrchestrator(second).handle_message(
        sid, SendMessageRequest(content="虚构演示：恢复后继续"), model)
    updated = await recovered.save(sid, SaveArchiveRequest(
        expected_revision=second.get(sid).revision, expected_archive_version=info.archive_version))
    await AgentOrchestrator(second).clear_history(sid)
    assert next(item for item in await recovered.list() if item.archive_id == sid).message_count == 4
    print("恢复后可继续并保存新版本；清空内存不等于删除磁盘存档。")
    await recovered.delete(sid, updated.archive_version)
    try:
        await ArchiveService(SessionService(), SQLiteArchiveStore(path)).restore(sid)
    except RuntimeFailure as error:
        assert error.code == "archive_not_found"
    else:
        raise AssertionError("deleted archive must not restore")
    print("存档删除完成，新运行环境无法再次恢复。本演示只使用虚构数据和模拟模型。")
    return 0

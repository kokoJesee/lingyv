import argparse
import asyncio
import getpass
import sys
from time import perf_counter
from pathlib import Path
from uuid import UUID

from dotenv import set_key
from pydantic import ValidationError

from app.agent.errors import RuntimeFailure
from app.draft_cli import DRAFT_HELP, draft_demo, handle_draft_command
from app.context_cli import context_demo
from app.archive_cli import ARCHIVE_HELP, archive_demo, handle_archive_command
from app.services.archive_service import ArchiveService
from app.services.archive_store import DEFAULT_ARCHIVE_PATH, SQLiteArchiveStore
from app.agent.orchestrator import AgentOrchestrator
from app.config.logging import configure_logging
from app.config.settings import PROJECT_ROOT, get_settings
from app.llm.client import DeepSeekClient
from app.llm.errors import LLMError
from app.schemas.message import SendMessageRequest
from app.services.session_service import SessionService

HELP = """
/new             新建并切换会话
/sessions        列出本终端的会话
/use 编号前缀    切换会话
/history         查看当前会话消息
/status          查看业务状态、运行状态和结束原因
/context         查看最近成功调用的来源记录（不含正文）
/demo            启动无需模型的流程演示，停在等待确认
/confirm         确认当前演示并继续
/cancel          取消当前流程
/events          查看最近的运行事件（不含对话正文）
/end             结束当前会话
/clear           清空内存会话及缓存回复；磁盘存档保留，已结束状态保持
/help            显示命令
/exit            退出
""".strip()


class OfflineModel:
    def __init__(self):
        self.calls = 0

    async def generate_reply(self, messages):
        self.calls += 1
        return "离线演示回复：本轮仅验证运行机制，没有访问模型服务。"


def check_configuration() -> int:
    settings = get_settings()
    print("Personal Emotional Agent | 本地配置检查")
    print("项目目录：", PROJECT_ROOT)
    print("模型：", settings.deepseek_model)
    print("API Key：", "已配置（尚未验证）" if settings.key_configured else "未配置")
    print("超时：", settings.llm_timeout_seconds, "秒")
    print("上下文预算：", settings.runtime_max_context_chars, "字符（不是精确 token）")
    print("步骤上限：", settings.runtime_max_steps)
    print("运行 python -m app.cli --demo 可查看离线流程演示。")
    return 0


def configure_key() -> int:
    if not sys.stdin.isatty():
        print("请在交互式终端运行，或直接编辑项目 .env。")
        return 2
    key = getpass.getpass("请输入 DeepSeek API Key（输入不显示）：").strip()
    if not key:
        print("未输入 Key，配置保持不变。")
        return 2
    set_key(str(PROJECT_ROOT / ".env"), "DEEPSEEK_API_KEY", key)
    print("Key 已保存到本地 .env。")
    return 0


def show_status(session):
    workflow = session.workflow
    reason = workflow.stop_reason.value if workflow.stop_reason else "-"
    print(
        f"[会话 {str(session.session_id)[:8]} | {session.status.value} | "
        f"业务 {session.state.value} | 运行 {workflow.phase.value} | "
        f"步骤 {workflow.step} | 原因 {reason} | 消息 {len(session.messages)} 条]"
    )


async def scripted_demo() -> int:
    configure_logging()
    service = SessionService()
    runtime = AgentOrchestrator(service)
    model = OfflineModel()
    session = service.create()
    print("离线流程演示：不访问模型服务、不消耗 API 额度。", flush=True)
    waiting = await runtime.start_demo(session.session_id)
    show_status(waiting)
    finished = await runtime.confirm(
        session.session_id, waiting.workflow.run_id,
        waiting.workflow.confirmation_token,
    )
    show_status(finished)
    request = SendMessageRequest(content="离线测试消息")
    first = await runtime.handle_message(session.session_id, request, model)
    replay = await runtime.handle_message(session.session_id, request, model)
    assert first == replay and model.calls == 1
    print("重复请求检查：两次提交，只执行一次模型调用。")
    await runtime.clear_history(session.session_id)
    show_status(service.get(session.session_id))
    await runtime.end_session(session.session_id)
    show_status(service.get(session.session_id))
    print("演示完成：暂停、确认、完成、去重、清空和结束均已执行。")
    return 0


async def chat(offline: bool = False, archive_path: Path = DEFAULT_ARCHIVE_PATH) -> int:
    settings = get_settings()
    if not offline and not settings.key_configured:
        print("请先在项目 .env 中填写 DEEPSEEK_API_KEY，或使用 --offline。")
        return 2
    configure_logging()
    model = OfflineModel() if offline else DeepSeekClient(settings)
    service = SessionService()
    runtime = AgentOrchestrator(service, settings)
    archives = ArchiveService(service, SQLiteArchiveStore(archive_path))
    current = service.create().session_id
    print("Personal Emotional Agent | " + ("离线交互演示" if offline else "DeepSeek 终端聊天"))
    print("聊天默认只保存在内存；主动 /save 才保存快照。本终端与 API 的内存会话独立。")
    print(HELP + '\n' + DRAFT_HELP + '\n' + ARCHIVE_HELP)
    show_status(service.get(current))
    while True:
        try:
            content = input("\n你 > ").strip()
        except EOFError:
            return 0
        try:
            handled, current = await handle_archive_command(content, current, archives)
            if handled:
                continue
            if await handle_draft_command(content, current, service):
                continue
            command, _, argument = content.partition(" ")
            if command == "/exit":
                return 0
            if command == "/new":
                current = service.create().session_id
                show_status(service.get(current))
            elif command == "/sessions":
                for summary in service.list():
                    print(
                        str(summary.session_id), summary.status.value,
                        summary.phase, f"消息 {summary.message_count} 条",
                    )
            elif command == "/use":
                matches = [
                    item.session_id for item in service.list()
                    if str(item.session_id).startswith(argument.strip())
                ]
                if not argument.strip() or len(matches) != 1:
                    print("请提供唯一的会话编号或前缀。")
                else:
                    current = matches[0]
                    show_status(service.get(current))
            elif command == "/history":
                for message in service.get(current).messages:
                    print(message.role, ">", message.content)
            elif command == "/status":
                show_status(service.get(current))
            elif command == "/demo":
                show_status(await runtime.start_demo(current))
            elif command == "/confirm":
                workflow = service.get(current).workflow
                if workflow.run_id is None or workflow.confirmation_token is None:
                    print("当前没有等待确认的流程。")
                else:
                    show_status(await runtime.confirm(
                        current, workflow.run_id, workflow.confirmation_token
                    ))
            elif command == "/cancel":
                workflow = service.get(current).workflow
                if workflow.run_id is None:
                    print("当前没有可取消的流程。")
                else:
                    show_status(await runtime.cancel(current, workflow.run_id))
            elif command == "/end":
                show_status(await runtime.end_session(current))
            elif command == "/clear":
                show_status(await runtime.clear_history(current))
            elif command == "/context":
                manifest = service.require(current).last_context
                if manifest is None:
                    print("当前没有成功调用的来源记录。")
                else:
                    print(manifest.model_dump_json(indent=2))
            elif command == "/events":
                for event in service.require(current).events:
                    print(event.event, event.phase.value, event.step, event.request_id)
            elif command == "/help":
                print(HELP + '\n' + DRAFT_HELP + '\n' + ARCHIVE_HELP)
            elif content.startswith("/"):
                print("未知命令，输入 /help 查看命令。")
            else:
                request = SendMessageRequest(content=content)
                print(f"正在执行，请求编号 {request.request_id}", flush=True)
                started = perf_counter()
                response = await runtime.handle_message(current, request, model)
                print("\nAgent >", response.reply)
                print(f"[耗时 {perf_counter() - started:.1f}s]")
                show_status(service.get(current))
        except ValidationError:
            print("请输入有效的非空消息。")
        except (LLMError, RuntimeFailure) as error:
            print(f"操作失败 [{error.code}]：{error.message}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Personal Emotional Agent 终端入口")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="检查本地配置")
    group.add_argument("--configure", action="store_true", help="输入本地 API Key")
    group.add_argument("--demo", action="store_true", help="自动运行离线流程演示")
    group.add_argument("--draft-demo", action="store_true", help="自动运行离线草稿版本演示")
    group.add_argument("--context-demo", action="store_true", help="离线验证草稿上下文与来源")
    group.add_argument("--archive-demo", action="store_true", help="离线验证保存、恢复与删除")
    parser.add_argument("--archive-db", type=Path, default=None, help="本地存档 SQLite 路径")
    parser.add_argument("--offline", action="store_true", help="交互式离线演示")
    arguments = parser.parse_args()
    if arguments.check:
        return check_configuration()
    if arguments.configure:
        return configure_key()
    try:
        if arguments.archive_demo:
            return asyncio.run(archive_demo(arguments.archive_db))
        if arguments.context_demo:
            return asyncio.run(context_demo())
        if arguments.draft_demo:
            return asyncio.run(draft_demo())
        if arguments.demo:
            return asyncio.run(scripted_demo())
        return asyncio.run(chat(arguments.offline, arguments.archive_db or DEFAULT_ARCHIVE_PATH))
    except KeyboardInterrupt:
        print("\n已取消当前操作并退出。")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

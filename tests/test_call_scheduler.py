import asyncio
import io
import json
import time
import wave
from uuid import uuid4

import httpx
import pytest

from app.calls.scheduler import CallLimitError, CallScheduler, call_scope
from app.config.settings import Settings
from app.llm.client import DeepSeekClient, get_chat_model
from app.llm.errors import LLMError
from app.audio.provider import AudioError
from app.audio.service import AudioService
from app.schemas.session import Message


def settings(**kwargs):
    return Settings(
        _env_file=None, deepseek_api_key="secret-key-sentinel", asr_provider="tencent",
        tencent_secret_id="test-id", tencent_secret_key="test-secret", **kwargs,
    )


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0)


def response(usage=None):
    result = {"choices": [{"finish_reason": "stop", "message": {"content": "reply-sentinel"}}]}
    if usage is not None:
        result["usage"] = usage
    return httpx.Response(200, json=result)


def wav_bytes():
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\0" * 3200)
    return output.getvalue()


def test_oldest_eligible_waiter_and_per_provider_fifo():
    async def run():
        scheduler = CallScheduler(settings(external_max_concurrent=2, llm_max_concurrent=1, asr_max_concurrent=1))
        order = []
        deepseek_release = asyncio.Event()
        audio_release = asyncio.Event()

        async def call(provider, label, gate):
            async with scheduler.slot(provider, 2):
                order.append(label)
                await gate.wait()

        first = asyncio.create_task(call("deepseek", "d1", deepseek_release))
        await until(lambda: order == ["d1"])
        second = asyncio.create_task(call("deepseek", "d2", deepseek_release))
        await until(lambda: scheduler.snapshot()["queued"] == 1)
        third = asyncio.create_task(call("deepseek", "d3", deepseek_release))
        await until(lambda: scheduler.snapshot()["queued"] == 2)
        audio = asyncio.create_task(call("tencent_asr", "a1", audio_release))
        await until(lambda: "a1" in order)
        assert order == ["d1", "a1"]
        assert scheduler.snapshot()["active"] == 2
        deepseek_release.set()
        await asyncio.gather(first, second, third)
        assert order == ["d1", "a1", "d2", "d3"]
        audio_release.set()
        await audio
        assert scheduler.snapshot()["active"] == scheduler.snapshot()["queued"] == 0
        await scheduler.close()

    asyncio.run(run())


def test_global_limit_and_queue_full_never_enter_operation():
    async def run():
        scheduler = CallScheduler(settings(external_max_concurrent=1, external_max_queue=1))
        holder = scheduler.slot("deepseek", 2)
        await holder.__aenter__()
        entered = []

        async def work():
            async with scheduler.slot("tencent_asr", 2):
                entered.append(True)

        queued = asyncio.create_task(work())
        await until(lambda: scheduler.snapshot()["queued"] == 1)
        with pytest.raises(CallLimitError) as full:
            async with scheduler.slot("deepseek", 1):
                pytest.fail("queue-full operation executed")
        assert full.value.code == "external_queue_full" and full.value.status_code == 503
        assert not entered
        await holder.__aexit__(None, None, None)
        await queued
        assert entered == [True]
        assert scheduler.snapshot()["providers"]["deepseek"]["queue_full"] == 1
        await scheduler.close()

    asyncio.run(run())


@pytest.mark.parametrize("grant_first", [False, True])
def test_cancel_waiter_even_after_slot_granted_releases_reservation(grant_first):
    async def run():
        scheduler = CallScheduler(settings(external_max_concurrent=1))
        holder = scheduler.slot("deepseek", 2)
        await holder.__aenter__()

        async def work():
            async with scheduler.slot("tencent_asr", 2):
                pytest.fail("cancelled queued operation executed")

        queued = asyncio.create_task(work())
        await until(lambda: scheduler.snapshot()["queued"] == 1)
        if grant_first:
            await holder.__aexit__(None, None, None)
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        if not grant_first:
            await holder.__aexit__(None, None, None)
        assert scheduler.snapshot()["active"] == scheduler.snapshot()["queued"] == 0
        assert scheduler.snapshot()["providers"]["tencent_asr"]["started"] == 0
        await scheduler.close()

    asyncio.run(run())


def test_queue_timeout_is_removed_and_never_calls_provider():
    async def run():
        scheduler = CallScheduler(settings(external_max_concurrent=1, external_queue_timeout_seconds=0.01))
        holder = scheduler.slot("deepseek", 2)
        await holder.__aenter__()
        with pytest.raises(CallLimitError) as error:
            async with scheduler.slot("tencent_asr", 1):
                pytest.fail("expired queue entry executed")
        assert error.value.code == "external_queue_timeout"
        assert scheduler.snapshot()["queued"] == 0
        await holder.__aexit__(None, None, None)
        assert scheduler.snapshot()["providers"]["tencent_asr"]["queue_timeout"] == 1
        await scheduler.close()

    asyncio.run(run())


def test_queue_wait_is_included_in_execution_deadline():
    async def run():
        scheduler = CallScheduler(settings(external_max_concurrent=1))
        holder = scheduler.slot("deepseek", 2)
        await holder.__aenter__()

        async def work():
            async with scheduler.slot("tencent_asr", 0.15):
                await asyncio.Event().wait()

        task = asyncio.create_task(work())
        await until(lambda: scheduler.snapshot()["queued"] == 1)
        await asyncio.sleep(0.08)
        await holder.__aexit__(None, None, None)
        with pytest.raises(CallLimitError) as error:
            await task
        assert error.value.code == "external_call_timeout"
        record = scheduler.snapshot()["recent"][-1]
        assert record["queue_ms"] >= 50
        assert record["execution_ms"] < 125
        assert record["state"] == "execution_timeout"
        assert scheduler.snapshot()["active"] == 0
        await scheduler.close()

    asyncio.run(run())


@pytest.mark.parametrize("own_deadline", [True, False])
def test_adapter_suppressing_cancellation_cannot_return_late_success(own_deadline):
    async def run():
        scheduler = CallScheduler(settings())
        entered = asyncio.Event()

        async def work():
            async with scheduler.slot("deepseek", 0.02 if own_deadline else 2):
                entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    return "late private reply"

        task = asyncio.create_task(work())
        await entered.wait()
        if not own_deadline:
            task.cancel()
        with pytest.raises(CallLimitError if own_deadline else asyncio.CancelledError):
            await task
        assert scheduler.snapshot()["active"] == 0
        assert scheduler.snapshot()["recent"][-1]["state"] == ("execution_timeout" if own_deadline else "cancelled")
        await scheduler.close()

    asyncio.run(run())


def test_shutdown_stops_waiting_and_running_calls():
    async def run():
        scheduler = CallScheduler(settings(external_max_concurrent=1))

        async def work(provider):
            async with scheduler.slot(provider, 2):
                await asyncio.Event().wait()

        running = asyncio.create_task(work("deepseek"))
        await until(lambda: scheduler.snapshot()["active"] == 1)
        waiting = asyncio.create_task(work("tencent_asr"))
        await until(lambda: scheduler.snapshot()["queued"] == 1)
        await scheduler.close()
        results = await asyncio.gather(running, waiting, return_exceptions=True)
        assert isinstance(results[0], asyncio.CancelledError)
        assert isinstance(results[1], CallLimitError)
        assert scheduler.snapshot()["active"] == scheduler.snapshot()["queued"] == 0
        with pytest.raises(CallLimitError):
            async with scheduler.slot("deepseek", 1):
                pytest.fail("closed scheduler executed")

    asyncio.run(run())


def test_parent_deadline_and_delayed_event_loop_cannot_extend_budget():
    async def run():
        scheduler = CallScheduler(settings())
        with call_scope(uuid4(), deadline=asyncio.get_running_loop().time() - 1):
            with pytest.raises(CallLimitError):
                async with scheduler.slot("deepseek", 2):
                    pytest.fail("expired parent budget executed provider")
        with pytest.raises(CallLimitError) as error:
            async with scheduler.slot("deepseek", 0.01):
                time.sleep(0.02)
        assert error.value.code == "external_call_timeout"
        assert scheduler.snapshot()["active"] == scheduler.snapshot()["queued"] == 0
        await scheduler.close()

    asyncio.run(run())


def test_deepseek_and_audio_share_capacity_and_cancel_queued_audio():
    async def run():
        config = settings(external_max_concurrent=1)
        scheduler = CallScheduler(config)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def handler(request):
            entered.set()
            await release.wait()
            return response({"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8})

        class ASR:
            calls = 0

            async def transcribe(self, audio):
                self.calls += 1
                return "transcript-sentinel"

        recognizer = ASR()
        audio = AudioService(config, recognizer, scheduler)
        model = DeepSeekClient(config, httpx.MockTransport(handler), scheduler)
        request_id, run_id = uuid4(), uuid4()
        with call_scope(request_id, run_id):
            chat = asyncio.create_task(model.generate_reply([Message(role="user", content="private-prompt-sentinel")]))
        await entered.wait()
        audio_id = uuid4()
        transcription = asyncio.create_task(audio.transcribe(wav_bytes(), audio_id, "configured"))
        await until(lambda: scheduler.snapshot()["queued"] == 1)
        assert recognizer.calls == 0
        audio.cancel(audio_id)
        with pytest.raises(AudioError) as error:
            await transcription
        assert error.value.code == "audio_cancelled"
        release.set()
        assert await chat == "reply-sentinel"
        result = await audio.transcribe(wav_bytes(), uuid4(), "configured")
        assert await audio.transcribe(wav_bytes(), result.request_id, "configured") == result
        assert recognizer.calls == 1
        snapshot = scheduler.snapshot()
        assert snapshot["providers"]["deepseek"]["total_tokens"] == 8
        assert snapshot["providers"]["tencent_asr"]["input_audio_seconds"] == pytest.approx(0.1)
        assert snapshot["providers"]["tencent_asr"]["started"] == 1
        chat_record = next(r for r in snapshot["recent"] if r["provider"] == "deepseek")
        assert chat_record["request_id"] == str(request_id) and chat_record["run_id"] == str(run_id)
        for private in ("secret-key-sentinel", "private-prompt-sentinel", "reply-sentinel", "transcript-sentinel"):
            assert private not in json.dumps(snapshot)
        await audio.close()
        await scheduler.close()

    asyncio.run(run())


@pytest.mark.parametrize("usage", [
    None, {}, {"prompt_tokens": -1, "completion_tokens": 2, "total_tokens": 1},
    {"prompt_tokens": True, "completion_tokens": 2, "total_tokens": 3},
    {"prompt_tokens": "1", "completion_tokens": 2, "total_tokens": 3},
    {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 999},
])
def test_missing_or_invalid_usage_does_not_invent_tokens_or_break_reply(usage):
    async def run():
        config = settings()
        scheduler = CallScheduler(config)
        model = DeepSeekClient(config, httpx.MockTransport(lambda request: response(usage)), scheduler)
        assert await model.generate_reply([]) == "reply-sentinel"
        stats = scheduler.snapshot()["providers"]["deepseek"]
        assert stats["token_usage_missing_calls"] == 1
        assert stats["token_usage_reported_calls"] == stats["total_tokens"] == 0
        assert scheduler.snapshot()["recent"][-1]["token_usage"] is None
        await scheduler.close()

    asyncio.run(run())


def test_usage_is_recorded_even_when_reply_validation_fails():
    async def run():
        config = settings()
        scheduler = CallScheduler(config)

        def handler(request):
            return httpx.Response(200, json={"choices": [], "usage": {"prompt_tokens": 4, "completion_tokens": 3, "total_tokens": 7}})

        model = DeepSeekClient(config, httpx.MockTransport(handler), scheduler)
        with pytest.raises(LLMError):
            await model.generate_reply([])
        stats = scheduler.snapshot()["providers"]["deepseek"]
        assert stats["failed"] == 1 and stats["total_tokens"] == 7
        await scheduler.close()

    asyncio.run(run())


def test_demo_and_replay_do_not_consume_call_budget():
    async def run():
        config = settings()
        scheduler = CallScheduler(config)
        audio = AudioService(config, scheduler=scheduler)
        request_id = uuid4()
        await audio.transcribe(wav_bytes(), request_id, "demo")
        await audio.transcribe(wav_bytes(), request_id, "demo")
        assert not scheduler.snapshot()["recent"]
        await audio.close()
        await scheduler.close()

    asyncio.run(run())


def test_queue_failure_replays_without_changing_chat_or_reinvoking_asr():
    from app.agent.errors import RuntimeFailure
    from app.agent.orchestrator import AgentOrchestrator
    from app.schemas.message import SendMessageRequest
    from app.services.session_service import SessionService

    async def run():
        config = settings(external_max_concurrent=1, external_queue_timeout_seconds=0.01)
        scheduler = CallScheduler(config)
        holder = scheduler.slot("deepseek", 2)
        await holder.__aenter__()

        class ASR:
            async def transcribe(self, audio):
                pytest.fail("queue-expired ASR called supplier")

        audio = AudioService(config, ASR(), scheduler)
        audio_id = uuid4()
        for _ in range(2):
            with pytest.raises(AudioError) as error:
                await audio.transcribe(wav_bytes(), audio_id, "configured")
            assert error.value.code == "external_queue_timeout"
        assert scheduler.snapshot()["providers"]["tencent_asr"]["queue_timeout"] == 1
        sessions = SessionService()
        session = sessions.create()
        runtime = AgentOrchestrator(sessions, config)
        model = DeepSeekClient(config, httpx.MockTransport(lambda request: pytest.fail("expired chat called supplier")), scheduler)
        request = SendMessageRequest(content="private unsent prompt", request_id=uuid4())
        for _ in range(2):
            with pytest.raises((LLMError, RuntimeFailure)) as error:
                await runtime.handle_message(session.session_id, request, model)
            assert error.value.code == "external_queue_timeout"
        assert not session.messages
        assert scheduler.snapshot()["providers"]["deepseek"]["queue_timeout"] == 1
        await holder.__aexit__(None, None, None)
        await audio.close()
        await scheduler.close()

    asyncio.run(run())


def test_recent_records_are_bounded_detached_and_metadata_only(caplog):
    async def run():
        scheduler = CallScheduler(settings())
        for _ in range(205):
            async with scheduler.slot("deepseek", 1) as ticket:
                ticket.record_tokens({"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3, "ignored": "private-extra-sentinel"})
        snapshot = scheduler.snapshot()
        assert len(snapshot["recent"]) == 200
        assert snapshot["providers"]["deepseek"]["total_tokens"] == 615
        snapshot["recent"][0]["token_usage"]["total_tokens"] = 999
        assert scheduler.snapshot()["recent"][0]["token_usage"]["total_tokens"] == 3
        assert "private-extra-sentinel" not in json.dumps(scheduler.snapshot())
        assert not scheduler.waiting and not scheduler.active
        await scheduler.close()

    with caplog.at_level("INFO", logger="app.calls"):
        asyncio.run(run())
    assert "private-extra-sentinel" not in caplog.text


def test_api_factory_uses_shared_scheduler_and_keeps_chat_dedup(client, monkeypatch):
    import app.llm.client as llm_module

    original = llm_module.DeepSeekClient
    config = settings()
    calls = []

    def handler(request):
        calls.append(True)
        return response({"prompt_tokens": 6, "completion_tokens": 4, "total_tokens": 10})

    def make(settings, scheduler=None):
        return original(settings, httpx.MockTransport(handler), scheduler)

    monkeypatch.setattr(llm_module, "get_settings", lambda: config)
    monkeypatch.setattr(llm_module, "DeepSeekClient", make)
    client.app.dependency_overrides.pop(get_chat_model)
    assert client.app.state.audio_service.scheduler is client.app.state.call_scheduler
    session = client.post("/sessions").json()["session_id"]
    request_id = str(uuid4())
    payload = {"content": "private-prompt-sentinel", "request_id": request_id}
    first = client.post(f"/sessions/{session}/messages", json=payload)
    assert first.status_code == 200
    assert client.post(f"/sessions/{session}/messages", json=payload).json() == first.json()
    assert len(calls) == 1
    snapshot = client.get("/runtime/calls").json()
    assert snapshot["providers"]["deepseek"]["started"] == 1
    record = snapshot["recent"][0]
    assert record["request_id"] == request_id and record["run_id"] == first.json()["run_id"]
    assert "private-prompt-sentinel" not in json.dumps(snapshot)

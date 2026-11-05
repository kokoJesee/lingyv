import asyncio
import logging
import math
from collections import Counter, deque
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from app.config.settings import Settings
from app.llm.errors import LLMError

logger = logging.getLogger(__name__)
Provider = Literal["deepseek", "tencent_asr"]


class CallLimitError(LLMError):
    """An admission or deadline failure with a safe public message."""


@dataclass(frozen=True)
class CallContext:
    request_id: UUID | None = None
    run_id: UUID | None = None
    input_audio_seconds: float | None = None
    deadline: float | None = None


_context = ContextVar("external_call_context", default=CallContext())


@contextmanager
def call_scope(request_id: UUID, run_id: UUID | None = None, input_audio_seconds: float | None = None, deadline: float | None = None):
    if not isinstance(request_id, UUID) or (run_id is not None and not isinstance(run_id, UUID)):
        raise ValueError("Call correlation IDs must be UUIDs")
    if input_audio_seconds is not None and (
        isinstance(input_audio_seconds, bool) or not math.isfinite(input_audio_seconds) or input_audio_seconds < 0
    ):
        raise ValueError("Audio duration must be finite and nonnegative")
    if deadline is not None and (isinstance(deadline, bool) or not math.isfinite(deadline)):
        raise ValueError("Call deadline must be finite")
    token = _context.set(CallContext(request_id, run_id, input_audio_seconds, deadline))
    try:
        yield
    finally:
        _context.reset(token)


@dataclass
class CallTicket:
    provider: Provider
    created_at: float
    context: CallContext
    call_id: UUID = field(default_factory=uuid4)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: float | None = None
    token_usage: dict[str, int] | None = None

    def record_tokens(self, usage):
        if self.token_usage is not None or not isinstance(usage, dict):
            return
        keys = ("prompt_tokens", "completion_tokens", "total_tokens")
        values = {key: usage.get(key) for key in keys}
        if all(type(value) is int and value >= 0 for value in values.values()):
            if values["total_tokens"] == values["prompt_tokens"] + values["completion_tokens"]:
                self.token_usage = values

    def record(self, state: str, now: float) -> dict:
        started = self.started_at
        return {
            "call_id": str(self.call_id), "provider": self.provider,
            "request_id": str(self.context.request_id) if self.context.request_id else None,
            "run_id": str(self.context.run_id) if self.context.run_id else None,
            "timestamp": self.timestamp, "state": state,
            "queue_ms": round(((started if started is not None else now) - self.created_at) * 1000, 3),
            "execution_ms": round((now - started) * 1000, 3) if started is not None else 0,
            "input_audio_seconds": self.context.input_audio_seconds,
            "token_usage": dict(self.token_usage) if self.token_usage is not None else None,
        }


@dataclass
class PendingCall:
    ticket: CallTicket
    future: asyncio.Future
    owner: asyncio.Task


class CallScheduler:
    """Oldest eligible waiter wins; same-provider calls retain FIFO order."""

    def __init__(self, settings: Settings):
        self.max_concurrent = settings.external_max_concurrent
        self.max_queue = settings.external_max_queue
        self.queue_timeout = settings.external_queue_timeout_seconds
        self.provider_limits = {"deepseek": settings.llm_max_concurrent, "tencent_asr": settings.asr_max_concurrent}
        self.waiting: deque[PendingCall] = deque()
        self.active: dict[UUID, PendingCall] = {}
        self.counts = {provider: Counter() for provider in self.provider_limits}
        self.recent: deque[dict] = deque(maxlen=200)
        self.closed = False
        self.loop = None

    def _bind(self):
        loop = asyncio.get_running_loop()
        if self.loop is not loop:
            if self.active or self.waiting:
                raise RuntimeError("An active scheduler belongs to one event loop")
            self.loop = loop
        return loop

    def _can_start(self, provider: Provider) -> bool:
        return len(self.active) < self.max_concurrent and sum(
            call.ticket.provider == provider for call in self.active.values()
        ) < self.provider_limits[provider]

    def _pump(self):
        if self.closed:
            return
        for call in list(self.waiting):
            if self._can_start(call.ticket.provider):
                self.waiting.remove(call)
                self.active[call.ticket.call_id] = call
                call.future.set_result(None)

    def _finish(self, call: PendingCall, state: str):
        if call in self.waiting:
            self.waiting.remove(call)
        self.active.pop(call.ticket.call_id, None)
        if not call.future.done():
            call.future.cancel()
        elif not call.future.cancelled():
            call.future.exception()
        ticket = call.ticket
        counts = self.counts[ticket.provider]
        counts[state] += 1
        if ticket.started_at is not None:
            if ticket.provider == "deepseek":
                counts["token_usage_reported_calls" if ticket.token_usage is not None else "token_usage_missing_calls"] += 1
            if ticket.token_usage is not None:
                for key, value in ticket.token_usage.items():
                    counts[key] += value
            if ticket.provider == "tencent_asr" and ticket.context.input_audio_seconds is not None:
                counts["input_audio_seconds"] += ticket.context.input_audio_seconds
        record = ticket.record(state, self.loop.time())
        self.recent.append(record)
        logger.info(
            "External call | call_id=%s | request_id=%s | run_id=%s | provider=%s | state=%s | queue_ms=%s | execution_ms=%s",
            record["call_id"], record["request_id"], record["run_id"], ticket.provider,
            state, record["queue_ms"], record["execution_ms"],
        )
        self._pump()

    @asynccontextmanager
    async def slot(self, provider: Provider, timeout_seconds: float):
        if provider not in self.provider_limits:
            raise ValueError("Unknown external call provider")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("Call timeout must be finite and positive")
        loop = self._bind()
        created = loop.time()
        deadline = created + timeout_seconds
        context = _context.get()
        if context.deadline is not None:
            deadline = min(deadline, context.deadline)
        queue_deadline = min(deadline, created + self.queue_timeout)
        call = PendingCall(CallTicket(provider, created, context), loop.create_future(), asyncio.current_task())
        state = "failed"
        try:
            if self.closed:
                state = "closed"
                raise CallLimitError("external_calls_closed", "外部调用正在停止，请稍后重试。", 503)
            self._pump()
            if len(self.waiting) >= self.max_queue and not self._can_start(provider):
                state = "queue_full"
                raise CallLimitError("external_queue_full", "外部调用队列已满，请稍后重试。", 503)
            self.waiting.append(call)
            self._pump()
            try:
                await asyncio.wait_for(asyncio.shield(call.future), max(0, queue_deadline - loop.time()))
            except TimeoutError:
                state = "queue_timeout"
                raise CallLimitError("external_queue_timeout", "外部调用排队超时，请使用新请求编号重试。", 503) from None
            if loop.time() >= queue_deadline:
                state = "queue_timeout"
                raise CallLimitError("external_queue_timeout", "外部调用排队超时，请使用新请求编号重试。", 503)
            call.ticket.started_at = loop.time()
            self.counts[provider]["started"] += 1
            cancel_count = call.owner.cancelling()
            try:
                async with asyncio.timeout_at(deadline) as timer:
                    yield call.ticket
                    if timer.expired() or loop.time() >= deadline:
                        raise TimeoutError
                    if call.owner.cancelling() > cancel_count:
                        raise asyncio.CancelledError
            except TimeoutError:
                state = "execution_timeout"
                raise CallLimitError("external_call_timeout", "外部调用超过本次总时限。", 504) from None
            state = "completed"
        except asyncio.CancelledError:
            if loop.time() >= deadline:
                state = "execution_timeout" if call.ticket.started_at is not None else "queue_timeout"
            else:
                state = "cancelled"
            raise
        except CallLimitError as error:
            if error.code == "external_calls_closed":
                state = "closed"
            raise
        except BaseException as error:
            if isinstance(error, LLMError) and error.code in {"model_timeout", "audio_timeout"}:
                state = "execution_timeout"
            raise
        finally:
            self._finish(call, state)

    def snapshot(self) -> dict:
        now = self._bind().time()
        providers = {}
        keys = (
            "started", "completed", "failed", "cancelled", "queue_full", "queue_timeout",
            "execution_timeout", "closed", "token_usage_reported_calls", "token_usage_missing_calls",
            "prompt_tokens", "completion_tokens", "total_tokens", "input_audio_seconds",
        )
        for provider, limit in self.provider_limits.items():
            providers[provider] = {
                "limit": limit,
                "active": sum(c.ticket.provider == provider for c in self.active.values()),
                "queued": sum(c.ticket.provider == provider for c in self.waiting),
                **{key: self.counts[provider][key] for key in keys},
            }
        return {
            "scope": "process", "closed": self.closed,
            "limits": {"global_concurrent": self.max_concurrent, "queue_size": self.max_queue, "queue_timeout_seconds": self.queue_timeout},
            "active": len(self.active), "queued": len(self.waiting), "providers": providers,
            "inflight": [c.ticket.record("running" if c.ticket.started_at is not None else "queued", now) for c in [*self.active.values(), *self.waiting]],
            "recent": [{**record, "token_usage": dict(record["token_usage"]) if record["token_usage"] is not None else None} for record in self.recent],
        }

    async def close(self):
        self._bind()
        self.closed = True
        current = asyncio.current_task()
        tasks = {c.owner for c in [*self.active.values(), *self.waiting] if c.owner is not current}
        for call in self.waiting:
            if not call.future.done():
                call.future.set_exception(CallLimitError("external_calls_closed", "外部调用正在停止。", 503))
        for call in list(self.active.values()):
            if call.owner is not current:
                call.owner.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

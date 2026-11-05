import logging
from collections.abc import Sequence
from time import perf_counter
from typing import Protocol

import httpx
from starlette.requests import Request

from app.calls.scheduler import CallLimitError, CallScheduler, CallTicket
from app.config.settings import Settings, get_settings
from app.llm.errors import LLMError
from app.llm.prompts import PROMPT_VERSION, SYSTEM_PROMPT
from app.schemas.session import Message

logger = logging.getLogger(__name__)


class ChatModel(Protocol):
    async def generate_reply(self, messages: Sequence[Message]) -> str: ...


class DeepSeekClient:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
        scheduler: CallScheduler | None = None,
    ):
        self.settings = settings
        self.transport = transport
        self.scheduler = scheduler or CallScheduler(settings)

    async def generate_reply(self, messages: Sequence[Message]) -> str:
        return await self._generate(messages, SYSTEM_PROMPT, PROMPT_VERSION)

    async def generate_structured(self, *, system_prompt: str, request_json: str) -> str:
        # Separate system instructions; preserve the existing chat endpoint and transport.
        from app.psychology.prompts import PROMPT_VERSION as psychology_prompt_version
        return await self._generate([Message(role="user", content=request_json)], system_prompt, psychology_prompt_version)

    async def _generate(self, messages: Sequence[Message], system_prompt: str, prompt_version: str) -> str:
        if not self.settings.key_configured:
            raise LLMError(
                "model_not_configured",
                "尚未配置 DeepSeek API Key，请在项目 .env 中填写 DEEPSEEK_API_KEY。",
                status_code=503,
            )

        try:
            async with self.scheduler.slot("deepseek", self.settings.llm_timeout_seconds) as ticket:
                return await self._request_reply(messages, ticket, system_prompt, prompt_version)
        except CallLimitError as error:
            if error.code == "external_call_timeout":
                raise LLMError("model_timeout", "模型回复超时，请稍后重试。", 504) from None
            raise

    async def _request_reply(self, messages: Sequence[Message], ticket: CallTicket, system_prompt: str, prompt_version: str) -> str:

        payload = {
            "model": self.settings.deepseek_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                *[message.model_dump() for message in messages],
            ],
            "stream": False,
            "thinking": {"type": self.settings.llm_thinking},
            "max_tokens": self.settings.llm_max_tokens,
        }
        started = perf_counter()
        logger.info(
            "DeepSeek request started | model=%s | context_messages=%d | prompt_version=%s",
            self.settings.deepseek_model,
            len(messages), prompt_version,
        )

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.settings.llm_timeout_seconds),
                transport=self.transport,
            ) as client:
                response = await client.post(
                    str(self.settings.deepseek_base_url).rstrip("/")
                    + "/chat/completions",
                    headers={
                        "Authorization": "Bearer "
                        + self.settings.deepseek_api_key.get_secret_value().strip(),
                    },
                    json=payload,
                )
        except httpx.TimeoutException:
            logger.warning("DeepSeek request timed out")
            raise LLMError(
                "model_timeout", "模型回复超时，请稍后重试。", status_code=504
            ) from None
        except httpx.RequestError:
            logger.warning("DeepSeek connection failed")
            raise LLMError(
                "model_connection_failed", "无法连接 DeepSeek，请检查网络后重试。"
            ) from None

        if not response.is_success:
            # 不记录响应正文，避免上游回显用户内容或凭据。
            logger.warning(
                "DeepSeek request rejected | upstream_status=%d",
                response.status_code,
            )
            if response.status_code in (401, 403):
                raise LLMError(
                    "model_authentication_failed",
                    "DeepSeek 身份验证失败，请检查本地 API Key。",
                )
            if response.status_code == 402:
                raise LLMError(
                    "model_balance_insufficient",
                    "DeepSeek 账户余额不足，请检查账户。",
                )
            if response.status_code in (429, 500, 502, 503, 504):
                raise LLMError(
                    "model_unavailable",
                    "DeepSeek 服务暂时繁忙，请稍后重试。",
                    status_code=503,
                )
            raise LLMError(
                "model_request_rejected",
                "DeepSeek 拒绝了本次请求，请检查模型名称和接口配置。",
            )

        try:
            body = response.json()
            if isinstance(body, dict):
                ticket.record_tokens(body.get("usage"))
            choice = body["choices"][0]
            finish_reason = choice["finish_reason"]
            reply = choice["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            logger.warning("DeepSeek returned an invalid response")
            raise LLMError(
                "invalid_model_response", "模型返回格式异常，请稍后重试。"
            ) from None

        if finish_reason != "stop":
            logger.warning("DeepSeek returned an incomplete response")
            raise LLMError(
                "incomplete_model_response",
                "本次未获得完整回复，请稍后重试或调整生成长度配置。",
            )
        if not isinstance(reply, str) or not reply.strip():
            logger.warning("DeepSeek returned empty content")
            raise LLMError(
                "invalid_model_response", "模型未返回可用内容，请稍后重试。"
            )
        reply = reply.strip()
        logger.info(
            "DeepSeek request completed | elapsed_ms=%.0f | response_chars=%d",
            (perf_counter() - started) * 1000,
            len(reply),
        )
        return reply


def get_chat_model(request: Request = None) -> ChatModel:
    settings = get_settings()
    if settings.psychology_enabled and settings.psychology_demo_mode:
        from app.psychology.demo import ScriptedSupportModel
        return ScriptedSupportModel(settings.psychology_demo_route)
    scheduler = request.app.state.call_scheduler if request is not None else None
    return DeepSeekClient(settings, scheduler=scheduler)

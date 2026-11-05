"""One bounded model call, strict result validation, no state mutation or implicit retries."""
import asyncio
from typing import Callable, Protocol

from app.config.settings import get_settings
from app.llm.errors import LLMError
from app.llm.structured import parse_structured_output
from app.agent.errors import RuntimeFailure
from app.schemas.psychology import PsychologyRequest, PsychologyResponse, PsychologyResult
from app.psychology.policy import PsychologyFailure, REVIEW_STATUS, validate_policy
from app.psychology.prompts import system_prompt, PROMPT_VERSION


class StructuredPsychologyModel(Protocol):
    async def generate_structured(self, *, system_prompt: str, request_json: str) -> str: ...


def _discard(task):
    # Retrieve eventual exceptions without logging body or allowing late submission.
    if not task.cancelled():
        task.exception()


class PsychologyAdapter:
    review_status = REVIEW_STATUS
    prompt_version = PROMPT_VERSION

    def __init__(self, model: StructuredPsychologyModel, settings=None, *, max_output_chars=12000):
        if type(max_output_chars) is not int or max_output_chars < 1:
            raise ValueError('max_output_chars must be a positive integer')
        self.model = model
        self.settings = settings or get_settings()
        self.max_output_chars = max_output_chars

    async def evaluate(self, request: PsychologyRequest, *, ensure_current: Callable[[], None] | None = None,
                       previous_route=None, unresolved=None) -> PsychologyResponse:
        # Never trust a mutable/constructed instance at the boundary.
        request = PsychologyRequest.model_validate_json(request.model_dump_json())
        if unresolved is None:
            unresolved = previous_route in ('safety_support', 'urgent_real_world_help')
        if previous_route not in (None, 'routine_support', 'clarify_safety', 'safety_support', 'urgent_real_world_help') or type(unresolved) is not bool:
            raise ValueError('invalid server-owned route context')
        prompt, payload = system_prompt(), request.model_dump_json()
        if len(prompt)+len(payload) > self.settings.runtime_max_context_chars:
            raise PsychologyFailure('context_limit_exceeded', '心理输入超过上下文字符预算。', 413)
        if ensure_current: ensure_current()
        task = asyncio.create_task(self.model.generate_structured(system_prompt=prompt, request_json=payload))
        try:
            done, _ = await asyncio.wait({task}, timeout=self.settings.llm_timeout_seconds)
            if not done:
                raise PsychologyFailure('model_timeout', '心理模块调用超时，未生成有效评估。', 504)
            text = task.result()
            if not isinstance(text, str) or len(text) > self.max_output_chars:
                raise PsychologyFailure('output_validation_failed', '心理输出类型或长度无效。')
            result = parse_structured_output(text, PsychologyResult)
            validate_policy(request, result, previous_route=previous_route, unresolved=unresolved)
            if ensure_current: ensure_current()
            return PsychologyResponse(schema_version=request.schema_version, session_id=request.session_id,
                request_id=request.request_id, session_revision=request.session_revision, result=result)
        except asyncio.CancelledError:
            raise  # Cancellation is never converted into a success or fallback reply.
        except PsychologyFailure:
            raise
        except LLMError as error:
            raise PsychologyFailure(error.code, '心理模块暂不可用，未生成有效评估。', error.status_code) from None
        except RuntimeFailure as error:
            raise PsychologyFailure(error.code, error.message, error.status_code) from None
        except Exception:
            raise PsychologyFailure('model_unavailable', '心理模块执行失败，未生成有效评估。', 503) from None
        finally:
            if not task.done():
                task.cancel()
            task.add_done_callback(_discard)

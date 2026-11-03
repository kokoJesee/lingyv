import json
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agent.errors import RuntimeFailure

Result = TypeVar("Result", bound=BaseModel)


class ReplyContent(BaseModel):
    model_config = ConfigDict(strict=True, str_strip_whitespace=True)
    content: str = Field(min_length=1)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("non-finite JSON number")


def parse_structured_output(text: str, schema: type[Result]) -> Result:
    """严格解码和校验，不猜字段、不自动补造模型内容。"""
    try:
        value = json.loads(
            text, object_pairs_hook=_unique_object, parse_constant=_reject_constant
        )
        return schema.model_validate(value)
    except (ValueError, TypeError, ValidationError):
        raise RuntimeFailure(
            "output_validation_failed", "模型输出未通过结构校验。", 502
        ) from None


def validate_reply(value: object) -> str:
    try:
        return ReplyContent(content=value).content
    except ValidationError:
        raise RuntimeFailure(
            "output_validation_failed", "模型未返回有效文本。", 502
        ) from None

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Protocol

import httpx

from app.config.settings import Settings
from app.llm.errors import LLMError


class AudioError(LLMError):
    """Safe public error without provider bodies, audio or credentials."""


class Recognizer(Protocol):
    async def transcribe(self, audio: bytes) -> str: ...


def signed_headers(payload: bytes, settings: Settings, timestamp: int) -> dict[str, str]:
    host = "asr.tencentcloudapi.com"
    content_type = "application/json; charset=utf-8"
    date = datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%d")
    scope = f"{date}/asr/tc3_request"
    canonical_headers = f"content-type:{content_type}\nhost:{host}\n"
    canonical_request = "\n".join([
        "POST", "/", "", canonical_headers, "content-type;host",
        hashlib.sha256(payload).hexdigest(),
    ])
    string_to_sign = "\n".join([
        "TC3-HMAC-SHA256", str(timestamp), scope,
        hashlib.sha256(canonical_request.encode()).hexdigest(),
    ])

    def sign(key: bytes, value: str) -> bytes:
        return hmac.new(key, value.encode(), hashlib.sha256).digest()

    secret = settings.tencent_secret_key.get_secret_value().strip()
    date_key = sign(("TC3" + secret).encode(), date)
    signing_key = sign(sign(date_key, "asr"), "tc3_request")
    signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()
    secret_id = settings.tencent_secret_id.get_secret_value().strip()
    headers = {
        "Content-Type": content_type,
        "Host": host,
        "X-TC-Action": "SentenceRecognition",
        "X-TC-Version": "2019-06-14",
        "X-TC-Timestamp": str(timestamp),
        "Authorization": (
            f"TC3-HMAC-SHA256 Credential={secret_id}/{scope}, "
            f"SignedHeaders=content-type;host, Signature={signature}"
        ),
    }
    token = settings.tencent_token.get_secret_value().strip()
    if token:
        headers["X-TC-Token"] = token
    return headers


class TencentRecognizer:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.transport = transport

    async def transcribe(self, audio: bytes) -> str:
        if not self.settings.asr_configured:
            raise AudioError("audio_not_configured", "请在本地配置腾讯云语音识别凭据并重启服务。", 503)
        payload = json.dumps({
            "EngSerViceType": self.settings.asr_engine,
            "SourceType": 1,
            "VoiceFormat": "wav",
            "Data": base64.b64encode(audio).decode("ascii"),
            "DataLen": len(audio),
            "FilterDirty": 0,
            "FilterModal": 0,
            "FilterPunc": 0,
            "ConvertNumMode": 0,
        }, separators=(",", ":")).encode()
        headers = signed_headers(payload, self.settings, int(time.time()))
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.asr_timeout_seconds, transport=self.transport,
            ) as client:
                async with client.stream(
                    "POST", "https://asr.tencentcloudapi.com/", content=payload, headers=headers,
                ) as response:
                    if response.status_code != 200:
                        raise AudioError("audio_provider_unavailable", "语音识别服务暂不可用，请稍后重新尝试。", 503)
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 65536:
                            raise AudioError("audio_invalid_response", "语音识别结果格式异常。")
        except httpx.TimeoutException:
            raise AudioError("audio_timeout", "语音识别超时，可使用新请求编号重新尝试。", 504) from None
        except httpx.RequestError:
            raise AudioError("audio_connection_failed", "无法连接语音识别服务，请检查网络。", 503) from None
        try:
            result = json.loads(body)["Response"]
            if not isinstance(result, dict):
                raise ValueError
            if "Error" in result:
                code = str(result["Error"].get("Code", ""))
                if code.startswith("AuthFailure"):
                    raise AudioError("audio_authentication_failed", "语音服务认证失败，请检查本地腾讯云凭据。", 503)
                if code.startswith(("LimitExceeded", "RequestLimitExceeded", "ResourceUnavailable")):
                    raise AudioError("audio_provider_unavailable", "语音服务额度不足或繁忙，请检查账户后重试。", 503)
                raise AudioError("audio_provider_rejected", "语音服务拒绝识别，请检查音频和服务开通状态。")
            text = result["Result"]
            if not isinstance(text, str) or len(text) > 8000:
                raise ValueError
            if not text.strip():
                raise AudioError("audio_no_speech", "未识别到文字，请检查麦克风并重新录音。", 422)
            return text.strip()
        except (ValueError, KeyError, TypeError, AttributeError):
            raise AudioError("audio_invalid_response", "语音识别结果格式异常。") from None

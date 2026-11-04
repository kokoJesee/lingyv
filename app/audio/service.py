import asyncio
import hashlib
import io
import logging
import time
import wave
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from app.audio.provider import AudioError, Recognizer, TencentRecognizer
from app.config.settings import Settings

logger = logging.getLogger(__name__)
MAX_AUDIO_BYTES = 2_000_000
MAX_UPLOAD_BYTES = MAX_AUDIO_BYTES + 16384
MAX_SECONDS = 60


class Transcription(BaseModel):
    request_id: UUID
    text: str
    provider: Literal["demo", "tencent"]
    simulated: bool
    duration_seconds: float


def validate_wav(audio: bytes) -> float:
    if len(audio) > MAX_AUDIO_BYTES:
        raise AudioError("audio_too_large", "音频文件不能超过 2 MB。", 413)
    try:
        if audio[:4] != b"RIFF" or int.from_bytes(audio[4:8], "little") + 8 != len(audio):
            raise ValueError
        with wave.open(io.BytesIO(audio), "rb") as wav:
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getcomptype()) != (1, 2, 16000, "NONE"):
                raise ValueError
            frames = wav.getnframes()
            if frames > 16000 * MAX_SECONDS:
                raise AudioError("audio_too_long", "每段录音最多 60 秒。", 413)
            if frames == 0 or len(wav.readframes(frames)) != frames * 2:
                raise ValueError
            return frames / 16000
    except (wave.Error, EOFError, ValueError, OverflowError):
        raise AudioError("audio_format_invalid", "请上传完整的 16 kHz、16 bit、单声道 PCM WAV 音频。", 422) from None


@dataclass
class Entry:
    digest: str | None = None
    task: asyncio.Task | None = None
    finished_at: float | None = None
    cancelled: bool = False
    expiry: asyncio.TimerHandle | None = None


@dataclass(frozen=True)
class Failure:
    code: str
    message: str
    status_code: int = 502


class AudioService:
    """Process-local bounded transcription cache, independent of chat history."""

    def __init__(self, settings: Settings, recognizer: Recognizer | None = None):
        self.settings = settings
        self.recognizer = recognizer or TencentRecognizer(settings)
        self.entries: dict[UUID, Entry] = {}
        self.uploads = 0

    def prune(self):
        now = time.monotonic()
        expired = [
            key for key, entry in self.entries.items()
            if entry.finished_at is not None and now - entry.finished_at >= self.settings.asr_cache_seconds
        ]
        for key in expired:
            entry = self.entries.pop(key)
            if entry.expiry is not None:
                entry.expiry.cancel()

    def schedule_expiry(self, request_id: UUID, entry: Entry):
        def expire():
            if self.entries.get(request_id) is entry:
                self.entries.pop(request_id)

        entry.expiry = asyncio.get_running_loop().call_later(self.settings.asr_cache_seconds, expire)

    async def transcribe(self, audio: bytes, request_id: UUID, mode: str) -> Transcription:
        duration = validate_wav(audio)
        self.prune()
        digest = hashlib.sha256(mode.encode() + b"\0" + audio).hexdigest()
        entry = self.entries.get(request_id)
        if entry is not None:
            if entry.cancelled:
                raise AudioError("audio_cancelled", "这次转写已取消，请用新请求编号重新录音。", 409)
            if entry.digest != digest:
                raise AudioError("audio_request_conflict", "请求编号已用于其他音频或识别模式。", 409)
        else:
            self.require_space()
            active = sum(e.task is not None and not e.task.done() for e in self.entries.values())
            if active >= self.settings.asr_max_concurrent:
                raise AudioError("audio_busy", "语音识别正在处理其他请求，请稍后重试。", 503)
            entry = Entry(digest=digest)
            entry.task = asyncio.create_task(self._recognize(audio, request_id, mode, duration))
            self.entries[request_id] = entry
            def finish(task):
                entry.finished_at = time.monotonic()
                if entry.cancelled:
                    entry.task = None
                self.schedule_expiry(request_id, entry)

            entry.task.add_done_callback(finish)
        try:
            outcome = await asyncio.shield(entry.task)
        except asyncio.CancelledError:
            if entry.cancelled:
                raise AudioError("audio_cancelled", "这次转写已取消。", 409) from None
            raise
        if entry.cancelled:
            raise AudioError("audio_cancelled", "这次转写已取消。", 409)
        if isinstance(outcome, Failure):
            raise AudioError(outcome.code, outcome.message, outcome.status_code)
        return outcome.model_copy(deep=True)

    async def _recognize(self, audio: bytes, request_id: UUID, mode: str, duration: float):
        started = time.monotonic()
        logger.info("Audio started | request_id=%s | mode=%s | bytes=%s", request_id, mode, len(audio))
        try:
            if mode == "demo":
                text = "这是一条模拟转写文字，请编辑后再发送给 Agent。"
            else:
                if not self.settings.asr_configured:
                    raise AudioError("audio_not_configured", "请在本地配置腾讯云语音识别凭据并重启服务。", 503)
                text = await asyncio.wait_for(self.recognizer.transcribe(audio), self.settings.asr_timeout_seconds)
            if not isinstance(text, str) or not text.strip() or len(text) > 8000:
                raise AudioError("audio_invalid_response", "语音识别结果为空或格式异常。")
            result = Transcription(
                request_id=request_id, text=text.strip(), duration_seconds=round(duration, 3),
                provider="demo" if mode == "demo" else "tencent", simulated=mode == "demo",
            )
            logger.info("Audio completed | request_id=%s | elapsed_ms=%d", request_id, (time.monotonic()-started)*1000)
            return result
        except asyncio.CancelledError:
            return Failure("audio_cancelled", "这次转写已取消。", 409)
        except TimeoutError:
            logger.info("Audio failed | request_id=%s | code=audio_timeout", request_id)
            return Failure("audio_timeout", "语音识别超时，请使用新请求编号重试。", 504)
        except AudioError as error:
            logger.info("Audio failed | request_id=%s | code=%s", request_id, error.code)
            # Cache plain error data, not tracebacks retaining audio or request frames.
            return Failure(error.code, error.message, error.status_code)
        except Exception:
            logger.warning("Audio failed | request_id=%s | code=audio_internal_error", request_id)
            return Failure("audio_internal_error", "本次转写失败，请使用新请求编号重试。")

    def require_space(self):
        if len(self.entries) >= self.settings.asr_max_requests:
            raise AudioError("audio_cache_full", "短期转写记录已达上限，请稍后重试。", 503)

    def cancel(self, request_id: UUID):
        self.prune()
        entry = self.entries.get(request_id)
        if entry is None:
            self.require_space()
            entry = Entry(finished_at=time.monotonic())
            self.entries[request_id] = entry
            self.schedule_expiry(request_id, entry)
        entry.cancelled = True
        if entry.task is not None and not entry.task.done():
            entry.task.cancel()
        elif entry.task is not None:
            entry.task = None

    async def close(self):
        tasks = [e.task for e in self.entries.values() if e.task is not None and not e.task.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for entry in self.entries.values():
            if entry.expiry is not None:
                entry.expiry.cancel()
        self.entries.clear()

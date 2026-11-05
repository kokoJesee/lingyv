import asyncio
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from starlette.datastructures import UploadFile
from starlette.requests import ClientDisconnect
from starlette.formparsers import MultiPartParser

from app.audio.provider import AudioError
from app.audio.service import AudioService, MAX_AUDIO_BYTES, MAX_SECONDS, MAX_UPLOAD_BYTES, Transcription

router = APIRouter(tags=["voice"])
STATIC = Path(__file__).with_name("static")


class MemoryAudioParser(MultiPartParser):
    # The whole body is bounded below this threshold before parsing, so files stay in memory.
    spool_max_size = MAX_UPLOAD_BYTES + 1


def get_audio_service(request: Request) -> AudioService:
    return request.app.state.audio_service


@router.get("/voice", include_in_schema=False)
async def voice_page():
    return FileResponse(STATIC / "voice.html", headers={
        "Cache-Control": "no-store",
        "Permissions-Policy": "microphone=(self)",
        "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; worker-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    })


@router.get("/voice.js", include_in_schema=False)
async def voice_script():
    return FileResponse(STATIC / "voice.js", media_type="application/javascript", headers={"Cache-Control": "no-store"})


@router.get("/voice-worklet.js", include_in_schema=False)
async def voice_worklet():
    return FileResponse(STATIC / "voice-worklet.js", media_type="application/javascript")


def require_same_origin(request: Request):
    origin = request.headers.get("origin")
    if request.headers.get("sec-fetch-site") == "cross-site" or (
        origin is not None and origin.rstrip("/") != str(request.base_url).rstrip("/")
    ):
        raise AudioError("audio_origin_rejected", "请从当前服务的语音页面提交录音。", 403)


@router.get("/audio/status")
async def audio_status(service: Annotated[AudioService, Depends(get_audio_service)]):
    return {
        "provider": service.settings.asr_provider,
        "configured": service.settings.asr_configured,
        "max_seconds": MAX_SECONDS, "max_audio_bytes": MAX_AUDIO_BYTES,
        "format": "PCM WAV, mono, 16000 Hz, 16 bit",
        "demo_available": True,
    }


async def read_upload(request: Request):
    if not request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
        raise AudioError("audio_upload_invalid", "请使用 multipart/form-data 上传 file 字段。", 415)
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            length = int(content_length)
        except ValueError:
            raise AudioError("audio_upload_invalid", "上传长度格式错误。", 400) from None
        if length < 0 or length > MAX_UPLOAD_BYTES:
            raise AudioError("audio_too_large", "上传内容超过大小限制。", 413)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_UPLOAD_BYTES:
            raise AudioError("audio_too_large", "上传内容超过大小限制。", 413)

    async def receive():
        return {"type": "http.request", "body": bytes(body), "more_body": False}

    buffered = Request(request.scope, receive)
    try:
        form = await MemoryAudioParser(
            buffered.headers, buffered.stream(), max_files=1, max_fields=2, max_part_size=1024,
        ).parse()
        try:
            if any(key not in {"file", "request_id", "mode"} for key in form) or len(form.multi_items()) != len(form):
                raise AudioError("audio_upload_invalid", "上传包含未知或重复字段。", 422)
            file = form.get("file")
            if not isinstance(file, UploadFile):
                raise AudioError("audio_upload_invalid", "缺少音频 file 字段。", 422)
            mode = form.get("mode", "configured")
            if mode not in ("configured", "demo"):
                raise AudioError("audio_upload_invalid", "mode 只允许 configured 或 demo。", 422)
            try:
                request_id = UUID(str(form["request_id"])) if "request_id" in form else uuid4()
            except (ValueError, TypeError):
                raise AudioError("audio_upload_invalid", "request_id 必须是 UUID。", 422) from None
            audio = await file.read(MAX_AUDIO_BYTES + 1)
            return audio, request_id, mode
        finally:
            await form.close()
    except AudioError:
        raise
    except Exception:
        raise AudioError("audio_upload_invalid", "音频上传格式错误。", 400) from None


@router.post(
    "/audio/transcriptions", response_model=Transcription,
    description="multipart/form-data: file (WAV), request_id (可选 UUID), mode (configured/demo)。转写后请编辑确认，再调用聊天入口。",
    openapi_extra={"requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
        "type": "object", "required": ["file"], "properties": {
            "file": {"type": "string", "format": "binary"},
            "request_id": {"type": "string", "format": "uuid"},
            "mode": {"type": "string", "enum": ["configured", "demo"], "default": "configured"},
        },
    }}}}},
)
async def transcribe(request: Request, service: Annotated[AudioService, Depends(get_audio_service)]):
    require_same_origin(request)
    if service.uploads >= 4:
        raise AudioError("audio_busy", "当前录音上传较多，请稍后重试。", 503)
    service.uploads += 1
    try:
        try:
            audio, request_id, mode = await asyncio.wait_for(read_upload(request), timeout=15)
        except TimeoutError:
            raise AudioError("audio_upload_timeout", "上传音频超时，请重新录音。", 408) from None
        except ClientDisconnect:
            raise AudioError("audio_upload_cancelled", "音频上传已中断。", 400) from None
        return await service.transcribe(audio, request_id, mode)
    finally:
        service.uploads -= 1


@router.delete("/audio/transcriptions/{request_id}", status_code=204)
async def cancel_transcription(request: Request, request_id: UUID, service: Annotated[AudioService, Depends(get_audio_service)]):
    require_same_origin(request)
    service.cancel(request_id)

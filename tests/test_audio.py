import asyncio
import base64
import io
import json
import time
import wave
from uuid import uuid4

import httpx
import pytest

from app.audio.provider import AudioError, TencentRecognizer, signed_headers
from app.audio.service import AudioService, MAX_UPLOAD_BYTES, validate_wav
from app.config.settings import Settings


def wav_bytes(seconds=0.1, rate=16000, channels=1, width=2):
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(rate)
        wav.writeframes(b"\0" * int(seconds * rate) * width * channels)
    return output.getvalue()


def configured(**kwargs):
    return Settings(
        _env_file=None, asr_provider="tencent", tencent_secret_id="test-id",
        tencent_secret_key="test-secret", **kwargs,
    )


class StubRecognizer:
    def __init__(self):
        self.calls = 0

    async def transcribe(self, audio):
        self.calls += 1
        return "识别到的文字"


def install_service(client, settings=None, recognizer=None):
    service = AudioService(settings or configured(), recognizer or StubRecognizer())
    client.app.state.audio_service = service
    return service


def upload(client, request_id=None, mode="configured", audio=None):
    return client.post(
        "/audio/transcriptions",
        files={"file": ("voice.wav", wav_bytes() if audio is None else audio, "audio/wav")},
        data={"request_id": str(request_id or uuid4()), "mode": mode},
    )


def test_demo_does_not_call_provider_or_create_chat(client):
    service = install_service(client, Settings(_env_file=None))
    result = upload(client, mode="demo")
    assert result.status_code == 200
    assert result.json()["simulated"] is True
    assert result.json()["provider"] == "demo"
    assert service.recognizer.calls == 0
    assert client.get("/sessions").json() == []


def test_edit_then_explicitly_send_uses_existing_chat(client, recording_model):
    install_service(client)
    result = upload(client)
    assert result.status_code == 200
    assert not recording_model.calls
    session = client.post("/sessions").json()["session_id"]
    assert client.get(f"/sessions/{session}").json()["messages"] == []
    sent = client.post(f"/sessions/{session}/messages", json={"content": "用户校对后的文字"})
    assert sent.status_code == 200
    messages = client.get(f"/sessions/{session}").json()["messages"]
    assert messages[0]["content"] == "用户校对后的文字"


def test_transcription_replay_and_conflict(client):
    service = install_service(client)
    request_id = uuid4()
    first = upload(client, request_id)
    assert upload(client, request_id).json() == first.json()
    assert service.recognizer.calls == 1
    assert upload(client, request_id, audio=wav_bytes(0.2)).status_code == 409
    assert upload(client, request_id, mode="demo").status_code == 409


def test_cancel_before_upload_and_after_completion(client):
    install_service(client)
    request_id = uuid4()
    assert client.delete(f"/audio/transcriptions/{request_id}").status_code == 204
    assert upload(client, request_id).json()["detail"]["code"] == "audio_cancelled"
    other = uuid4()
    assert upload(client, other).status_code == 200
    assert client.delete(f"/audio/transcriptions/{other}").status_code == 204
    assert upload(client, other).status_code == 409


def test_missing_credentials_never_falls_back_to_demo(client):
    service = install_service(client, Settings(_env_file=None))
    result = upload(client)
    assert result.status_code == 503
    assert result.json()["detail"]["code"] == "audio_not_configured"
    assert service.recognizer.calls == 0


@pytest.mark.parametrize("audio", [
    b"", b"not a wav", wav_bytes(rate=48000), wav_bytes(channels=2),
    wav_bytes(width=1), wav_bytes(seconds=0), wav_bytes()[:-2],
])
def test_invalid_audio_rejected_before_provider(client, audio):
    service = install_service(client)
    result = upload(client, audio=audio)
    assert result.status_code == 422
    assert service.recognizer.calls == 0


def test_limits_and_chunked_upload(client):
    service = install_service(client)
    assert upload(client, audio=wav_bytes(60.1)).status_code == 413
    assert upload(client, audio=b"x" * (MAX_UPLOAD_BYTES + 1)).status_code == 413
    result = client.post(
        "/audio/transcriptions", content=iter([b"x" * MAX_UPLOAD_BYTES, b"x"]),
        headers={"content-type": "multipart/form-data; boundary=anything"},
    )
    assert result.status_code == 413
    assert service.recognizer.calls == 0
    assert validate_wav(wav_bytes(60)) == 60


@pytest.mark.parametrize("fields", [
    {"request_id": "invalid"}, {"mode": "other"}, {"extra": "field"},
])
def test_invalid_form_fields(client, fields):
    install_service(client)
    assert client.post("/audio/transcriptions", files={"file": ("a.wav", wav_bytes())}, data=fields).status_code == 422


def test_missing_and_malformed_upload(client):
    install_service(client)
    assert client.post("/audio/transcriptions", json={}).status_code == 415
    assert client.post("/audio/transcriptions", content=b"", headers={"content-type": "multipart/form-data"}).status_code == 400
    assert client.post("/audio/transcriptions", files=[("file", ("a.wav", wav_bytes())), ("file", ("b.wav", wav_bytes()))]).status_code == 400


def test_origin_and_configuration_do_not_expose_credentials(client):
    install_service(client)
    status = client.get("/audio/status")
    assert status.json()["configured"] is True
    assert "test-secret" not in status.text and "test-id" not in status.text
    response = client.post(
        "/audio/transcriptions", headers={"Origin": "https://unrelated.example"},
        files={"file": ("a.wav", wav_bytes())},
    )
    assert response.status_code == 403
    assert client.delete(f"/audio/transcriptions/{uuid4()}", headers={"sec-fetch-site": "cross-site"}).status_code == 403


def test_voice_assets_and_openapi(client):
    page = client.get("/voice")
    assert page.status_code == 200
    assert "预设文字" in page.text
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert client.get("/voice.js").status_code == 200
    assert client.get("/voice-worklet.js").status_code == 200
    schema = client.get("/openapi.json").json()
    body = schema["paths"]["/audio/transcriptions"]["post"]["requestBody"]
    assert body["content"]["multipart/form-data"]["schema"]["required"] == ["file"]


def test_concurrent_replay_capacity_cancel_and_late_result():
    async def run():
        entered = asyncio.Event()
        release = asyncio.Event()

        class Slow:
            calls = 0

            async def transcribe(self, audio):
                self.calls += 1
                entered.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    return "迟到的文字"
                return "正常文字"

        recognizer = Slow()
        service = AudioService(configured(asr_max_concurrent=1), recognizer)
        request_id = uuid4()
        first = asyncio.create_task(service.transcribe(wav_bytes(), request_id, "configured"))
        await entered.wait()
        second = asyncio.create_task(service.transcribe(wav_bytes(), request_id, "configured"))
        await asyncio.sleep(0)
        with pytest.raises(AudioError) as busy:
            await service.transcribe(wav_bytes(), uuid4(), "configured")
        assert busy.value.code == "audio_busy"
        service.cancel(request_id)
        for task in (first, second):
            with pytest.raises(AudioError) as cancelled:
                await task
            assert cancelled.value.code == "audio_cancelled"
        assert recognizer.calls == 1
        assert service.entries[request_id].task is None
        await service.close()

    asyncio.run(run())


def test_error_replay_timeout_cache_expiry_and_shutdown():
    async def run():
        class Slow:
            calls = 0

            async def transcribe(self, audio):
                self.calls += 1
                await asyncio.sleep(10)

        recognizer = Slow()
        service = AudioService(configured(asr_timeout_seconds=0.01, asr_max_requests=1), recognizer)
        request_id = uuid4()
        for _ in range(2):
            with pytest.raises(AudioError) as timeout:
                await service.transcribe(wav_bytes(), request_id, "configured")
            assert timeout.value.code == "audio_timeout"
        assert recognizer.calls == 1
        cached_error = service.entries[request_id].task.result()
        assert not isinstance(cached_error, BaseException)
        assert set(vars(cached_error)) == {"code", "message", "status_code"}
        with pytest.raises(AudioError) as full:
            await service.transcribe(wav_bytes(), uuid4(), "demo")
        assert full.value.code == "audio_cache_full"
        service.entries[request_id].finished_at = time.monotonic() - 601
        result = await service.transcribe(wav_bytes(), uuid4(), "demo")
        assert result.simulated and request_id not in service.entries
        await service.close()
        assert not service.entries

    asyncio.run(run())


def test_cache_expires_while_idle_and_large_audio_stays_in_memory(client, monkeypatch):
    from starlette.datastructures import UploadFile

    installed = install_service(client)
    original = UploadFile.read
    observed = []

    async def read(file, size=-1):
        observed.append(file._in_memory)
        return await original(file, size)

    monkeypatch.setattr(UploadFile, "read", read)
    assert upload(client, audio=wav_bytes(40)).status_code == 200
    assert observed == [True]
    assert installed.recognizer.calls == 1

    async def run():
        service = AudioService(configured(asr_cache_seconds=1), StubRecognizer())
        await service.transcribe(wav_bytes(), uuid4(), "demo")
        assert service.entries
        await asyncio.sleep(1.1)
        assert not service.entries
        await service.close()

    asyncio.run(run())


def test_provider_payload_signature_and_text():
    audio = wav_bytes()
    settings = configured()

    async def handler(request):
        assert request.url == "https://asr.tencentcloudapi.com/"
        assert request.headers["X-TC-Action"] == "SentenceRecognition"
        assert request.headers["X-TC-Version"] == "2019-06-14"
        data = json.loads(request.content)
        assert base64.b64decode(data["Data"]) == audio
        assert data["DataLen"] == len(audio)
        assert data["FilterDirty"] == data["FilterModal"] == 0
        headers = signed_headers(request.content, settings, int(request.headers["X-TC-Timestamp"]))
        assert request.headers["Authorization"] == headers["Authorization"]
        return httpx.Response(200, json={"Response": {"Result": "  已识别  ", "RequestId": "provider-id"}})

    assert asyncio.run(TencentRecognizer(settings, httpx.MockTransport(handler)).transcribe(audio)) == "已识别"


@pytest.mark.parametrize("response,expected", [
    ({"Response": {"Error": {"Code": "AuthFailure.SignatureFailure", "Message": "sensitive upstream text"}}}, "audio_authentication_failed"),
    ({"Response": {"Error": {"Code": "LimitExceeded", "Message": "sensitive upstream text"}}}, "audio_provider_unavailable"),
    ({"Response": {"Result": ""}}, "audio_no_speech"),
    ({"Response": {"Result": None}}, "audio_invalid_response"),
    ({"Response": {}}, "audio_invalid_response"),
    ({"Response": None}, "audio_invalid_response"),
])
def test_provider_errors_are_sanitized(response, expected):
    async def handler(request):
        return httpx.Response(200, json=response)

    with pytest.raises(AudioError) as error:
        asyncio.run(TencentRecognizer(configured(), httpx.MockTransport(handler)).transcribe(wav_bytes()))
    assert error.value.code == expected
    assert "sensitive" not in str(error.value)


def test_no_audio_or_transcript_in_logs(client, caplog):
    install_service(client)
    with caplog.at_level("INFO", logger="app.audio.service"):
        assert upload(client).status_code == 200
    assert "Audio completed" in caplog.text
    assert "识别到的文字" not in caplog.text
    assert "test-secret" not in caplog.text

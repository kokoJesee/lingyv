"""HTTP surface for long-term memory (L2 notes)."""
from __future__ import annotations

import json

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, PlainTextResponse

from app.config.settings import get_settings
from app.schemas.memory import (
    Memory,
    MemoryConfirmRequest,
    MemoryCreateRequest,
    MemoryUpdateRequest,
)
from app.services.memory_service import MemoryService

router = APIRouter(tags=["memory"])

_service: MemoryService | None = None


def get_memory_service() -> MemoryService:
    """Lazily build the process-wide service; tests may replace ``_service``."""
    global _service
    if _service is None:
        _service = MemoryService(settings=get_settings())
    return _service


def reset_memory_service(service: MemoryService | None = None) -> None:
    global _service
    _service = service


@router.get("/memories", response_model=list[Memory])
def list_memories(status: str | None = Query(default=None), kind: str | None = Query(default=None)):
    items = get_memory_service().list(status=status)
    if kind:
        items = [item for item in items if item.kind == kind]
    return items


@router.post("/memories", response_model=Memory, status_code=201)
def create_memory(request: MemoryCreateRequest):
    return get_memory_service().create(request)


@router.patch("/memories/{memory_id}", response_model=Memory)
def update_memory(memory_id: str, request: MemoryUpdateRequest):
    return get_memory_service().update(memory_id, request)


@router.post("/memories/{memory_id}/confirm", response_model=Memory)
def confirm_memory(memory_id: str, request: MemoryConfirmRequest):
    return get_memory_service().confirm(memory_id, request)


@router.delete("/memories/{memory_id}", status_code=204)
def delete_memory(memory_id: str):
    get_memory_service().delete(memory_id)
    return None


@router.get("/memories/export")
def export_memories(fmt: str = Query(default="markdown")):
    if fmt not in {"markdown", "json"}:
        return JSONResponse(status_code=422, content={"detail": "fmt must be markdown or json"})
    payload = get_memory_service().export(fmt)
    if fmt == "json":
        return JSONResponse(content=json.loads(payload))
    return PlainTextResponse(content=payload)


@router.post("/memories/propose", status_code=501)
def propose_memory():
    """Reserved: candidates will come from the canvas confirmation screen."""
    return JSONResponse(
        status_code=501,
        content={"detail": {"code": "memory_propose_not_implemented",
                            "message": "从画布提议记忆要等画布对象接入后开放。"}},
    )

"""Canvas service: draft -> confirm -> record, identity, versions and guards.

The service knows nothing about sessions, orchestrator or psychology: a session
id is only provenance. Other modules consume it through events or the read API.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from app.agent.errors import RuntimeFailure
from app.config.settings import get_settings
from app.events import publish
from app.owner import DEFAULT_OWNER
from app.schemas.canvas import (
    CANVAS_LANES,
    Canvas,
    CanvasEntry,
    CanvasOp,
    CanvasReference,
    CanvasSnapshot,
    ConfirmCanvasRequest,
    ConfirmResult,
    CreateCanvasRequest,
    DayView,
    PatchCanvasRequest,
    Record,
    RecordPreview,
    Sensitivity,
)
from app.services.canvas_store import CanvasStore
from app.services.content_cipher import ContentCipher
from app.services.sensitivity import scan as scan_sensitivity


class CanvasService:
    def __init__(self, store: CanvasStore | None = None, settings=None,
                 cipher: ContentCipher | None = None, clock=None):
        self.settings = settings or get_settings()
        self._cipher = cipher
        self._store = store
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @property
    def cipher(self) -> ContentCipher:
        """Built lazily: constructing the service must not touch disk."""
        if self._cipher is None:
            self._cipher = ContentCipher.from_settings(self.settings)
        return self._cipher

    @property
    def store(self) -> CanvasStore:
        if self._store is None:
            self._store = CanvasStore(self.settings.canvas_db_path, self.cipher)
        return self._store

    # ---- helpers ---------------------------------------------------------
    def _now(self) -> datetime:
        return self._clock()

    def today(self) -> str:
        zone = ZoneInfo(self.settings.canvas_timezone)
        return datetime.now(zone).date().isoformat()

    def _require_enabled(self) -> None:
        if not self.settings.canvas_enabled:
            raise RuntimeFailure("canvas_disabled", "画布当前未启用。", 409)

    def _require(self, canvas_id: str, owner_id: str) -> Canvas:
        canvas = self.store.get_canvas(canvas_id, owner_id)
        if canvas is None or canvas.status == "deleted":
            raise RuntimeFailure("canvas_not_found", "画布不存在或已删除。", 404)
        return canvas

    def _require_record(self, record_id: str, owner_id: str) -> Record:
        record = self.store.get_record(record_id, owner_id)
        if record is None or record.status == "deleted":
            raise RuntimeFailure("record_not_found", "记录不存在或已删除。", 404)
        return record

    def _check_revision(self, canvas: Canvas, expected: int) -> None:
        if canvas.revision != expected:
            raise RuntimeFailure("canvas_revision_conflict", "画布已被修改，请刷新后重试。", 409)

    def _sensitive(self, canvas: Canvas) -> Sensitivity:
        texts = [entry.text for entries in canvas.lanes.values() for entry in entries]
        if canvas.note:
            texts.append(canvas.note)
        return scan_sensitivity(texts)

    def _validate_size(self, canvas: Canvas) -> None:
        for lane in CANVAS_LANES:
            if len(canvas.lanes.get(lane, [])) > self.settings.canvas_max_entries:
                raise RuntimeFailure(
                    "canvas_too_many_entries",
                    f"「{lane}」最多 {self.settings.canvas_max_entries} 条。", 422,
                )
        for entries in canvas.lanes.values():
            for entry in entries:
                if len(entry.text) > self.settings.canvas_max_text_chars:
                    raise RuntimeFailure("canvas_text_too_long", "单条内容过长。", 422)
        if canvas.total_chars() > self.settings.canvas_max_total_chars:
            raise RuntimeFailure("canvas_too_large", "整张画布内容过多，请挑重点。", 422)

    def _revision_ref(self, canvas: Canvas) -> CanvasReference:
        return CanvasReference(canvas_id=canvas.canvas_id, revision=canvas.revision)

    # ---- writes ----------------------------------------------------------
    def create(self, origin_session_id: str, request: CreateCanvasRequest,
               owner_id: str = DEFAULT_OWNER) -> Canvas:
        self._require_enabled()
        now = self._now()
        canvas = Canvas(
            owner_id=owner_id,
            origin_session_id=origin_session_id,
            day=request.day or self.today(),
            lanes={
                lane: [CanvasEntry(text=item.text, source=item.source, origin=item.origin,
                                   confirmed=item.source == "user")
                       for item in request.lanes.get(lane, [])]
                for lane in CANVAS_LANES
            },
            note=request.note,
            source=request.source,
            persona_id=request.persona_id,
            draft_profile=request.draft_profile,
            prompt_version=request.prompt_version,
            model=request.model,
            enc_key_id=self.cipher.key_id,
            created_at=now,
            updated_at=now,
        )
        canvas.sensitivity = self._sensitive(canvas)
        self._validate_size(canvas)
        self.store.insert_canvas(canvas)
        return canvas

    def patch(self, canvas_id: str, request: PatchCanvasRequest,
              owner_id: str = DEFAULT_OWNER) -> Canvas:
        self._require_enabled()
        canvas = self._require(canvas_id, owner_id)
        self._check_revision(canvas, request.expected_revision)
        for op in request.ops:
            self._apply_op(canvas, op)
        canvas.revision += 1
        canvas.updated_at = self._now()
        canvas.sensitivity = self._sensitive(canvas)
        # 编辑已确认画布会让确认失效；已确认版本仍可作为历史引用。
        if canvas.status == "confirmed":
            canvas.status = "draft"
        self._validate_size(canvas)
        self.store.save_canvas(canvas)
        return canvas

    @staticmethod
    def _apply_op(canvas: Canvas, op: CanvasOp) -> None:
        entries = canvas.lanes.setdefault(op.lane, [])
        if op.op == "add":
            if not op.text:
                raise RuntimeFailure("canvas_op_invalid", "新增条目缺少内容。", 422)
            entries.append(CanvasEntry(text=op.text, source=op.source))
            return
        if not op.entry_id:
            raise RuntimeFailure("canvas_op_invalid", "缺少 entry_id。", 422)
        index = next((i for i, entry in enumerate(entries) if entry.entry_id == op.entry_id), None)
        if index is None:
            raise RuntimeFailure("canvas_entry_not_found", "要修改的条目不存在。", 404)
        if op.op == "delete":
            entries.pop(index)
            return
        if not op.text:
            raise RuntimeFailure("canvas_op_invalid", "修改条目缺少内容。", 422)
        entries[index] = entries[index].model_copy(
            update={"text": op.text, "source": "user", "confirmed": True}
        )

    def confirm(self, canvas_id: str, request: ConfirmCanvasRequest,
                owner_id: str = DEFAULT_OWNER) -> ConfirmResult:
        self._require_enabled()
        if request.request_id:
            existing = self.store.get_record_by_request(request.request_id, owner_id)
            if existing is not None:
                return ConfirmResult(canvas=self._require(existing.canvas_id, owner_id), record=existing)
        canvas = self._require(canvas_id, owner_id)
        self._check_revision(canvas, request.expected_revision)
        # 同一版已确认过：重复提交（双击/重试）只返回原记录。
        if canvas.is_confirmed():
            existing = self.store.latest_record_for_canvas(canvas.canvas_id, owner_id)
            if existing is not None and existing.canvas_revision == canvas.revision:
                return ConfirmResult(canvas=canvas, record=existing)
        if canvas.is_empty():
            raise RuntimeFailure("canvas_empty", "画布还是空的，至少写一句再收下。", 422)
        now = self._now()
        previous = self.store.latest_record_for_canvas(canvas.canvas_id, owner_id)
        record = Record(
            owner_id=owner_id,
            canvas_id=canvas.canvas_id,
            canvas_revision=canvas.revision,
            version=(previous.version + 1) if previous else 1,
            day=request.observed_at or canvas.day,
            observed_at=request.observed_at or canvas.day,
            recorded_at=now,
            snapshot=CanvasSnapshot(
                canvas_id=canvas.canvas_id, revision=canvas.revision,
                day=canvas.day, lanes=canvas.lanes, note=canvas.note,
            ),
            note=canvas.note,
            summary=self._summary(canvas),
            sensitivity=canvas.sensitivity,
            enc_key_id=self.cipher.key_id,
            created_at=now,
        )
        canvas.status = "confirmed"
        canvas.confirmed_revision = canvas.revision
        canvas.confirmed_at = now
        canvas.updated_at = now
        self.store.save_canvas(canvas)
        self.store.insert_record(record, request_id=request.request_id)
        if previous is not None:
            self.store.mark_superseded(previous.record_id, record.record_id, owner_id)
            publish("record.superseded", {
                "record_id": previous.record_id, "new_record_id": record.record_id,
                "canvas_id": canvas.canvas_id, "owner_id": owner_id,
            })
        publish("canvas.confirmed", {
            "canvas_id": canvas.canvas_id, "revision": canvas.revision,
            "record_id": record.record_id, "session_id": canvas.origin_session_id,
            "owner_id": owner_id, "sensitive": canvas.sensitivity.level,
        })
        return ConfirmResult(canvas=canvas, record=record)

    @staticmethod
    def _summary(canvas: Canvas) -> str:
        for lane in ("interpretation", "facts", "emotion", "needs"):
            for entry in canvas.lanes.get(lane, []):
                text = entry.text.strip()
                if text:
                    return text[:60]
        return "（只有留白）"

    # ---- reads -----------------------------------------------------------
    def get(self, canvas_id: str, owner_id: str = DEFAULT_OWNER) -> Canvas:
        return self._require(canvas_id, owner_id)

    def list_by_session(self, session_id: str, owner_id: str = DEFAULT_OWNER,
                        limit: int = 50) -> list[Canvas]:
        return self.store.list_canvases(owner_id, origin_session_id=session_id, limit=limit)

    def latest_confirmed(self, session_id: str | None = None,
                         owner_id: str = DEFAULT_OWNER) -> Canvas | None:
        """Newest confirmed canvas for this session, else for the owner (cross-session)."""
        if session_id:
            for canvas in self.store.list_canvases(owner_id, origin_session_id=session_id, limit=50):
                if canvas.is_confirmed():
                    return canvas
        for canvas in self.store.list_canvases(owner_id, limit=50):
            if canvas.is_confirmed():
                return canvas
        return None

    def active_references(self, owner_id: str = DEFAULT_OWNER) -> set[tuple[str, int]]:
        """Confirmed (canvas_id, revision) pairs; used to prune stale assistant replies."""
        return {
            (canvas.canvas_id, canvas.revision)
            for canvas in self.store.list_canvases(owner_id, status="confirmed")
            if canvas.is_confirmed()
        }

    def get_record(self, record_id: str, owner_id: str = DEFAULT_OWNER) -> Record:
        return self._require_record(record_id, owner_id)

    def snapshot(self, record_id: str, owner_id: str = DEFAULT_OWNER) -> CanvasSnapshot:
        return self._require_record(record_id, owner_id).snapshot

    def delete_record(self, record_id: str, owner_id: str = DEFAULT_OWNER) -> Record:
        self._require_enabled()
        record = self._require_record(record_id, owner_id)
        record.status = "deleted"
        record.deleted_at = self._now()
        self.store.save_record(record)
        publish("record.deleted", {
            "record_id": record.record_id, "canvas_id": record.canvas_id,
            "canvas_revision": record.canvas_revision, "owner_id": owner_id,
        })
        return record

    def month_view(self, month: str, owner_id: str = DEFAULT_OWNER) -> list[DayView]:
        records = self.store.list_records(owner_id, month=month, include_superseded=False)
        grouped: dict[str, list[RecordPreview]] = {}
        for record in records:
            grouped.setdefault(record.day, []).append(self._preview(record))
        return [DayView(day=day, records=items) for day, items in sorted(grouped.items(), reverse=True)]

    def day_view(self, day: str, owner_id: str = DEFAULT_OWNER) -> DayView:
        records = self.store.list_records(owner_id, day=day, include_superseded=False)
        return DayView(day=day, records=[self._preview(record) for record in records])

    @staticmethod
    def _preview(record: Record) -> RecordPreview:
        lanes = record.snapshot.lanes
        title = (record.snapshot.note or "").strip()
        if not title:
            for lane in CANVAS_LANES:
                for entry in lanes.get(lane, []):
                    if entry.text.strip():
                        title = entry.text.strip()
                        break
                if title:
                    break
        return RecordPreview(
            record_id=record.record_id, day=record.day,
            title=title[:24] or "（空白的一天）",
            summary=(record.summary or record.note or "")[:60] or "（还没有写内容）",
            card_count=len(record.cards),
            sensitivity=record.sensitivity,
        )

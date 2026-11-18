"""SQLite storage for canvases and confirmed records.

Content columns (lanes / note / snapshot / cards / summary) are encrypted with a
per-row AAD; ids, dates and status stay plaintext so indexes keep working.
Schema upgrades go through the shared migration ladder.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from app.schemas.canvas import Canvas, CanvasEntry, Record, Sensitivity
from app.services.content_cipher import ContentCipher
from app.services.sqlite_migrations import migrate
from app.owner import DEFAULT_OWNER


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _v1(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS canvases (
            canvas_id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL DEFAULT 'local',
            schema_version TEXT NOT NULL,
            origin_session_id TEXT,
            day TEXT NOT NULL,
            status TEXT NOT NULL,
            revision INTEGER NOT NULL,
            lanes_json TEXT NOT NULL,
            note TEXT,
            source TEXT NOT NULL,
            persona_id TEXT,
            draft_profile TEXT,
            prompt_version TEXT,
            model TEXT,
            sensitivity_json TEXT,
            enc_key_id TEXT NOT NULL DEFAULT 'k1',
            merged_from_json TEXT NOT NULL DEFAULT '[]',
            export_version INTEGER NOT NULL DEFAULT 1,
            confirmed_revision INTEGER,
            confirmed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            deleted_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_canvas_owner_day ON canvases(owner_id, day);
        CREATE INDEX IF NOT EXISTS idx_canvas_owner_session ON canvases(owner_id, origin_session_id);
        CREATE INDEX IF NOT EXISTS idx_canvas_owner_status ON canvases(owner_id, status);

        CREATE TABLE IF NOT EXISTS records (
            record_id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL DEFAULT 'local',
            canvas_id TEXT NOT NULL,
            canvas_revision INTEGER NOT NULL,
            version INTEGER NOT NULL,
            day TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            status TEXT NOT NULL,
            snapshot_json TEXT NOT NULL,
            cards_json TEXT NOT NULL DEFAULT '[]',
            note TEXT,
            summary TEXT,
            tags_json TEXT NOT NULL DEFAULT '[]',
            sensitivity_json TEXT,
            superseded_by TEXT,
            export_version INTEGER NOT NULL DEFAULT 1,
            enc_key_id TEXT NOT NULL DEFAULT 'k1',
            request_id TEXT,
            created_at TEXT NOT NULL,
            deleted_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_record_owner_day ON records(owner_id, day);
        CREATE INDEX IF NOT EXISTS idx_record_canvas ON records(owner_id, canvas_id, version);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_record_request
            ON records(owner_id, request_id) WHERE request_id IS NOT NULL;
        """
    )


MIGRATIONS = {1: _v1}

CANVAS_CONTENT_COLUMNS = ("lanes_json", "note")
RECORD_CONTENT_COLUMNS = ("snapshot_json", "cards_json", "summary")


class CanvasStore:
    def __init__(self, path: Path | str, cipher: ContentCipher):
        self.path = Path(path)
        self.cipher = cipher

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection | None]:
        if not write and not self.path.exists():
            yield None
            return
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=2, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            if write:
                connection.execute("PRAGMA journal_mode=WAL")
                migrate(connection, MIGRATIONS)
            yield connection
        finally:
            connection.close()

    # ---- canvases --------------------------------------------------------
    def insert_canvas(self, canvas: Canvas) -> None:
        with self._connection(write=True) as connection:
            connection.execute(
                """INSERT INTO canvases (canvas_id, owner_id, schema_version, origin_session_id, day,
                    status, revision, lanes_json, note, source, persona_id, draft_profile, prompt_version,
                    model, sensitivity_json, enc_key_id, merged_from_json, export_version,
                    confirmed_revision, confirmed_at, created_at, updated_at, deleted_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    canvas.canvas_id, canvas.owner_id, canvas.schema_version,
                    canvas.origin_session_id, canvas.day, canvas.status, canvas.revision,
                    self._enc_lanes(canvas), self._enc_note(canvas), canvas.source,
                    canvas.persona_id, canvas.draft_profile, canvas.prompt_version, canvas.model,
                    canvas.sensitivity.model_dump_json(), canvas.enc_key_id,
                    json.dumps(canvas.merged_from), canvas.export_version,
                    canvas.confirmed_revision, _iso(canvas.confirmed_at),
                    _iso(canvas.created_at), _iso(canvas.updated_at), _iso(canvas.deleted_at),
                ),
            )

    def save_canvas(self, canvas: Canvas) -> None:
        with self._connection(write=True) as connection:
            connection.execute(
                """UPDATE canvases SET status=?, revision=?, lanes_json=?, note=?, source=?,
                    sensitivity_json=?, confirmed_revision=?, confirmed_at=?, updated_at=?, deleted_at=?
                   WHERE canvas_id=? AND owner_id=?""",
                (
                    canvas.status, canvas.revision, self._enc_lanes(canvas), self._enc_note(canvas),
                    canvas.source, canvas.sensitivity.model_dump_json(), canvas.confirmed_revision,
                    _iso(canvas.confirmed_at), _iso(canvas.updated_at), _iso(canvas.deleted_at),
                    canvas.canvas_id, canvas.owner_id,
                ),
            )

    def get_canvas(self, canvas_id: str, owner_id: str = DEFAULT_OWNER) -> Canvas | None:
        with self._connection() as connection:
            if connection is None:
                return None
            row = connection.execute(
                "SELECT * FROM canvases WHERE canvas_id=? AND owner_id=?", (canvas_id, owner_id)
            ).fetchone()
        return self._row_to_canvas(row) if row else None

    def list_canvases(self, owner_id: str = DEFAULT_OWNER, origin_session_id: str | None = None,
                      status: str | None = None, limit: int = 100) -> list[Canvas]:
        query = "SELECT * FROM canvases WHERE owner_id=?"
        params: list[object] = [owner_id]
        if origin_session_id is not None:
            query += " AND origin_session_id=?"
            params.append(origin_session_id)
        if status is not None:
            query += " AND status=?"
            params.append(status)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with self._connection() as connection:
            if connection is None:
                return []
            rows = connection.execute(query, params).fetchall()
        return [self._row_to_canvas(row) for row in rows]

    # ---- records ---------------------------------------------------------
    def insert_record(self, record: Record, request_id: str | None = None) -> None:
        with self._connection(write=True) as connection:
            connection.execute(
                """INSERT INTO records (record_id, owner_id, canvas_id, canvas_revision, version, day,
                    observed_at, recorded_at, status, snapshot_json, cards_json, note, summary, tags_json,
                    sensitivity_json, superseded_by, export_version, enc_key_id, request_id,
                    created_at, deleted_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    record.record_id, record.owner_id, record.canvas_id, record.canvas_revision,
                    record.version, record.day, record.observed_at, _iso(record.recorded_at),
                    record.status, self._enc_snapshot(record), self._enc_cards(record),
                    self._enc_record_note(record), self._enc_summary(record),
                    json.dumps(record.tags), record.sensitivity.model_dump_json(),
                    record.superseded_by, record.export_version, record.enc_key_id,
                    request_id, _iso(record.created_at), _iso(record.deleted_at),
                ),
            )

    def save_record(self, record: Record, request_id: str | None = None) -> None:
        with self._connection(write=True) as connection:
            connection.execute(
                """UPDATE records SET status=?, superseded_by=?, deleted_at=?, snapshot_json=?,
                    cards_json=?, note=?, summary=? WHERE record_id=? AND owner_id=?""",
                (
                    record.status, record.superseded_by, _iso(record.deleted_at),
                    self._enc_snapshot(record), self._enc_cards(record),
                    self._enc_record_note(record), self._enc_summary(record),
                    record.record_id, record.owner_id,
                ),
            )

    def get_record(self, record_id: str, owner_id: str = DEFAULT_OWNER) -> Record | None:
        with self._connection() as connection:
            if connection is None:
                return None
            row = connection.execute(
                "SELECT * FROM records WHERE record_id=? AND owner_id=?", (record_id, owner_id)
            ).fetchone()
        return self._row_to_record(row) if row else None

    def get_record_by_request(self, request_id: str, owner_id: str = DEFAULT_OWNER) -> Record | None:
        with self._connection() as connection:
            if connection is None:
                return None
            row = connection.execute(
                "SELECT * FROM records WHERE request_id=? AND owner_id=?", (request_id, owner_id)
            ).fetchone()
        return self._row_to_record(row) if row else None

    def list_records(self, owner_id: str = DEFAULT_OWNER, day: str | None = None,
                     month: str | None = None, include_superseded: bool = False,
                     limit: int = 500) -> list[Record]:
        query = "SELECT * FROM records WHERE owner_id=? AND status<>'deleted'"
        params: list[object] = [owner_id]
        if day is not None:
            query += " AND day=?"
            params.append(day)
        if month is not None:
            query += " AND day LIKE ?"
            params.append(f"{month}-%")
        if not include_superseded:
            query += " AND status='saved'"
        query += " ORDER BY day DESC, version DESC LIMIT ?"
        params.append(limit)
        with self._connection() as connection:
            if connection is None:
                return []
            rows = connection.execute(query, params).fetchall()
        return [self._row_to_record(row) for row in rows]

    def latest_record_for_canvas(self, canvas_id: str, owner_id: str = DEFAULT_OWNER) -> Record | None:
        with self._connection() as connection:
            if connection is None:
                return None
            row = connection.execute(
                "SELECT * FROM records WHERE canvas_id=? AND owner_id=? AND status='saved' "
                "ORDER BY version DESC LIMIT 1",
                (canvas_id, owner_id),
            ).fetchone()
        return self._row_to_record(row) if row else None

    def mark_superseded(self, record_id: str, superseded_by: str, owner_id: str = DEFAULT_OWNER) -> None:
        with self._connection(write=True) as connection:
            connection.execute(
                "UPDATE records SET status='superseded', superseded_by=? WHERE record_id=? AND owner_id=?",
                (superseded_by, record_id, owner_id),
            )

    # ---- content helpers -------------------------------------------------
    def _canvas_aad(self, canvas_id: str) -> str:
        return f"canvas:{canvas_id}"

    def _record_aad(self, record_id: str) -> str:
        return f"record:{record_id}"

    def _enc_lanes(self, canvas: Canvas) -> str:
        lanes = {lane: [entry.model_dump(mode="json") for entry in entries]
                 for lane, entries in canvas.lanes.items()}
        return self.cipher.encrypt_json(lanes, self._canvas_aad(canvas.canvas_id))

    def _enc_note(self, canvas: Canvas) -> str | None:
        if canvas.note is None:
            return None
        return self.cipher.encrypt(canvas.note, self._canvas_aad(canvas.canvas_id))

    def _enc_snapshot(self, record: Record) -> str:
        return self.cipher.encrypt_json(
            record.snapshot.model_dump(mode="json"), self._record_aad(record.record_id)
        )

    def _enc_cards(self, record: Record) -> str:
        return self.cipher.encrypt_json(record.cards, self._record_aad(record.record_id))

    def _enc_record_note(self, record: Record) -> str | None:
        if record.note is None:
            return None
        return self.cipher.encrypt(record.note, self._record_aad(record.record_id))

    def _enc_summary(self, record: Record) -> str | None:
        if record.summary is None:
            return None
        return self.cipher.encrypt(record.summary, self._record_aad(record.record_id))

    def _row_to_canvas(self, row: sqlite3.Row) -> Canvas:
        aad = self._canvas_aad(row["canvas_id"])
        lanes = {
            lane: [CanvasEntry.model_validate(item) for item in entries]
            for lane, entries in self.cipher.decrypt_json(row["lanes_json"], aad).items()
        }
        note = row["note"]
        return Canvas(
            canvas_id=row["canvas_id"],
            owner_id=row["owner_id"],
            schema_version=row["schema_version"],
            origin_session_id=row["origin_session_id"],
            day=row["day"],
            status=row["status"],
            revision=row["revision"],
            lanes=lanes,
            note=self.cipher.decrypt(note, aad) if note else None,
            source=row["source"],
            persona_id=row["persona_id"],
            draft_profile=row["draft_profile"],
            prompt_version=row["prompt_version"],
            model=row["model"],
            sensitivity=Sensitivity.model_validate_json(row["sensitivity_json"] or "{}"),
            enc_key_id=row["enc_key_id"],
            merged_from=json.loads(row["merged_from_json"] or "[]"),
            export_version=row["export_version"],
            confirmed_revision=row["confirmed_revision"],
            confirmed_at=_parse(row["confirmed_at"]),
            created_at=_parse(row["created_at"]),
            updated_at=_parse(row["updated_at"]),
            deleted_at=_parse(row["deleted_at"]),
        )

    def _row_to_record(self, row: sqlite3.Row) -> Record:
        aad = self._record_aad(row["record_id"])
        snapshot = self.cipher.decrypt_json(row["snapshot_json"], aad)
        cards = self.cipher.decrypt_json(row["cards_json"], aad)
        summary = row["summary"]
        return Record(
            record_id=row["record_id"],
            owner_id=row["owner_id"],
            canvas_id=row["canvas_id"],
            canvas_revision=row["canvas_revision"],
            version=row["version"],
            day=row["day"],
            observed_at=row["observed_at"],
            recorded_at=_parse(row["recorded_at"]),
            status=row["status"],
            snapshot=snapshot,
            cards=cards,
            note=self.cipher.decrypt(row["note"], aad) if row["note"] else None,
            summary=self.cipher.decrypt(summary, aad) if summary else None,
            tags=json.loads(row["tags_json"] or "[]"),
            sensitivity=Sensitivity.model_validate_json(row["sensitivity_json"] or "{}"),
            superseded_by=row["superseded_by"],
            export_version=row["export_version"],
            enc_key_id=row["enc_key_id"],
            created_at=_parse(row["created_at"]),
            deleted_at=_parse(row["deleted_at"]),
        )

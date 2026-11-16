"""SQLite storage for long-term memory items.

One row per memory. Timestamps are stored as ISO-8601 strings so they survive
round-trips without a custom adapter. Deletion clears the text and keeps a
tombstone (status/deleted_at) used by dependency checks.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from app.schemas.memory import Memory

SCHEMA_VERSION = 1

_CREATE = """
CREATE TABLE IF NOT EXISTS memory_items (
    memory_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL DEFAULT 'local',
    kind TEXT NOT NULL,
    summary TEXT NOT NULL,
    text TEXT NOT NULL,
    importance INTEGER NOT NULL,
    source_json TEXT NOT NULL,
    status TEXT NOT NULL,
    revision INTEGER NOT NULL,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    review_at TEXT,
    evidence_count INTEGER NOT NULL DEFAULT 0,
    last_used_at TEXT,
    use_count INTEGER NOT NULL DEFAULT 0,
    derived_from_record_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_memory_pick ON memory_items(owner_id, status, kind, last_used_at DESC);
CREATE INDEX IF NOT EXISTS idx_memory_valid ON memory_items(owner_id, status, valid_to);
"""


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class MemoryStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=2, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(_CREATE)
        connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        return connection

    @staticmethod
    def _row_to_memory(row: sqlite3.Row) -> Memory:
        return Memory(
            memory_id=row["memory_id"],
            owner_id=row["owner_id"],
            kind=row["kind"],
            summary=row["summary"],
            text=row["text"],
            importance=row["importance"],
            source=json.loads(row["source_json"]),
            status=row["status"],
            revision=row["revision"],
            valid_from=_parse(row["valid_from"]),
            valid_to=_parse(row["valid_to"]),
            review_at=_parse(row["review_at"]),
            evidence_count=row["evidence_count"],
            last_used_at=_parse(row["last_used_at"]),
            use_count=row["use_count"],
            derived_from_record_id=row["derived_from_record_id"],
            created_at=_parse(row["created_at"]),
            updated_at=_parse(row["updated_at"]),
            deleted_at=_parse(row["deleted_at"]),
        )

    def insert(self, memory: Memory) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO memory_items (memory_id, owner_id, kind, summary, text, importance,
                    source_json, status, revision, valid_from, valid_to, review_at, evidence_count,
                    last_used_at, use_count, derived_from_record_id, created_at, updated_at, deleted_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    memory.memory_id, memory.owner_id, memory.kind, memory.summary, memory.text,
                    memory.importance, memory.source.model_dump_json(), memory.status, memory.revision,
                    _iso(memory.valid_from), _iso(memory.valid_to), _iso(memory.review_at),
                    memory.evidence_count, _iso(memory.last_used_at), memory.use_count,
                    memory.derived_from_record_id, _iso(memory.created_at), _iso(memory.updated_at),
                    _iso(memory.deleted_at),
                ),
            )

    def save(self, memory: Memory) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE memory_items SET kind=?, summary=?, text=?, importance=?, source_json=?,
                    status=?, revision=?, valid_from=?, valid_to=?, review_at=?, evidence_count=?,
                    last_used_at=?, use_count=?, derived_from_record_id=?, updated_at=?, deleted_at=?
                   WHERE memory_id=? AND owner_id=?""",
                (
                    memory.kind, memory.summary, memory.text, memory.importance,
                    memory.source.model_dump_json(), memory.status, memory.revision,
                    _iso(memory.valid_from), _iso(memory.valid_to), _iso(memory.review_at),
                    memory.evidence_count, _iso(memory.last_used_at), memory.use_count,
                    memory.derived_from_record_id, _iso(memory.updated_at), _iso(memory.deleted_at),
                    memory.memory_id, memory.owner_id,
                ),
            )

    def get(self, memory_id: str, owner_id: str = "local") -> Memory | None:
        if not self.path.exists():
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM memory_items WHERE memory_id=? AND owner_id=?", (memory_id, owner_id)
            ).fetchone()
        return self._row_to_memory(row) if row else None

    def list(self, owner_id: str = "local", status: str | None = None) -> list[Memory]:
        if not self.path.exists():
            return []
        query = "SELECT * FROM memory_items WHERE owner_id=?"
        params: list[object] = [owner_id]
        if status:
            query += " AND status=?"
            params.append(status)
        query += " ORDER BY updated_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._row_to_memory(row) for row in rows]

    def touch(self, memory_ids: list[str], used_at: datetime, owner_id: str = "local") -> None:
        if not memory_ids:
            return
        with self._connect() as connection:
            for memory_id in memory_ids:
                connection.execute(
                    "UPDATE memory_items SET use_count=use_count+1, last_used_at=? "
                    "WHERE memory_id=? AND owner_id=?",
                    (_iso(used_at), memory_id, owner_id),
                )

    def purge_deleted(self, before: datetime, owner_id: str = "local") -> int:
        """Physically remove tombstones older than ``before``."""
        if not self.path.exists():
            return 0
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM memory_items WHERE owner_id=? AND status='deleted' AND deleted_at < ?",
                (owner_id, _iso(before)),
            )
            return cursor.rowcount or 0

"""SQLite latest snapshots, version checks and atomic writes; constructor has no IO."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from app.agent.errors import RuntimeFailure
from app.config.settings import PROJECT_ROOT
from app.schemas.archive import ArchiveInfo, MAX_ARCHIVES, MAX_SNAPSHOT_BYTES, Snapshot

DEFAULT_ARCHIVE_PATH = PROJECT_ROOT / "data/session-archives.sqlite3"
DB_VERSION = 1


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON keys")
        result[key] = value
    return result


def _invalid():
    return RuntimeFailure("archive_invalid", "存档结构损坏或来源记录不一致，未恢复。")


class SQLiteArchiveStore:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()

    @contextmanager
    def _connection(self, *, write=False):
        connection = None
        try:
            if not write and not self.path.exists():
                raise RuntimeFailure("archive_not_found", "存档不存在或已删除。", 404)
            if write:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                connection = sqlite3.connect(self.path, timeout=1, isolation_level=None)
            else:
                connection = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True,
                                             timeout=1, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_SNAPSHOT_BYTES + 65536)
            if write:
                connection.execute("PRAGMA secure_delete=ON")
                connection.execute("BEGIN IMMEDIATE")
            else:
                connection.execute("BEGIN")  # Metadata and payload must come from one read snapshot.
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version == 0 and write and not connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' LIMIT 1"
            ).fetchone():
                connection.execute("""CREATE TABLE archives (
                    archive_id TEXT PRIMARY KEY, archive_version INTEGER NOT NULL,
                    saved_at TEXT NOT NULL, session_revision INTEGER NOT NULL,
                    message_count INTEGER NOT NULL, draft_count INTEGER NOT NULL,
                    status TEXT NOT NULL, payload TEXT NOT NULL)""")
                connection.execute("CREATE TABLE archive_clock (value INTEGER NOT NULL)")
                connection.execute("INSERT INTO archive_clock VALUES (0)")
                connection.execute("PRAGMA user_version=1")
            elif version != DB_VERSION:
                raise RuntimeFailure("archive_version_unsupported", "存档数据库版本不受支持，未修改。")
            yield connection
            if write:
                connection.commit()
        except (sqlite3.Error, OSError):
            raise RuntimeFailure("archive_storage_error", "本地存档读写失败，请检查目录、文件或锁占用。", 503) from None
        finally:
            if connection:
                if connection.in_transaction:
                    connection.rollback()
                connection.close()

    @staticmethod
    def _info(row):
        try:
            return ArchiveInfo.model_validate(dict(row))
        except (ValidationError, ValueError, TypeError):
            raise _invalid() from None

    def list(self) -> list[ArchiveInfo]:
        if not self.path.exists():
            return []
        with self._connection() as connection:
            rows = connection.execute("""SELECT archive_id, archive_version, saved_at,
                session_revision, message_count, draft_count, status
                FROM archives ORDER BY saved_at DESC, archive_id LIMIT ?""", (MAX_ARCHIVES + 1,)).fetchall()
            if len(rows) > MAX_ARCHIVES:
                raise _invalid()
            return [self._info(row) for row in rows]

    def save(self, snapshot: Snapshot, expected_version: int | None) -> ArchiveInfo:
        payload = snapshot.model_dump_json()
        if len(payload.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
            raise RuntimeFailure("archive_too_large", "会话快照超过 4 MiB，未保存。", 413)
        sid = str(snapshot.session.session_id)
        with self._connection(write=True) as connection:
            row = connection.execute(
                "SELECT archive_version FROM archives WHERE archive_id=?", (sid,)).fetchone()
            current = row["archive_version"] if row else None
            if current != expected_version:
                raise RuntimeFailure("archive_version_conflict", "存档版本已变化，请先刷新存档列表。")
            if row is None and connection.execute("SELECT count(*) FROM archives").fetchone()[0] >= MAX_ARCHIVES:
                raise RuntimeFailure("archive_limit_exceeded", "本地存档数量达到上限，请先删除不需要的存档。")
            # Global monotonic versions also prevent stale deletes after delete/recreate (ABA).
            connection.execute("UPDATE archive_clock SET value=value+1")
            next_version = connection.execute("SELECT value FROM archive_clock").fetchone()[0]
            info = ArchiveInfo(
                archive_id=snapshot.session.session_id, archive_version=next_version,
                saved_at=datetime.now(timezone.utc), session_revision=snapshot.session.revision,
                message_count=len(snapshot.session.messages), draft_count=len(snapshot.drafts),
                status=snapshot.session.status,
            )
            connection.execute("""INSERT INTO archives VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(archive_id) DO UPDATE SET
                archive_version=excluded.archive_version, saved_at=excluded.saved_at,
                session_revision=excluded.session_revision, message_count=excluded.message_count,
                draft_count=excluded.draft_count, status=excluded.status, payload=excluded.payload""",
                (sid, info.archive_version, info.saved_at.isoformat(), info.session_revision,
                 info.message_count, info.draft_count, info.status.value, payload))
            return info

    def load(self, archive_id: UUID) -> tuple[ArchiveInfo, Snapshot]:
        with self._connection() as connection:
            row = connection.execute("""SELECT archive_id, archive_version, saved_at,
                session_revision, message_count, draft_count, status, length(CAST(payload AS BLOB)) AS size
                FROM archives WHERE archive_id=?""", (str(archive_id),)).fetchone()
            if row is None:
                raise RuntimeFailure("archive_not_found", "存档不存在或已删除。", 404)
            data = dict(row)
            size = data.pop("size")
            if not isinstance(size, int) or size > MAX_SNAPSHOT_BYTES:
                raise _invalid()
            info = self._info(data)
            raw = connection.execute("SELECT payload FROM archives WHERE archive_id=?",
                                     (str(archive_id),)).fetchone()[0]
            try:
                document = json.loads(raw, object_pairs_hook=_unique_object)
                if type(document.get("version")) is not int or document["version"] != 1:
                    raise RuntimeFailure("archive_version_unsupported", "快照版本不受支持，未恢复。")
                snapshot = Snapshot.model_validate(document)
                if (snapshot.session.session_id != archive_id or
                    snapshot.session.revision != info.session_revision or
                    len(snapshot.session.messages) != info.message_count or
                    len(snapshot.drafts) != info.draft_count or
                    snapshot.session.status != info.status):
                    raise ValueError("metadata mismatch")
            except (ValueError, TypeError, AttributeError, ValidationError):
                raise _invalid() from None
            return info, snapshot

    def delete(self, archive_id: UUID, expected_version: int) -> None:
        if not self.path.exists():
            raise RuntimeFailure("archive_not_found", "存档不存在或已删除。", 404)
        with self._connection(write=True) as connection:
            row = connection.execute("SELECT archive_version FROM archives WHERE archive_id=?",
                                     (str(archive_id),)).fetchone()
            if row is None:
                raise RuntimeFailure("archive_not_found", "存档不存在或已删除。", 404)
            if row["archive_version"] != expected_version:
                raise RuntimeFailure("archive_version_conflict", "存档版本已变化，请刷新后再删除。")
            connection.execute("DELETE FROM archives WHERE archive_id=?", (str(archive_id),))

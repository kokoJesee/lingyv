"""Tiny migration ladder for the project's SQLite stores.

Each store keeps `PRAGMA user_version` and a dict of version -> migration.
Opening a database upgrades it one step at a time, so adding a column later
never means hand-editing or clearing the file.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable

Migration = Callable[[sqlite3.Connection], None]


def current_version(connection: sqlite3.Connection) -> int:
    return int(connection.execute("PRAGMA user_version").fetchone()[0])


def migrate(connection: sqlite3.Connection, migrations: dict[int, Migration]) -> int:
    """Apply pending migrations in order and return the resulting version."""
    target = max(migrations) if migrations else 0
    version = current_version(connection)
    if version > target:
        raise RuntimeError(f"database version {version} is newer than supported {target}")
    for step in range(version + 1, target + 1):
        migration = migrations.get(step)
        if migration is not None:
            migration(connection)
        connection.execute(f"PRAGMA user_version={step}")
    return target


def has_table(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None

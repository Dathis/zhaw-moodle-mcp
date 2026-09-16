"""SQLite metadata store: known resources, downloads and sync history."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..models import Course, Resource

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS courses (
    id             INTEGER PRIMARY KEY,
    name           TEXT NOT NULL,
    short_name     TEXT,
    status         TEXT,
    last_synced_at TEXT
);
CREATE TABLE IF NOT EXISTS resources (
    id            TEXT PRIMARY KEY,
    course_id     INTEGER NOT NULL,
    folder_id     INTEGER,
    name          TEXT NOT NULL,
    section       TEXT,
    type          TEXT,
    module        TEXT,
    url           TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL,
    removed_at    TEXT,
    -- download state
    file_url      TEXT,
    filename      TEXT,
    etag          TEXT,
    last_modified TEXT,
    size          INTEGER,
    sha256        TEXT,
    local_path    TEXT,
    downloaded_at TEXT
);
CREATE INDEX IF NOT EXISTS resources_course ON resources(course_id);
CREATE TABLE IF NOT EXISTS sync_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id   INTEGER NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    dry_run     INTEGER NOT NULL,
    new         INTEGER NOT NULL,
    updated     INTEGER NOT NULL,
    restored    INTEGER NOT NULL,
    removed     INTEGER NOT NULL,
    failed      INTEGER NOT NULL,
    unchanged   INTEGER NOT NULL
);
"""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class StoredResource:
    id: str
    course_id: int
    name: str
    removed_at: str | None
    file_url: str | None
    filename: str | None
    etag: str | None
    last_modified: str | None
    size: int | None
    sha256: str | None
    local_path: str | None
    downloaded_at: str | None


class Database:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def upsert_course(self, course: Course, synced: bool = False) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO courses (id, name, short_name, status, last_synced_at) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET name=excluded.name, short_name=excluded.short_name,
                     status=excluded.status,
                     last_synced_at=COALESCE(excluded.last_synced_at, courses.last_synced_at)""",
                (course.id, course.name, course.short_name, course.status, now() if synced else None),
            )

    def upsert_resources(self, resources: Iterable[Resource]) -> None:
        """Record listed resources; does not touch download state."""
        ts = now()
        with self.conn:
            self.conn.executemany(
                """INSERT INTO resources (id, course_id, folder_id, name, section, type, module, url,
                                          first_seen_at, last_seen_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET name=excluded.name, section=excluded.section,
                     type=excluded.type, url=excluded.url, last_seen_at=excluded.last_seen_at,
                     removed_at=NULL""",
                [(r.id, r.course_id, r.folder_id, r.name, r.section, r.type, r.module, r.url, ts, ts)
                 for r in resources],
            )

    def _stored(self, row: sqlite3.Row) -> StoredResource:
        return StoredResource(**{k: row[k] for k in StoredResource.__dataclass_fields__})

    def get_resource(self, resource_id: str) -> StoredResource | None:
        row = self.conn.execute("SELECT * FROM resources WHERE id = ?", (resource_id,)).fetchone()
        return self._stored(row) if row else None

    def resources_for_course(self, course_id: int) -> dict[str, StoredResource]:
        rows = self.conn.execute("SELECT * FROM resources WHERE course_id = ?", (course_id,)).fetchall()
        return {row["id"]: self._stored(row) for row in rows}

    def set_file_url(self, resource_id: str, file_url: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE resources SET file_url = ? WHERE id = ?", (file_url, resource_id))

    def record_download(self, resource_id: str, *, file_url: str, filename: str, etag: str | None,
                        last_modified: str | None, size: int, sha256: str, local_path: str) -> None:
        with self.conn:
            self.conn.execute(
                """UPDATE resources SET file_url=?, filename=?, etag=?, last_modified=?, size=?, sha256=?,
                     local_path=?, downloaded_at=? WHERE id=?""",
                (file_url, filename, etag, last_modified, size, sha256, local_path, now(), resource_id),
            )

    def mark_removed(self, resource_ids: Iterable[str]) -> None:
        with self.conn:
            self.conn.executemany("UPDATE resources SET removed_at = ? WHERE id = ?",
                                  [(now(), rid) for rid in resource_ids])

    def add_sync_history(self, course_id: int, started_at: str, dry_run: bool, *, new: int, updated: int,
                         restored: int, removed: int, failed: int, unchanged: int) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO sync_history (course_id, started_at, finished_at, dry_run, new, updated,
                                             restored, removed, failed, unchanged)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (course_id, started_at, now(), int(dry_run), new, updated, restored, removed, failed, unchanged),
            )

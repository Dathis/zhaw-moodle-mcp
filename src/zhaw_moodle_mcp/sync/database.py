"""SQLite metadata store: course index (for search/changes), resources, downloads
and sync history."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..models import Course, Resource

SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS courses (
    id             INTEGER PRIMARY KEY,
    name           TEXT NOT NULL,
    short_name     TEXT,
    status         TEXT,
    last_synced_at TEXT
);
CREATE TABLE IF NOT EXISTS sections (
    id        INTEGER PRIMARY KEY,
    course_id INTEGER NOT NULL,
    number    INTEGER,
    title     TEXT NOT NULL,
    path      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sections_course ON sections(course_id);
CREATE TABLE IF NOT EXISTS modules (
    id            INTEGER PRIMARY KEY,
    course_id     INTEGER NOT NULL,
    section       TEXT,
    name          TEXT NOT NULL,
    module        TEXT NOT NULL,
    type          TEXT,
    url           TEXT,
    downloadable  INTEGER NOT NULL DEFAULT 0,
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL,
    removed_at    TEXT
);
CREATE INDEX IF NOT EXISTS modules_course ON modules(course_id);
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


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


@dataclass(frozen=True)
class IndexedSection:
    id: int
    number: int
    title: str
    path: str


@dataclass(frozen=True)
class IndexedModule:
    id: int
    section: str
    name: str
    module: str
    type: str
    url: str | None
    downloadable: bool


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
        self._migrate()
        self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.conn.commit()

    def _migrate(self) -> None:
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(courses)")}
        for column in ("indexed_at", "first_indexed_at"):
            if column not in columns:
                self.conn.execute(f"ALTER TABLE courses ADD COLUMN {column} TEXT")

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

    # --- course index -------------------------------------------------------

    def index_course(self, course: Course, sections: Iterable[IndexedSection],
                     modules: Iterable[IndexedModule]) -> None:
        """Replace the stored structure of a course; keeps first-seen times of modules."""
        ts = now()
        modules = list(modules)
        self.upsert_course(course)
        with self.conn:
            self.conn.execute(
                "UPDATE courses SET indexed_at = ?, first_indexed_at = COALESCE(first_indexed_at, ?) WHERE id = ?",
                (ts, ts, course.id),
            )
            self.conn.execute("DELETE FROM sections WHERE course_id = ?", (course.id,))
            self.conn.executemany(
                "INSERT OR REPLACE INTO sections (id, course_id, number, title, path) VALUES (?, ?, ?, ?, ?)",
                [(s.id, course.id, s.number, s.title, s.path) for s in sections],
            )
            self.conn.executemany(
                """INSERT INTO modules (id, course_id, section, name, module, type, url, downloadable,
                                        first_seen_at, last_seen_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET course_id=excluded.course_id, section=excluded.section,
                     name=excluded.name, module=excluded.module, type=excluded.type, url=excluded.url,
                     downloadable=excluded.downloadable, last_seen_at=excluded.last_seen_at, removed_at=NULL""",
                [(m.id, course.id, m.section, m.name, m.module, m.type, m.url, int(m.downloadable), ts, ts)
                 for m in modules],
            )
            current = [m.id for m in modules]
            self.conn.execute(
                f"""UPDATE modules SET removed_at = ? WHERE course_id = ? AND removed_at IS NULL
                    AND id NOT IN ({",".join("?" * len(current))})""",
                (ts, course.id, *current),
            )

    def course_index_times(self, course_id: int) -> tuple[datetime | None, datetime | None]:
        """(indexed_at, first_indexed_at) of a course."""
        row = self.conn.execute(
            "SELECT indexed_at, first_indexed_at FROM courses WHERE id = ?", (course_id,)).fetchone()
        if row is None:
            return None, None
        return _parse(row["indexed_at"]), _parse(row["first_indexed_at"])

    def module_first_seen(self, course_id: int) -> dict[int, datetime]:
        rows = self.conn.execute("SELECT id, first_seen_at FROM modules WHERE course_id = ?", (course_id,))
        return {row["id"]: _parse(row["first_seen_at"]) for row in rows}

    def search_rows(self, course_ids: Iterable[int] | None = None) -> list[sqlite3.Row]:
        """Everything searchable as rows with: kind, id, name, course_id, course_name, section,
        type, url, downloadable."""
        ids = None if course_ids is None else list(course_ids)
        where = "" if ids is None else f"WHERE c.id IN ({','.join('?' * len(ids))})"
        params = [] if ids is None else ids
        sql = f"""
            SELECT 'course' AS kind, CAST(c.id AS TEXT) AS id, c.name AS name, c.id AS course_id,
                   c.name AS course_name, c.short_name AS section, 'course' AS type, NULL AS url,
                   0 AS downloadable
              FROM courses c {where}
            UNION ALL
            SELECT 'section', CAST(s.id AS TEXT), s.title, c.id, c.name, s.path, 'section', NULL, 0
              FROM sections s JOIN courses c ON c.id = s.course_id {where}
            UNION ALL
            SELECT 'module', CAST(m.id AS TEXT), m.name, c.id, c.name, m.section, m.type, m.url, m.downloadable
              FROM modules m JOIN courses c ON c.id = m.course_id
              {where + " AND" if where else "WHERE"} m.removed_at IS NULL AND m.module NOT IN ('label', 'subsection')
            UNION ALL
            SELECT 'file', r.id, r.name, c.id, c.name, r.section, r.type, r.url, 1
              FROM resources r JOIN courses c ON c.id = r.course_id
              {where + " AND" if where else "WHERE"} r.folder_id IS NOT NULL AND r.removed_at IS NULL
        """
        return self.conn.execute(sql, params * 4).fetchall()

    # --- resources ----------------------------------------------------------

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

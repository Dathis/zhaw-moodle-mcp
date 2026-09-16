"""Downloading files and synchronising a course with the local directory."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..errors import ErrorCode, MoodleError, SessionExpired
from ..models import Course, SyncChange, SyncResult
from ..moodle.client import DownloadInfo, MoodleClient
from ..moodle.parser import filename_from_url, normalize_etag, sanitize_component
from ..moodle.resources import ResourceEntry
from .database import Database, StoredResource, now

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FileResult:
    path: Path
    size: int
    status: Literal["downloaded", "unchanged"]


class SyncService:
    def __init__(self, client: MoodleClient, db: Database, download_root: Path) -> None:
        self.client = client
        self.db = db
        self.download_root = download_root

    async def file_url(self, entry: ResourceEntry, refresh: bool = False) -> str:
        if entry.file_url and not refresh:
            return entry.file_url
        if not refresh and entry.resource.module == "resource":
            stored = self.db.get_resource(entry.id)
            if stored and stored.file_url:
                entry.file_url = stored.file_url
                return stored.file_url
        if entry.resource.module != "resource":
            raise MoodleError(ErrorCode.RESOURCE_NOT_DOWNLOADABLE, f"Resource {entry.id} is not a file.")
        entry.file_url = await self.client.resolve_resource_file(int(entry.id))
        self.db.set_file_url(entry.id, entry.file_url)
        return entry.file_url

    async def _download(self, entry: ResourceEntry, course: Course, destination: Path | None) -> DownloadInfo:
        url = await self.file_url(entry)
        try:
            return await self._download_url(entry, course, destination, url)
        except MoodleError as exc:
            # stored pluginfile URL may be stale after the file was replaced/renamed
            if exc.code != ErrorCode.RESOURCE_NOT_FOUND or entry.resource.module != "resource":
                raise
            url = await self.file_url(entry, refresh=True)
            return await self._download_url(entry, course, destination, url)

    def target_path(self, entry: ResourceEntry, course: Course, destination: Path | None, url: str) -> Path:
        filename = filename_from_url(url)
        if destination is None:
            return entry.default_path(self.download_root, course.name, filename)
        return destination.joinpath(*entry.folder_dirs[1:], sanitize_component(filename))

    async def _download_url(self, entry: ResourceEntry, course: Course, destination: Path | None,
                            url: str) -> DownloadInfo:
        info = await self.client.download(url, self.target_path(entry, course, destination, url))
        if destination is None:  # only the managed directory is tracked for sync
            self.db.record_download(
                entry.id, file_url=url, filename=info.path.name, etag=info.etag,
                last_modified=info.last_modified, size=info.size, sha256=info.sha256,
                local_path=str(info.path),
            )
        return info

    async def download_file(self, entry: ResourceEntry, course: Course, destination: Path | None,
                            overwrite: bool) -> FileResult:
        if not overwrite:
            url = await self.file_url(entry)
            existing = self.target_path(entry, course, destination, url)
            if existing.exists():
                return FileResult(existing, existing.stat().st_size, "unchanged")
        info = await self._download(entry, course, destination)
        return FileResult(info.path, info.size, "downloaded")

    # --- sync ---------------------------------------------------------------

    async def sync_course(self, course: Course, entries: list[ResourceEntry], *, dry_run: bool,
                          update_changed: bool) -> SyncResult:
        started = now()
        known = self.db.resources_for_course(course.id)
        if not dry_run:  # a dry run must not change what the next real sync compares against
            self.db.upsert_course(course)
            self.db.upsert_resources(e.resource for e in entries)
        files = [e for e in entries if e.is_file]
        result = SyncResult(course_id=course.id, course_name=course.name, dry_run=dry_run,
                            download_directory=str(self.download_root / sanitize_component(course.name)))

        async def handle(entry: ResourceEntry) -> None:
            stored = known.get(entry.id)
            change = SyncChange(resource_id=entry.id, name=entry.resource.name, section=entry.resource.section)
            try:
                bucket = await self._sync_file(entry, stored, course, change, dry_run, update_changed)
            except SessionExpired:
                raise
            except MoodleError as exc:
                log.warning("Sync of resource %s failed: %s", entry.id, exc.code)
                change.detail = exc.message
                bucket = "failed"
            if bucket == "unchanged":
                result.unchanged += 1
            else:
                getattr(result, bucket).append(change)
            if stored and stored.downloaded_at and stored.name != entry.resource.name:
                result.renamed.append(change.model_copy(update={"detail": f"was: {stored.name}"}))

        outcomes = await asyncio.gather(*(handle(e) for e in files), return_exceptions=True)
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                raise outcome

        current = {e.id for e in files}
        gone = [s for s in known.values()
                if s.id not in current and s.removed_at is None and s.downloaded_at is not None]
        result.removed = [SyncChange(resource_id=s.id, name=s.name, section="", path=s.local_path) for s in gone]
        if not dry_run:
            self.db.mark_removed(s.id for s in gone)
            self.db.upsert_course(course, synced=True)
        for bucket in (result.new, result.updated, result.restored, result.renamed, result.removed, result.failed):
            bucket.sort(key=lambda c: (c.section, c.name))
        self.db.add_sync_history(
            course.id, started, dry_run, new=len(result.new), updated=len(result.updated),
            restored=len(result.restored), removed=len(result.removed), failed=len(result.failed),
            unchanged=result.unchanged,
        )
        log.info("Synced course %s: %d new, %d updated, %d restored, %d unchanged, %d failed",
                 course.id, len(result.new), len(result.updated), len(result.restored),
                 result.unchanged, len(result.failed))
        return result

    async def _sync_file(self, entry: ResourceEntry, stored: StoredResource | None, course: Course,
                         change: SyncChange, dry_run: bool, update_changed: bool) -> str:
        if stored is None or stored.downloaded_at is None:
            if not dry_run:
                change.path = str((await self._download(entry, course, None)).path)
            return "new"

        change.path = stored.local_path
        url = await self.file_url(entry)
        probe = await self.client.probe_file(url)
        if probe.status == 404 and entry.resource.module == "resource":
            url = await self.file_url(entry, refresh=True)
            probe = await self.client.probe_file(url)
        if probe.status != 200:
            raise MoodleError(ErrorCode.DOWNLOAD_FAILED, f"File check returned HTTP {probe.status}.")

        stored_etag = normalize_etag(stored.etag)
        if probe.etag and stored_etag:
            changed = probe.etag != stored_etag
        else:
            changed = probe.last_modified != stored.last_modified or url != stored.file_url
        if changed:
            change.detail = f"modified {probe.last_modified}" if probe.last_modified else None
            if update_changed and not dry_run:
                change.path = str((await self._download(entry, course, None)).path)
            return "updated"

        if not stored.local_path or not Path(stored.local_path).exists():
            if not dry_run:
                change.path = str((await self._download(entry, course, None)).path)
            return "restored"
        return "unchanged"

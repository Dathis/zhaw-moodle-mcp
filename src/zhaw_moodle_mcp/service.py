"""MoodleService: the API the MCP tools use. Handles session validation,
automatic re-login and retrying the original operation."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypeVar

from .auth import SessionStore, interactive_login
from .config import Config
from .errors import AuthRequired, ErrorCode, MoodleError, SessionExpired
from .models import (
    AuthStatus,
    Course,
    CourseStructure,
    DownloadedFile,
    DownloadResult,
    LogoutResult,
    ResourceList,
    SyncResult,
)
from .models.course import CourseStatus
from .moodle import courses as moodle_courses
from .moodle.client import MoodleClient
from .moodle.resources import ResourceEntry, entries_from_structure, expand_folders
from .sync.database import Database
from .sync.sync_service import SyncService

log = logging.getLogger(__name__)

T = TypeVar("T")


class MoodleService:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.store = SessionStore(config.session.storage_state, config.browser.profile_directory)
        self.client = MoodleClient(config.moodle, self.store)
        self.db = Database(config.sync.database)
        self.sync = SyncService(self.client, self.db, config.moodle.download_directory)
        self._login_lock = asyncio.Lock()
        self._validated_at: float | None = None
        self._courses: dict[int, Course] = {}

    async def aclose(self) -> None:
        await self.client.aclose()
        self.db.close()

    # --- authentication -----------------------------------------------------

    async def auth_status(self) -> AuthStatus:
        url = self.config.moodle.base_url
        if not self.store.exists():
            return AuthStatus(authenticated=False, state="missing", moodle_url=url,
                              message="Not logged in. Call moodle_login.")
        await self.client.reset()
        if await self.client.check_session():
            self._validated_at = time.monotonic()
            return AuthStatus(authenticated=True, state="valid", moodle_url=url, message="Session is valid.")
        self._validated_at = None
        return AuthStatus(authenticated=False, state="expired", moodle_url=url,
                          message="Session expired. Call moodle_login.")

    async def login(self, force: bool = False) -> AuthStatus:
        async with self._login_lock:
            if not force:
                status = await self.auth_status()
                if status.authenticated:
                    return status
            await self._interactive_login()
        return AuthStatus(authenticated=True, state="valid", moodle_url=self.config.moodle.base_url,
                          message="Logged in.")

    async def _interactive_login(self) -> None:
        """Caller must hold the login lock."""
        log.info("Authentication state: login required")
        await interactive_login(self.config.moodle.base_url, self.config.browser, self.store)
        await self.client.reset()
        if not await self.client.check_session():
            raise MoodleError(ErrorCode.LOGIN_FAILED, "Login finished but Moodle does not accept the session.")
        self._validated_at = time.monotonic()
        log.info("Authentication state: logged in")

    async def logout(self) -> LogoutResult:
        server_logout = False
        if self.store.exists():
            try:
                if await self.client.check_session():
                    server_logout = await self.client.logout()
            except MoodleError:
                pass  # offline: still remove local state
        await self.client.reset()
        self.store.clear()
        self._validated_at = None
        log.info("Authentication state: logged out")
        return LogoutResult(logged_out=True, server_session_invalidated=server_logout,
                            message="Local session removed; the next request requires login.")

    async def _ensure_session(self) -> None:
        ttl = self.config.session.validation_ttl
        if self._validated_at is not None and time.monotonic() - self._validated_at < ttl:
            return
        async with self._login_lock:
            if self._validated_at is not None and time.monotonic() - self._validated_at < ttl:
                return  # another task logged in meanwhile
            if self.store.exists() and await self.client.check_session():
                self._validated_at = time.monotonic()
                return
            if not self.config.browser.auto_login:
                raise AuthRequired() if not self.store.exists() else SessionExpired(
                    "The Moodle session has expired. Call moodle_login.")
            await self._interactive_login()

    async def _run(self, op: Callable[[], Awaitable[T]]) -> T:
        """Run an idempotent operation; on session expiry log in again and retry once."""
        await self._ensure_session()
        try:
            return await op()
        except SessionExpired:
            log.info("Authentication state: session expired during request")
            self._validated_at = None
            await self.client.reset()
            await self._ensure_session()
            return await op()

    # --- courses ------------------------------------------------------------

    async def list_courses(self, status: CourseStatus | None) -> list[Course]:
        courses = await self._run(lambda: moodle_courses.list_courses(self.client))
        self._courses = {c.id: c for c in courses}
        return [c for c in courses if status is None or c.status == status]

    async def _course(self, course_id: int) -> Course:
        if course_id not in self._courses:
            await self.list_courses(None)
        try:
            return self._courses[course_id]
        except KeyError:
            raise MoodleError(ErrorCode.COURSE_NOT_FOUND, f"You are not enrolled in course {course_id}.") from None

    async def get_course(self, course_id: int) -> CourseStructure:
        course = await self._course(course_id)

        async def op() -> CourseStructure:
            state, icons = await asyncio.gather(
                moodle_courses.get_course_state(self.client, course_id),
                moodle_courses.get_activity_icons(self.client, course_id),
            )
            return moodle_courses.build_structure(course, state, icons)

        return await self._run(op)

    # --- resources ----------------------------------------------------------

    async def _entries(self, course_id: int, expand: bool | set[int], record: bool = True) -> list[ResourceEntry]:
        structure = await self.get_course(course_id)
        entries = entries_from_structure(structure)
        if expand:
            only = expand if isinstance(expand, set) else None
            entries = await self._run(lambda: expand_folders(self.client, entries, only))
        if record:  # sync records itself after comparing with the stored state
            self.db.upsert_course(structure.course)
            self.db.upsert_resources(e.resource for e in entries)
        return entries

    async def list_resources(self, course_id: int, include_folder_contents: bool) -> ResourceList:
        entries = await self._entries(course_id, include_folder_contents)
        known = self.db.resources_for_course(course_id)
        resources = []
        for e in entries:
            stored = known.get(e.id)
            if stored and stored.downloaded_at:
                e.resource.filename = stored.filename
                e.resource.size = stored.size
                e.resource.local_path = stored.local_path
            resources.append(e.resource)
        return ResourceList(course_id=course_id, resources=resources)

    async def _find_entry(self, resource_id: str) -> tuple[Course, ResourceEntry]:
        cmid_part, _, file_path = resource_id.partition("/")
        if not cmid_part.isdigit():
            raise MoodleError(ErrorCode.RESOURCE_NOT_FOUND, f"Invalid resource id {resource_id!r}.")
        cmid = int(cmid_part)
        stored = self.db.get_resource(cmid_part)
        if stored:
            candidates = [stored.course_id]
        else:  # unknown resource: search enrolled courses, active first
            candidates = [c.id for c in await self.list_courses(None)]
        for course_id in candidates:
            try:
                entries = await self._entries(course_id, {cmid} if file_path else False)
            except MoodleError as exc:
                if exc.code == ErrorCode.COURSE_NOT_FOUND:
                    continue
                raise
            for e in entries:
                if e.id == resource_id:
                    return await self._course(course_id), e
        raise MoodleError(ErrorCode.RESOURCE_NOT_FOUND, f"Resource {resource_id} not found in your courses.")

    async def download_resource(self, resource_id: str, destination: str | None,
                                overwrite: bool) -> DownloadResult:
        course, entry = await self._find_entry(resource_id)
        if not entry.resource.downloadable:
            raise MoodleError(ErrorCode.RESOURCE_NOT_DOWNLOADABLE,
                              f"{entry.resource.name!r} is a {entry.resource.type}, not a file. "
                              f"Open it at {entry.resource.url}")
        dest = self._destination(destination)

        async def op() -> list[DownloadedFile]:
            if entry.is_file:
                targets = [entry]
            else:  # whole folder
                targets = [e for e in await expand_folders(self.client, [entry]) if e.is_file]
            results = await asyncio.gather(
                *(self.sync.download_file(t, course, dest, overwrite) for t in targets)
            )
            return [DownloadedFile(resource_id=t.id, path=str(r.path), size=r.size, status=r.status)
                    for t, r in zip(targets, results, strict=True)]

        return DownloadResult(resource_id=resource_id, files=await self._run(op))

    def _destination(self, destination: str | None) -> Path | None:
        if not destination:
            return None
        path = Path(destination).expanduser()
        # relative paths are relative to the download directory, not the server's cwd
        return path if path.is_absolute() else self.config.moodle.download_directory / path

    # --- sync ---------------------------------------------------------------

    async def sync_course(self, course_id: int, dry_run: bool, update_changed: bool) -> SyncResult:
        course = await self._course(course_id)

        async def op() -> SyncResult:
            entries = await self._entries(course_id, True, record=False)
            return await self.sync.sync_course(course, entries, dry_run=dry_run, update_changed=update_changed)

        return await self._run(op)

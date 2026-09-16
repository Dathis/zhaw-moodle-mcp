"""MoodleService: the API the MCP tools use. Handles session validation,
automatic re-login and retrying the original operation."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import TypeVar
from zoneinfo import ZoneInfo

from .activity_service import ActivityService
from .auth import SessionStore, interactive_login
from .config import Config
from .errors import AuthRequired, ErrorCode, MoodleError, SessionExpired
from .models import (
    ActivityContent,
    AuthStatus,
    ContentLink,
    Course,
    CourseModule,
    CourseStructure,
    CourseSyncSummary,
    DownloadedFile,
    DownloadFailure,
    DownloadResult,
    FileText,
    LogoutResult,
    ResourceList,
    SyncAllResult,
    SyncResult,
)
from .models.course import CourseStatus
from .moodle import courses as moodle_courses
from .moodle.client import MoodleClient
from .moodle.content import Content, embedded_file_path, parse_page_view
from .moodle.content import ContentLink as ParsedLink
from .moodle.documents import extract_text
from .moodle.parser import sanitize_component
from .moodle.resources import ResourceEntry, entries_from_structure, expand_folders, module_dirs
from .sync.database import Database
from .sync.sync_service import SyncService

log = logging.getLogger(__name__)

T = TypeVar("T")

# activities whose text can link to files (downloadable via '<cmid>/<path>')
TEXT_MODULES = {"page", "label"}


def http_date(value: str | None) -> datetime | None:
    try:
        return parsedate_to_datetime(value) if value else None
    except (TypeError, ValueError):
        return None


class MoodleService:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.store = SessionStore(config.session.storage_state, config.browser.profile_directory)
        self.client = MoodleClient(config.moodle, self.store)
        self.db = Database(config.sync.database)
        self.sync = SyncService(self.client, self.db, config.moodle.download_directory)
        self._login_lock = asyncio.Lock()
        self._validated_at: float | None = None
        self._state_signature = self.store.signature()
        self._courses: dict[int, Course] = {}
        self._label_texts: dict[int, dict[int, Content]] = {}  # course id -> label cmid -> content
        self.activities = ActivityService(self)

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
        self._state_signature = self.store.signature()
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
        self._state_signature = self.store.signature()
        self._validated_at = None
        log.info("Authentication state: logged out")
        return LogoutResult(logged_out=True, server_session_invalidated=server_logout,
                            message="Local session removed; the next request requires login.")

    async def _follow_external_changes(self) -> None:
        """Pick up a logout or login done by another process (e.g. the CLI)."""
        signature = self.store.signature()
        if signature != self._state_signature:
            log.info("Stored session changed outside this server, reloading it")
            self._state_signature = signature
            self._validated_at = None
            await self.client.reset()

    async def _ensure_session(self) -> None:
        await self._follow_external_changes()
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

    async def run(self, op: Callable[[], Awaitable[T]]) -> T:
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
        courses = await self.run(lambda: moodle_courses.list_courses(self.client))
        self._courses = {c.id: c for c in courses}
        return [c for c in courses if status is None or c.status == status]

    async def course(self, course_id: int) -> Course:
        if course_id not in self._courses:
            await self.list_courses(None)
        try:
            return self._courses[course_id]
        except KeyError:
            raise MoodleError(ErrorCode.COURSE_NOT_FOUND, f"You are not enrolled in course {course_id}.") from None

    async def get_course(self, course_id: int) -> CourseStructure:
        course = await self.course(course_id)

        async def op() -> CourseStructure:
            state, page = await asyncio.gather(
                moodle_courses.get_course_state(self.client, course_id),
                moodle_courses.get_course_page(self.client, course_id),
            )
            self._label_texts[course_id] = page.labels
            labels = {cmid: content.text for cmid, content in page.labels.items()}
            return moodle_courses.build_structure(course, state, page.icons, labels)

        structure = await self.run(op)
        self.db.index_course(course, *moodle_courses.index_rows(structure))
        return structure

    # --- page / label content ----------------------------------------------

    async def _find_module(self, activity_id: int) -> tuple[CourseStructure, CourseModule, str]:
        """Course structure, module and section path of an activity; refreshes the index if unknown."""
        for attempt in range(2):
            row = self.db.conn.execute(
                "SELECT course_id FROM modules WHERE id = ? AND removed_at IS NULL", (activity_id,)).fetchone()
            course_ids = [row["course_id"]] if row else []
            if not course_ids and attempt == 0:
                course_ids = [c.id for c in await self.list_courses("active")]
            for course_id in course_ids:
                structure = await self.get_course(course_id)
                for section, module in moodle_courses.walk_modules(structure):
                    if module.id == activity_id:
                        return structure, module, section
        raise MoodleError(ErrorCode.RESOURCE_NOT_FOUND, f"Activity {activity_id} not found in your courses.")

    async def get_content(self, activity_id: int) -> ActivityContent:
        structure, module, section = await self._find_module(activity_id)
        return await self._content(structure.course, module, section)

    async def _content(self, course: Course, module: CourseModule, section: str) -> ActivityContent:
        base = dict(id=module.id, course_id=course.id, course_name=course.name, section=section,
                    name=module.name, module=module.module, url=module.url)
        if module.module == "label":
            content = self._label_texts.get(course.id, {}).get(module.id) or Content(text="")
        elif module.module == "page":
            tz = ZoneInfo(self.config.moodle.timezone)

            async def op() -> Content | None:
                html, path = await self.client.get_page("/mod/page/view.php", {"id": module.id})
                if not path.endswith("/mod/page/view.php"):
                    return None
                return parse_page_view(html, self.client.base_url, tz)

            content = await self.run(op)
            if content is None:
                return ActivityContent(**base, accessible=False, text="",
                                       note="Moodle does not let you open this page (yet).")
        else:
            raise MoodleError(ErrorCode.RESOURCE_NOT_DOWNLOADABLE,
                              f"{module.name!r} is a {module.type}; moodle_get_content supports pages and labels. "
                              "Use moodle_download_resource for files and moodle_list_assignments for assignments.")
        return ActivityContent(
            **base, text=content.text, modified_at=content.modified_at,
            links=[self._content_link(module.id, link) for link in content.links],
        )

    @staticmethod
    def _content_link(activity_id: int, link: ParsedLink) -> ContentLink:
        path = embedded_file_path(link.url) if link.kind == "file" else None
        return ContentLink(text=link.text, url=link.url, kind=link.kind, activity_id=link.activity_id,
                           resource_id=f"{activity_id}/{path}" if path else None)

    async def _download_embedded(self, structure: CourseStructure, module: CourseModule, section: str,
                                 file_path: str, destination: Path | None, overwrite: bool) -> DownloadResult:
        """Files linked in the text of a page or label: one (file_path) or all of them.
        When downloading all, files that are gone are reported instead of failing the rest."""
        course = structure.course
        content = await self._content(course, module, section)
        files: dict[str, ContentLink] = {}
        for link in content.links:
            if link.resource_id and link.resource_id not in files:
                files[link.resource_id] = link
        if file_path:
            wanted = f"{module.id}/{file_path}"
            if wanted not in files:
                raise MoodleError(ErrorCode.RESOURCE_NOT_FOUND,
                                  f"No file {file_path!r} is linked in {module.name!r}.")
            files = {wanted: files[wanted]}
        if not files:
            raise MoodleError(ErrorCode.RESOURCE_NOT_DOWNLOADABLE,
                              f"{module.name!r} contains no downloadable files; read it with moodle_get_content.")
        if destination is None:
            dirs = module_dirs(structure).get(module.id, [])
            destination = self.config.moodle.download_directory.joinpath(
                sanitize_component(course.name), *dirs, sanitize_component(module.name))

        async def fetch(resource_id: str, link: ContentLink) -> DownloadedFile | DownloadFailure:
            parts = resource_id.split("/", 1)[1].split("/")
            target = destination.joinpath(*map(sanitize_component, parts))
            if not overwrite and target.exists():
                return DownloadedFile(resource_id=resource_id, path=str(target),
                                      size=target.stat().st_size, status="unchanged")
            try:
                info = await self.client.download(link.url, target)
            except MoodleError as exc:
                if file_path or exc.code not in (ErrorCode.RESOURCE_NOT_FOUND, ErrorCode.DOWNLOAD_FAILED):
                    raise
                return DownloadFailure(resource_id=resource_id, error=str(exc))
            return DownloadedFile(resource_id=resource_id, path=str(info.path), size=info.size,
                                  status="downloaded")

        async def op() -> list[DownloadedFile | DownloadFailure]:
            return list(await asyncio.gather(*(fetch(rid, link) for rid, link in files.items())))

        results = await self.run(op)
        return DownloadResult(
            resource_id=f"{module.id}/{file_path}" if file_path else str(module.id),
            files=[r for r in results if isinstance(r, DownloadedFile)],
            failed=[r for r in results if isinstance(r, DownloadFailure)],
        )

    # --- resources ----------------------------------------------------------

    async def _entries(self, course_id: int, expand: bool | set[int], record: bool = True) -> list[ResourceEntry]:
        structure = await self.get_course(course_id)
        entries = entries_from_structure(structure)
        if expand:
            only = expand if isinstance(expand, set) else None
            entries = await self.run(lambda: expand_folders(self.client, entries, only))
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
                e.resource.modified_at = http_date(stored.last_modified)
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
                    return await self.course(course_id), e
        raise MoodleError(ErrorCode.RESOURCE_NOT_FOUND, f"Resource {resource_id} not found in your courses.")

    async def download_resource(self, resource_id: str, destination: str | None,
                                overwrite: bool) -> DownloadResult:
        dest = self._destination(destination)
        cmid_part, _, file_path = resource_id.partition("/")
        if cmid_part.isdigit():
            row = self.db.conn.execute(
                "SELECT module FROM modules WHERE id = ? AND removed_at IS NULL", (int(cmid_part),)).fetchone()
            if row is None or row["module"] in TEXT_MODULES:
                try:
                    structure, module, section = await self._find_module(int(cmid_part))
                except MoodleError as exc:
                    if exc.code != ErrorCode.RESOURCE_NOT_FOUND:
                        raise
                    structure = None  # unknown activity: the general lookup reports it
                if structure is not None and module.module in TEXT_MODULES:
                    return await self._download_embedded(structure, module, section, file_path, dest, overwrite)

        course, entry = await self._find_entry(resource_id)
        if not entry.resource.downloadable:
            raise MoodleError(ErrorCode.RESOURCE_NOT_DOWNLOADABLE,
                              f"{entry.resource.name!r} is a {entry.resource.type}, not a file. "
                              f"Open it at {entry.resource.url}")

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

        return DownloadResult(resource_id=resource_id, files=await self.run(op))

    async def read_file(self, resource_id: str, pages: str | None, max_chars: int) -> FileText:
        """Text of a single course file; downloads it first if it is not stored locally yet."""
        result = await self.download_resource(resource_id, None, overwrite=False)
        if len(result.files) != 1:
            ids = ", ".join(f.resource_id for f in result.files[:10])
            raise MoodleError(ErrorCode.RESOURCE_NOT_DOWNLOADABLE,
                              f"{resource_id} contains {len(result.files)} files; read them one by one: {ids}")
        file = result.files[0]
        doc = await asyncio.to_thread(extract_text, Path(file.path), pages, max_chars, self.client.base_url)
        next_pages = None
        if doc.truncated and doc.last_page is not None and doc.total_pages:
            next_pages = f"{doc.last_page + 1}-"
        return FileText(
            resource_id=file.resource_id, path=file.path, file_type=doc.file_type, text=doc.text,
            total_pages=doc.total_pages,
            pages=f"{doc.first_page}-{doc.last_page}" if doc.first_page and doc.last_page else None,
            truncated=doc.truncated, next_pages=next_pages, note=doc.note,
        )

    def _destination(self, destination: str | None) -> Path | None:
        if not destination:
            return None
        path = Path(destination).expanduser()
        # relative paths are relative to the download directory, not the server's cwd
        return path if path.is_absolute() else self.config.moodle.download_directory / path

    # --- sync ---------------------------------------------------------------

    async def sync_all(self, dry_run: bool, update_changed: bool) -> SyncAllResult:
        """Sync all active courses one after another; one failing course does not stop the rest."""
        summaries = []
        for course in await self.list_courses("active"):
            summary = CourseSyncSummary(course_id=course.id, course_name=course.name)
            try:
                r = await self.sync_course(course.id, dry_run, update_changed)
            except (AuthRequired, SessionExpired):
                raise
            except MoodleError as exc:
                summary.error = str(exc)
            else:
                summary = CourseSyncSummary(
                    course_id=course.id, course_name=course.name, new=len(r.new), updated=len(r.updated),
                    restored=len(r.restored), renamed=len(r.renamed), removed=len(r.removed),
                    failed=len(r.failed), unchanged=r.unchanged,
                    new_files=[c.name for c in (*r.new, *r.updated)][:20], failed_items=r.failed,
                )
            summaries.append(summary)
        return SyncAllResult(dry_run=dry_run, courses=summaries,
                             download_directory=str(self.config.moodle.download_directory))

    async def sync_course(self, course_id: int, dry_run: bool, update_changed: bool) -> SyncResult:
        course = await self.course(course_id)

        async def op() -> SyncResult:
            entries = await self._entries(course_id, True, record=False)
            return await self.sync.sync_course(course, entries, dry_run=dry_run, update_changed=update_changed)

        return await self.run(op)

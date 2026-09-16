"""Deadlines, assignments, announcements, recent changes and search.

Built on MoodleService, which provides session handling, courses and the index."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal
from zoneinfo import ZoneInfo

from .errors import ErrorCode, MoodleError, SessionExpired
from .models import (
    Announcement,
    AnnouncementList,
    Assignment,
    AssignmentList,
    Course,
    CourseChanges,
    Deadline,
    DeadlineList,
    ModuleChange,
    RecentChanges,
    SearchHit,
    SearchResult,
)
from .moodle import activities
from .moodle.courses import index_rows
from .moodle.dates import from_timestamp, parse_since
from .moodle.parser import clean_text
from .search import search as search_index

if TYPE_CHECKING:
    from .service import MoodleService

log = logging.getLogger(__name__)

SearchSource = Literal["auto", "index", "moodle"]
DAY = 86400
ANNOUNCEMENT_BODY_LIMIT = 10


class ActivityService:
    def __init__(self, core: MoodleService) -> None:
        self.core = core
        self.client = core.client
        self.db = core.db
        self.tz = ZoneInfo(core.config.moodle.timezone)
        self._news_forums: dict[int, int | None] = {}  # course id -> cmid of the announcements forum

    async def _courses(self, course_id: int | None) -> list[Course]:
        if course_id is not None:
            return [await self.core.course(course_id)]
        return await self.core.list_courses("active")

    # --- deadlines ----------------------------------------------------------

    def _deadline(self, event: dict[str, Any], names: dict[int, str]) -> Deadline:
        course = event.get("course") or {}
        course_id = int(course.get("id", 0))
        action = event.get("action") or {}
        return Deadline(
            course_id=course_id,
            course_name=names.get(course_id) or clean_text(course.get("fullname")),
            activity_id=activities.cmid_from_url(event.get("url")),
            activity_name=clean_text(event.get("activityname") or event.get("name")),
            module=event.get("modulename") or "",
            event=event.get("eventtype") or "",
            due_at=from_timestamp(event.get("timesort")) or datetime.now(UTC),
            overdue=bool(event.get("overdue")),
            action=clean_text(action.get("name")) or None,
            actionable=bool(action.get("actionable")),
            url=event.get("url") or event.get("viewurl") or "",
        )

    async def _events(self, time_from: int, time_to: int) -> list[dict[str, Any]]:
        return await self.core.run(lambda: activities.fetch_action_events(self.client, time_from, time_to))

    async def deadlines(self, days_ahead: int, course_id: int | None, include_overdue: bool) -> DeadlineList:
        now = int(time.time())
        start = now - 30 * DAY if include_overdue else now
        events = await self._events(start, now + days_ahead * DAY)
        names = {c.id: c.name for c in await self.core.list_courses(None)}
        items = [self._deadline(e, names) for e in events]
        items = [d for d in items
                 if (course_id is None or d.course_id == course_id) and (include_overdue or not d.overdue)]
        items.sort(key=lambda d: d.due_at)
        return DeadlineList(now=datetime.fromtimestamp(now, UTC),
                            until=datetime.fromtimestamp(now + days_ahead * DAY, UTC), deadlines=items)

    # --- assignments --------------------------------------------------------

    async def assignments(self, course_id: int | None) -> AssignmentList:
        result = AssignmentList(assignments=[])
        for course in await self._courses(course_id):
            structure = await self.core.get_course(course.id)
            _, modules = index_rows(structure)
            assigns = [m for m in modules if m.module == "assign"]

            async def read(m, course=course) -> Assignment | None:
                try:
                    page = await activities.fetch_assignment_page(self.client, m.id, self.tz)
                except SessionExpired:
                    raise
                except MoodleError as exc:
                    log.warning("Assignment %s could not be read: %s", m.id, exc.code)
                    result.failed.append(f"{course.name}: {m.name} ({exc.code})")
                    return None
                return Assignment(
                    id=m.id, course_id=course.id, course_name=course.name, section=m.section, name=m.name,
                    url=m.url or f"{self.client.base_url}/mod/assign/view.php?id={m.id}",
                    opens_at=page.opens_at, due_at=page.due_at, cutoff_at=page.cutoff_at, dates=page.dates,
                    submitted=None if page.submission_status == "unknown"
                    else page.submission_status == "submitted",
                    submission_status=page.submission_status, grading_status=page.grading_status,
                    overdue=page.overdue, time_remaining=page.time_remaining, details=page.details,
                    description=page.description, accessible=page.accessible,
                )

            async def read_all(assigns=assigns, read=read) -> list[Assignment | None]:
                return list(await asyncio.gather(*(read(m) for m in assigns)))

            result.assignments.extend(a for a in await self.core.run(read_all) if a)
        far_future = datetime.max.replace(tzinfo=UTC)
        result.assignments.sort(key=lambda a: (a.due_at or far_future, a.course_name, a.name))
        return result

    # --- announcements ------------------------------------------------------

    async def _news_forum(self, course: Course) -> tuple[int, list[activities.Discussion]] | None:
        """Find the announcements forum of a course (cached) and read its discussion list."""
        known = self._news_forums.get(course.id, -1)
        if known is None:
            return None
        if known != -1:
            try:
                html = await self.client.get_html("/mod/forum/view.php", {"id": known})
                return known, activities.parse_discussions(html)
            except MoodleError as exc:
                if exc.code != ErrorCode.RESOURCE_NOT_FOUND:
                    raise
                del self._news_forums[course.id]  # forum was removed: look again
        structure = await self.core.get_course(course.id)
        forums = [m for m in index_rows(structure)[1] if m.module == "forum"]
        for forum in forums:
            try:
                html = await self.client.get_html("/mod/forum/view.php", {"id": forum.id})
            except SessionExpired:
                raise
            except MoodleError as exc:
                log.info("Forum %s not readable (%s), skipping", forum.id, exc.code)
                continue
            if activities.is_news_forum(html):
                self._news_forums[course.id] = forum.id
                return forum.id, activities.parse_discussions(html)
        self._news_forums[course.id] = None
        return None

    async def announcements(self, course_id: int | None, since: datetime | None, limit: int,
                            include_message: bool = True) -> AnnouncementList:
        items: list[Announcement] = []
        for course in await self._courses(course_id):
            found = await self.core.run(lambda course=course: self._news_forum(course))
            if not found:
                continue
            for d in found[1]:
                changed = d.modified_at or d.created_at
                if since and changed and changed < since:
                    continue
                items.append(Announcement(
                    course_id=course.id, course_name=course.name, discussion_id=d.id, subject=d.subject,
                    author=d.author, created_at=d.created_at, modified_at=d.modified_at,
                    url=f"{self.client.base_url}/mod/forum/discuss.php?d={d.id}",
                ))
        oldest = datetime.min.replace(tzinfo=UTC)
        items.sort(key=lambda a: a.modified_at or a.created_at or oldest, reverse=True)
        items = items[:limit]

        if include_message:
            async def fill(a: Announcement) -> None:
                try:
                    post = await activities.fetch_first_post(self.client, a.discussion_id)
                except SessionExpired:
                    raise
                except MoodleError as exc:
                    log.warning("Announcement %s could not be read: %s", a.discussion_id, exc.code)
                    return
                if post:
                    a.message = post.message
                    a.attachments = post.attachments
                    a.author = post.author or a.author

            async def fill_all() -> None:
                await asyncio.gather(*(fill(a) for a in items[:ANNOUNCEMENT_BODY_LIMIT]))

            await self.core.run(fill_all)
        return AnnouncementList(announcements=items)

    # --- recent changes -----------------------------------------------------

    async def recent_changes(self, since_text: str, course_id: int | None) -> RecentChanges:
        try:
            since = parse_since(since_text, self.tz)
        except ValueError as exc:
            raise MoodleError(ErrorCode.MOODLE_ERROR,
                              f"Invalid 'since' value {since_text!r}; use e.g. 2026-09-14 or 7d.") from exc
        now = datetime.now(UTC)
        since_ts = int(since.timestamp())
        events = await self._events(int(now.timestamp()) - 30 * DAY, int(now.timestamp()) + 120 * DAY)
        names = {c.id: c.name for c in await self.core.list_courses(None)}
        new_deadlines = [self._deadline(e, names) for e in events if (e.get("timemodified") or 0) >= since_ts]

        result = RecentChanges(since=since, until=now, courses=[])
        for course in await self._courses(course_id):
            structure = await self.core.get_course(course.id)  # also refreshes the index
            _, modules = index_rows(structure)
            by_id = {m.id: m for m in modules}
            first_seen = self.db.module_first_seen(course.id)
            _, first_indexed = self.db.course_index_times(course.id)
            tracked = first_indexed is not None and first_indexed <= since

            updates = await self.core.run(
                lambda course=course: activities.fetch_updates_since(self.client, course.id, since_ts))
            changes = CourseChanges(course_id=course.id, course_name=course.name)
            for u in updates:
                m = by_id.get(u.cmid)
                if m is None or m.module == "subsection":
                    continue
                if not tracked:
                    kind = "changed"
                else:
                    seen = first_seen.get(m.id)
                    kind = "new" if seen and seen > since else "updated"
                changes.modules.append(ModuleChange(
                    id=m.id, name=m.name, module=m.module, type=m.type, section=m.section,
                    change=kind, updates=u.updates, updated_at=u.updated_at, url=m.url,
                ))
            changes.modules.sort(key=lambda c: c.updated_at or since, reverse=True)
            changes.announcements = (await self.announcements(course.id, since, limit=10)).announcements
            changes.deadlines = [d for d in new_deadlines if d.course_id == course.id]
            if changes.modules or changes.announcements or changes.deadlines:
                result.courses.append(changes)
            else:
                result.unchanged_courses.append(course.name)
        return result

    # --- search -------------------------------------------------------------

    async def _refresh_index(self, course_ids: list[int], force: bool) -> None:
        max_age = timedelta(minutes=self.core.config.search.index_max_age_minutes)
        now = datetime.now(UTC)
        for cid in course_ids:
            indexed_at, _ = self.db.course_index_times(cid)
            if force or indexed_at is None or now - indexed_at > max_age:
                await self.core.get_course(cid)

    def _course_url(self, course_id: int) -> str:
        return f"{self.client.base_url}/course/view.php?id={course_id}"

    async def search(self, query: str, course_id: int | None, kinds: set[str] | None, limit: int,
                     source: SearchSource, refresh: bool) -> SearchResult:
        if not query.strip():
            raise MoodleError(ErrorCode.MOODLE_ERROR, "Empty search query.")
        if source == "moodle":
            return await self._moodle_search(query, course_id, limit)

        active = [c.id for c in await self.core.list_courses("active")]
        scope = [course_id] if course_id is not None else active
        await self._refresh_index(scope, refresh)
        rows = self.db.search_rows([course_id] if course_id is not None else None)
        hits = []
        for score, row in search_index(query, rows, limit=limit, kinds=kinds):
            url = row["url"] or self._course_url(row["course_id"])
            hits.append(SearchHit(
                kind=row["kind"], id=row["id"], name=row["name"], course_id=row["course_id"],
                course_name=row["course_name"], section=row["section"] if row["kind"] != "course" else None,
                type=row["type"], url=url, downloadable=bool(row["downloadable"]), score=score,
            ))
        indexed = len({row["course_id"] for row in rows})
        result = SearchResult(query=query, source="index", hits=hits, indexed_courses=indexed)
        if not hits and source == "auto":
            moodle = await self._moodle_search(query, course_id, limit)
            moodle.note = "No match in names; these results come from Moodle's full-text search."
            return moodle
        if hits:
            result.note = ("Files inside folders are only searchable after moodle_list_resources "
                           "or moodle_sync_course has seen them.")
        return result

    async def _moodle_search(self, query: str, course_id: int | None, limit: int) -> SearchResult:
        found = await self.core.run(lambda: activities.global_search(self.client, query))
        modules = {row["id"]: row for row in self.db.search_rows() if row["kind"] == "module"}
        hits = []
        for rank, h in enumerate(found):
            if course_id is not None and h.course_id != course_id:
                continue
            cmid = str(h.cmid) if h.cmid else None
            known = modules.get(cmid or "")
            hits.append(SearchHit(
                kind="module" if cmid else "course",
                id=cmid or str(h.course_id or ""),
                name=h.title, course_id=h.course_id or 0, course_name=h.course_name or "",
                section=known["section"] if known else None,
                type=known["type"] if known else h.module, url=h.url,
                downloadable=bool(known and known["downloadable"]),
                score=round(1 - rank / max(len(found), 1), 3), snippet=h.snippet,
            ))
        return SearchResult(query=query, source="moodle", hits=hits[:limit],
                            note="Moodle full-text search (first result page).")

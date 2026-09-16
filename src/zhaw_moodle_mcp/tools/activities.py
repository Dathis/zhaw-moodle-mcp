from mcp.server.mcpserver import MCPServer

from ..errors import ErrorCode, MoodleError
from ..models import AnnouncementList, AssignmentList, DeadlineList, RecentChanges
from ..moodle.dates import parse_since
from ..service import MoodleService
from . import READ_ONLY, tool_call


def register(server: MCPServer, service: MoodleService) -> None:
    activities = service.activities

    @server.tool(annotations=READ_ONLY)
    async def moodle_get_deadlines(
        days_ahead: int = 14, course_id: int | None = None, include_overdue: bool = True
    ) -> DeadlineList:
        """Open to-dos with deadlines across all courses (assignment due dates, quiz closing
        times, ...), sorted by date. Answers "What do I need to submit this week?".
        Activities already completed are not listed.

        Args:
            days_ahead: how far to look ahead (days)
            course_id: restrict to one course
            include_overdue: also list overdue to-dos from the last 30 days
        """
        with tool_call("moodle_get_deadlines", days_ahead=days_ahead, course_id=course_id):
            return await activities.deadlines(max(1, min(days_ahead, 365)), course_id, include_overdue)

    @server.tool(annotations=READ_ONLY)
    async def moodle_list_assignments(course_id: int | None = None) -> AssignmentList:
        """Assignments with opening date, deadline, submission and grading status and
        description. Without course_id all current courses are included (one request per assignment).

        Args:
            course_id: restrict to one course
        """
        with tool_call("moodle_list_assignments", course_id=course_id):
            return await activities.assignments(course_id)

    @server.tool(annotations=READ_ONLY)
    async def moodle_get_announcements(
        course_id: int | None = None, since: str | None = None, limit: int = 10
    ) -> AnnouncementList:
        """Recent announcements ("Ankündigungen") posted by lecturers, newest first,
        with the message text of the most recent ones.

        Args:
            course_id: restrict to one course (default: all current courses)
            since: only announcements newer than this: ISO date/datetime (2026-09-14) or relative (7d, 12h, 2w)
            limit: maximum number of announcements
        """
        with tool_call("moodle_get_announcements", course_id=course_id, since=since):
            try:
                since_dt = parse_since(since, activities.tz) if since else None
            except ValueError as exc:
                raise MoodleError(ErrorCode.MOODLE_ERROR, f"Invalid 'since' value {since!r}.") from exc
            return await activities.announcements(course_id, since_dt, max(1, min(limit, 50)))

    @server.tool(annotations=READ_ONLY)
    async def moodle_get_recent_changes(since: str = "7d", course_id: int | None = None) -> RecentChanges:
        """What changed in Moodle since a point in time: new or updated materials and
        activities, new announcements, and new or changed deadlines, grouped by course.
        Answers "What is new in Moodle since Monday?".

        Args:
            since: ISO date/datetime (2026-09-14, 2026-09-14T08:00) or relative (7d, 12h, 2w)
            course_id: restrict to one course (default: all current courses)
        """
        with tool_call("moodle_get_recent_changes", since=since, course_id=course_id):
            return await activities.recent_changes(since, course_id)

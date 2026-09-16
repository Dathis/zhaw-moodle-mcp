from typing import Literal

from mcp.server.mcpserver import MCPServer

from ..models import CourseList, CourseStructure
from ..service import MoodleService
from . import READ_ONLY, tool_call


def register(server: MCPServer, service: MoodleService) -> None:
    @server.tool(annotations=READ_ONLY)
    async def moodle_list_courses(status: Literal["active", "past", "future", "all"] = "active") -> CourseList:
        """List the user's ZHAW Moodle courses.

        Args:
            status: "active" (current semester, default), "past", "future" or "all"
        """
        with tool_call("moodle_list_courses", status=status):
            courses = await service.list_courses(None if status == "all" else status)
            return CourseList(courses=courses)

    @server.tool(annotations=READ_ONLY)
    async def moodle_get_course(course_id: int) -> CourseStructure:
        """Get the structure of a course: sections, nested subsections and all
        activities/materials (files, folders, links, pages, quizzes, assignments, ...).

        Args:
            course_id: course id from moodle_list_courses
        """
        with tool_call("moodle_get_course", course_id=course_id):
            return await service.get_course(course_id)

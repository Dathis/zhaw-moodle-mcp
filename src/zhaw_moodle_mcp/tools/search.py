from typing import Literal

from mcp.server.mcpserver import MCPServer

from ..models import SearchResult
from ..service import MoodleService
from . import READ_ONLY, tool_call

Kind = Literal["course", "section", "module", "file"]


def register(server: MCPServer, service: MoodleService) -> None:
    @server.tool(annotations=READ_ONLY)
    async def moodle_search(
        query: str,
        course_id: int | None = None,
        kinds: list[Kind] | None = None,
        source: Literal["auto", "index", "moodle"] = "auto",
        limit: int = 25,
        refresh: bool = False,
    ) -> SearchResult:
        """Search Moodle by name: courses, sections, materials (files, folders, links, pages),
        assignments, quizzes and files inside folders. Case- and umlaut-insensitive, tolerant
        to small typos; all words must match (e.g. "OR ZGB", "Übung 3", "Semesterprogramm").

        Args:
            query: words to look for
            course_id: restrict to one course
            kinds: restrict result kinds: course, section, module (activities/materials), file (in folders)
            source: "index" = local index of names (fast; current courses are refreshed automatically),
                "moodle" = Moodle's full-text search (also finds words inside text areas),
                "auto" = index first, Moodle full-text search if nothing matches
            limit: maximum number of hits
            refresh: re-read the courses from Moodle before searching
        """
        with tool_call("moodle_search", course_id=course_id, source=source):
            return await service.activities.search(
                query, course_id, set(kinds) if kinds else None, max(1, min(limit, 100)), source, refresh)

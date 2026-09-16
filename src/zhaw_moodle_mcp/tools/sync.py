from mcp.server.mcpserver import MCPServer

from ..models import SyncResult
from ..service import MoodleService
from . import WRITES_LOCAL_FILES, tool_call


def register(server: MCPServer, service: MoodleService) -> None:
    @server.tool(annotations=WRITES_LOCAL_FILES)
    async def moodle_sync_course(course_id: int, dry_run: bool = False, update_changed: bool = True) -> SyncResult:
        """Synchronise all files of a course into the local download directory.
        Downloads new files, re-downloads changed ones and reports what changed.
        Files removed from Moodle are reported but never deleted locally.

        Args:
            course_id: course id from moodle_list_courses
            dry_run: only report what would change, download nothing
            update_changed: re-download files that changed on Moodle
        """
        with tool_call("moodle_sync_course", course_id=course_id, dry_run=dry_run):
            return await service.sync_course(course_id, dry_run, update_changed)

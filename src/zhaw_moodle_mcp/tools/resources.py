from mcp.server.mcpserver import MCPServer

from ..models import DownloadResult, ResourceList
from ..service import MoodleService
from . import READ_ONLY, WRITES_LOCAL_FILES, tool_call


def register(server: MCPServer, service: MoodleService) -> None:
    @server.tool(annotations=READ_ONLY)
    async def moodle_list_resources(course_id: int, include_folder_contents: bool = True) -> ResourceList:
        """List learning materials of a course: files (pdf, powerpoint, word, excel, zip, ...),
        folders, external links and Moodle pages, with their section.

        Args:
            course_id: course id from moodle_list_courses
            include_folder_contents: also list the individual files inside folders
                (one extra request per folder)
        """
        with tool_call("moodle_list_resources", course_id=course_id):
            return await service.list_resources(course_id, include_folder_contents)

    @server.tool(annotations=WRITES_LOCAL_FILES)
    async def moodle_download_resource(
        resource_id: str, destination: str | None = None, overwrite: bool = True
    ) -> DownloadResult:
        """Download a file, all files of a folder, or files linked in a page/text block,
        and return the local paths.

        By default files go to the managed directory <download_directory>/<course>/<section>/,
        which moodle_sync_course also uses.

        Args:
            resource_id: id from moodle_list_resources ("<cmid>" or "<folder cmid>/<path>"),
                or a file link's resource_id from moodle_get_content ("<page/label id>/<file>");
                the id of a page or text block downloads all files linked in it
            destination: optional target directory; relative paths are resolved
                against the configured download directory
            overwrite: replace an existing local file (otherwise it is left as is)
        """
        with tool_call("moodle_download_resource", resource_id=resource_id):
            return await service.download_resource(resource_id, destination, overwrite)

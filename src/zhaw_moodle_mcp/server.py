"""MCP server assembly (stdio transport)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.mcpserver import MCPServer

from . import __version__
from .config import Config
from .service import MoodleService
from .tools import register_all

INSTRUCTIONS = """\
Access to the user's ZHAW Moodle (moodle.zhaw.ch): courses, course structure,
learning materials, downloads and synchronisation into a local folder, search,
assignments, deadlines, announcements and recent changes.
Authentication happens in a browser window where the user signs in with SWITCH edu-ID;
tools trigger it automatically when needed. Never ask the user for passwords or cookies.
Typical flows: moodle_search or moodle_list_courses -> moodle_get_course / moodle_list_resources ->
moodle_download_resource or moodle_sync_course; "what is due / new?" -> moodle_get_deadlines,
moodle_get_recent_changes, moodle_get_announcements."""


def create_server(config: Config) -> MCPServer:
    service = MoodleService(config)

    @asynccontextmanager
    async def lifespan(_: MCPServer) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await service.aclose()

    server = MCPServer(
        name="zhaw-moodle",
        version=__version__,
        instructions=INSTRUCTIONS,
        lifespan=lifespan,
    )
    register_all(server, service)
    return server

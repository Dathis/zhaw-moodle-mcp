from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations

from ..models import AuthStatus, LogoutResult
from ..service import MoodleService
from . import READ_ONLY, tool_call


def register(server: MCPServer, service: MoodleService) -> None:
    @server.tool(annotations=READ_ONLY)
    async def moodle_auth_status() -> AuthStatus:
        """Check whether a valid ZHAW Moodle session exists. Never returns credentials."""
        with tool_call("moodle_auth_status"):
            return await service.auth_status()

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True, openWorldHint=True))
    async def moodle_login(force: bool = False) -> AuthStatus:
        """Log in to ZHAW Moodle. Opens a browser window where the USER signs in with
        SWITCH edu-ID (incl. MFA) themselves; returns once login is detected.
        Never ask the user for their password. Other tools log in automatically when needed.

        Args:
            force: open the login browser even if the current session is still valid
        """
        with tool_call("moodle_login"):
            return await service.login(force)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True))
    async def moodle_logout() -> LogoutResult:
        """Log out: end the Moodle session and delete the locally stored browser session
        and cookies. The next Moodle request will require a new login."""
        with tool_call("moodle_logout"):
            return await service.logout()

"""MCP tool definitions. Tools only translate between MCP and MoodleService."""

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from ..errors import MoodleError
from ..service import MoodleService

log = logging.getLogger(__name__)

READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=True)
WRITES_LOCAL_FILES = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True,
                                     openWorldHint=True)


@contextmanager
def tool_call(name: str, **ids: object) -> Iterator[None]:
    """Log the call and turn domain errors into MCP tool errors with a stable code."""
    details = " ".join(f"{k}={v}" for k, v in ids.items() if v is not None)
    log.info("Tool %s %s", name, details)
    started = time.monotonic()
    try:
        yield
    except MoodleError as exc:
        log.info("Tool %s failed: %s (%.1fs)", name, exc.code, time.monotonic() - started)
        raise ToolError(str(exc)) from exc
    log.info("Tool %s done (%.1fs)", name, time.monotonic() - started)


def register_all(server: MCPServer, service: MoodleService) -> None:
    from . import activities, auth, courses, resources, search, sync

    for module in (auth, courses, resources, sync, search, activities):
        module.register(server, service)

"""Command line: `zhaw-moodle-mcp` runs the MCP server (stdio); subcommands help
with authentication outside of an MCP client."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from . import __version__
from .config import config_path, load_config
from .errors import MoodleError
from .logging_setup import setup_logging


def _run_service_command(command: str) -> int:
    from .service import MoodleService

    async def run() -> int:
        service = MoodleService(load_config())
        try:
            if command == "login":
                result = await service.login(force=False)
            elif command == "logout":
                result = await service.logout()
            else:
                result = await service.auth_status()
            print(result.model_dump_json(indent=2))
            return 0
        except MoodleError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        finally:
            await service.aclose()

    return asyncio.run(run())


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="zhaw-moodle-mcp", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--debug", action="store_true", help="verbose logging (secrets stay redacted)")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="run the MCP server on stdio (default)")
    sub.add_parser("login", help="log in via browser and store the session")
    sub.add_parser("logout", help="end the session and delete local auth data")
    sub.add_parser("status", help="check whether the stored session is valid")
    sub.add_parser("config-path", help="print the location of the config file")
    args = parser.parse_args(argv)

    setup_logging(logging.DEBUG if args.debug else logging.INFO)
    if args.command == "config-path":
        print(config_path())
        return
    if args.command in ("login", "logout", "status"):
        sys.exit(_run_service_command(args.command))

    from .server import create_server

    create_server(load_config()).run("stdio")
